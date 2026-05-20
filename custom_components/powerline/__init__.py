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

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, NETWORK_DEVICE_ID, PLATFORMS
from .coordinator import TpLinkPowerlineCoordinator
from .homeplug import is_available

_LOGGER = logging.getLogger(__name__)


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

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Migrate old text-based "Status" sensors to binary_sensor before platform setup
    _migrate_old_status_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Clean up stale/duplicate device entries from the registry
    _cleanup_stale_devices(hass, entry, coordinator)

    # Listen for options changes (e.g. scan interval) -- apply without restart
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


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


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update -- adjust scan interval dynamically."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]
    new_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    coordinator.update_interval = timedelta(seconds=new_interval)
    _LOGGER.info("Powerline scan interval updated to %ds", new_interval)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
