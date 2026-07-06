"""Smart topology alerts: turn history + events into actionable hints.

Produces alert dicts the coordinator surfaces as persistent
notifications, e.g. "link much slower than usual for 30 minutes",
"adapter removed", "new adapter discovered".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .history import TopologyHistory, edge_key

# A link is "slow" below this fraction of its 24h baseline …
SLOW_FACTOR = 0.6
# … and recovered above this fraction (hysteresis so the alert doesn't
# flap when the rate hovers around the threshold).
RECOVER_FACTOR = 0.8
# The slowdown must persist this long before alerting.
SLOW_DURATION_SECONDS = 1800


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
