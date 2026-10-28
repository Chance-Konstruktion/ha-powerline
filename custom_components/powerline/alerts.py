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

# A directional difference must be substantial and persist before it is
# reported. Estimated star links reuse node-level readings and are excluded.
ASYMMETRY_RATIO = 0.50
MIN_ASYMMETRY_DIFFERENCE = 150
ASYMMETRY_DURATION_SECONDS = 30 * 60


class TopologyAlerts:
    """Stateful alert detection across poll cycles."""

    def __init__(self) -> None:
        # edge key -> when the rate first dropped below the threshold
        self._slow_since: dict[str, float] = {}
        # edge keys already alerted (until they recover)
        self._alerted: set[str] = set()
        # (edge key, cause key) pairs already emitted while the pattern holds
        self._cause_alerted: set[tuple[str, str]] = set()
        # Edge keys whose measured TX/RX asymmetry is currently persisting.
        self._asymmetry_since: dict[str, float] = {}
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

        # Compute each edge's history context once. Bucket counts inspect the
        # lookback window without sorting; peer comparisons reuse this cache.
        edge_history: dict[str, dict[str, Any]] = {}
        for edge in edges:
            source, destination = edge["source"], edge["destination"]
            key = edge_key(source, destination)
            if key in edge_history:
                continue
            enough = (
                history.bucket_count(
                    source,
                    destination,
                    hours=CAUSE_HINT_HISTORY_HOURS,
                    now=timestamp,
                )
                >= MIN_BUCKETS_FOR_CAUSE_HINT
            )
            edge_history[key] = {
                "enough": enough,
                "baseline": history.baseline(source, destination, now=timestamp),
                "recurring_low_hours": (
                    history.recurring_low_hours(
                        source, destination, now=timestamp
                    )
                    if enough
                    else []
                ),
            }

        # Sustained slowdowns against the 24h baseline.
        for edge in edges:
            key = edge_key(edge["source"], edge["destination"])
            baseline = edge_history[key]["baseline"]
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
            hints = self._cause_hints(edge, edges, edge_history, timestamp)
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
        for key in tuple(self._asymmetry_since):
            if key not in current_edge_keys:
                self._asymmetry_since.pop(key, None)

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

    def _cause_hints(
        self,
        edge: dict[str, Any],
        edges: list[dict[str, Any]],
        edge_history: dict[str, dict[str, Any]],
        now: datetime,
    ) -> dict[str, dict[str, Any]]:
        """Return currently supported hints for one edge, keyed by translation key."""
        source = edge["source"]
        destination = edge["destination"]
        key = edge_key(source, destination)
        context = edge_history.get(key)
        if context is None or not context["enough"]:
            self._asymmetry_since.pop(key, None)
            return {}
        average = float(edge.get("average_rate") or 0)

        hints: dict[str, dict[str, Any]] = {}

        # Repeated slow windows at the same time of day can fit an appliance
        # or switched-mode power supply coming on regularly.
        if context["recurring_low_hours"]:
            hints["recurring_load"] = {}

        # Compare only edges with their own adequate history. Requiring a
        # healthy peer baseline avoids labelling a generally poor network.
        own_baseline = context["baseline"]
        peer_baselines: list[float] = []
        peer_current_rates: list[float] = []
        for peer in edges:
            if edge_key(peer["source"], peer["destination"]) == edge_key(
                source, destination
            ):
                continue
            peer_key = edge_key(peer["source"], peer["destination"])
            peer_context = edge_history.get(peer_key)
            if peer_context is None or not peer_context["enough"]:
                continue
            peer_baseline = peer_context["baseline"]
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

        # Estimated star links reuse node-level TX/RX readings and cannot
        # support a directional diagnosis. Measured asymmetry must persist.
        tx = float(edge.get("tx_phy_rate") or 0)
        rx = float(edge.get("rx_phy_rate") or 0)
        asymmetric = (
            not edge.get("estimated", False)
            and tx > 0
            and rx > 0
            and min(tx, rx) <= ASYMMETRY_RATIO * max(tx, rx)
            and max(tx, rx) - min(tx, rx) >= MIN_ASYMMETRY_DIFFERENCE
        )
        if asymmetric:
            since = self._asymmetry_since.setdefault(key, now.timestamp())
            if now.timestamp() - since >= ASYMMETRY_DURATION_SECONDS:
                hints["directional_asymmetry"] = {
                    "tx": round(tx),
                    "rx": round(rx),
                }
        else:
            self._asymmetry_since.pop(key, None)

        return hints
