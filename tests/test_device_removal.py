"""Unit tests for per-device removal.

Covers ``TpLinkPowerlineCoordinator.forget_device`` and the integration's
``async_remove_config_entry_device`` hook that lets a user delete a single
adapter from the UI instead of removing the whole integration.
"""

from unittest import IsolatedAsyncioTestCase, TestCase

# conftest.py installs all HA stubs before this module is collected.
import custom_components.powerline as integration
from custom_components.powerline.const import DOMAIN, NETWORK_DEVICE_ID
from custom_components.powerline.coordinator import TpLinkPowerlineCoordinator

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"


def _coordinator_with(macs):
    """Build a coordinator pre-populated as if those adapters were seen."""
    coord = TpLinkPowerlineCoordinator.__new__(TpLinkPowerlineCoordinator)
    coord.devices = {m: {"mac": m} for m in macs}
    coord._known_macs = set(macs)
    coord.led_states = {m: True for m in macs}
    coord.power_saving_states = {m: False for m in macs}
    coord.qos_states = {m: "internet" for m in macs}
    coord._tracked_mac_sets = []
    return coord


class _FakeDeviceEntry:
    def __init__(self, identifiers, name="device"):
        self.identifiers = identifiers
        self.name = name


class _FakeConfigEntries:
    def async_update_entry(self, entry, data=None):
        entry.data = data


class _FakeConfigEntry:
    def __init__(self, entry_id, data):
        self.entry_id = entry_id
        self.data = data


class _FakeHass:
    def __init__(self, coordinator, entry_id):
        self.data = {DOMAIN: {entry_id: coordinator}}
        self.config_entries = _FakeConfigEntries()


# ---------------------------------------------------------------------------
# forget_device
# ---------------------------------------------------------------------------

class TestForgetDevice(TestCase):
    def test_clears_all_state_and_tracked_sets(self):
        coord = _coordinator_with([MAC_A, MAC_B])
        tracked = {MAC_A, MAC_B}
        coord._tracked_mac_sets = [tracked]

        coord.forget_device(MAC_A)

        for store in (
            coord.devices,
            coord._known_macs,
            coord.led_states,
            coord.power_saving_states,
            coord.qos_states,
            tracked,
        ):
            self.assertNotIn(MAC_A, store)
        # The other adapter must be left untouched.
        self.assertIn(MAC_B, coord.devices)
        self.assertIn(MAC_B, tracked)

    def test_unknown_mac_is_noop(self):
        coord = _coordinator_with([MAC_A])
        coord.forget_device("00:00:00:00:00:99")  # must not raise
        self.assertIn(MAC_A, coord.devices)

    def test_normalises_mac(self):
        coord = _coordinator_with([MAC_A])
        coord.forget_device(MAC_A.lower())
        self.assertNotIn(MAC_A, coord.devices)


# ---------------------------------------------------------------------------
# async_remove_config_entry_device
# ---------------------------------------------------------------------------

class TestRemoveConfigEntryDevice(IsolatedAsyncioTestCase):
    async def test_refuses_network_hub_device(self):
        coord = _coordinator_with([MAC_A])
        entry = _FakeConfigEntry("e1", {"devices": [{"mac": MAC_A}]})
        hass = _FakeHass(coord, "e1")
        device = _FakeDeviceEntry({(DOMAIN, NETWORK_DEVICE_ID)}, name="Powerline Network")

        result = await integration.async_remove_config_entry_device(hass, entry, device)

        self.assertFalse(result)
        # Nothing was forgotten.
        self.assertIn(MAC_A, coord.devices)
        self.assertEqual(entry.data["devices"], [{"mac": MAC_A}])

    async def test_removes_adapter_forgets_and_persists(self):
        coord = _coordinator_with([MAC_A, MAC_B])
        entry = _FakeConfigEntry(
            "e1", {"interface": "eth0", "devices": [{"mac": MAC_A}, {"mac": MAC_B}]}
        )
        hass = _FakeHass(coord, "e1")
        device = _FakeDeviceEntry({(DOMAIN, MAC_A)})

        result = await integration.async_remove_config_entry_device(hass, entry, device)

        self.assertTrue(result)
        # Forgotten from the live coordinator, the other adapter stays.
        self.assertNotIn(MAC_A, coord.devices)
        self.assertIn(MAC_B, coord.devices)
        # Persisted list dropped MAC_A but kept MAC_B and other config keys.
        self.assertEqual(entry.data["devices"], [{"mac": MAC_B}])
        self.assertEqual(entry.data["interface"], "eth0")

    async def test_succeeds_without_loaded_coordinator(self):
        entry = _FakeConfigEntry("e1", {"devices": [{"mac": MAC_A}]})
        hass = _FakeHass(None, "e1")  # integration not loaded / already gone
        device = _FakeDeviceEntry({(DOMAIN, MAC_A)})

        result = await integration.async_remove_config_entry_device(hass, entry, device)

        self.assertTrue(result)
        self.assertEqual(entry.data["devices"], [])
