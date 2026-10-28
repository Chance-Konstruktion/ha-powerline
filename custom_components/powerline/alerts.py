"""Smart topology alerts: turn history + events into actionable hints.

Produces alert dicts for persistent notifications. Cause hints are deliberately
heuristic: each one requires a minimum history window and describes a likely
cause, never a confirmed diagnosis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median
from typing import Any

from .history import TopologyHistory, edge_key

# A link is "slow" below this fraction of its 24h baseline.
SLOW_FACTOR = 0.6
# Alert recovery hysteresis avoids flapping near the threshold.
RECOVER_FACTOR = 0.8
# The slowdown must persist this long before alerting.
SLOW_DURATION_SECONDS = 1800

# Cause hints require at least eight 15-minute buckets (~2h); this avoids
# labelling one-off noise or newly discovered links as installation faults.
CAUSE_HINT_HISTORY_HOURS = 24
MIN_BUCKETS_FOR_CAUSE_HINT = 8

# A route is an outlier only when its historic and current rates are much
# lower than peers, and those peers themselves have a healthy baseline.
PEER_GOOD_RATE = 300
ISOLATED_BASELINE_FACTOR = 0.55
ISOLATED_CURRENT_FACTOR = 0.60

# A large directional difference is meaningful only when both directions
# report non-zero rates and the absolute gap is substantial.
ASYMMETRY_RATIO = 0.50
MIN_ASYMMETRY_DIFFERENCE = 150

# Physical distance is not inferred from rate or graph shape. It must be
# provided explicitly by a future/other topology source as distance_m.
MAX_SHORT_LINK_DISTANCE_M = 10
WEAK_LINK_RATE = 150
WEAK_SHORT_LINK_BASELINE_MAX = 250


class TopologyAlerts:
    """Stateful alert detection across poll cycles."""

    def __init__(self) -> None:
        # edge key -> when the rate first dropped below the threshold
        self._slow_since: dict[str, float] = {}
        # edge keys already alerted (until they recover)
        self._alerted: set[str] = set()
        # (edge key, cause key) pairs already emitted while the pattern holds
        self._cause_alerted: set[tuple[str, str]] = set()
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
        edges = topology.get("edges", [])

        # Sustained slowdowns against the 24h baseline.
        for edge in edges:
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
                    alerts.append(
                        {
                            "type": "link_slow",
                            "source": edge["source"],
                            "destination": edge["destination"],
                            "average": average,
                            "baseline": round(baseline),
                            "minutes": int((ts - since) // 60),
                        }
                    )
            elif not baseline or average >= RECOVER_FACTOR * baseline:
                self._slow_since.pop(key, None)
                self._alerted.discard(key)

        # Likely causes and practical checks.
        current_edge_keys = set()
        for edge in edges:
            key = edge_key(edge["source"], edge["destination"])
            current_edge_keys.add(key)
            hints = self._cause_hints(edge, edges, history, timestamp)
            active_causes = set(hints)

            # A pattern that no longer meets its thresholds can alert again
            # if it returns later.
            for token in tuple(self._cause_alerted):
                if token[0] == key and token[1] not in active_causes:
                    self._cause_alerted.discard(token)

            for cause, details in hints.items():
                token = (key, cause)
                if token in self._cause_alerted:
                    continue
                self._cause_alerted.add(token)
                alerts.append(
                    {
                        "type": "link_cause",
                        "cause": cause,
                        "source": edge["source"],
                        "destination": edge["destination"],
                        "average": edge.get("average_rate", 0),
                        **details,
                    }
                )

        # If an edge disappears, do not suppress a diagnosis if that same
        # edge is later rediscovered.
        for token in tuple(self._cause_alerted):
            if token[0] not in current_edge_keys:
                self._cause_alerted.discard(token)

        # Adapters removed after the offline retention window.
        for event in events:
            if event.get("event") == "adapter_removed":
                alerts.append({"type": "adapter_removed", "mac": event["mac"]})

        # Newly discovered adapters.
        new_adapters = topology.get("analysis", {}).get("new_adapters") or []
        if self._first_check:
            self._first_check = False
        else:
            for mac in new_adapters:
                alerts.append({"type": "adapter_new", "mac": mac})

        return alerts

    def _has_enough_history(
        self,
        source: str,
        destination: str,
        history: TopologyHistory,
        now: datetime,
    ) -> bool:
        """Require eight real buckets in the lookback window (~two hours)."""
        cutoff = now.timestamp() - CAUSE_HINT_HISTORY_HOURS * 3600
        current = now.timestamp()
        points = history.series(
            source, destination, CAUSE_HINT_HISTORY_HOURS, now=now
        )
        return sum(
            1 for point in points if cutoff <= point["t"] <= current
        ) >= MIN_BUCKETS_FOR_CAUSE_HINT

    def _cause_hints(
        self,
        edge: dict[str, Any],
        edges: list[dict[str, Any]],
        history: TopologyHistory,
        now: datetime,
    ) -> dict[str, dict[str, Any]]:
        """Return currently supported hints for one edge, keyed by translation key."""
        source = edge["source"]
        destination = edge["destination"]
        average = float(edge.get("average_rate") or 0)
        if not self._has_enough_history(source, destination, history, now):
            return {}

        hints: dict[str, dict[str, Any]] = {}

        # Repeated slow windows at the same time of day can fit an appliance
        # or switched-mode power supply coming on regularly.
        if history.recurring_low_hours(source, destination, now=now):
            hints["recurring_load"] = {}

        # Compare only edges with their own adequate history. Requiring a
        # healthy peer baseline avoids labelling a generally poor network.
        own_baseline = history.baseline(source, destination, now=now)
        peer_baselines: list[float] = []
        peer_current_rates: list[float] = []
        for peer in edges:
            if edge_key(peer["source"], peer["destination"]) == edge_key(
                source, destination
            ):
                continue
            peer_source, peer_destination = peer["source"], peer["destination"]
            if not self._has_enough_history(
                peer_source, peer_destination, history, now
            ):
                continue
            peer_baseline = history.baseline(
                peer_source, peer_destination, now=now
            )
            peer_rate = float(peer.get("average_rate") or 0)
            if peer_baseline is None or peer_rate <= 0:
                continue
            peer_baselines.append(peer_baseline)
            peer_current_rates.append(peer_rate)

        if own_baseline is not None and peer_baselines:
            typical_peer_baseline = median(peer_baselines)
            typical_peer_current = median(peer_current_rates)
            if (
                typical_peer_baseline >= PEER_GOOD_RATE
                and typical_peer_current >= PEER_GOOD_RATE
                and own_baseline <= ISOLATED_BASELINE_FACTOR * typical_peer_baseline
                and average <= ISOLATED_CURRENT_FACTOR * typical_peer_current
            ):
                hints["isolated_weak_link"] = {
                    "peer_average": round(typical_peer_current)
                }

        # A zero reading can mean a direction did not report a rate, so it
        # must not be treated as reliable asymmetry.
        tx = float(edge.get("tx_phy_rate") or 0)
        rx = float(edge.get("rx_phy_rate") or 0)
        if (
            tx > 0
            and rx > 0
            and min(tx, rx) <= ASYMMETRY_RATIO * max(tx, rx)
            and max(tx, rx) - min(tx, rx) >= MIN_ASYMMETRY_DIFFERENCE
        ):
            hints["directional_asymmetry"] = {
                "tx": round(tx),
                "rx": round(rx),
            }

        # Distance is optional by design. Never infer a "short" route merely
        # from adapter count, a high rate, or an unmeasured physical layout.
        distance_m = edge.get("distance_m")
        if (
            isinstance(distance_m, (int, float))
            and not isinstance(distance_m, bool)
            and 0 < distance_m <= MAX_SHORT_LINK_DISTANCE_M
            and average <= WEAK_LINK_RATE
            and own_baseline is not None
            and own_baseline <= WEAK_SHORT_LINK_BASELINE_MAX
        ):
            hints["short_weak_link"] = {
                "distance_m": round(float(distance_m), 1)
            }

        return hints
