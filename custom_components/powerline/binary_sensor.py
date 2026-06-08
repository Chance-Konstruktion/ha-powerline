"""Binary sensor platform for Powerline Network.

Provides a connectivity binary sensor per adapter.
"""

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, get_mac
from .coordinator import TpLinkPowerlineCoordinator
from .sensor import device_info_for_adapter, network_device_info, setup_dynamic_platform

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors from config entry."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _factory(mac: str, dev: dict[str, Any]) -> list[BinarySensorEntity]:
        return [PlcConnectivitySensor(coordinator, mac, device_info_for_adapter(mac, dev))]

    setup_dynamic_platform(coordinator, async_add_entities, _factory)

    # Network-wide health indicator on the overview device.
    async_add_entities([NetworkProblemSensor(coordinator)])


class NetworkProblemSensor(CoordinatorEntity[TpLinkPowerlineCoordinator], BinarySensorEntity):
    """Network health: on when something is wrong (an adapter is offline)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "network_problem"

    def __init__(self, coordinator: TpLinkPowerlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "tplink_plc_network_problem"
        self._attr_device_info = network_device_info()

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        return bool(data.get("network_problem"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "adapters_online": data.get("plc_device_count"),
            "adapters_total": data.get("plc_device_count_total"),
        }


class PlcConnectivitySensor(CoordinatorEntity[TpLinkPowerlineCoordinator], BinarySensorEntity):
    """Connectivity binary sensor for a Powerline adapter."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator, mac: str, device_info) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_online"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        devs = (self.coordinator.data or {}).get("plc_devices", {})
        dev = devs.get(self._mac) if isinstance(devs, dict) else None
        if dev is None:
            return None
        return dev.get("_online", True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        devs = (self.coordinator.data or {}).get("plc_devices", {})
        dev = devs.get(self._mac) if isinstance(devs, dict) else None
        if dev:
            return {
                "mac": self._mac,
                "firmware": dev.get("firmware_ver", ""),
                "same_network": dev.get("same_network", True),
            }
        return {"mac": self._mac}
