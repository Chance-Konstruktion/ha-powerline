"""Smart topology alerts: turn history + events into actionable hints.

Produces alert dicts the coordinator surfaces as persistent
notifications, e.g. "link much slower than usual for 30 minutes",
"adapter removed", "new adapter discovered".
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median
from typing import Any

from .history import BUCKET_SECONDS, TopologyHistory, edge_key

# A link is "slow" below this fraction of its 24h baseline …
SLOW_FACTOR = 0.6
# … and recovered above this fraction (hysteresis so the alert doesn't
# flap when the rate hovers around the threshold).
RECOVER_FACTOR = 0.8
# The slowdown must persist this long before alerting.
SLOW_DURATION_SECONDS = 1800

# Cause hints intentionally need more evidence than the basic slow-link alert.
# Four days provide at least three occurrences of a daily pattern; requiring
# 192 of the 384 possible quarter-hour buckets tolerates some missing polls.
DAILY_PATTERN_HOURS = 96
MIN_DAILY_PATTERN_BUCKETS = 192
MIN_DAILY_PATTERN_DAYS = 3
DAILY_DIP_FACTOR = 0.6

# A "bad outlet/filter" hint needs at least two hours of samples and a healthy
# peer link; one isolated low reading is not enough.
PEER_COMPARISON_HOURS = 6
MIN_PEER_BUCKETS = 8
PEER_SLOW_FACTOR = 0.5
PEER_GOOD_MIN_RATE = 400

# TX/RX asymmetry is judged from recent raw polls rather than one snapshot.
DIRECTIONAL_HISTORY_HOURS = 1
MIN_DIRECTIONAL_SAMPLES = 8
ASYMMETRY_RATIO = 2.0
ASYMMETRY_SAMPLE_FRACTION = 0.75

# A low-rate phase/RCD hint requires a persistent low rate on a measured,
# reasonably balanced link; estimated graph edges are not suitable evidence.
WEAK_LINK_MAX_RATE = 250
MIN_WEAK_LINK_BUCKETS = 4
WEAK_LINK_SAMPLE_FRACTION = 0.75
BALANCED_DIRECTION_RATIO = 1.7


class TopologyAlerts:
    """Stateful alert detection across poll cycles."""

    def __init__(self) -> None:
        # edge key -> when the rate first dropped below the threshold
        self._slow_since: dict[str, float] = {}
        # edge keys already alerted (until they recover)
        self._alerted: set[str] = set()
        # First check() after startup: every adapter looks "new", so
        # new-adapter alerts are suppressed once.
        self._first_check = True

    def check(
        self,
        topology: dict[str, Any],
        history: TopologyHistory,
        events: list[dict[str, Any]],
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return alerts raised by this poll's snapshot."""
        timestamp = now or datetime.now(UTC)
        ts = timestamp.timestamp()
        alerts: list[dict[str, Any]] = []

        # ── Sustained slowdowns against the 24h baseline ──
        for edge in topology.get("edges", []):
            key = edge_key(edge["source"], edge["destination"])
            baseline = history.baseline(
                edge["source"], edge["destination"], now=timestamp
            )
            average = edge["average_rate"]
            if baseline and average < SLOW_FACTOR * baseline:
                since = self._slow_since.setdefault(key, ts)
                if (
                    ts - since >= SLOW_DURATION_SECONDS
                    and key not in self._alerted
                ):
                    self._alerted.add(key)
                    alert = {
                        "type": "link_slow",
                        "source": edge["source"],
                        "destination": edge["destination"],
                        "average": average,
                        "baseline": round(baseline),
                        "minutes": int((ts - since) // 60),
                    }
                    diagnosis = self._diagnosis(edge, topology, history, timestamp)
                    if diagnosis:
                        alert.update(diagnosis)
                    alerts.append(alert)
            elif not baseline or average >= RECOVER_FACTOR * baseline:
                self._slow_since.pop(key, None)
                self._alerted.discard(key)

        # ── Adapters removed after the offline retention window ──
        for event in events:
            if event.get("event") == "adapter_removed":
                alerts.append({"type": "adapter_removed", "mac": event["mac"]})

        # ── Newly discovered adapters ──
        new_adapters = topology.get("analysis", {}).get("new_adapters") or []
        if self._first_check:
            self._first_check = False
        else:
            for mac in new_adapters:
                alerts.append({"type": "adapter_new", "mac": mac})

        return alerts

    def _diagnosis(
        self,
        edge: dict[str, Any],
        topology: dict[str, Any],
        history: TopologyHistory,
        now: datetime,
    ) -> dict[str, str] | None:
        """Return a cautious cause/action code only when history supports it."""
        source = edge["source"]
        destination = edge["destination"]

        if self._has_daily_interference(history, source, destination, now):
            return {
                "cause": "daily_interference",
                "action": "check_noise_sources",
            }

        directional = history.directions(
            source,
            destination,
            hours=DIRECTIONAL_HISTORY_HOURS,
            now=now,
        )
        usable = [
            sample for sample in directional
            if max(sample["tx"], sample["rx"]) > 0
        ]
        if len(usable) >= MIN_DIRECTIONAL_SAMPLES:
            tx_lower = sum(
                1 for sample in usable
                if sample["rx"] > 0
                and sample["tx"] * ASYMMETRY_RATIO <= sample["rx"]
            )
            rx_lower = sum(
                1 for sample in usable
                if sample["tx"] > 0
                and sample["rx"] * ASYMMETRY_RATIO <= sample["tx"]
            )
            if (
                max(tx_lower, rx_lower) / len(usable)
                >= ASYMMETRY_SAMPLE_FRACTION
            ):
                return {
                    "cause": "directional_interference",
                    "action": "check_both_ends",
                }

        target_samples = history.series(
            source,
            destination,
            hours=PEER_COMPARISON_HOURS,
            now=now,
        )
        peer_edges = [
            candidate for candidate in topology.get("edges", [])
            if (
                candidate.get("source") != source
                or candidate.get("destination") != destination
            )
            and candidate.get("average_rate", 0) > 0
        ]
        if len(target_samples) >= MIN_PEER_BUCKETS and peer_edges:
            peer_history_rates: list[float] = []
            for peer in peer_edges:
                peer_samples = history.series(
                    peer["source"],
                    peer["destination"],
                    hours=PEER_COMPARISON_HOURS,
                    now=now,
                )
                if len(peer_samples) >= MIN_PEER_BUCKETS:
                    peer_history_rates.extend(
                        sample["avg"] for sample in peer_samples
                    )
            peer_current_rates = [
                float(peer["average_rate"]) for peer in peer_edges
            ]
            if peer_history_rates and peer_current_rates:
                target_median = median(sample["avg"] for sample in target_samples)
                peer_history_median = median(peer_history_rates)
                peer_current_median = median(peer_current_rates)
                if (
                    peer_history_median >= PEER_GOOD_MIN_RATE
                    and peer_current_median >= PEER_GOOD_MIN_RATE
                    and target_median <= PEER_SLOW_FACTOR * peer_history_median
                    and edge["average_rate"] <= PEER_SLOW_FACTOR * peer_current_median
                ):
                    return {
                        "cause": "power_strip_or_filter",
                        "action": "use_wall_socket",
                    }

        tx = int(edge.get("tx_phy_rate") or 0)
        rx = int(edge.get("rx_phy_rate") or 0)
        weak_samples = history.series(
            source,
            destination,
            hours=PEER_COMPARISON_HOURS,
            now=now,
        )
        if (
            not edge.get("estimated", False)
            and tx > 0
            and rx > 0
            and max(tx, rx) / min(tx, rx) <= BALANCED_DIRECTION_RATIO
            and edge.get("average_rate", 0) <= WEAK_LINK_MAX_RATE
            and len(weak_samples) >= MIN_WEAK_LINK_BUCKETS
        ):
            weak_fraction = sum(
                1 for sample in weak_samples
                if sample["avg"] <= WEAK_LINK_MAX_RATE
            ) / len(weak_samples)
            if (
                median(sample["avg"] for sample in weak_samples)
                <= WEAK_LINK_MAX_RATE
                and weak_fraction >= WEAK_LINK_SAMPLE_FRACTION
            ):
                return {
                    "cause": "phase_or_rcd",
                    "action": "check_circuit_phase",
                }

        return None

    @staticmethod
    def _has_daily_interference(
        history: TopologyHistory,
        source: str,
        destination: str,
        now: datetime,
    ) -> bool:
        """Detect a strong rate dip recurring in the same 15-minute daily slot."""
        samples = history.series(
            source,
            destination,
            hours=DAILY_PATTERN_HOURS,
            now=now,
        )
        if len(samples) < MIN_DAILY_PATTERN_BUCKETS:
            return False

        by_time = {int(sample["t"]): sample for sample in samples}
        recurring_days: dict[int, set[int]] = {}
        for sample in samples:
            stamp = int(sample["t"])
            previous = by_time.get(stamp - BUCKET_SECONDS)
            following = by_time.get(stamp + BUCKET_SECONDS)
            if previous is None or following is None:
                continue
            local_reference = median(
                [previous["avg"], following["avg"]]
            )
            if (
                local_reference > 0
                and sample["avg"] <= DAILY_DIP_FACTOR * local_reference
            ):
                day = stamp // 86400
                slot = (stamp % 86400) // BUCKET_SECONDS
                recurring_days.setdefault(slot, set()).add(day)

        return any(
            len(days) >= MIN_DAILY_PATTERN_DAYS
            for days in recurring_days.values()
        )
