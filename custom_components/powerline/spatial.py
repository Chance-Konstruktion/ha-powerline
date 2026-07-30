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
from homeassistant.helpers import device_registry as dr, entity_registry as er

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
#
# Es ist derselbe Adapter, den unser eigenes Panel zeichnet -- Gehaeuse,
# Frontblende, drei LEDs, Kabel nach unten -- und nicht ein allgemeines
# Netzwerksymbol. Vorher lagen hier drei MDI-artige Strichzeichnungen: auf
# unserem Dashboard stand ein Adapter, auf dem Grundriss ein Router, und
# dass beides dasselbe Geraet ist, musste man wissen.
#
# Statisch statt gerechnet. Das Panel skaliert seine Grafik und schaltet
# die LEDs nach dem echten Zustand; ein Icon-Set kann das nicht, es ist
# ein Bild je Name. Also drei Bilder fuer die drei Namen, die unsere
# Knoten ohnehin schon anfragen -- der Zustand steckt im Namen.
#
# `currentColor` ist Absicht: der Hub faerbt ein Node nach seinem Zustand,
# und ein Icon, das seine Farbe selbst festlegt, wuerde dem widersprechen.
# Nur die LEDs tragen eigene Farben, denn eine dunkle LED an einem gruenen
# Adapter ist genau die Aussage, um die es geht.
_ADAPTER_BODY = (
    # Kabel nach unten, hinter dem Gehaeuse. Es beginnt noch darunter, damit
    # kein heller Spalt zwischen Kabel und Gehaeuse steht.
    '<rect x="10.9" y="19.4" width="2.2" height="4.4" rx="1.1" fill="#8a949c"/>'
    # Gehaeuse: Kartenfarbe innen, Zustandsfarbe als Kontur.
    '<rect x="4.6" y="3.6" width="14.8" height="16.6" rx="3.2"'
    ' fill="var(--card-background-color, #fff)" stroke="currentColor"'
    ' stroke-width="1.7"/>'
    # Frontblende, in der Zustandsfarbe angedeutet.
    '<rect x="7" y="9.8" width="10" height="8.6" rx="1.8"'
    ' fill="currentColor" opacity="0.14"/>'
)


def _adapter_icon(lit: bool, cco: bool = False) -> str:
    """Ein Adapter in 24x24, mit LEDs an oder aus.

    Der CCo bekommt zusaetzlich zwei Funkboegen. Er ist das Geraet, an dem
    alle anderen haengen, und auf einem Grundriss voller gleicher Kaesten
    ist genau das die eine Information, die man auf einen Blick braucht.
    """
    glow = "#5fe08a" if lit else "#9aa4ad"
    leds = "".join(
        (
            f'<circle cx="{cx}" cy="7" r="2.2" fill="#5fe08a"'
            ' opacity="0.28"/>' if lit else ""
        )
        + f'<circle cx="{cx}" cy="7" r="1.05" fill="{glow}"/>'
        for cx in ("8.6", "12", "15.4")
    )
    # Der Bogen woelbt sich nach oben. Zu gross gewaehlt liegt sein
    # Scheitel ausserhalb des 24er-Feldes und wird abgeschnitten -- ein
    # Radius von 7 laesst ihn bei y≈1,2 enden und damit knapp drin.
    crown = (
        '<path d="M7.6 2.8A7 7 0 0 1 16.4 2.8" fill="none"'
        ' stroke="currentColor" stroke-width="1.3" stroke-linecap="round"'
        ' opacity="0.75"/>'
        if cco
        else ""
    )
    return f'<svg viewBox="0 0 24 24">{crown}{_ADAPTER_BODY}{leds}</svg>'


# Keyed by the icon name a node asks for, so the same node definition
# degrades to the plain MDI icon on a renderer that ignores icon sets.
_ICON_SET = {
    # The CCo: the adapter the others answer to.
    "mdi:router-network": {"svg": _adapter_icon(lit=True, cco=True)},
    "mdi:lan-connect": {"svg": _adapter_icon(lit=True)},
    # Offline: das Gehaeuse steht noch da, die LEDs sind aus. Ein Adapter,
    # der nicht antwortet, ist nicht verschwunden -- er ist dunkel.
    "mdi:lan-disconnect": {"svg": _adapter_icon(lit=False)},
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
        registry_name = self.coordinator.adapter_name(mac)
        device = self._device(mac)
        return node(
            mac,
            # The user's own rename wins over whatever the adapter calls
            # itself over the wire -- usually nothing, which is how a floor
            # plan full of MAC addresses happens. Only a real rename counts:
            # the lookup returns the MAC itself when there is none.
            label=(
                registry_name if registry_name != mac
                else adapter.get("name") or mac
            ),
            # No position: the hub centres the adapter in its area and the
            # user drags it from there. We genuinely don't know where it is.
            area_id=getattr(device, "area_id", None),
            # The door into Home Assistant itself. Without it the hub's
            # popup has nothing to link to and reads as an empty card: no
            # more-info dialog, no device page, no settings. The hub fills
            # in the device behind the entity on its own.
            entity_id=self._entity_id(device),
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

    def _device(self, mac: str) -> Any | None:
        """This adapter's registry entry -- its area and its device page."""
        try:
            return dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, mac)}
            )
        except (AttributeError, KeyError):  # pragma: no cover - registry absent
            return None

    def _entity_id(self, device: Any | None) -> str | None:
        """One entity to stand for the adapter in the more-info dialog.

        Any of them opens the same dialog, so the only thing that matters
        is picking a useful one: diagnostics sort last, because "Firmware"
        is a poor answer to "show me this adapter".
        """
        if device is None:
            return None
        try:
            entries = er.async_entries_for_device(
                er.async_get(self.hass), device.id, include_disabled_entities=False
            )
        except (AttributeError, KeyError, TypeError):  # pragma: no cover
            return None
        if not entries:
            return None
        entries = sorted(
            entries,
            key=lambda entry: (
                getattr(entry, "entity_category", None) is not None,
                entry.entity_id,
            ),
        )
        return entries[0].entity_id

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
