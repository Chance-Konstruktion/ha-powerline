"""Unit tests for TpLinkPowerlineCoordinator._async_update_data."""

import asyncio
import types
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

# conftest.py installs all HA stubs before this module is collected.
from custom_components.powerline.coordinator import (
    HISTORY_SAVE_INTERVAL,
    OFFLINE_AFTER_MISSED_POLLS,
    TpLinkPowerlineCoordinator,
)
from custom_components.powerline.const import TOPOLOGY_EVENT, get_mac
from custom_components.powerline.topology import TopologyManager

# Use uppercase MACs so get_mac() normalisation is a no-op.
MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"


def _make_device(mac, tx=100, rx=50):
    return {"mac": mac, "tx_rate": tx, "rx_rate": rx}


class _FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, event_data):
        self.events.append((event_type, event_data))


class _FakeClock:
    """Controllable stand-in patched over coordinator.monotonic."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _FakeStore:
    """Counts what the coordinator hands to the real Store."""

    def __init__(self):
        self.save_count = 0
        self.last_data = None

    async def async_save(self, data):
        self.save_count += 1
        self.last_data = data


class _FakeHass:
    """Minimal hass stub that runs executor jobs synchronously."""

    def __init__(self):
        self.bus = _FakeBus()
        self.background_tasks = []

    async def async_add_executor_job(self, func, *args):
        # Simply call the (possibly mocked) function; MagicMock.return_value is returned.
        return func(*args)

    def async_create_background_task(self, coro, name=None, **kwargs):
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.background_tasks.append(task)
        return task

    async def await_background_tasks(self):
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks)
            self.background_tasks.clear()


def _build_coordinator(discover_result, state_result=None):
    """Instantiate a coordinator without opening real sockets."""
    coord = TpLinkPowerlineCoordinator.__new__(TpLinkPowerlineCoordinator)
    coord.hass = _FakeHass()
    coord.hp = MagicMock()
    coord.hp.discover.return_value = discover_result
    coord.hp.query_device_states.return_value = state_result or {}
    coord.hp.plc_links = {}
    coord.devices = {}
    coord._known_macs = set()
    coord._missed_polls = {}
    coord._new_device_callbacks = []
    coord.led_states = {}
    coord.power_saving_states = {}
    coord.qos_states = {}
    coord.topology = TopologyManager()
    from custom_components.powerline.history import TopologyHistory
    from custom_components.powerline.alerts import TopologyAlerts

    coord.history = TopologyHistory()
    coord.alerts = TopologyAlerts()
    coord.alerts_enabled = True
    coord.history_store = None
    coord.logger = MagicMock()
    return coord


def _preload(coord, devices):
    """Pre-populate coordinator as if those devices were seen before."""
    for dev in devices:
        mac = get_mac(dev)
        if mac:
            coord.devices[mac] = dict(dev)
            coord._known_macs.add(mac)


async def _poll_until_offline(coord):
    """Run enough polls for a missing adapter to cross the offline threshold."""
    data = None
    for _ in range(OFFLINE_AFTER_MISSED_POLLS):
        data = await coord._async_update_data()
    return data


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

    async def test_history_is_saved_on_a_fixed_cadence(self):
        """(#11) Store.async_delay_save debounces: with polls faster than the
        delay (the default 120 s poll against the old 300 s delay) every poll
        pushed the pending write time out again and the store never wrote --
        the history only survived shutdowns. The coordinator writes itself
        now, at most once per HISTORY_SAVE_INTERVAL."""
        devices = [_make_device(MAC_A, tx=200, rx=100), _make_device(MAC_B, tx=300, rx=150)]
        coord = _build_coordinator(discover_result=devices)
        store = _FakeStore()
        coord.history_store = store
        clock = _FakeClock()

        with patch(
            "custom_components.powerline.coordinator.monotonic", clock
        ):
            # Polls five seconds apart, well under the cadence: only the
            # first poll may write.
            for _ in range(3):
                await coord._async_update_data()
                clock.advance(5)
            await coord.hass.await_background_tasks()
            self.assertEqual(1, store.save_count)
            self.assertIn("buckets", store.last_data)

            # Once the cadence has passed, the next poll writes again.
            clock.advance(HISTORY_SAVE_INTERVAL)
            await coord._async_update_data()
            await coord.hass.await_background_tasks()
            self.assertEqual(2, store.save_count)

    async def test_single_device_miss_does_not_hide_other_adapters(self):
        device_a = _make_device(MAC_A)
        device_b = _make_device(MAC_B)
        # A answers while B misses one discovery round. Neither adapter should
        # disappear from the panel because of a single missed response.
        coord = _build_coordinator(discover_result=[device_a])
        _preload(coord, [device_a, device_b])

        data = await coord._async_update_data()

        self.assertTrue(coord.adapter_online(MAC_A))
        self.assertTrue(coord.adapter_online(MAC_B))
        self.assertEqual(data["plc_device_count"], 2)
        self.assertFalse(data["network_problem"])

        # A second consecutive miss marks only B offline; A stays visible.
        data = await coord._async_update_data()

        self.assertTrue(coord.adapter_online(MAC_A))
        self.assertFalse(coord.adapter_online(MAC_B))
        self.assertEqual(data["plc_device_count"], 1)
        self.assertEqual(data["plc_device_count_total"], 2)
        self.assertTrue(data["network_problem"])

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
        # Nothing is left pending after a successful query.
        self.assertFalse(getattr(coord, "_states_pending", None))

    async def test_state_query_runs_only_once(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device], state_result={})

        await coord._async_update_data()
        coord.hp.query_device_states.reset_mock()
        await coord._async_update_data()

        # Second update must not call query_device_states again.
        coord.hp.query_device_states.assert_not_called()

    async def test_state_query_failure_is_retried_next_poll(self):
        """(#9 M2) A failed state query must not burn the one-shot flag:
        the next poll retries, meanwhile the defaults keep showing."""
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device])
        coord.hp.query_device_states.side_effect = RuntimeError("probe down")

        await coord._async_update_data()

        # Defaults applied meanwhile, no crash, no UpdateFailed.
        self.assertTrue(coord.led_states.get(MAC_A))
        self.assertEqual(coord.qos_states.get(MAC_A), "internet")

        # Next poll queries again and applies the real state.
        coord.hp.query_device_states.side_effect = None
        coord.hp.query_device_states.return_value = {
            MAC_A: {"led": False, "qos": "gaming", "power_saving": True},
        }
        await coord._async_update_data()

        self.assertFalse(coord.led_states.get(MAC_A))
        self.assertEqual(coord.qos_states.get(MAC_A), "gaming")

    async def test_later_discovered_adapter_gets_state_queried(self):
        """(#9 M2) An adapter found after the first successful query must not
        stay on defaults until reload — its state is read on a later poll."""
        device_a = _make_device(MAC_A)
        coord = _build_coordinator(
            discover_result=[device_a],
            state_result={MAC_A: {"led": False, "qos": "gaming",
                                  "power_saving": True}},
        )
        await coord._async_update_data()

        coord.hp.discover.return_value = [device_a, _make_device(MAC_B)]
        coord.hp.query_device_states.reset_mock()
        coord.hp.query_device_states.return_value = {
            MAC_B: {"led": False, "qos": "voip", "power_saving": True},
        }
        await coord._async_update_data()

        # Only the new adapter is queried; the old one is not re-read.
        coord.hp.query_device_states.assert_called_once_with([MAC_B])
        self.assertEqual(coord.qos_states.get(MAC_B), "voip")


class TestAdapterOnline(IsolatedAsyncioTestCase):
    """adapter_online() drives the per-adapter entity 'available' property."""

    async def test_reflects_last_poll(self):
        device_a = _make_device(MAC_A)
        device_b = _make_device(MAC_B)
        # Only A is seen now; B was known before but is unplugged.
        coord = _build_coordinator(discover_result=[device_a])
        _preload(coord, [device_a, device_b])

        await _poll_until_offline(coord)

        self.assertTrue(coord.adapter_online(MAC_A))    # online -> available
        self.assertFalse(coord.adapter_online(MAC_B))   # offline -> unavailable

    async def test_single_missed_poll_keeps_adapter_online(self):
        # Issue #109: on noisy wiring one discovery can come back empty.
        # That alone must not flip every adapter to "unavailable".
        device_a = _make_device(MAC_A)
        device_b = _make_device(MAC_B)
        coord = _build_coordinator(discover_result=[])
        _preload(coord, [device_a, device_b])

        data = await coord._async_update_data()

        self.assertTrue(coord.adapter_online(MAC_A))
        self.assertTrue(coord.adapter_online(MAC_B))
        self.assertEqual(data["plc_device_count"], 2)
        self.assertFalse(data["network_problem"])

    async def test_answer_resets_the_miss_counter(self):
        # miss, answer, miss: never two misses in a row -> never offline.
        device_a = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[])
        _preload(coord, [device_a])

        for result in ([], [device_a], []):
            coord.hp.discover.return_value = result
            await coord._async_update_data()
            self.assertTrue(coord.adapter_online(MAC_A))

    async def test_offline_adapter_comes_back_on_first_answer(self):
        device_a = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[])
        _preload(coord, [device_a])
        await _poll_until_offline(coord)
        self.assertFalse(coord.adapter_online(MAC_A))

        coord.hp.discover.return_value = [device_a]
        await coord._async_update_data()

        self.assertTrue(coord.adapter_online(MAC_A))

    def test_forget_device_clears_miss_counter(self):
        coord = _build_coordinator(discover_result=[])
        coord._tracked_mac_sets = []
        _preload(coord, [_make_device(MAC_A)])
        coord._missed_polls[MAC_A] = 1

        coord.forget_device(MAC_A)

        self.assertNotIn(MAC_A, coord._missed_polls)

    def test_unknown_mac_is_offline(self):
        coord = _build_coordinator(discover_result=[])
        self.assertFalse(coord.adapter_online("00:00:00:00:00:00"))

    def test_docstring_matches_the_unknown_mac_behavior(self):
        """(#9 K1) The code returns False for unknown MACs (the test above);
        the docstring must not claim the opposite ("default to online")."""
        doc = TpLinkPowerlineCoordinator.adapter_online.__doc__ or ""
        self.assertNotIn("default to online", doc)

    def test_known_device_without_flag_defaults_online(self):
        # Before the first poll a device has no _online flag yet.
        coord = _build_coordinator(discover_result=[])
        coord.devices = {MAC_A: {"mac": MAC_A}}
        self.assertTrue(coord.adapter_online(MAC_A))


class TestTopology(IsolatedAsyncioTestCase):
    """The coordinator exposes the topology payload and fires its events."""

    async def test_update_returns_topology_payload(self):
        devices = [_make_device(MAC_A), _make_device(MAC_B)]
        coord = _build_coordinator(discover_result=devices)
        coord.hp.plc_links = {
            (MAC_A, MAC_B): {"tx_rate": 720, "rx_rate": 610},
        }

        data = await coord._async_update_data()

        topology = data["topology"]
        self.assertEqual([n["mac"] for n in topology["nodes"]], [MAC_A, MAC_B])
        self.assertEqual(len(topology["edges"]), 1)
        edge = topology["edges"][0]
        self.assertEqual(edge["tx_phy_rate"], 720)
        self.assertEqual(edge["rx_phy_rate"], 610)
        self.assertFalse(edge["estimated"])

    async def test_fires_topology_events_after_update(self):
        device = _make_device(MAC_A)
        coord = _build_coordinator(discover_result=[device])

        await coord._async_update_data()

        events = coord.hass.bus.events
        self.assertEqual(len(events), 1)
        event_type, event_data = events[0]
        self.assertEqual(event_type, TOPOLOGY_EVENT)
        self.assertEqual(event_data["event"], "adapter_online")
        self.assertEqual(event_data["mac"], MAC_A)
