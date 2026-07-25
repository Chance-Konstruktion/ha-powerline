"""Reference provider shim -- copy this file into your integration.

Copy, do not import. The hub may not be installed, may be a different
version, or may be removed while your integration keeps running. This file
therefore has zero imports from ``floorplan_hub``: it only writes a dict
into ``hass.data`` and fires dispatcher signals. If the hub is absent, the
dict sits there unread and costs you nothing.

Usage in ``async_setup_entry``::

    from .floorplan_hub_provider import FloorplanHubProvider

    provider = FloorplanHubProvider(
        hass,
        provider_id="my_integration",
        name="My Integration",
        icon="mdi:flash",
        version="1.0.0",
        capabilities={"nodes": True, "edges": True, "history": True},
        layers=[{"id": "my_layer", "name": "My Layer", "icon": "mdi:flash"}],
        data=my_data_callback,        # -> {"nodes": [...], "edges": [...]}
        history=my_history_callback,  # optional
        action=my_action_callback,    # optional
    )
    provider.async_register()
    entry.async_on_unload(provider.async_unregister)

and whenever your data changed::

    provider.async_notify()
"""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

# ── Frozen contract strings (must match the hub verbatim) ─────────────
DATA_PROVIDERS = "floorplan_hub_providers"
SIGNAL_PROVIDER_REGISTERED = "floorplan_hub_provider_registered"
SIGNAL_PROVIDER_REMOVED = "floorplan_hub_provider_removed"
SIGNAL_DATA_UPDATED = "floorplan_hub_data_updated"
API_VERSION = 1


class FloorplanHubProvider:
    """Announces one integration's spatial data to the hub, if present."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider_id: str,
        name: str,
        data: Callable[[], Any],
        icon: str = "",
        version: str = "",
        capabilities: dict[str, bool] | None = None,
        layers: list[dict[str, Any]] | None = None,
        icon_set: dict[str, Any] | None = None,
        history: Callable[..., Any] | None = None,
        action: Callable[..., Any] | None = None,
    ) -> None:
        self.hass = hass
        self.provider_id = provider_id
        self._registration: dict[str, Any] = {
            "provider_id": provider_id,
            "api_version": API_VERSION,
            "name": name,
            "icon": icon,
            "version": version,
            "capabilities": capabilities or {"nodes": True},
            "layers": layers or [],
            "icon_set": icon_set or {},
            "data": data,
        }
        if history is not None:
            self._registration["history"] = history
        if action is not None:
            self._registration["action"] = action

    @callback
    def async_register(self) -> None:
        """Publish the registration. Safe whether or not the hub exists."""
        self.hass.data.setdefault(DATA_PROVIDERS, {})[
            self.provider_id
        ] = self._registration
        async_dispatcher_send(
            self.hass, SIGNAL_PROVIDER_REGISTERED, self.provider_id
        )

    @callback
    def async_unregister(self) -> None:
        """Withdraw on unload, so the hub drops the layer immediately."""
        self.hass.data.get(DATA_PROVIDERS, {}).pop(self.provider_id, None)
        async_dispatcher_send(self.hass, SIGNAL_PROVIDER_REMOVED, self.provider_id)

    @callback
    def async_notify(self) -> None:
        """Tell the hub the spatial data changed; it will re-fetch."""
        async_dispatcher_send(self.hass, SIGNAL_DATA_UPDATED, self.provider_id)
