"""Powerline Network integration for Home Assistant.

Communicates with Powerline adapters via HomePlug AV Management Messages
(raw Ethernet, Ethertype 0x88E1) and MEDIAXTREAM (Ethertype 0x8912).
No IP address needed -- works with pure PLC adapters that are invisible
to the router.

Supports: TP-Link, FRITZ!Powerline, devolo, and other HomePlug AV adapters.

Requires: CAP_NET_RAW capability or running HA as root.
"""

import logging
from datetime import timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SIDEBAR_PANEL,
    CONF_TOPOLOGY_ALERTS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SIDEBAR_PANEL,
    DEFAULT_TOPOLOGY_ALERTS,
    DOMAIN,
    FRONTEND_BASE_URL,
    NETWORK_DEVICE_ID,
    PANEL_URL_PATH,
    PLATFORMS,
    TOPOLOGY_CARD_URL,
    TOPOLOGY_PANEL_URL,
    get_mac,
)
from .coordinator import TpLinkPowerlineCoordinator
from .homeplug import is_available

_LOGGER = logging.getLogger(__name__)

# hass.data flags so the websocket command and the card resource are
# registered exactly once, no matter how many config entries exist.
_DATA_WS_REGISTERED = f"{DOMAIN}_ws_registered"
_DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
_DATA_FRONTEND_VERSION = f"{DOMAIN}_frontend_version"
_DATA_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Powerline Network from a config entry."""
    interface = entry.data.get("interface")
    initial_devices = entry.data.get("devices", [])
    scan_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    if not is_available():
        _LOGGER.error(
            "HomePlug AV raw sockets not available. "
            "Home Assistant needs CAP_NET_RAW capability. "
            "For HAOS: install as add-on with host network. "
            "For Docker: use --cap-add=NET_RAW --network=host. "
            "For venv: sudo setcap cap_net_raw+ep $(readlink -f $(which python3))"
        )
        return False

    coordinator = TpLinkPowerlineCoordinator(
        hass, interface, initial_devices, scan_interval=scan_interval
    )
    coordinator.alerts_enabled = bool(
        entry.options.get(CONF_TOPOLOGY_ALERTS, DEFAULT_TOPOLOGY_ALERTS)
    )

    # Restore link-rate history (survives restarts, ~30 days of aggregates)
    try:
        from homeassistant.helpers.storage import Store

        store = Store(hass, 1, f"{DOMAIN}.topology_history")
        coordinator.history.restore(await store.async_load())
        coordinator.history_store = store
    except ImportError:  # pragma: no cover - Store exists in every real HA
        pass

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Migrate old text-based "Status" sensors to binary_sensor before platform setup
    _migrate_old_status_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_websocket_api(hass)
    await _register_frontend(hass)
    _async_update_panel(
        hass, entry.options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL)
    )

    # Clean up stale/duplicate device entries from the registry
    _cleanup_stale_devices(hass, entry, coordinator)

    # Listen for options changes (e.g. scan interval) -- apply without restart
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


def _register_websocket_api(hass: HomeAssistant) -> None:
    """Register the topology websocket commands (once)."""
    if hass.data.get(_DATA_WS_REGISTERED):
        return
    hass.data[_DATA_WS_REGISTERED] = True
    websocket_api.async_register_command(hass, _websocket_get_topology)
    websocket_api.async_register_command(hass, _websocket_get_history)


def _get_coordinator(hass: HomeAssistant, msg: dict):
    """Resolve the coordinator for a websocket message (entry_id optional)."""
    coordinators = hass.data.get(DOMAIN, {})
    entry_id = msg.get("entry_id")
    if entry_id:
        return coordinators.get(entry_id)
    return next(iter(coordinators.values()), None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/topology",
        vol.Optional("entry_id"): str,
    }
)
@callback
def _websocket_get_topology(
    hass: HomeAssistant, connection, msg: dict
) -> None:
    """Return the current topology payload ({nodes, edges, analysis}).

    Without entry_id the first (usually only) config entry is used. Node
    names are enriched from the device registry so user renames ("Wohnzimmer")
    show up in the graph instead of raw MACs.
    """
    coordinator = _get_coordinator(hass, msg)
    if coordinator is None:
        connection.send_error(
            msg["id"], "not_found", "Powerline config entry not found"
        )
        return

    data = coordinator.data or {}
    topology = dict(data.get("topology") or {"nodes": [], "edges": [], "analysis": {}})
    topology["nodes"] = [
        {**node, "name": _display_name(hass, node["mac"]) or node["name"]}
        for node in topology.get("nodes", [])
    ]
    connection.send_result(msg["id"], topology)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/topology/history",
        vol.Required("source"): str,
        vol.Required("destination"): str,
        vol.Optional("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=744)),
        vol.Optional("entry_id"): str,
    }
)
@callback
def _websocket_get_history(
    hass: HomeAssistant, connection, msg: dict
) -> None:
    """Return the link-rate history of one edge (raw ≤1h, else 15-min buckets)."""
    coordinator = _get_coordinator(hass, msg)
    if coordinator is None:
        connection.send_error(
            msg["id"], "not_found", "Powerline config entry not found"
        )
        return

    hours = float(msg.get("hours", 24))
    series = coordinator.history.series(msg["source"], msg["destination"], hours)
    connection.send_result(
        msg["id"],
        {
            "source": msg["source"],
            "destination": msg["destination"],
            "hours": hours,
            "series": series,
        },
    )


def _display_name(hass: HomeAssistant, mac: str) -> str | None:
    """The adapter's device-registry name (user rename wins), if known."""
    try:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_device(identifiers={(DOMAIN, mac)})
    except AttributeError:
        return None
    if device is None:
        return None
    return device.name_by_user or device.name


async def _register_frontend(hass: HomeAssistant) -> None:
    """Serve the frontend dir and load the card on every dashboard (once)."""
    if hass.data.get(_DATA_FRONTEND_REGISTERED):
        return
    hass.data[_DATA_FRONTEND_REGISTERED] = True

    frontend_dir = Path(__file__).parent / "frontend"
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_BASE_URL, str(frontend_dir), False)]
        )
    except ImportError:
        # HA < 2024.7 has no StaticPathConfig
        hass.http.register_static_path(FRONTEND_BASE_URL, str(frontend_dir), False)

    version = "0"
    try:
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(hass, DOMAIN)
        version = integration.version or version
    except Exception:  # pragma: no cover - version only busts browser cache
        pass
    hass.data[_DATA_FRONTEND_VERSION] = version

    from homeassistant.components.frontend import add_extra_js_url

    add_extra_js_url(hass, f"{TOPOLOGY_CARD_URL}?v={version}")


def _async_update_panel(hass: HomeAssistant, enabled: bool) -> None:
    """Add or remove the 'Powerline' sidebar panel to match the option."""
    from homeassistant.components import frontend

    registered = bool(hass.data.get(_DATA_PANEL_REGISTERED))
    if enabled and not registered:
        version = hass.data.get(_DATA_FRONTEND_VERSION, "0")
        frontend.async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Powerline",
            sidebar_icon="mdi:lan",
            frontend_url_path=PANEL_URL_PATH,
            config={
                "_panel_custom": {
                    "name": "powerline-topology-panel",
                    "module_url": f"{TOPOLOGY_PANEL_URL}?v={version}",
                    "embed_iframe": False,
                    "trust_external": False,
                }
            },
            require_admin=False,
        )
        hass.data[_DATA_PANEL_REGISTERED] = True
    elif not enabled and registered:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        hass.data[_DATA_PANEL_REGISTERED] = False


def _migrate_old_status_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove old text-based 'Status' sensor entities (replaced by binary_sensor).

    In v4.0.x the online status was a sensor with unique_id 'plc_{mac}_online'.
    Since v4.1.0 this is a binary_sensor with the same unique_id.
    Remove the old sensor entity so the binary_sensor can take its place.
    """
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    for entity in entries:
        if (
            entity.domain == "sensor"
            and entity.unique_id
            and entity.unique_id.endswith("_online")
        ):
            _LOGGER.info(
                "Migrating old status sensor %s to binary_sensor (removing old entity)",
                entity.entity_id,
            )
            ent_reg.async_remove(entity.entity_id)


def _cleanup_stale_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: TpLinkPowerlineCoordinator
) -> None:
    """Remove stale/duplicate device entries from the device registry."""
    dev_reg = dr.async_get(hass)
    valid_ids: set[tuple[str, str]] = {(DOMAIN, NETWORK_DEVICE_ID)}
    for mac in coordinator.devices:
        valid_ids.add((DOMAIN, mac))

    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not any(ident in valid_ids for ident in device.identifiers):
            _LOGGER.info("Removing stale device: %s (%s)", device.name, device.identifiers)
            dev_reg.async_remove_device(device.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Let the user delete a single adapter device from the UI.

    Without this, Home Assistant only offers to remove the whole integration.
    With it, each adapter (its own device) gets a "Delete" button -- handy to
    clear out a wrongly detected adapter or one that has been swapped/replaced.

    The "Powerline Network" overview device represents the integration itself
    and cannot be deleted. A deleted adapter is forgotten from the coordinator
    and dropped from the stored device list so it does not reappear after a
    restart. If the adapter is still plugged in it will be rediscovered on the
    next poll -- you can't make Home Assistant forget hardware that is really
    there; unplug it first, then delete it.
    """
    device_macs: set[str] = set()
    is_network_device = False
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == NETWORK_DEVICE_ID:
            is_network_device = True
        else:
            device_macs.add(identifier)

    # The network overview device is the hub -- removing it makes no sense.
    if is_network_device:
        return False

    coordinator: TpLinkPowerlineCoordinator | None = hass.data.get(DOMAIN, {}).get(
        config_entry.entry_id
    )
    if coordinator is not None:
        for mac in device_macs:
            coordinator.forget_device(mac)

    # Drop the adapter(s) from the persisted device list so they are not
    # restored from config on the next start-up.
    stored = config_entry.data.get("devices", [])
    remaining = [dev for dev in stored if get_mac(dev) not in device_macs]
    if len(remaining) != len(stored):
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, "devices": remaining},
        )

    _LOGGER.info(
        "Removed Powerline device %s (%s) on user request",
        device_entry.name,
        ", ".join(device_macs) or device_entry.identifiers,
    )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update -- apply scan interval and panel setting live."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]
    new_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    coordinator.update_interval = timedelta(seconds=new_interval)
    _LOGGER.info("Powerline scan interval updated to %ds", new_interval)

    _async_update_panel(
        hass, entry.options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL)
    )
    coordinator.alerts_enabled = bool(
        entry.options.get(CONF_TOPOLOGY_ALERTS, DEFAULT_TOPOLOGY_ALERTS)
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            # Last entry gone — take the sidebar panel down with it.
            _async_update_panel(hass, False)
    return unload_ok
