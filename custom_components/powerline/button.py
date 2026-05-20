"""Button platform for Powerline Network diagnostics.

Press to run a full diagnostic scan and dump raw HomePlug AV
frame data to the Home Assistant logs for troubleshooting.
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TpLinkPowerlineCoordinator
from .homeplug import async_diagnose
from .sensor import network_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic button."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]
    interface = entry.data.get("interface")
    async_add_entities([DiagnosticButton(coordinator, interface)])


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
