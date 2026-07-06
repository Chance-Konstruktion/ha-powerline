"""Topology history: link-rate and online-time tracking over time.

Two-tier storage per edge keeps memory bounded:
- raw samples for the last hour (one per poll), and
- 15-minute aggregate buckets (avg/min/max) for the last 30 days.

Node online/offline transitions are stored as a compact change list.
The bucket tier and the transitions survive restarts via the HA Store
(see ``as_dict``/``restore``); the raw tier is transient by design.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

BUCKET_SECONDS = 900
RECENT_SECONDS = 3600
RETENTION_SECONDS = 30 * 24 * 3600

# instability(): minimum aggregate buckets before a coefficient of
# variation is considered meaningful rather than startup noise.
MIN_BUCKETS_FOR_INSTABILITY = 4


def edge_key(source: str, destination: str) -> str:
    return f"{source}|{destination}"


class TopologyHistory:
    """Rolling history of edge link rates and node online transitions."""

    def __init__(self) -> None:
        # edge key -> deque[(unix_ts, average_rate)]
        self._recent: dict[str, deque[tuple[float, int]]] = {}
        # edge key -> {bucket_start: [sum, count, min, max]}
        self._buckets: dict[str, dict[int, list[int]]] = {}
        # mac -> [[unix_ts, online], ...] (transitions only)
        self._online: dict[str, list[list[Any]]] = {}

    # ── Recording ─────────────────────────────────────────

    def record(self, topology: dict[str, Any], now: datetime | None = None) -> None:
        """Add one poll's topology snapshot to the history."""
        ts = (now or datetime.now(UTC)).timestamp()

        for edge in topology.get("edges", []):
            key = edge_key(edge["source"], edge["destination"])
            avg = int(edge["average_rate"])
            self._recent.setdefault(key, deque()).append((ts, avg))
            bucket = int(ts // BUCKET_SECONDS) * BUCKET_SECONDS
            agg = self._buckets.setdefault(key, {}).setdefault(
                bucket, [0, 0, avg, avg]
            )
            agg[0] += avg
            agg[1] += 1
            agg[2] = min(agg[2], avg)
            agg[3] = max(agg[3], avg)

        for node in topology.get("nodes", []):
            transitions = self._online.setdefault(node["mac"], [])
            online = bool(node.get("online"))
            if not transitions or bool(transitions[-1][1]) != online:
                transitions.append([ts, online])

        self._prune(ts)

    def _prune(self, ts: float) -> None:
        recent_cutoff = ts - RECENT_SECONDS
        for samples in self._recent.values():
            while samples and samples[0][0] < recent_cutoff:
                samples.popleft()

        bucket_cutoff = ts - RETENTION_SECONDS
        for key, buckets in list(self._buckets.items()):
            for start in [b for b in buckets if b < bucket_cutoff]:
                del buckets[start]
            if not buckets:
                del self._buckets[key]
                self._recent.pop(key, None)

        for mac, transitions in list(self._online.items()):
            # Keep the last transition before the cutoff so the current
            # state's start is never lost.
            while len(transitions) > 1 and transitions[1][0] < bucket_cutoff:
                transitions.pop(0)
            if not transitions:
                del self._online[mac]

    # ── Queries ───────────────────────────────────────────

    def series(
        self,
        source: str,
        destination: str,
        hours: float,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Time series of the edge's average rate over the last `hours`.

        Up to one hour: raw per-poll samples. Beyond that: 15-minute
        aggregates with min/max.
        """
        ts = (now or datetime.now(UTC)).timestamp()
        cutoff = ts - hours * 3600
        key = edge_key(source, destination)

        if hours <= 1:
            return [
                {"t": int(t), "avg": avg}
                for t, avg in self._recent.get(key, ())
                if t >= cutoff
            ]

        return [
            {
                "t": start,
                "avg": round(agg[0] / agg[1]),
                "min": agg[2],
                "max": agg[3],
            }
            for start, agg in sorted(self._buckets.get(key, {}).items())
            if start >= cutoff
        ]

    def baseline(
        self,
        source: str,
        destination: str,
        now: datetime | None = None,
        hours: float = 24,
        exclude_seconds: int = 1800,
    ) -> float | None:
        """Typical average rate over `hours`, ignoring the newest samples.

        The exclusion window keeps an ongoing slowdown from dragging its
        own baseline down, which would mask the alert.
        """
        ts = (now or datetime.now(UTC)).timestamp()
        cutoff = ts - hours * 3600
        newest = ts - exclude_seconds
        values = [
            agg[0] / agg[1]
            for start, agg in self._buckets.get(edge_key(source, destination), {}).items()
            if cutoff <= start < newest
        ]
        if not values:
            return None
        return sum(values) / len(values)

    def instability(
        self,
        source: str,
        destination: str,
        hours: float = 24,
        now: datetime | None = None,
    ) -> float | None:
        """How jumpy the edge's rate was over `hours` (0 = steady).

        Combines the variation *between* buckets (coefficient of variation
        of bucket averages) with the swing *inside* buckets (mean min–max
        range) — fast flapping would otherwise average out to a flat-looking
        bucket mean. None until enough buckets exist to say anything.
        """
        ts = (now or datetime.now(UTC)).timestamp()
        cutoff = ts - hours * 3600
        entries = [
            (agg[0] / agg[1], agg[2], agg[3])
            for start, agg in self._buckets.get(edge_key(source, destination), {}).items()
            if start >= cutoff
        ]
        if len(entries) < MIN_BUCKETS_FOR_INSTABILITY:
            return None
        means = [e[0] for e in entries]
        mean = sum(means) / len(means)
        if mean <= 0:
            return None
        variance = sum((v - mean) ** 2 for v in means) / len(means)
        between = (variance**0.5) / mean
        within = sum(mx - mn for _, mn, mx in entries) / len(entries) / (2 * mean)
        return round(between + within, 4)

    def most_unstable(
        self, hours: float = 24, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """The edge with the highest instability over `hours`, if any."""
        worst: dict[str, Any] | None = None
        for key in self._buckets:
            source, _, destination = key.partition("|")
            value = self.instability(source, destination, hours, now)
            if value is None or value == 0:
                continue
            if worst is None or value > worst["instability"]:
                worst = {
                    "source": source,
                    "destination": destination,
                    "instability": value,
                }
        return worst

    def online_ratio(
        self, mac: str, hours: float = 24, now: datetime | None = None
    ) -> float | None:
        """Fraction of the last `hours` the adapter was online (0..1)."""
        transitions = self._online.get(mac)
        if not transitions:
            return None
        ts = (now or datetime.now(UTC)).timestamp()
        cutoff = ts - hours * 3600
        online_seconds = 0.0
        prev_ts, prev_state = None, None
        for t, state in transitions:
            if prev_ts is not None and prev_state:
                online_seconds += max(0.0, min(t, ts) - max(prev_ts, cutoff))
            prev_ts, prev_state = t, state
        if prev_state:
            online_seconds += max(0.0, ts - max(prev_ts, cutoff))
        window = min(hours * 3600, ts - transitions[0][0])
        if window <= 0:
            return 1.0 if prev_state else 0.0
        return round(min(1.0, online_seconds / window), 4)

    # ── Persistence ───────────────────────────────────────

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable snapshot (bucket tier + online transitions)."""
        return {
            "buckets": {
                key: {str(start): agg for start, agg in buckets.items()}
                for key, buckets in self._buckets.items()
            },
            "online": self._online,
        }

    def restore(self, data: dict[str, Any] | None) -> None:
        """Load a snapshot produced by ``as_dict`` (e.g. after a restart)."""
        if not data:
            return
        self._buckets = {
            key: {int(start): list(agg) for start, agg in buckets.items()}
            for key, buckets in (data.get("buckets") or {}).items()
        }
        self._online = {
            mac: [list(t) for t in transitions]
            for mac, transitions in (data.get("online") or {}).items()
        }
