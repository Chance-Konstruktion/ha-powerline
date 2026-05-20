"""Select platform for Powerline Network -- QoS priority per adapter.

Sets traffic priority via MEDIAXTREAM MME 0xA058 two-frame sequence.
Supported priorities: Gaming, VoIP, Audio/Video, Internet.
Only works on Broadcom-based adapters (e.g. TL-PA7017).
"""

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, QOS_OPTIONS, get_mac
from .coordinator import TpLinkPowerlineCoordinator
from .sensor import device_info_for_adapter, setup_dynamic_platform

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up QoS priority selectors."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _factory(mac: str, dev: dict[str, Any]) -> list[SelectEntity]:
        return [QosPrioritySelect(coordinator, mac, device_info_for_adapter(mac, dev))]

    setup_dynamic_platform(coordinator, async_add_entities, _factory)


class QosPrioritySelect(CoordinatorEntity[TpLinkPowerlineCoordinator], SelectEntity):
    """QoS traffic priority selector for a single Powerline adapter."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:quality-high"
    _attr_translation_key = "qos_priority"
    _attr_options = QOS_OPTIONS

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 mac: str, device_info) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_qos"
        self._attr_device_info = device_info

    @property
    def current_option(self) -> str:
        return self.coordinator.qos_states.get(self._mac, "internet")

    async def async_select_option(self, option: str) -> None:
        """Set new QoS priority."""
        if option not in QOS_OPTIONS:
            _LOGGER.error("Invalid QoS option: %s", option)
            return
        ok = await self.coordinator.async_set_qos_priority(self._mac, option)
        if ok:
            self.async_write_ha_state()
        else:
            _LOGGER.warning("QoS priority change to '%s' failed for %s", option, self._mac)
