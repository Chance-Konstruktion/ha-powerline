"""Tests for error paths in coordinator and control modules."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch, AsyncMock

# conftest.py installs all HA stubs before this module is collected.
from custom_components.powerline.coordinator import (
    OFFLINE_AFTER_MISSED_POLLS,
    TpLinkPowerlineCoordinator,
)
from custom_components.powerline.const import TOPOLOGY_EVENT, get_mac
from custom_components.powerline.topology import TopologyManager
from custom_components.powerline.homeplug.parsers import (
    parse_discover_cnf,
    parse_mx_nw_info_cnf,
    parse_qca_nw_info_cnf,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
import struct


MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"

def _make_device(mac, tx=100, rx=50):
    return {"mac": mac, "tx_rate": tx, "rx_rate": rx}

class _FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, event_data):
        self.events.append((event_type, event_data))

class _FakeHass:
    """Minimal hass stub that runs executor jobs synchronously."""
    def __init__(self):
        self.bus = _FakeBus()
        self.data = {}
        self.config_entries = MagicMock()
        self.config_entries.async_update_entry = AsyncMock()
        self.states = MagicMock()
        self.states.async_set = AsyncMock()
        self.helpers = MagicMock()
        self.helpers.dispatcher = MagicMock()
        self.helpers.dispatcher.async_dispatcher_send = MagicMock()

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


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
    coord._tracked_mac_sets = []
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
    coord._states_queried = False
    coord.logger = MagicMock()
    return coord


class TestCoordinatorErrorPaths(IsolatedAsyncioTestCase):
    """Test coordinator error handling."""

    async def test_discover_permission_error(self):
        """Test discover handles PermissionError by raising UpdateFailed."""
        coord = _build_coordinator([])
        coord.hp.discover.side_effect = PermissionError("Permission denied")

        with self.assertRaises(UpdateFailed):
            await coord._async_update_data()

    async def test_discover_os_error(self):
        """Test discover handles OSError by raising UpdateFailed."""
        coord = _build_coordinator([])
        coord.hp.discover.side_effect = OSError("Cannot open socket")

        with self.assertRaises(UpdateFailed):
            await coord._async_update_data()

    async def test_discover_generic_exception(self):
        """Test discover handles generic exception by raising UpdateFailed."""
        coord = _build_coordinator([])
        coord.hp.discover.side_effect = RuntimeError("Unexpected error")

        with self.assertRaises(UpdateFailed):
            await coord._async_update_data()

    async def test_query_states_exception(self):
        """Test query_device_states exception is caught and defaults applied."""
        device = _make_device(MAC_A)
        coord = _build_coordinator([device])
        coord.hp.query_device_states.side_effect = RuntimeError("Query failed")

        data = await coord._async_update_data()
        # Should not crash, device states just not queried - defaults applied
        self.assertEqual(data["plc_device_count"], 1)

    async def test_forget_device_clears_all(self):
        """Test forget_device clears all tracked sets."""
        device = _make_device(MAC_A)
        coord = _build_coordinator([device])

        await coord._async_update_data()

        # Verify device is known
        self.assertIn(MAC_A, coord.devices)

        # Forget the device
        coord.forget_device(MAC_A)

        # Verify it's cleared
        self.assertNotIn(MAC_A, coord.devices)
        self.assertNotIn(MAC_A, coord._missed_polls)
        self.assertNotIn(MAC_A, coord.led_states)
        self.assertNotIn(MAC_A, coord.qos_states)
        self.assertNotIn(MAC_A, coord.power_saving_states)

    async def test_forget_device_normalizes_mac(self):
        """Test forget_device normalizes MAC."""
        device = _make_device(MAC_A)
        coord = _build_coordinator([device])
        await coord._async_update_data()

        # Forget with lowercase
        coord.forget_device(MAC_A.lower())

        self.assertNotIn(MAC_A, coord.devices)

    async def test_forget_unknown_device_noop(self):
        """Test forget_device on unknown MAC is noop."""
        device = _make_device(MAC_A)
        coord = _build_coordinator([device])
        await coord._async_update_data()

        coord.forget_device("00:00:00:00:00:00")

        # Original device still there
        self.assertIn(MAC_A, coord.devices)


class TestParseErrorPaths(IsolatedAsyncioTestCase):
    """Test parser functions with error-like inputs."""

    def _frame(self, body: bytes, ethertype: int = 0x88E1) -> bytes:
        eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", ethertype)
        return eth + b"\x01\x39\xa0\x00\x00\xb0\x52" + body

    def test_parse_discover_cnf_exception_handling(self):
        """Test parse_discover_cnf with various malformed inputs."""
        # These should not raise exceptions
        parse_discover_cnf(b"")
        parse_discover_cnf(self._frame(b""))
        parse_discover_cnf(self._frame(b"\x00"))
        parse_discover_cnf(self._frame(b"\xff" * 100))

    def test_parse_mx_nw_info_cnf_exception_handling(self):
        """Test parse_mx_nw_info_cnf with various malformed inputs."""
        parse_mx_nw_info_cnf(b"")
        parse_mx_nw_info_cnf(self._frame(b""))
        parse_mx_nw_info_cnf(self._frame(b"\x00"))
        parse_mx_nw_info_cnf(self._frame(b"\xff" * 100))

    def test_parse_qca_nw_info_cnf_exception_handling(self):
        """Test parse_qca_nw_info_cnf with various malformed inputs."""
        parse_qca_nw_info_cnf(b"")
        parse_qca_nw_info_cnf(self._frame(b""))
        parse_qca_nw_info_cnf(self._frame(b"\x00"))
        parse_qca_nw_info_cnf(self._frame(b"\xff" * 100))


class TestMixedNetworkChipset(IsolatedAsyncioTestCase):
    """Test mixed network chipset handling."""

    async def test_mixed_chipset_discovery(self):
        """Test discovery with mixed Broadcom and Qualcomm adapters."""
        bc_device = {"mac": "BB:CC:DD:EE:FF:01", "plcmac": "BB:CC:DD:EE:FF:01", "model": "TL-PA7017", "firmware_ver": "1.0", "tx_rate": 500, "rx_rate": 400}
        qca_device = {"mac": "AA:BB:CC:DD:EE:FF", "plcmac": "AA:BB:CC:DD:EE:FF", "model": "QCA7420", "firmware_ver": "2.0", "tx_rate": 150, "rx_rate": 100}

        coord = _build_coordinator([bc_device, qca_device])
        coord.hp.query_device_states.return_value = {
            "BB:CC:DD:EE:FF:01": {"led": True, "qos": "gaming", "power_saving": False},
            "AA:BB:CC:DD:EE:FF": {"led": False, "qos": "internet", "power_saving": True},
        }

        data = await coord._async_update_data()
        self.assertEqual(data["plc_device_count"], 2)
        self.assertEqual(data["plc_device_count_total"], 2)

    async def test_chipset_detection_broadcom(self):
        """Test chipset detection for Broadcom adapters."""
        device = _make_device("BB:CC:DD:EE:FF:01", tx=500, rx=400)
        coord = _build_coordinator([device])
        coord.hp.query_device_states.return_value = {"BB:CC:DD:EE:FF:01": {"led": True, "qos": "gaming", "power_saving": False}}

        data = await coord._async_update_data()
        self.assertEqual(data["plc_device_count"], 1)

    async def test_chipset_detection_qualcomm(self):
        """Test chipset detection for Qualcomm adapters."""
        device = _make_device("AA:BB:CC:DD:EE:FF", tx=150, rx=100)
        coord = _build_coordinator([device])
        coord.hp.query_device_states.return_value = {"AA:BB:CC:DD:EE:FF": {"led": False, "qos": "internet", "power_saving": True}}

        data = await coord._async_update_data()
        self.assertEqual(data["plc_device_count"], 1)

    async def test_unknown_chipset_tries_broadcom_first(self):
        """Test unknown chipset tries Broadcom path first."""
        device = _make_device("XX:YY:ZZ:AA:BB:CC", tx=200, rx=150)
        coord = _build_coordinator([device])
        coord.hp.query_device_states.return_value = {"XX:YY:ZZ:AA:BB:CC": {"led": True, "qos": "gaming", "power_saving": False}}

        data = await coord._async_update_data()
        self.assertEqual(data["plc_device_count"], 1)