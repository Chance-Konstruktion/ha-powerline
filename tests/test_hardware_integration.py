"""Opt-in tests for a real HomePlug AV network."""

import os
import re

import pytest

from custom_components.powerline import homeplug as _MODULE

HomeplugAV = _MODULE.HomeplugAV
is_available = _MODULE.is_available

_MAC_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _hardware_enabled() -> bool:
    return os.getenv("POWERLINE_HARDWARE_TESTS") == "1"


@pytest.fixture(scope="module")
def hardware_interface() -> str | None:
    if not _hardware_enabled():
        pytest.skip("Set POWERLINE_HARDWARE_TESTS=1 to run hardware integration tests")
    interface = os.getenv("POWERLINE_INTERFACE")
    if not is_available():
        pytest.skip("Raw HomePlug sockets are not available; run with root or CAP_NET_RAW")
    return interface


def test_hardware_discovery_returns_adapter_inventory(hardware_interface: str | None) -> None:
    hp = HomeplugAV(hardware_interface)
    devices = hp.discover(timeout=float(os.getenv("POWERLINE_DISCOVERY_TIMEOUT", "5.0")))
    min_devices = int(os.getenv("POWERLINE_EXPECT_MIN_DEVICES", "1"))

    assert len(devices) >= min_devices
    for device in devices:
        assert _MAC_RE.match(device["mac"])
        assert device["plcmac"] == device["mac"]
        assert isinstance(device["tx_rate"], int)
        assert isinstance(device["rx_rate"], int)
        assert "capabilities" in device
        assert device["capabilities"]["supports_standard_discovery"] is True


def test_hardware_diagnostics_exercises_hpav_and_vendor_paths(
    hardware_interface: str | None,
) -> None:
    hp = HomeplugAV(hardware_interface)
    report = hp.diagnose(timeout=float(os.getenv("POWERLINE_DIAG_TIMEOUT", "10.0")))

    assert "Dual sockets: 0x88E1 (HomePlug AV) + 0x8912 (MEDIAXTREAM)" in report
    assert "CC_DISCOVER_LIST (0x0014) on 0x88E1" in report
    assert "MX DISCOVER (0xA070) on 0x8912" in report
    assert "QCA VS_NW_INFO (0xA038) on 0x88E1" in report
    assert "Responses:" in report


def test_hardware_passive_rate_polling_returns_well_formed_rates(
    hardware_interface: str | None,
) -> None:
    hp = HomeplugAV(hardware_interface)
    rates = hp.get_passive_rates(timeout=float(os.getenv("POWERLINE_PASSIVE_TIMEOUT", "6.0")))

    assert isinstance(rates, dict)
    for mac, values in rates.items():
        assert _MAC_RE.match(mac)
        assert set(values) == {"tx_rate", "rx_rate"}
        assert isinstance(values["tx_rate"], int)
        assert isinstance(values["rx_rate"], int)
        assert values["tx_rate"] >= 0
        assert values["rx_rate"] >= 0
