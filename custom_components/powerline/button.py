"""Button platform for Powerline Network diagnostics.

Press to run a full diagnostic scan and dump raw HomePlug AV
frame data to the Home Assistant logs for troubleshooting.
"""

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from typing import Any

from .const import DOMAIN, get_mac
from .coordinator import TpLinkPowerlineCoordinator
from .homeplug import async_diagnose
from .homeplug.fritz import is_avm_device
from .sensor import (
    device_info_for_adapter,
    network_device_info,
    setup_dynamic_platform,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic + per-adapter buttons."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]
    interface = entry.data.get("interface")
    async_add_entities([
        DiagnosticButton(coordinator, interface),
        AllLedsButton(coordinator, on=True),
        AllLedsButton(coordinator, on=False),
    ])

    # Per-adapter Restart button. Only created for FRITZ!Powerline (AVM), where
    # the VS_RS_DEV reset is captured and verified; other vendors aren't exposed
    # until confirmed.
    def _factory(mac: str, dev: dict[str, Any]) -> list[ButtonEntity]:
        if not is_avm_device(mac, dev):
            return []
        return [RestartButton(coordinator, mac, device_info_for_adapter(mac, dev))]

    setup_dynamic_platform(coordinator, async_add_entities, _factory)


class AllLedsButton(ButtonEntity):
    """Turn every adapter's LED on or off at once (like tpPLC's all-LED buttons)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TpLinkPowerlineCoordinator, on: bool) -> None:
        self._coordinator = coordinator
        self._on = on
        key = "all_leds_on" if on else "all_leds_off"
        self._attr_translation_key = key
        self._attr_unique_id = f"tplink_plc_{key}"
        self._attr_icon = "mdi:led-on" if on else "mdi:led-off"
        self._attr_device_info = network_device_info()

    async def async_press(self) -> None:
        """Apply the LED state to every known adapter."""
        await self._coordinator.async_set_all_leds(self._on)


class RestartButton(ButtonEntity):
    """Reboot a single adapter (soft restart via VS_RS_DEV)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:restart"
    _attr_translation_key = "restart"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 mac: str, device_info) -> None:
        self._coordinator = coordinator
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_restart"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Send the restart command to this adapter."""
        if not await self._coordinator.async_restart_adapter(self._mac):
            _LOGGER.warning("Restart failed for %s", self._mac)


class DiagnosticButton(ButtonEntity):
    """Button that runs full HomePlug AV diagnostics and logs raw frames."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:stethoscope"
    _attr_translation_key = "diagnose"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 interface: str | None) -> None:
        self._coordinator = coordinator
        self._interface = interface
        self._attr_unique_id = "tplink_plc_diagnose"
        self._attr_device_info = network_device_info()

    async def async_press(self) -> None:
        """Run diagnostics and log results including integration state."""
        _LOGGER.info("=== Powerline Network Diagnostic Scan START ===")

        # Log current integration state
        _LOGGER.info("DIAG: LED states: %s", self._coordinator.led_states)
        _LOGGER.info("DIAG: Power saving states: %s", self._coordinator.power_saving_states)
        _LOGGER.info("DIAG: QoS states: %s", self._coordinator.qos_states)
        _LOGGER.info("DIAG: Known MACs: %s", list(self._coordinator.devices.keys()))
        for mac, dev in self._coordinator.devices.items():
            _LOGGER.info(
                "DIAG: Device %s: online=%s tx=%d rx=%d fw=%s model=%s",
                mac,
                dev.get("_online", "?"),
                dev.get("tx_rate", 0),
                dev.get("rx_rate", 0),
                dev.get("firmware_ver", ""),
                dev.get("model", ""),
            )

        # Run full protocol diagnostics
        report = await async_diagnose(self._interface, timeout=8.0)
        for line in report.split("\n"):
            _LOGGER.info("DIAG: %s", line)
        _LOGGER.info("=== Powerline Network Diagnostic Scan END ===")
