"""Powerline topology graph model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .const import MANUFACTURER, get_mac

DEFAULT_OFFLINE_RETENTION_SECONDS = 3600


class TopologyManager:
    """Build and track the Powerline network graph."""

    def __init__(self, offline_retention_seconds: int = DEFAULT_OFFLINE_RETENTION_SECONDS) -> None:
        self._offline_retention_seconds = offline_retention_seconds
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []

    def update(
        self,
        devices: dict[str, dict[str, Any]],
        now: datetime | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Update the graph from the latest adapter inventory."""
        timestamp = now or datetime.now(UTC)
        seen_macs: set[str] = set()

        for dev in devices.values():
            mac = get_mac(dev)
            if not mac:
                continue
            seen_macs.add(mac)
            node = self._node_from_device(mac, dev, timestamp)
            previous = self._nodes.get(mac)
            if previous is None:
                self._record_event("adapter_online", timestamp, mac=mac)
            elif not previous.get("online", True):
                self._record_event("adapter_online", timestamp, mac=mac)
            self._nodes[mac] = node

        for mac, node in list(self._nodes.items()):
            if mac in seen_macs:
                continue
            if node.get("online", True):
                node["online"] = False
                self._record_event("adapter_offline", timestamp, mac=mac)
            last_update = datetime.fromisoformat(node["last_update"])
            if (timestamp - last_update).total_seconds() > self._offline_retention_seconds:
                self._nodes.pop(mac)

        old_edges = self._edges
        self._edges = self._build_edges(devices, seen_macs, timestamp)
        self._record_edge_changes(old_edges, self._edges, timestamp)

        return self.as_dict()

    def drain_events(self) -> list[dict[str, Any]]:
        """Return pending topology events and clear the queue."""
        events = self.events
        self.events = []
        return events

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        """Return the topology API payload."""
        return {
            "nodes": [dict(node) for _, node in sorted(self._nodes.items())],
            "edges": [dict(edge) for _, edge in sorted(self._edges.items())],
        }

    def _node_from_device(
        self, mac: str, dev: dict[str, Any], timestamp: datetime
    ) -> dict[str, Any]:
        return {
            "mac": mac,
            "name": dev.get("name") or dev.get("alias") or mac,
            "manufacturer": dev.get("manufacturer") or MANUFACTURER,
            "model": dev.get("model") or "",
            "firmware": dev.get("firmware") or dev.get("firmware_ver") or "",
            "hardware_revision": dev.get("hardware_revision") or dev.get("hw_revision") or "",
            "role": self._role(dev),
            "online": bool(dev.get("_online", True)),
            "last_update": timestamp.isoformat(),
        }

    def _build_edges(
        self,
        devices: dict[str, dict[str, Any]],
        seen_macs: set[str],
        timestamp: datetime,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        edges: dict[tuple[str, str], dict[str, Any]] = {}
        online_macs = sorted(
            mac for mac in seen_macs if devices.get(mac, {}).get("_online", True)
        )
        if len(online_macs) < 2:
            return edges

        root = self._root_mac(devices, online_macs)
        for mac in online_macs:
            if mac == root:
                continue
            source, destination = sorted((root, mac))
            peer = devices[mac]
            root_dev = devices[root]
            rate_dev = root_dev if root_dev.get("tx_rate") or root_dev.get("rx_rate") else peer
            tx = int(rate_dev.get("tx_rate") or 0)
            rx = int(rate_dev.get("rx_rate") or 0)
            average = round((tx + rx) / 2)
            edges[(source, destination)] = {
                "source": source,
                "destination": destination,
                "tx_phy_rate": tx,
                "rx_phy_rate": rx,
                "average_rate": average,
                "link_quality": self._link_quality(average),
                "errors": max(
                    int(root_dev.get("errors") or root_dev.get("error_count") or 0),
                    int(peer.get("errors") or peer.get("error_count") or 0),
                ),
                "timestamp": timestamp.isoformat(),
            }
        return edges

    def _record_edge_changes(
        self,
        old_edges: dict[tuple[str, str], dict[str, Any]],
        new_edges: dict[tuple[str, str], dict[str, Any]],
        timestamp: datetime,
    ) -> None:
        for key in new_edges.keys() - old_edges.keys():
            self._record_event("connection_added", timestamp, source=key[0], destination=key[1])
        for key in old_edges.keys() - new_edges.keys():
            self._record_event("connection_lost", timestamp, source=key[0], destination=key[1])
        for key in new_edges.keys() & old_edges.keys():
            if new_edges[key]["average_rate"] != old_edges[key]["average_rate"]:
                self._record_event(
                    "link_rate_changed",
                    timestamp,
                    source=key[0],
                    destination=key[1],
                    previous_average=old_edges[key]["average_rate"],
                    average=new_edges[key]["average_rate"],
                )

    def _record_event(self, event: str, timestamp: datetime, **data: Any) -> None:
        self.events.append({"event": event, "timestamp": timestamp.isoformat(), **data})

    @staticmethod
    def _root_mac(devices: dict[str, dict[str, Any]], online_macs: list[str]) -> str:
        for mac in online_macs:
            role = str(devices.get(mac, {}).get("role") or "").lower()
            if role in {"cco", "central coordinator"}:
                return mac
        return online_macs[0]

    @staticmethod
    def _role(dev: dict[str, Any]) -> str:
        role = dev.get("role")
        if isinstance(role, str) and role:
            return role
        if role in (1, "1"):
            return "CCo"
        return "Station"

    @staticmethod
    def _link_quality(average_rate: int) -> str:
        if average_rate > 700:
            return "green"
        if average_rate >= 400:
            return "yellow"
        if average_rate >= 150:
            return "orange"
        return "red"
