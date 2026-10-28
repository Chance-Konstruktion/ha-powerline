"""Tests for privacy-safe Powerline diagnostics."""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest


def _fake_async_redact_data(data, to_redact):
    """Small test stand-in for Home Assistant's recursive redaction helper."""
    if isinstance(data, dict):
        return {
            key: (
                "**REDACTED**"
                if key in to_redact and value is not None and value != ""
                else _fake_async_redact_data(value, to_redact)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_fake_async_redact_data(value, to_redact) for value in data]
    if isinstance(data, tuple):
        return tuple(_fake_async_redact_data(value, to_redact) for value in data)
    return data


# tests/ uses lightweight Home Assistant stubs from conftest.py. Provide the
# one diagnostics utility this module imports, with the documented semantics.
if "homeassistant.components.diagnostics" not in sys.modules:
    diagnostics_module = types.ModuleType("homeassistant.components.diagnostics")
    diagnostics_module.async_redact_data = _fake_async_redact_data
    sys.modules["homeassistant.components.diagnostics"] = diagnostics_module
    components_module = sys.modules.get("homeassistant.components")
    if components_module is not None:
        components_module.diagnostics = diagnostics_module

from custom_components.powerline.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)


MAC_ONE = "AA:BB:CC:DD:EE:01"
MAC_TWO = "AA:BB:CC:DD:EE:02"


def _poll(index: int) -> dict:
    timed_out = index == 21
    return {
        "timestamp": str(index),
        "status": "success" if not timed_out else "partial",
        "discovered_count": 1,
        "error_type": None,
        "timed_out": False,
        "state_query_error_type": None,
        "devices": [
            {
                "mac": MAC_ONE,
                "status": "ok" if not timed_out else "not_seen",
                "error": None if not timed_out else "no_response_during_discovery",
                "timed_out": timed_out,
                "online": not timed_out,
                "missed_polls": 0 if not timed_out else 1,
                "model": "PLA5456",
                "firmware": "1.0",
                "chipset": "qualcomm",
                "tx_rate": 480,
                "rx_rate": 460,
            }
        ],
    }


@pytest.mark.asyncio
async def test_diagnostics_contains_expected_data_and_redacts_sensitive_values():
    coordinator = SimpleNamespace(
        devices={
            MAC_ONE: {
                "mac": MAC_ONE,
                "model": "PLA5456",
                "firmware_ver": "1.0",
                "chipset": "qualcomm",
                "tx_rate": 480,
                "rx_rate": 460,
                "_online": True,
                "name": "private-adapter-name",
            },
            MAC_TWO: {
                "mac": MAC_TWO,
                "model": "PLA5456",
                "firmware_ver": "1.0",
                "chipset": "qualcomm",
                "tx_rate": 420,
                "rx_rate": 400,
                "_online": False,
            },
        },
        data={
            "topology": {
                "nodes": [
                    {
                        "mac": MAC_ONE,
                        "name": MAC_ONE,
                        "model": "PLA5456",
                        "manufacturer": "Zyxel",
                        "firmware": "1.0",
                        "chipset": "qualcomm",
                        "role": "CCo",
                        "online": True,
                        "last_update": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "mac": MAC_TWO,
                        "name": MAC_TWO,
                        "model": "PLA5456",
                        "manufacturer": "Zyxel",
                        "firmware": "1.0",
                        "chipset": "qualcomm",
                        "role": "Station",
                        "online": False,
                        "last_update": "2026-01-01T00:00:00+00:00",
                    },
                ],
                "edges": [
                    {
                        "source": MAC_ONE,
                        "destination": MAC_TWO,
                        "tx_phy_rate": 480,
                        "rx_phy_rate": 460,
                        "average_rate": 470,
                        "link_quality": "yellow",
                        "estimated": False,
                        "timestamp": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "analysis": {
                    "worst_link": {
                        "source": MAC_ONE,
                        "destination": MAC_TWO,
                        "tx_phy_rate": 480,
                        "rx_phy_rate": 460,
                        "average_rate": 470,
                        "link_quality": "yellow",
                        "estimated": False,
                        "timestamp": "2026-01-01T00:00:00+00:00",
                    },
                    "best_adapter": MAC_ONE,
                    "offline_adapters": [MAC_TWO],
                    "new_adapters": [MAC_ONE],
                },
            }
        },
        hp=SimpleNamespace(
            _chipset="qualcomm",
            plc_links={(MAC_ONE, MAC_TWO): {"tx_rate": 480, "rx_rate": 460}},
            rate_samples=[
                {
                    "responder": MAC_ONE,
                    "peer": MAC_TWO,
                    "direction": "tx",
                    "raw": "0x01D0",
                    "flag_nibble": "0x0",
                    "capability_bit": False,
                    "rate": 480,
                }
            ],
        ),
        poll_history=[_poll(index) for index in range(22)],
    )
    entry = SimpleNamespace(
        entry_id="entry-test",
        version=1,
        minor_version=2,
        data={
            "interface": "enp2s0",
            "probe_url": "http://powerline-host.local:8765",
            "probe_token": "top-secret-probe-token",
            "devices": [
                {
                    "mac": MAC_ONE,
                    "plcmac": MAC_ONE,
                    "hostname": "homeplug-host.local",
                    "ip_address": "192.0.2.15",
                    "serial_number": "SERIAL-991",
                    "name": "private-adapter-name",
                    "model": "PLA5456",
                }
            ],
        },
        options={"scan_interval": 120, "password": "secret-option-password"},
    )
    hass = SimpleNamespace(data={"powerline": {"entry-test": coordinator}})

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    exported = json.dumps(diagnostics)

    assert diagnostics["integration"]["domain"] == "powerline"
    assert diagnostics["integration"]["version"]
    assert diagnostics["entry"]["options"]["scan_interval"] == 120
    assert len(diagnostics["adapters"]) == 2
    assert diagnostics["adapters"][0]["model"] == "PLA5456"
    assert diagnostics["adapters"][0]["firmware"] == "1.0"
    assert diagnostics["adapters"][0]["chipset"] == "qualcomm"
    assert len(diagnostics["topology"]["nodes"]) == 2
    assert diagnostics["topology"]["edges"][0]["source"] == "adapter_01"
    assert diagnostics["plc_links"][0]["responder"] == "adapter_01"
    assert len(diagnostics["poll_history"]) == 20
    assert diagnostics["poll_history"][0]["timestamp"] == "2"
    assert diagnostics["poll_history"][-1]["timestamp"] == "21"
    assert diagnostics["poll_history"][-1]["devices"][0]["timed_out"] is True
    assert (
        diagnostics["poll_history"][-1]["devices"][0]["error"]
        == "no_response_during_discovery"
    )
    assert diagnostics["rate_samples"][0]["responder"] == "**REDACTED**"
    assert diagnostics["rate_sample_summary"]["total"] == 1

    for sensitive_value in (
        MAC_ONE,
        MAC_TWO,
        "192.0.2.15",
        "powerline-host.local",
        "homeplug-host.local",
        "top-secret-probe-token",
        "secret-option-password",
        "SERIAL-991",
        "enp2s0",
        "private-adapter-name",
    ):
        assert sensitive_value not in exported
    assert "**REDACTED**" in exported
