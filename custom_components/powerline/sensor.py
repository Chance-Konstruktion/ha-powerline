"""Sensor platform for Powerline Network.

Creates TX/RX sensors per adapter. Dynamically adds entities
when new devices are discovered during polling.
"""

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, NETWORK_DEVICE_ID, NETWORK_DEVICE_NAME, get_mac
from .coordinator import TpLinkPowerlineCoordinator

_LOGGER = logging.getLogger(__name__)


def device_info_for_adapter(mac: str, dev: dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo for a single Powerline adapter."""
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        name=f"Powerline {mac[-8:]}",
        manufacturer="TP-Link",
        model=dev.get("model") or "Powerline Adapter",
        sw_version=dev.get("firmware_ver") or None,
        suggested_area="Netzwerk",
    )


def network_device_info() -> DeviceInfo:
    """Build DeviceInfo for the network-wide virtual device."""
    return DeviceInfo(
        identifiers={(DOMAIN, NETWORK_DEVICE_ID)},
        name=NETWORK_DEVICE_NAME,
        manufacturer="TP-Link",
        model="Powerline Network",
        suggested_area="Netzwerk",
    )


def setup_dynamic_platform(
    coordinator: TpLinkPowerlineCoordinator,
    async_add_entities: AddEntitiesCallback,
    entity_factory: "Callable[[str, dict[str, Any]], list]",
) -> None:
    """Register callback + create entities for already-known devices.

    Eliminates the duplicated pattern across sensor/switch/binary_sensor.
    entity_factory(mac, dev_dict) must return a list of entities.
    """
    tracked_macs: set[str] = set()

    def _on_new_devices(devices: list[dict[str, Any]]) -> None:
        new_entities: list = []
        for dev in devices:
            mac = get_mac(dev)
            if not mac or mac in tracked_macs:
                continue
            tracked_macs.add(mac)
            new_entities.extend(entity_factory(mac, dev))
        if new_entities:
            async_add_entities(new_entities)

    coordinator.register_new_device_callback(_on_new_devices)

    # Create entities for already-known devices
    if coordinator.data:
        devs = coordinator.data.get("plc_devices", {})
        device_list = list(devs.values()) if isinstance(devs, dict) else devs
        _on_new_devices(device_list)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from config entry."""
    coordinator: TpLinkPowerlineCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _sensor_factory(mac: str, dev: dict[str, Any]) -> list[SensorEntity]:
        info = device_info_for_adapter(mac, dev)
        return [
            PlcDeviceTxSensor(coordinator, mac, info),
            PlcDeviceRxSensor(coordinator, mac, info),
        ]

    setup_dynamic_platform(coordinator, async_add_entities, _sensor_factory)

    # Network-wide aggregate sensors
    async_add_entities([
        TotalSensor(coordinator, "total_tx_rate", "tx_total",
                    UnitOfDataRate.MEGABITS_PER_SECOND, "mdi:upload-network"),
        TotalSensor(coordinator, "total_rx_rate", "rx_total",
                    UnitOfDataRate.MEGABITS_PER_SECOND, "mdi:download-network"),
        TotalSensor(coordinator, "plc_device_count", "adapters_online",
                    None, "mdi:lan"),
        TotalSensor(coordinator, "plc_device_count_total", "adapters_total",
                    None, "mdi:lan-check"),
    ])


# --- Network-wide sensors ---

class TotalSensor(CoordinatorEntity[TpLinkPowerlineCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TpLinkPowerlineCoordinator, key: str,
                 translation_key: str, unit: str | None, icon: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"tplink_plc_{key}"
        self._attr_translation_key = translation_key
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        if unit:
            self._attr_device_class = SensorDeviceClass.DATA_RATE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = network_device_info()

    @property
    def native_value(self) -> Any:
        return (self.coordinator.data or {}).get(self._key)


# --- Per-device sensors ---

class PlcDeviceTxSensor(CoordinatorEntity[TpLinkPowerlineCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:upload-network"
    _attr_translation_key = "tx_rate"

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 mac: str, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_tx"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int | None:
        rates = (self.coordinator.data or {}).get("plc_rates", {})
        r = rates.get(self._mac)
        return r["tx"] if r else None


class PlcDeviceRxSensor(CoordinatorEntity[TpLinkPowerlineCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:download-network"
    _attr_translation_key = "rx_rate"

    def __init__(self, coordinator: TpLinkPowerlineCoordinator,
                 mac: str, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"plc_{mac}_rx"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> int | None:
        rates = (self.coordinator.data or {}).get("plc_rates", {})
        r = rates.get(self._mac)
        return r["rx"] if r else None
