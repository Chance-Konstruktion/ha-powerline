"""Powerline as a Spatial Hub provider.

Translates the topology graph this integration already maintains into the
hub's vocabulary: adapters become nodes, PHY links become edges, and the
adapters' Home Assistant areas decide where they sit. The hub does the
placing, the drawing and the arranging -- everything below is data.

Nothing here imports the hub. If Spatial Hub is not installed, the
registration dict simply sits in ``hass.data`` unread, and the built-in
topology panel keeps working exactly as before.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PANEL_URL_PATH, PROVIDER_ID, PROVIDER_LAYER_ID
from .coordinator import TpLinkPowerlineCoordinator
from .spatial_hub_provider import (
    SpatialHubProvider,
    action,
    edge,
    spatial_provider,
    node,
)

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
# Our own icons, so an adapter on somebody else's floor plan still looks
# like one of ours. The hub decides where a node is drawn and never touches
# what it looks like -- that half is ours, and this is us using it.
#
# Keyed by the icon name a node asks for, so the same node definition
# degrades to the plain MDI icon on a renderer that ignores icon sets.
_ICON_SET = {
    "mdi:router-network": {
        # The CCo: the adapter the others answer to.
        "svg": (
            '<svg viewBox="0 0 24 24">'
            '<path d="M12 2 4 6v5h2V7.2l6-3 6 3V11h2V6l-8-4z"/>'
            '<path d="M3 13h18a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7'
            'a1 1 0 0 1 1-1zm2 3v3h2v-3H5zm4 0v3h2v-3H9zm8 0v3h2v-3h-2z"/>'
            "</svg>"
        ),
    },
    "mdi:lan-connect": {
        "svg": (
            '<svg viewBox="0 0 24 24">'
            '<path d="M9 3h6v4h3l-6 6-6-6h3V3z"/>'
            '<path d="M3 15h18a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-5'
            'a1 1 0 0 1 1-1zm2 2v3h2v-3H5zm4 0v3h2v-3H9z"/>'
            "</svg>"
        ),
    },
    "mdi:lan-disconnect": {
        "svg": (
            '<svg viewBox="0 0 24 24">'
            '<path d="M9 3h6v4h3l-6 6-6-6h3V3z" opacity=".45"/>'
            '<path d="M3 15h18a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-5'
            'a1 1 0 0 1 1-1z" opacity=".45"/>'
            '<path d="M4 4l16 16-1.4 1.4L2.6 5.4 4 4z"/>'
            "</svg>"
        ),
    },
}

_NODE_ACTIONS = [
    action("led_on", "LED on", "mdi:led-on"),
    action("led_off", "LED off", "mdi:led-off"),
    action("restart", "Restart adapter", "mdi:restart", confirm=True),
]


def async_create_provider(
    hass: HomeAssistant,
    entry: Any,
    coordinator: TpLinkPowerlineCoordinator,
) -> SpatialHubProvider:
    """Register with the hub and let it manage the whole lifecycle.

    Registration is withdrawn when the config entry unloads, and every
    coordinator refresh tells the hub to re-fetch -- so the floor plan
    follows the adapters live without a single push from here.
    """
    adapter = PowerlineSpatialAdapter(hass, coordinator)
    return spatial_provider(
        hass,
        entry,
        provider_id=PROVIDER_ID,
        name="Powerline Network",
        icon="mdi:lan",
        version=_integration_version(hass),
        coordinator=coordinator,
        capabilities={
            "nodes": True,
            "edges": True,
            "history": True,
            "animation": True,
            "popup": True,
            "actions": True,
            "custom_icons": True,
        },
        icon_set=_ICON_SET,
        # Our own view, one click from any of our nodes on the plan. The
        # hub links to it and asks nothing about what is on the other side.
        panel_url=f"/{PANEL_URL_PATH}",
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


class PowerlineSpatialAdapter:
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

    def _node(self, adapter: dict[str, Any]) -> dict[str, Any]:
        mac = adapter["mac"]
        online = bool(adapter.get("online"))
        is_cco = adapter.get("role") == "CCo"
        return node(
            mac,
            label=adapter.get("name") or mac,
            # No position: the hub centres the adapter in its area and the
            # user drags it from there. We genuinely don't know where it is.
            area_id=self._area_id(mac),
            state="online" if online else "offline",
            icon="mdi:router-network" if is_cco else (
                "mdi:lan-connect" if online else "mdi:lan-disconnect"
            ),
            color="#4caf50" if online else "#f44336",
            actions=_NODE_ACTIONS if online else (),
            # Keyword arguments beyond the known fields become metadata,
            # which is what the popup shows.
            mac=mac,
            role=adapter.get("role", "unknown"),
            model=adapter.get("model", ""),
            firmware=adapter.get("firmware", ""),
            chipset=adapter.get("chipset", ""),
            manufacturer=adapter.get("manufacturer", ""),
            last_update=adapter.get("last_update"),
        ) | {"layer_id": PROVIDER_LAYER_ID}

    def _edge(self, link: dict[str, Any]) -> dict[str, Any]:
        source, destination = link["source"], link["destination"]
        average = link.get("average_rate") or 0
        return edge(
            source,
            destination,
            # Same shape the history lookup splits back apart.
            id=f"{source}__{destination}",
            label=f"{average} Mbit/s" if average else "",
            value=average,
            quality=_QUALITY.get(link.get("link_quality", ""), "unknown"),
            # Thicker line for a faster link, within sane bounds.
            width=max(2.0, min(8.0, average / 150)) if average else 2.0,
            # An estimated edge is a guess, and should look like one.
            dashed=bool(link.get("estimated")),
            animated=average > 0,
            tx_phy_rate=link.get("tx_phy_rate"),
            rx_phy_rate=link.get("rx_phy_rate"),
            average_rate=average,
            link_quality=link.get("link_quality"),
            estimated=bool(link.get("estimated")),
        )

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

        _LOGGER.debug("Spatial action %s on %s -> %s", action_id, item_id, success)
        return {"success": bool(success)}
