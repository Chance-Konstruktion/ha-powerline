"""Powerline topology graph model (nodes + edges + analysis).

Builds the network graph the topology card renders. Edge data comes in
three quality tiers, best available wins:

1. Measured pairwise links (``links`` from NW_STATS/LINK_STATS confirms,
   where each responder reports its PHY rate towards each peer).
2. Two online adapters and no measured links: the single possible edge,
   rates taken from the adapters' own (mirrored) link rates.
3. Three or more online adapters and no measured links: a star from the
   CCo (or the first adapter) using each peer's own rates — flagged
   ``estimated`` so the card can render it dashed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .const import MANUFACTURER, get_mac, normalize_mac

DEFAULT_OFFLINE_RETENTION_SECONDS = 3600

# Suppress link_rate_changed noise: PHY rates jitter a few Mbit/s between
# polls. Only report a change when the average moves by this fraction or
# the quality colour class flips.
RATE_CHANGE_THRESHOLD = 0.10


class TopologyManager:
    """Build and track the Powerline network graph across poll cycles."""

    def __init__(
        self, offline_retention_seconds: int = DEFAULT_OFFLINE_RETENTION_SECONDS
    ) -> None:
        self._offline_retention_seconds = offline_retention_seconds
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str], dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._new_adapters: list[str] = []

    def update(
        self,
        devices: dict[str, dict[str, Any]],
        links: dict[tuple[str, str], dict[str, int]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Update the graph from the latest adapter inventory and links."""
        timestamp = now or datetime.now(UTC)
        seen_macs: set[str] = set()
        new_adapters: list[str] = []
        cco = self._cco_mac(devices)

        for dev in devices.values():
            mac = get_mac(dev)
            if not mac:
                continue
            seen_macs.add(mac)
            node = self._node_from_device(mac, dev, cco, timestamp)
            previous = self._nodes.get(mac)
            if previous is None:
                new_adapters.append(mac)
                if node["online"]:
                    self._record_event("adapter_online", timestamp, mac=mac)
            elif not previous.get("online", True) and node["online"]:
                self._record_event("adapter_online", timestamp, mac=mac)
            elif previous.get("online", True) and not node["online"]:
                self._record_event("adapter_offline", timestamp, mac=mac)
            if not node["online"]:
                # Keep the last known metadata but don't refresh last_update,
                # so retention eventually drops adapters that stay gone.
                node["last_update"] = (
                    previous["last_update"] if previous else timestamp.isoformat()
                )
            self._nodes[mac] = node

        # Adapters that vanished from the inventory entirely
        for mac, node in list(self._nodes.items()):
            if mac in seen_macs:
                continue
            if node.get("online", True):
                node["online"] = False
                self._record_event("adapter_offline", timestamp, mac=mac)
            last_update = datetime.fromisoformat(node["last_update"])
            if (
                timestamp - last_update
            ).total_seconds() > self._offline_retention_seconds:
                self._nodes.pop(mac)

        self._new_adapters = sorted(new_adapters)

        online_macs = sorted(
            mac for mac, node in self._nodes.items() if node.get("online")
        )
        old_edges = self._edges
        self._edges = self._build_edges(devices, links or {}, online_macs, cco,
                                        timestamp)
        self._record_edge_changes(old_edges, self._edges, timestamp)

        return self.as_dict()

    def drain_events(self) -> list[dict[str, Any]]:
        """Return pending topology events and clear the queue."""
        events = self._events
        self._events = []
        return events

    def as_dict(self) -> dict[str, Any]:
        """Return the topology API payload."""
        nodes = [dict(node) for _, node in sorted(self._nodes.items())]
        edges = [dict(edge) for _, edge in sorted(self._edges.items())]
        return {
            "nodes": nodes,
            "edges": edges,
            "analysis": self._analysis(nodes, edges),
        }

    # ── Nodes ─────────────────────────────────────────────

    @staticmethod
    def _cco_mac(devices: dict[str, dict[str, Any]]) -> str:
        """The network's Central Coordinator, if any adapter reported one."""
        for dev in devices.values():
            cco = dev.get("cco_mac")
            if cco:
                return normalize_mac(cco)
        return ""

    @staticmethod
    def _node_from_device(
        mac: str, dev: dict[str, Any], cco: str, timestamp: datetime
    ) -> dict[str, Any]:
        return {
            "mac": mac,
            "name": dev.get("name") or dev.get("alias") or mac,
            "manufacturer": dev.get("manufacturer") or MANUFACTURER,
            "model": dev.get("model") or "",
            "firmware": dev.get("firmware_ver") or dev.get("firmware") or "",
            "chipset": dev.get("chipset") or "",
            "role": "CCo" if cco and mac == cco else
                    ("Station" if cco else "unknown"),
            "online": bool(dev.get("_online", True)),
            "last_update": timestamp.isoformat(),
        }

    # ── Edges ─────────────────────────────────────────────

    def _build_edges(
        self,
        devices: dict[str, dict[str, Any]],
        links: dict[tuple[str, str], dict[str, int]],
        online_macs: list[str],
        cco: str,
        timestamp: datetime,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        if len(online_macs) < 2:
            return {}

        edges: dict[tuple[str, str], dict[str, Any]] = {}

        # Tier 1: measured pairwise links (responder → peer)
        for (responder, peer), rates in links.items():
            responder = normalize_mac(responder)
            peer = normalize_mac(peer)
            if responder not in online_macs or peer not in online_macs:
                continue
            source, destination = sorted((responder, peer))
            # Rates are from the responder's perspective; normalise to the
            # edge's source so tx always means source→destination.
            tx, rx = rates.get("tx_rate", 0), rates.get("rx_rate", 0)
            if responder != source:
                tx, rx = rx, tx
            key = (source, destination)
            if key in edges:
                # Both endpoints measured the link; keep the higher reading
                # (a stale/idle direction often reports 0).
                tx = max(tx, edges[key]["tx_phy_rate"])
                rx = max(rx, edges[key]["rx_phy_rate"])
            edges[key] = self._edge(source, destination, tx, rx,
                                    estimated=False, timestamp=timestamp)
        if edges:
            return edges

        # Tier 2: exactly two adapters — the only possible edge, taken from
        # the adapters' own (mirrored) link rates.
        if len(online_macs) == 2:
            source, destination = online_macs
            dev = devices.get(source) or {}
            tx, rx = int(dev.get("tx_rate") or 0), int(dev.get("rx_rate") or 0)
            if tx == 0 and rx == 0:
                peer_dev = devices.get(destination) or {}
                # Peer reports the same physical link, directions swapped.
                tx = int(peer_dev.get("rx_rate") or 0)
                rx = int(peer_dev.get("tx_rate") or 0)
            if tx or rx:
                edges[(source, destination)] = self._edge(
                    source, destination, tx, rx,
                    estimated=False, timestamp=timestamp)
            return edges

        # Tier 3: star fallback — no pairwise data, so approximate with each
        # peer's own rate towards the network and say so via `estimated`.
        root = cco if cco in online_macs else online_macs[0]
        for mac in online_macs:
            if mac == root:
                continue
            dev = devices.get(mac) or {}
            tx = int(dev.get("tx_rate") or 0)
            rx = int(dev.get("rx_rate") or 0)
            if tx == 0 and rx == 0:
                continue
            source, destination = sorted((root, mac))
            if mac != source:
                tx, rx = rx, tx
            edges[(source, destination)] = self._edge(
                source, destination, tx, rx,
                estimated=True, timestamp=timestamp)
        return edges

    def _edge(
        self,
        source: str,
        destination: str,
        tx: int,
        rx: int,
        estimated: bool,
        timestamp: datetime,
    ) -> dict[str, Any]:
        average = round((tx + rx) / 2)
        return {
            "source": source,
            "destination": destination,
            "tx_phy_rate": tx,
            "rx_phy_rate": rx,
            "average_rate": average,
            "link_quality": self._link_quality(average),
            "estimated": estimated,
            "timestamp": timestamp.isoformat(),
        }

    def _record_edge_changes(
        self,
        old_edges: dict[tuple[str, str], dict[str, Any]],
        new_edges: dict[tuple[str, str], dict[str, Any]],
        timestamp: datetime,
    ) -> None:
        for key in new_edges.keys() - old_edges.keys():
            self._record_event("connection_added", timestamp,
                               source=key[0], destination=key[1])
        for key in old_edges.keys() - new_edges.keys():
            self._record_event("connection_lost", timestamp,
                               source=key[0], destination=key[1])
        for key in new_edges.keys() & old_edges.keys():
            old_avg = old_edges[key]["average_rate"]
            new_avg = new_edges[key]["average_rate"]
            if old_avg == new_avg:
                continue
            quality_changed = (
                old_edges[key]["link_quality"] != new_edges[key]["link_quality"]
            )
            relative = abs(new_avg - old_avg) / old_avg if old_avg else 1.0
            if quality_changed or relative >= RATE_CHANGE_THRESHOLD:
                self._record_event(
                    "link_rate_changed",
                    timestamp,
                    source=key[0],
                    destination=key[1],
                    previous_average=old_avg,
                    average=new_avg,
                    link_quality=new_edges[key]["link_quality"],
                )

    def _record_event(self, event: str, timestamp: datetime, **data: Any) -> None:
        self._events.append(
            {"event": event, "timestamp": timestamp.isoformat(), **data}
        )

    # ── Analysis ──────────────────────────────────────────

    def _analysis(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
    ) -> dict[str, Any]:
        offline = sorted(node["mac"] for node in nodes if not node["online"])
        rated = [e for e in edges if e["average_rate"] > 0]
        worst = min(rated, key=lambda e: e["average_rate"], default=None)
        online = [node["mac"] for node in nodes if node["online"]]
        return {
            "worst_link": dict(worst) if worst else None,
            "best_adapter": self._best_adapter(online, edges),
            "offline_adapters": offline,
            "new_adapters": list(self._new_adapters),
        }

    @staticmethod
    def _best_adapter(
        online_macs: list[str], edges: list[dict[str, Any]]
    ) -> str | None:
        if not online_macs:
            return None
        scores = {mac: 0 for mac in online_macs}
        for edge in edges:
            average = edge["average_rate"]
            for mac in (edge["source"], edge["destination"]):
                if mac in scores:
                    scores[mac] = max(scores[mac], average)
        if not any(scores.values()):
            return None
        return max(sorted(scores), key=lambda mac: scores[mac])

    @staticmethod
    def _link_quality(average_rate: int) -> str:
        if average_rate > 700:
            return "green"
        if average_rate >= 400:
            return "yellow"
        if average_rate >= 150:
            return "orange"
        return "red"
