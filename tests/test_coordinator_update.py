"""Unit tests for TpLinkPowerlineCoordinator._async_update_data."""

import types
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

# conftest.py installs all HA stubs before this module is collected.
from custom_components.powerline.coordinator import TpLinkPowerlineCoordinator
from custom_components.powerline.const import TOPOLOGY_EVENT, get_mac
from custom_components.powerline.topology import TopologyManager

# Use uppercase MACs so get_mac() normalisation is a no-op.
MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"


def _make_device(mac, tx=100, rx=50):
    return {"mac": mac, "tx_rate": tx, "rx_rate": rx}


class _NoopBus:
    def async_fire(self, event_type, event_data):
        pass


class _FakeHass:
    """Minimal hass stub that runs executor jobs synchronously."""

    def __init__(self):
        self.bus = _NoopBus()

    async def async_add_executor_job(self, func, *args):
        # Simply call the (possibly mocked) function; MagicMock.return_value is returned.
        return func(*args)


def _build_coordinator(discover_result, state_result=None):
    """Instantiate a coordinator without opening real sockets."""
    coord = TpLinkPowerlineCoordinator.__new__(TpLinkPowerlineCoordinator)
    coord.hass = _FakeHass()
    coord.hp = MagicMock()
    coord.hp.discover.return_value = discover_result
    coord.hp.query_device_states.return_value = state_result or {}
    coord.devices = {}
    coord._known_macs = set()
    coord._new_device_callbacks = []
    coord.led_states = {}
    coord.power_saving_states = {}
    coord.qos_states = {}
    coord.topology = TopologyManager()
    coord._states_queried = False
    coord.logger = MagicMock()
    return coord


def _preload(coord, devices):
    """Pre-populate coordinator as if those devices were seen before."""
    for dev in devices:
        mac = get_mac(dev)
        if mac:
            coord.devices[mac] = dict(dev)
            coord._known_macs.add(mac)


class TestAsyncUpdateData(IsolatedAsyncioTestCase):
    async def test_returns_slowest_link_and_health(self):
        devices = [_make_device(MAC_A, tx=200, rx=100), _make_device(MAC_B, tx=300, rx=150)]
        coord = _build_coordinator(discover_result=devices)

        data = await coord._async_update_data()

        self.assertTrue(data["online"])
        self.assertEqual(data["plc_device_count"], 2)
        # Weakest link = min over adapters of min(tx, rx) = min(100, 150) = 100.
        self.assertEqual(data["slowest_link"], 100)
        self.assertEqual(data["slowest_link_mac"], MAC_A)
        # Both adapters online -> no problem.
        self.assertFalse(data["network_problem"])

    async def test_new_device_triggers_callback(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device])

        callback_received = []
        coord.register_new_device_callback(callback_received.extend)

        await coord._async_update_data()

        self.assertEqual(len(callback_received), 1)
        self.assertEqual(get_mac(callback_received[0]), MAC_A)

    async def test_known_device_does_not_trigger_callback(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device])
        _preload(coord, [device])

        callback_received = []
        coord.register_new_device_callback(callback_received.extend)

        await coord._async_update_data()

        self.assertEqual(len(callback_received), 0)

    async def test_offline_device_counted_in_total_not_online(self):
        device_a = _make_device(MAC_A)
        device_b = _make_device(MAC_B)
        # Only A is returned by discover; B was seen before but is now offline.
        coord = _build_coordinator(discover_result=[device_a])
        _preload(coord, [device_a, device_b])

        data = await coord._async_update_data()

        self.assertEqual(data["plc_device_count"], 1)        # online only
        self.assertEqual(data["plc_device_count_total"], 2)  # includes offline
        self.assertTrue(data["network_problem"])             # B offline -> problem

    async def test_default_states_set_for_new_device(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device], state_result={})

        await coord._async_update_data()

        self.assertTrue(coord.led_states.get(MAC_A))
        self.assertFalse(coord.power_saving_states.get(MAC_A))
        self.assertEqual(coord.qos_states.get(MAC_A), "internet")

    async def test_state_query_applied_on_first_update(self):
        device = _make_device(MAC_A)
        queried = {MAC_A: {"led": False, "qos": "gaming", "power_saving": True}}
        coord = _build_coordinator(discover_result=[device], state_result=queried)

        await coord._async_update_data()

        self.assertFalse(coord.led_states.get(MAC_A))
        self.assertEqual(coord.qos_states.get(MAC_A), "gaming")
        self.assertTrue(coord.power_saving_states.get(MAC_A))
        self.assertTrue(coord._states_queried)

    async def test_state_query_runs_only_once(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device], state_result={})

        await coord._async_update_data()
        coord.hp.query_device_states.reset_mock()
        await coord._async_update_data()

        # Second update must not call query_device_states again.
        coord.hp.query_device_states.assert_not_called()


class TestAdapterOnline(IsolatedAsyncioTestCase):
    """adapter_online() drives the per-adapter entity 'available' property."""

    async def test_reflects_last_poll(self):
        device_a = _make_device(MAC_A)
        device_b = _make_device(MAC_B)
        # Only A is seen now; B was known before but is unplugged.
        coord = _build_coordinator(discover_result=[device_a])
        _preload(coord, [device_a, device_b])

        await coord._async_update_data()

        self.assertTrue(coord.adapter_online(MAC_A))    # online -> available
        self.assertFalse(coord.adapter_online(MAC_B))   # offline -> unavailable

    def test_unknown_mac_is_offline(self):
        coord = _build_coordinator(discover_result=[])
        self.assertFalse(coord.adapter_online("00:00:00:00:00:00"))

    def test_known_device_without_flag_defaults_online(self):
        # Before the first poll a device has no _online flag yet.
        coord = _build_coordinator(discover_result=[])
        coord.devices = {MAC_A: {"mac": MAC_A}}
        self.assertTrue(coord.adapter_online(MAC_A))


class _FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, event_data):
        self.events.append((event_type, event_data))


class TestTopologyEvents(IsolatedAsyncioTestCase):
    async def test_fires_topology_events_after_update(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device])
        coord.hass.bus = _FakeBus()

        await coord._async_update_data()

        self.assertEqual(len(coord.hass.bus.events), 1)
        event_type, event_data = coord.hass.bus.events[0]
        self.assertEqual(event_type, TOPOLOGY_EVENT)
        self.assertEqual(event_data["event"], "adapter_online")
        self.assertEqual(event_data["mac"], MAC_A)
