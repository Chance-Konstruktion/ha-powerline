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

from .const import (
    DOMAIN,
    MANUFACTURER,
    NETWORK_DEVICE_ID,
    NETWORK_DEVICE_NAME,
    get_mac,
    vendor_for_mac,
)
from .coordinator import TpLinkPowerlineCoordinator

_LOGGER = logging.getLogger(__name__)


def device_info_for_adapter(mac: str, dev: dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo for a single Powerline adapter."""
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        name=f"Powerline {mac[-8:]}",
        manufacturer=dev.get("manufacturer") or vendor_for_mac(mac),
        model=dev.get("model") or "Powerline Adapter",
        sw_version=dev.get("firmware_ver") or None,
        suggested_area="Network",
    )


def network_device_info() -> DeviceInfo:
    """Build DeviceInfo for the network-wide virtual device."""
    return DeviceInfo(
        identifiers={(DOMAIN, NETWORK_DEVICE_ID)},
        name=NETWORK_DEVICE_NAME,
        manufacturer=MANUFACTURER,
        model="Powerline Network",
        suggested_area="Network",
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
    # Share this set with the coordinator so a device deleted from the UI can
    # be re-created if the adapter is rediscovered later.
    coordinator.register_tracked_macs(tracked_macs)

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

    # Only add route-level Recorder sensors when consumer correlation is enabled.
    # The route sensors are ordinary HA entities, so their history belongs to
    # Recorder and is retained/cleaned up under Home Assistant's normal policy.
    tracked_links: set[tuple[str, str]] = set()

    def _on_topology_links(edges: list[dict[str, Any]]) -> None:
        if not getattr(coordinator, "power_sensors", []):
            return
        new_entities: list[SensorEntity] = []
        for edge in edges:
            source = edge.get("source")
            destination = edge.get("destination")
            if not source or not destination:
                continue
            pair = (source, destination)
            if pair in tracked_links:
                continue
            tracked_links.add(pair)
            new_entities.append(
                PowerlineLinkRateSensor(coordinator, source, destination)
            )
        if new_entities:
            async_add_entities(new_entities)

    coordinator.register_new_link_callback(_on_topology_links)
    if coordinator.power_sensors and coordinator.data:
        _on_topology_links(
            (coordinator.data.get("topology") or {}).get("edges", [])
        )

    # Network-wide sensors. The old "TX/RX total" sums were meaningless (adding
    # unrelated link rates); replaced with the two things that actually matter:
    # how many adapters are reachable, and the weakest link in the network.
    async_add_entities([
        AdaptersOnlineSensor(coordinator),
        SlowestLinkSensor(coordinator),
    ])


# --- Network-wide sensors ---

class AdaptersOnlineSensor(CoordinatorEntity[TpLinkPowerlineCoordinator], SensorEntity):
    """How many adapters are currently reachable (total exposed as attribute)."""

    _attr_has_entity_name = True
    _attr_translation_key = "adapters_online"
    _attr_icon = "mdi:lan"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TpLinkPowerlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "tplink_plc_adapters_online"
        self._attr_device_info = network_device_info()

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("plc_device_count")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"total": (self.coordinator.data or {}).get("plc_device_count_total")}


class SlowestLinkSensor(CoordinatorEntity[TpLinkPowerlineCoordinator], SensorEntity):
    """The weakest link rate in the network — the bottleneck that matters."""

    _attr_has_entity_name = True
    _attr_translation_key = "slowest_link"
    _attr_icon = "mdi:speedometer-slow"
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TpLinkPowerlineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "tplink_plc_slowest_link"
        self._attr_device_info = network_device_info()

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("slowest_link")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"adapter": (self.coordinator.data or {}).get("slowest_link_mac")}


class PowerlineLinkRateSensor(
    CoordinatorEntity[TpLinkPowerlineCoordinator], SensorEntity
):
    """Recorder-backed, route-specific link-rate measurement for correlation."""

    _attr_has_entity_name = False
    _attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lan-connect"
    _attr_force_update = True

    def __init__(
        self, coordinator: TpLinkPowerlineCoordinator, source: str, destination: str
    ) -> None:
        super().__init__(coordinator)
        self._source = source
        self._destination = destination
        self._attr_unique_id = f"plc_link_{source}_{destination}_rate"
        self._attr_name = (
            f"{coordinator.adapter_name(source)} ↔ "
            f"{coordinator.adapter_name(destination)} link rate"
        )
        self._attr_device_info = network_device_info()

    @property
    def force_update(self) -> bool:
        """Record each poll while correlation is enabled, even for steady rates."""
        return bool(getattr(self.coordinator, "power_sensors", []))

    @property
    def native_value(self) -> int | None:
        topology = (self.coordinator.data or {}).get("topology") or {}
        for edge in topology.get("edges", []):
            if (
                edge.get("source") == self._source
                and edge.get("destination") == self._destination
            ):
                return edge.get("average_rate")
        return None

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None


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
    def available(self) -> bool:
        return super().available and self.coordinator.adapter_online(self._mac)

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
    def available(self) -> bool:
        return super().available and self.coordinator.adapter_online(self._mac)

    @property
    def native_value(self) -> int | None:
        rates = (self.coordinator.data or {}).get("plc_rates", {})
        r = rates.get(self._mac)
        return r["rx"] if r else None
