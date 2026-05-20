"""Switch platform for Powerline Network -- LED & Power Saving control.

Uses MEDIAXTREAM MME 0xA058 for Broadcom adapters (confirmed on TL-PA7017).
Qualcomm adapters use vendor-specific MMEs on 0x88E1 as fallback.

Note: Experimental -- not all adapters may support these controls over Layer 2.
"""

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, get_mac
from .coordinator import TpLinkPowerlineCoordinator
from .sensor import device_info_for_adapter, setup_dynamic_platform

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LED and power saving switches."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _factory(mac: str, dev: dict[str, Any]) -> list[SwitchEntity]:
        info = device_info_for_adapter(mac, dev)
        return [
            LedSwitch(coordinator, mac, info),
            PowerSavingSwitch(coordinator, mac, info),
        ]

    setup_dynamic_platform(coordinator, async_add_entities, _factory)


class LedSwitch(CoordinatorEntity[TpLinkPowerlineCoordinator], SwitchEntity):
    """LED on/off switch for a single Powerline adapter."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:led-on"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "led"

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 mac: str, device_info) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_led"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.led_states.get(self._mac, True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        ok = await self.coordinator.async_set_led(self._mac, True)
        if ok:
            self.async_write_ha_state()
        else:
            _LOGGER.warning("LED on failed for %s -- may not support Layer 2 LED control", self._mac)

    async def async_turn_off(self, **kwargs: Any) -> None:
        ok = await self.coordinator.async_set_led(self._mac, False)
        if ok:
            self.async_write_ha_state()
        else:
            _LOGGER.warning("LED off failed for %s -- may not support Layer 2 LED control", self._mac)


class PowerSavingSwitch(CoordinatorEntity[TpLinkPowerlineCoordinator], SwitchEntity):
    """Power saving mode switch for a single Powerline adapter (Broadcom only)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:leaf"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "power_saving"

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 mac: str, device_info) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_power_saving"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.power_saving_states.get(self._mac, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        ok = await self.coordinator.async_set_power_saving(self._mac, True)
        if ok:
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Power saving on failed for %s", self._mac)

    async def async_turn_off(self, **kwargs: Any) -> None:
        ok = await self.coordinator.async_set_power_saving(self._mac, False)
        if ok:
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Power saving off failed for %s", self._mac)
