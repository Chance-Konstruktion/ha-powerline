"""Unit tests for defensive LED handling."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from custom_components.powerline import homeplug as _MODULE

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
PARAM_QOS_PRIORITY_MAP = _MODULE.PARAM_QOS_PRIORITY_MAP
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


class TestLedEarlyBail(TestCase):
    """A non-Broadcom adapter (no MX reply) must fail fast, not run all writes."""

    def test_no_response_bails_after_first_write(self) -> None:
        hp = HomeplugAV("eth0")
        hp._chipset_by_mac = {"EC:08:6B:54:FE:E3": "broadcom"}  # this adapter is Broadcom
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        calls = []

        def _capture(sock, frame, *a, **k):
            calls.append(frame)
            return []  # adapter never answers (Qualcomm AV500)

        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_send_recv", side_effect=_capture), \
             patch.object(hp, "_close"):
            ok = hp.set_led("EC:08:6B:54:FE:E3", True)

        self.assertFalse(ok)
        # 2 attempts (set_led retries once), each bails after the first write,
        # so 2 sends total instead of the full 6 (3 writes x 2 attempts).
        self.assertEqual(2, len(calls))

    def test_qualcomm_led_fails_gracefully_when_pib_unreadable(self) -> None:
        """On QCA, set_led attempts a PIB read; if the adapter answers nothing it
        returns False without raising (and never reaches a write)."""
        hp = HomeplugAV("eth0")
        hp._chipset_by_mac = {"EC:08:6B:54:FE:E3": "qualcomm"}
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        sends = MagicMock(return_value=[])  # adapter answers nothing

        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_send_recv", sends), patch.object(hp, "_close"):
            ok = hp.set_led("EC:08:6B:54:FE:E3", True)

        self.assertFalse(ok)
        sends.assert_called()  # it tried to read the PIB


class TestMixedNetworkChipset(TestCase):
    """Per-adapter chipset, not one global guess, drives control + state.

    Regression: a mixed network (AV1000/Broadcom + AV500/Qualcomm) detected the
    chipset once for the whole instance, so when the AV500s set it to 'qualcomm'
    the Broadcom AV1000 stopped applying LED/QoS/power-saving and showed no rates.
    """

    AV1000 = "B0:19:21:F5:DB:A7"   # Broadcom (no QCA reply -> "unknown")
    AV500 = "EC:08:6B:54:FE:E3"    # Qualcomm

    def test_unknown_adapter_tries_broadcom_even_when_network_is_qca(self) -> None:
        hp = HomeplugAV("eth0")
        hp._chipset = "qualcomm"                       # global guess (wrong here)
        hp._chipset_by_mac = {self.AV500: "qualcomm"}  # only the AV500 is known QCA
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch.object(hp, "_set_led_broadcom", return_value=True) as bc, \
             patch.object(hp, "_set_led_qualcomm", return_value=False) as qc:
            ok = hp.set_led(self.AV1000, True)
        self.assertTrue(ok)
        bc.assert_called()      # the AV1000 still gets the MEDIAXTREAM path
        # and once it works the chipset is remembered for next time
        self.assertEqual("broadcom", hp._mac_chipset(self.AV1000))

    def test_known_qca_adapter_skips_broadcom(self) -> None:
        hp = HomeplugAV("eth0")
        hp._chipset_by_mac = {self.AV500: "qualcomm"}
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch.object(hp, "_set_led_broadcom", return_value=False) as bc, \
             patch.object(hp, "_set_led_qualcomm", return_value=True) as qc:
            ok = hp.set_led(self.AV500, True)
        self.assertTrue(ok)
        bc.assert_not_called()  # QCA adapter never uses the MEDIAXTREAM path
        qc.assert_called()

    def test_query_states_splits_by_chipset(self) -> None:
        hp = HomeplugAV("eth0")
        hp._chipset = "qualcomm"
        hp._chipset_by_mac = {self.AV500: "qualcomm"}  # AV1000 stays "unknown"
        hp._sock_mx = MagicMock()
        hp._sock_hpav = MagicMock()
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch.object(hp, "_qca_read_pib", return_value=None) as pib, \
             patch.object(hp, "_get_param_value", return_value=None) as gp:
            hp.query_device_states([self.AV500, self.AV1000])
        pib.assert_called_once_with(self.AV500)         # QCA via PIB
        mx_macs = {c.args[0] for c in gp.call_args_list}
        self.assertIn(self.AV1000, mx_macs)             # AV1000 via MEDIAXTREAM
        self.assertNotIn(self.AV500, mx_macs)


class TestQcaLedPib(TestCase):
    """The QCA LED read-modify-write must flip ONLY the 10 LED-table bytes."""

    def test_flips_only_led_bytes(self) -> None:
        hp = HomeplugAV("eth0")
        size = _MODULE.QCA_PIB_SIZE
        chunk = _MODULE.QCA_PIB_CHUNK
        offsets = _MODULE.QCA_LED_OFFSETS
        pib = bytearray(size)
        for i in range(size):
            pib[i] = i & 0xFF                 # arbitrary content
        for o in offsets:
            pib[o] = 0x01                     # LED currently OFF
        captured = {}

        def fake_write(mac, buf):
            captured["pib"] = bytes(buf)
            return True

        def fake_read_chunk(dst, mac, off, clen):
            return captured["pib"][off:off + clen]

        with patch.object(hp, "_qca_read_pib", return_value=bytes(pib)), \
             patch.object(hp, "_qca_write_pib", side_effect=fake_write), \
             patch.object(hp, "_qca_read_chunk", side_effect=fake_read_chunk):
            ok = hp._set_led_qualcomm("AA:BB:CC:DD:EE:FF", True)   # -> LED ON

        self.assertTrue(ok)
        written = captured["pib"]
        for o in offsets:
            self.assertEqual(written[o], 0x00)          # ON = 0x00
        changed = [i for i in range(size) if written[i] != pib[i]]
        self.assertEqual(sorted(changed), sorted(offsets))  # nothing else touched

    def test_aborts_on_unexpected_led_table(self) -> None:
        """If the LED-table bytes aren't 0x00/0x01, refuse to write."""
        hp = HomeplugAV("eth0")
        size = _MODULE.QCA_PIB_SIZE
        pib = bytearray(size)
        for o in _MODULE.QCA_LED_OFFSETS:
            pib[o] = 0x42                     # not a valid table value
        write = MagicMock()
        with patch.object(hp, "_qca_read_pib", return_value=bytes(pib)), \
             patch.object(hp, "_qca_write_pib", write):
            ok = hp._set_led_qualcomm("AA:BB:CC:DD:EE:FF", True)
        self.assertFalse(ok)
        write.assert_not_called()


class TestQosReadback(TestCase):
    """query_device_states should derive the QoS mode from the 0x0069 map."""

    def test_reads_led_ps_and_qos(self) -> None:
        hp = HomeplugAV("eth0")
        hp._sock_mx = MagicMock()
        mac = "B0:19:21:F5:E0:DC"
        table = bytearray(1000)
        offsets = (2, 27, 52, 77, 102, 127, 152, 177)
        gaming = (0x38, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78)
        for o, c in zip(offsets, gaming):
            table[o] = c

        def _get(m, param_id, *a, **k):
            if param_id == PARAM_LED_OPTIONS:
                return bytes.fromhex("02a00112")       # LED on (bit 0x10)
            if param_id == PARAM_POWER_STANDBY:
                return bytes.fromhex("2c81")           # 0x812C -> PS on
            if param_id == PARAM_QOS_PRIORITY_MAP:
                return bytes(table)
            return None

        with patch.object(hp, "_open_mx"), patch.object(hp, "_close"), \
             patch.object(hp, "_get_param_value", side_effect=_get):
            states = hp.query_device_states([mac])

        self.assertTrue(states[mac]["led"])
        self.assertTrue(states[mac]["power_saving"])
        self.assertEqual("gaming", states[mac]["qos"])


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


class TestQcaQos(TestCase):
    """QCA QoS write maintains the XOR checksums to match tpPLC byte-for-byte."""

    def test_gaming_reproduces_captured_checksums(self) -> None:
        import struct
        hp = HomeplugAV("eth0")
        size = _MODULE.QCA_PIB_SIZE
        qoff = _MODULE.QCA_QOS_OFFSET
        c0, c1 = _MODULE.QCA_CKSUM_OFFSETS  # 0x0374, 0x03BC
        pib = bytearray(size)
        # "internet" state from the capture: QoS=0x0000, checksum low words
        # 0x51F7 @0x0374 and 0xB276 @0x03BC.
        struct.pack_into("<H", pib, qoff, 0x0000)
        struct.pack_into("<H", pib, c0, 0x51F7)
        struct.pack_into("<H", pib, c1, 0xB276)
        captured = {}

        def fake_write(mac, buf):
            captured["pib"] = bytes(buf)
            return True

        def fake_read_chunk(dst, mac, off, clen):
            return captured["pib"][off:off + clen]

        with patch.object(hp, "_qca_read_pib", return_value=bytes(pib)), \
             patch.object(hp, "_qca_write_pib", side_effect=fake_write), \
             patch.object(hp, "_qca_read_chunk", side_effect=fake_read_chunk):
            ok = hp._set_qos_qualcomm("AA:BB:CC:DD:EE:FF", "gaming")

        self.assertTrue(ok)
        w = captured["pib"]
        # Match the captured "gaming" bytes exactly.
        self.assertEqual(struct.unpack_from("<H", w, qoff)[0], 0xFA41)
        self.assertEqual(struct.unpack_from("<H", w, c0)[0], 0xABB6)
        self.assertEqual(struct.unpack_from("<H", w, c1)[0], 0x4837)


class TestQcaPibChecksum(TestCase):
    """qca_pib_set_byte maintains both XOR checksums per the (o % 4) rule."""

    def test_powersave_delta_matches_capture(self) -> None:
        size = _MODULE.QCA_PIB_SIZE
        c0, c1 = _MODULE.QCA_CKSUM_OFFSETS
        buf = bytearray(size)  # all-zero (off state)
        for off, val in _MODULE.QCA_POWERSAVE_BYTES.items():
            _MODULE.qca_pib_set_byte(buf, off, val)
        # Captured power-saving off->on checksum delta is 01 08 97 02 on BOTH.
        self.assertEqual(bytes(buf[c0:c0 + 4]), bytes([0x01, 0x08, 0x97, 0x02]))
        self.assertEqual(bytes(buf[c1:c1 + 4]), bytes([0x01, 0x08, 0x97, 0x02]))

    def test_qos_gaming_delta_matches_capture(self) -> None:
        size = _MODULE.QCA_PIB_SIZE
        c0, c1 = _MODULE.QCA_CKSUM_OFFSETS
        buf = bytearray(size)  # internet (QoS=0) -> gaming (0xFA41)
        _MODULE.qca_pib_set_byte(buf, _MODULE.QCA_QOS_OFFSET, 0x41)
        _MODULE.qca_pib_set_byte(buf, _MODULE.QCA_QOS_OFFSET + 1, 0xFA)
        # Captured internet->gaming checksum delta is 41 fa 00 00 on BOTH fields.
        self.assertEqual(bytes(buf[c0:c0 + 4]), bytes([0x41, 0xFA, 0x00, 0x00]))
        self.assertEqual(bytes(buf[c1:c1 + 4]), bytes([0x41, 0xFA, 0x00, 0x00]))

    def test_power_saving_qualcomm_flow(self) -> None:
        hp = HomeplugAV("eth0")
        size = _MODULE.QCA_PIB_SIZE
        pib = bytearray(size)
        captured = {}

        def fake_write(mac, b):
            captured["pib"] = bytes(b)
            return True

        def fake_read_chunk(dst, mac, off, clen):
            return captured["pib"][off:off + clen]

        with patch.object(hp, "_qca_read_pib", return_value=bytes(pib)), \
             patch.object(hp, "_qca_write_pib", side_effect=fake_write), \
             patch.object(hp, "_qca_read_chunk", side_effect=fake_read_chunk):
            ok = hp._set_power_saving_qualcomm("AA:BB:CC:DD:EE:FF", True)

        self.assertTrue(ok)
        w = captured["pib"]
        for off, val in _MODULE.QCA_POWERSAVE_BYTES.items():
            self.assertEqual(w[off], val)


class TestQcaOpenChecksum(TestCase):
    """The write-open checksum = ~xorfold32(PIB) (open-plc-utils checksum32)."""

    import struct as _struct

    def _xorfold(self, pib: bytes) -> int:
        fold = 0
        for i in range(0, len(pib) - 3, 4):
            fold ^= self._struct.unpack_from("<I", pib, i)[0]
        return fold & 0xFFFFFFFF

    def test_all_zero_pib(self) -> None:
        pib = bytes(_MODULE.QCA_PIB_SIZE)            # XOR-fold = 0
        self.assertEqual(_MODULE.qca_pib_checksum(pib), b"\xff\xff\xff\xff")

    def test_is_complement_of_xorfold(self) -> None:
        import os
        pib = os.urandom(_MODULE.QCA_PIB_SIZE)
        expect = self._struct.pack("<I", (~self._xorfold(pib)) & 0xFFFFFFFF)
        self.assertEqual(_MODULE.qca_pib_checksum(pib), expect)

    def test_led_toggle_does_not_change_checksum(self) -> None:
        # Every LED enable byte sits at offset %4 == 3, so flipping 0<->1 on an
        # even number of them XOR-cancels: the open checksum is invariant for an
        # LED on/off toggle (exactly what the captures show).
        pib = bytearray(_MODULE.QCA_PIB_SIZE)
        base = _MODULE.qca_pib_checksum(bytes(pib))
        for off in _MODULE.QCA_LED_OFFSETS:
            self.assertEqual(off & 3, 3)
            pib[off] = 0x01
        self.assertEqual(_MODULE.qca_pib_checksum(bytes(pib)), base)

    def test_write_open_frame_carries_the_checksum(self) -> None:
        # The open frame _qca_write_pib emits must place ~xorfold at bytes 26:30
        # of the module payload (after MMV+MMTYPE+OUI).
        hp = HomeplugAV("eth0")
        import os
        pib = os.urandom(_MODULE.QCA_PIB_SIZE)
        seen = {}

        def fake_send(dst, mac, payload):
            op = (payload[4], payload[5])
            if op == (0x01, 0x10):
                seen["open"] = bytes(payload)
            return b"\x00" * 24  # any non-None ack; close status all-zero

        with patch.object(hp, "_qca_mod_send", side_effect=fake_send), \
             patch.object(hp, "_qca_mod_ack", return_value=True):
            hp._qca_write_pib("AA:BB:CC:DD:EE:FF", pib)

        self.assertEqual(seen["open"][26:30], _MODULE.qca_pib_checksum(pib))


from custom_components.powerline.homeplug import fritz as _FRITZ


class TestFritzPowerline(TestCase):
    """AVM FRITZ!Powerline (QCA7420): LED works via a full-size PIB write;
    QoS / power-saving are not offered (the 510E has no such controls)."""

    FRITZ_510E = "5C:49:79:E6:B2:D0"   # AVM OUI 5C:49:79
    QCA_AV500 = "EC:08:6B:54:FE:E3"    # generic Qualcomm

    def test_detects_fritz_by_oui(self) -> None:
        hp = HomeplugAV("eth0")
        self.assertTrue(hp.is_fritz(self.FRITZ_510E))
        self.assertFalse(hp.is_fritz(self.QCA_AV500))

    def test_detects_fritz_by_firmware_string(self) -> None:
        hp = HomeplugAV("eth0")
        mac = "AA:BB:CC:DD:EE:FF"            # not an AVM OUI
        self.assertFalse(hp.is_fritz(mac))
        hp.note_firmware(mac, "FRITZ!Powerline 510E")
        self.assertTrue(hp.is_fritz(mac))

    def test_set_led_on_fritz_uses_full_size_pib_write(self) -> None:
        """FRITZ LED uses the dedicated full-size path with AVM offsets, keeps
        the section checksums valid, and never truncates the PIB."""
        hp = HomeplugAV("eth0")
        size = _FRITZ.AVM_PIB_SIZE
        pib = bytearray(size)
        for o in _FRITZ.FRITZ_LED_OFFSETS:
            pib[o] = _FRITZ.FRITZ_LED_OFF             # currently OFF
        captured = {}

        def fake_read(mac, size=_MODULE.QCA_PIB_SIZE):
            captured["read_size"] = size
            return bytes(pib)

        def fake_write(mac, buf, close_retries=1):
            captured["written"] = bytes(buf)
            captured["retries"] = close_retries
            return True

        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch.object(hp, "_qca_read_pib", side_effect=fake_read), \
             patch.object(hp, "_qca_write_pib", side_effect=fake_write), \
             patch.object(hp, "_set_led_qualcomm") as qc, \
             patch.object(hp, "_set_led_broadcom") as bc:
            ok = hp.set_led(self.FRITZ_510E, True)        # -> LED ON

        self.assertTrue(ok)
        qc.assert_not_called()
        bc.assert_not_called()
        self.assertEqual(captured["read_size"], size)     # full AVM size read
        self.assertGreater(captured["retries"], 1)        # close retried
        written = captured["written"]
        self.assertEqual(len(written), size)              # no truncation
        for o in _FRITZ.FRITZ_LED_OFFSETS:
            self.assertEqual(written[o], _FRITZ.FRITZ_LED_ON)
        # only the LED bytes + their two folded checksum bytes changed
        idx = _FRITZ.FRITZ_LED_OFFSETS[0] & 3
        cksum = {c + idx for c in _MODULE.QCA_CKSUM_OFFSETS}
        changed = {i for i in range(size) if written[i] != pib[i]}
        self.assertEqual(changed, set(_FRITZ.FRITZ_LED_OFFSETS) | cksum)

    def test_set_led_on_fritz_aborts_on_unexpected_led_table(self) -> None:
        hp = HomeplugAV("eth0")
        pib = bytearray(_FRITZ.AVM_PIB_SIZE)
        for o in _FRITZ.FRITZ_LED_OFFSETS:
            pib[o] = 0x42                                 # not 0x00/0x01
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch.object(hp, "_qca_read_pib", return_value=bytes(pib)), \
             patch.object(hp, "_qca_write_pib") as wr:
            ok = hp.set_led(self.FRITZ_510E, True)
        self.assertFalse(ok)
        wr.assert_not_called()

    def test_qos_and_power_saving_on_fritz_never_write_pib(self) -> None:
        hp = HomeplugAV("eth0")
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch.object(hp, "_qca_write_pib") as wr:
            self.assertFalse(hp.set_qos_priority(self.FRITZ_510E, "gaming"))
            self.assertFalse(hp.set_power_saving(self.FRITZ_510E, True))
        wr.assert_not_called()


class TestAvmDeviceHelper(TestCase):
    """is_avm_device gates QoS / power-saving entity creation for FRITZ."""

    def test_by_oui(self) -> None:
        from custom_components.powerline.homeplug.fritz import is_avm_device
        self.assertTrue(is_avm_device("5C:49:79:E6:B2:D0"))
        self.assertFalse(is_avm_device("EC:08:6B:54:FE:E3"))

    def test_by_model_or_firmware_string(self) -> None:
        from custom_components.powerline.homeplug.fritz import is_avm_device
        mac = "AA:BB:CC:DD:EE:FF"
        self.assertFalse(is_avm_device(mac, {"model": "TP-LINK Product"}))
        self.assertTrue(is_avm_device(mac, {"model": "FRITZ!Powerline 510E"}))
        self.assertTrue(is_avm_device(mac, {"firmware_ver": "AVM Powerline"}))


class TestRestart(TestCase):
    """Adapter restart via VS_RS_DEV (0xA01C), captured from the FRITZ app."""

    MAC = "5C:49:79:E6:B2:D0"

    def test_restart_sends_vs_rs_dev_and_confirms(self) -> None:
        hp = HomeplugAV("eth0")
        hp._sock_hpav = MagicMock()
        sent = {}

        def fake_send_recv(sock, frame, timeout=2.0, expected_src=None,
                           stop_on=None):
            sent["frame"] = frame
            return [(_MODULE.VS_RS_DEV_CNF, self.MAC, b"")]

        with patch.object(hp, "_open_hpav"), patch.object(hp, "_close"), \
             patch.object(hp, "_send_recv", side_effect=fake_send_recv):
            ok = hp.restart(self.MAC)

        self.assertTrue(ok)
        frame = sent["frame"]
        # MMV=0x00, MMTYPE 0xA01C (LE), OUI 00:b0:52, empty body
        self.assertEqual(frame[14], 0x00)
        self.assertEqual(_struct.unpack("<H", frame[15:17])[0],
                         _MODULE.VS_RS_DEV_REQ)
        self.assertEqual(frame[17:20], b"\x00\xb0\x52")

    def test_restart_returns_false_without_confirmation(self) -> None:
        hp = HomeplugAV("eth0")
        hp._sock_hpav = MagicMock()
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_close"), \
             patch.object(hp, "_send_recv", return_value=[]):
            self.assertFalse(hp.restart(self.MAC))
