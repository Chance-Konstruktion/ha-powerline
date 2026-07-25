"""Powerline as a Floorplan-Hub provider.

Translates the topology graph this integration already maintains into the
hub's vocabulary: adapters become nodes, PHY links become edges, and the
adapters' Home Assistant areas decide where they sit. The hub does the
placing, the drawing and the arranging -- everything below is data.

Nothing here imports the hub. If Floorplan-Hub is not installed, the
registration dict simply sits in ``hass.data`` unread, and the built-in
topology panel keeps working exactly as before.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PROVIDER_ID, PROVIDER_LAYER_ID
from .coordinator import TpLinkPowerlineCoordinator
from .floorplan_hub_provider import FloorplanHubProvider

_LOGGER = logging.getLogger(__name__)

# The hub speaks good/fair/poor; the topology model has four colour tiers.
_QUALITY = {
    "green": "good",
    "yellow": "fair",
    "orange": "poor",
    "poor": "poor",
}

# Actions the hub may invoke on an adapter node. The hub forwards the id
# without interpreting it -- what "led_on" means is our business.
_NODE_ACTIONS = [
    {"id": "led_on", "label": "LED on", "icon": "mdi:led-on"},
    {"id": "led_off", "label": "LED off", "icon": "mdi:led-off"},
    {"id": "restart", "label": "Restart adapter", "icon": "mdi:restart",
     "confirm": True},
]


def async_create_provider(
    hass: HomeAssistant, coordinator: TpLinkPowerlineCoordinator
) -> FloorplanHubProvider:
    """Build the registration for this config entry's coordinator."""
    adapter = PowerlineFloorplanAdapter(hass, coordinator)
    return FloorplanHubProvider(
        hass,
        provider_id=PROVIDER_ID,
        name="Powerline Network",
        icon="mdi:lan",
        version=_integration_version(hass),
        capabilities={
            "nodes": True,
            "edges": True,
            "history": True,
            "animation": True,
            "popup": True,
            "actions": True,
            "custom_icons": False,
        },
        layers=[
            {
                "id": PROVIDER_LAYER_ID,
                "name": "Powerline Network",
                "icon": "mdi:lan",
                # Network sits above the room shapes but below anything
                # the user is actively interacting with.
                "z_index": 20,
            }
        ],
        data=adapter.async_data,
        history=adapter.history,
        action=adapter.async_action,
    )


def _integration_version(hass: HomeAssistant) -> str:
    try:
        from homeassistant.loader import async_get_loaded_integration

        return async_get_loaded_integration(hass, DOMAIN).version or ""
    except Exception:  # noqa: BLE001 - cosmetic only
        return ""


class PowerlineFloorplanAdapter:
    """Turns the topology payload into hub nodes and edges."""

    def __init__(
        self, hass: HomeAssistant, coordinator: TpLinkPowerlineCoordinator
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator

    # ── Data ──────────────────────────────────────────────

    @callback
    def async_data(self) -> dict[str, Any]:
        """The current graph, in the hub's vocabulary."""
        topology = (self.coordinator.data or {}).get("topology") or {}
        return {
            "nodes": [self._node(node) for node in topology.get("nodes", [])],
            "edges": [self._edge(edge) for edge in topology.get("edges", [])],
        }

    def _node(self, node: dict[str, Any]) -> dict[str, Any]:
        mac = node["mac"]
        online = bool(node.get("online"))
        is_cco = node.get("role") == "CCo"
        return {
            "id": mac,
            "label": node.get("name") or mac,
            # No position: the hub centres the adapter in its area and the
            # user drags it from there. We genuinely don't know where it is.
            "area_id": self._area_id(mac),
            "state": "online" if online else "offline",
            "icon": "mdi:router-network" if is_cco else (
                "mdi:lan-connect" if online else "mdi:lan-disconnect"
            ),
            "color": "#4caf50" if online else "#f44336",
            "layer_id": PROVIDER_LAYER_ID,
            "actions": _NODE_ACTIONS if online else [],
            "metadata": {
                "mac": mac,
                "role": node.get("role", "unknown"),
                "model": node.get("model", ""),
                "firmware": node.get("firmware", ""),
                "chipset": node.get("chipset", ""),
                "manufacturer": node.get("manufacturer", ""),
                "last_update": node.get("last_update"),
            },
        }

    def _edge(self, edge: dict[str, Any]) -> dict[str, Any]:
        source, destination = edge["source"], edge["destination"]
        average = edge.get("average_rate") or 0
        return {
            # Same shape the history lookup splits back apart.
            "id": f"{source}__{destination}",
            "source": source,
            "target": destination,
            "label": f"{average} Mbit/s" if average else "",
            "value": average,
            "quality": _QUALITY.get(edge.get("link_quality", ""), "unknown"),
            # Thicker line for a faster link, within sane bounds.
            "width": max(2.0, min(8.0, average / 150)) if average else 2.0,
            "directed": False,
            # An estimated edge is a guess, and should look like one.
            "dashed": bool(edge.get("estimated")),
            "animated": average > 0,
            "metadata": {
                "tx_phy_rate": edge.get("tx_phy_rate"),
                "rx_phy_rate": edge.get("rx_phy_rate"),
                "average_rate": average,
                "link_quality": edge.get("link_quality"),
                "estimated": bool(edge.get("estimated")),
            },
        }

    def _area_id(self, mac: str) -> str | None:
        """The area the user put this adapter in, if any."""
        try:
            device = dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, mac)}
            )
        except (AttributeError, KeyError):  # pragma: no cover - registry absent
            return None
        return device.area_id if device else None

    # ── History ───────────────────────────────────────────

    @callback
    def history(self, kind: str, item_id: str, hours: float) -> list[dict[str, Any]]:
        """Link-rate history for one edge (nodes have none of their own)."""
        if kind != "edge" or "__" not in item_id:
            return []
        source, destination = item_id.split("__", 1)
        return self.coordinator.history.series(source, destination, hours)

    # ── Actions ───────────────────────────────────────────

    async def async_action(
        self, kind: str, item_id: str, action_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Run an adapter action. ``item_id`` is the adapter's MAC."""
        if kind != "node":
            raise ValueError(f"Powerline has no {kind} actions")

        if action_id in ("led_on", "led_off"):
            success = await self.coordinator.async_set_led(
                item_id, action_id == "led_on"
            )
        elif action_id == "restart":
            success = await self.coordinator.async_restart_adapter(item_id)
        else:
            raise ValueError(f"unknown Powerline action: {action_id}")

        _LOGGER.debug("Floorplan action %s on %s -> %s", action_id, item_id, success)
        return {"success": bool(success)}
