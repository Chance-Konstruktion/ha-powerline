"""Unit tests for defensive LED handling."""

import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "powerline"
    / "homeplug.py"
)
_SPEC = importlib.util.spec_from_file_location("powerline_homeplug", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)

HomeplugAV = _MODULE.HomeplugAV
MX_ACTION_CNF = _MODULE.MX_ACTION_CNF
MX_SET_KEY_CNF = _MODULE.MX_SET_KEY_CNF
build_mx_set_param = _MODULE.build_mx_set_param
ETH_HDR = _MODULE.ETH_HDR
MX_MME_HDR = _MODULE.MX_MME_HDR
ETHERTYPE_MEDIAXTREAM = _MODULE.ETHERTYPE_MEDIAXTREAM
MX_SET_PARAM_REQ = _MODULE.MX_SET_PARAM_REQ
MX_SET_PARAM_CNF = _MODULE.MX_SET_PARAM_CNF
MX_APPLY_REQ = _MODULE.MX_APPLY_REQ
MX_APPLY_CNF = _MODULE.MX_APPLY_CNF
PARAM_LED_CONTROL = _MODULE.PARAM_LED_CONTROL
PARAM_LED_OPTIONS = _MODULE.PARAM_LED_OPTIONS
PARAM_LED_AUX = _MODULE.PARAM_LED_AUX
PARAM_POWER_STANDBY = _MODULE.PARAM_POWER_STANDBY
PARAM_POWER_STANDBY_AUX = _MODULE.PARAM_POWER_STANDBY_AUX
import struct as _struct


def _param_id(frame):
    return _struct.unpack("<H", _mme_payload(frame)[0:2])[0]


def _mme_type(frame):
    return _struct.unpack("<H", frame[ETH_HDR + 1:ETH_HDR + 3])[0]


def _mme_payload(frame):
    return frame[ETH_HDR + MX_MME_HDR:]


class TestSetParameterFrame(TestCase):
    """The LED/power-saving writes must be real Set Parameter (0xA058) frames."""

    def test_build_mx_set_param_carries_param_id_and_value(self) -> None:
        dst = bytes.fromhex("b01921f5dba7")
        src = bytes.fromhex("001122334455")
        frame = build_mx_set_param(dst, src, PARAM_LED_CONTROL, b"\x01", seq=7)

        # Ethertype 0x8912 (MEDIAXTREAM)
        self.assertEqual(ETHERTYPE_MEDIAXTREAM,
                         _struct.unpack("!H", frame[12:14])[0])
        # MMTYPE 0xA058 (Set Parameter), little-endian after version byte
        self.assertEqual(MX_SET_PARAM_REQ,
                         _struct.unpack("<H", frame[ETH_HDR + 1:ETH_HDR + 3])[0])
        # Payload: ParamID(2 LE) + OctetsPerElement + NumElements(2 LE) + Value
        payload = frame[ETH_HDR + MX_MME_HDR:]
        self.assertEqual(PARAM_LED_CONTROL,
                         _struct.unpack("<H", payload[0:2])[0])
        self.assertEqual(1, payload[2])               # octets per element
        self.assertEqual(1, _struct.unpack("<H", payload[3:5])[0])  # num elements
        self.assertEqual(0x01, payload[5])            # LED on

    def test_set_led_off_sends_captured_tpplc_sequence(self) -> None:
        """LED OFF must send 0x0095, 0x003F (=02a00102) and Apply 0xA020."""
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        frames = []

        def _capture(sock, frame, *args, **kwargs):
            frames.append(frame)
            # ACK the Apply so set_led reports success.
            return [(MX_APPLY_CNF, "B0:19:21:F5:DB:A7", b"")]

        with patch.object(hp, "_open_hpav"), \
             patch.object(hp, "_open_mx"), \
             patch.object(hp, "_send_recv", side_effect=_capture), \
             patch.object(hp, "_close"):
            result = hp.set_led("B0:19:21:F5:DB:A7", False)

        self.assertTrue(result)
        self.assertEqual(3, len(frames))
        # Frame 1: Set Parameter 0x0095
        p1 = _mme_payload(frames[0])
        self.assertEqual(MX_SET_PARAM_REQ, _mme_type(frames[0]))
        self.assertEqual(PARAM_LED_AUX, _struct.unpack("<H", p1[0:2])[0])
        # Frame 2: Set Parameter 0x003F with the OFF LED-options value
        p2 = _mme_payload(frames[1])
        self.assertEqual(PARAM_LED_OPTIONS, _struct.unpack("<H", p2[0:2])[0])
        self.assertEqual(bytes.fromhex("02a00102"), p2[5:9])  # OFF value
        # Frame 3: Apply
        self.assertEqual(MX_APPLY_REQ, _mme_type(frames[2]))

    def test_set_led_on_sets_enable_flag(self) -> None:
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        frames = []

        def _capture(sock, frame, *args, **kwargs):
            frames.append(frame)
            return [(MX_APPLY_CNF, "B0:19:21:F5:DB:A7", b"")]

        with patch.object(hp, "_open_hpav"), \
             patch.object(hp, "_open_mx"), \
             patch.object(hp, "_send_recv", side_effect=_capture), \
             patch.object(hp, "_close"):
            hp.set_led("B0:19:21:F5:DB:A7", True)

        p2 = _mme_payload(frames[1])
        self.assertEqual(bytes.fromhex("02a00112"), p2[5:9])  # ON value, bit 0x10 set
        self.assertTrue(p2[8] & 0x10)


class TestSendRecvEarlyStop(TestCase):
    """_send_recv must return as soon as a stop_on MMTYPE arrives."""

    @staticmethod
    def _mx_frame(src_mac, mmtype):
        dst = b"\x11" * 6
        eth = dst + src_mac + _struct.pack(">H", 0x8912)
        mme = _struct.pack("<BHH", 2, mmtype, 0) + b"\x00\x1f\x84" + b"\x01"
        return (eth + mme).ljust(60, b"\x00")

    def test_stops_on_ack_without_draining_background(self) -> None:
        import socket as _socket
        adapter = bytes.fromhex("b01921f5dba7")
        frames = [
            self._mx_frame(adapter, 0xA070),   # background beacon
            self._mx_frame(adapter, MX_SET_PARAM_CNF),  # our ACK -> should stop here
            self._mx_frame(adapter, 0xA070),   # must NOT be read
        ]

        class _FakeSock:
            def __init__(self): self.read = 0
            def settimeout(self, t): pass
            def send(self, f): pass
            def recv(self, n):
                if self.read < len(frames):
                    f = frames[self.read]; self.read += 1; return f
                raise _socket.timeout()

        hp = HomeplugAV("eth0")
        sock = _FakeSock()
        out = hp._send_recv(sock, b"x", timeout=5.0,
                            expected_src="B0:19:21:F5:DB:A7",
                            stop_on=frozenset((MX_SET_PARAM_CNF,)))
        self.assertEqual(2, sock.read)            # stopped right after the ACK
        self.assertEqual(MX_SET_PARAM_CNF, out[-1][0])


class TestSetQos(TestCase):
    """QoS does a read-modify-write of the 0x0069 priority map."""

    MAC = "B0:19:21:F5:E0:DC"

    def test_gaming_patches_cap_bytes(self) -> None:
        from custom_components.powerline import homeplug  # noqa
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        table = bytes(1000)  # zeroed table read back from the adapter
        sent = {}

        def _capture(sock, frame, *a, **k):
            sent["frame"] = frame
            return [(MX_SET_PARAM_CNF, self.MAC, b"")]

        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_get_param_value", return_value=table), \
             patch.object(hp, "_send_recv", side_effect=_capture), \
             patch.object(hp, "_close"):
            ok = hp.set_qos_priority(self.MAC, "gaming")

        self.assertTrue(ok)
        # value starts after param(2)+octets(1)+num(2) = 5 bytes
        value = _mme_payload(sent["frame"])[5:]
        offsets = (2, 27, 52, 77, 102, 127, 152, 177)
        gaming = (0x38, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78)
        for off, cap in zip(offsets, gaming):
            self.assertEqual(cap, value[off], f"offset {off}")


class TestSetPowerSaving(TestCase):
    """Power saving must toggle bit 0x8000 of param 0x0029 and Apply."""

    MAC = "B0:19:21:F5:DB:A7"

    def _run(self, on):
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        frames = []

        def _capture(sock, frame, *a, **k):
            frames.append(frame)
            mme = _mme_type(frame)
            if mme == MX_APPLY_REQ:
                return [(MX_APPLY_CNF, self.MAC, b"")]
            if mme == MX_SET_PARAM_REQ:
                return [(MX_SET_PARAM_CNF, self.MAC, b"")]
            return []  # GET -> no reply, timeout falls back to 300

        with patch.object(hp, "_open_hpav"), \
             patch.object(hp, "_open_mx"), \
             patch.object(hp, "_send_recv", side_effect=_capture), \
             patch.object(hp, "_close"):
            result = hp.set_power_saving(self.MAC, on)
        return result, frames

    def test_power_saving_on_sets_enable_bit(self) -> None:
        result, frames = self._run(True)
        self.assertTrue(result)
        set_029 = [f for f in frames
                   if _mme_type(f) == MX_SET_PARAM_REQ and _param_id(f) == PARAM_POWER_STANDBY]
        self.assertEqual(1, len(set_029))
        val = _struct.unpack("<H", _mme_payload(set_029[0])[5:7])[0]
        self.assertTrue(val & 0x8000)            # enabled flag
        self.assertEqual(300, val & 0x7FFF)      # standby timeout preserved
        self.assertTrue(any(_mme_type(f) == MX_APPLY_REQ for f in frames))

    def test_power_saving_off_clears_bit_and_writes_aux(self) -> None:
        result, frames = self._run(False)
        self.assertTrue(result)
        params = [_param_id(f) for f in frames if _mme_type(f) == MX_SET_PARAM_REQ]
        self.assertIn(PARAM_POWER_STANDBY, params)
        self.assertIn(PARAM_POWER_STANDBY_AUX, params)   # tpPLC clears 0x0074 on disable
        set_029 = next(f for f in frames
                       if _mme_type(f) == MX_SET_PARAM_REQ and _param_id(f) == PARAM_POWER_STANDBY)
        val = _struct.unpack("<H", _mme_payload(set_029)[5:7])[0]
        self.assertFalse(val & 0x8000)           # disabled


class TestHomeplugSetLed(TestCase):
    """Tests for HomeplugAV.set_led()."""

    def test_set_led_returns_false_when_socket_open_fails(self) -> None:
        hp = HomeplugAV("eth0")

        with patch.object(hp, "_open_hpav", side_effect=OSError("no socket")), \
             patch.object(hp, "_close") as close_mock:
            result = hp.set_led("AA:BB:CC:DD:EE:FF", True)

        self.assertFalse(result)
        close_mock.assert_called_once()

    def test_set_led_returns_false_on_unexpected_runtime_error(self) -> None:
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()

        with patch.object(hp, "_open_hpav"), \
             patch.object(hp, "_open_mx"), \
             patch.object(hp, "_send_recv", side_effect=RuntimeError("boom")), \
             patch.object(hp, "_close") as close_mock:
            result = hp.set_led("AA:BB:CC:DD:EE:FF", False)

        self.assertFalse(result)
        close_mock.assert_called_once()

    def test_set_led_returns_true_on_expected_confirmation(self) -> None:
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()

        with patch.object(hp, "_open_hpav"), \
             patch.object(hp, "_open_mx"), \
             patch.object(
                 hp,
                 "_send_recv",
                 return_value=[(MX_ACTION_CNF, "AA:BB:CC:DD:EE:FF", b"dummy")],
             ), \
             patch.object(hp, "_close") as close_mock:
            result = hp.set_led("AA:BB:CC:DD:EE:FF", True)

        self.assertTrue(result)
        close_mock.assert_called_once()

    def test_set_led_returns_false_when_only_status_broadcast_seen(self) -> None:
        """A 0x6046 status broadcast is NOT a valid action confirmation."""
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()

        MX_STATUS_IND = _MODULE.MX_STATUS_IND
        with patch.object(hp, "_open_hpav"), \
             patch.object(hp, "_open_mx"), \
             patch.object(
                 hp,
                 "_send_recv",
                 return_value=[(MX_STATUS_IND, "AA:BB:CC:DD:EE:FF", b"dummy")],
             ), \
             patch.object(hp, "_close"):
            result = hp.set_led("AA:BB:CC:DD:EE:FF", True)

        self.assertFalse(result)
