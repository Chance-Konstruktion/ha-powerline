"""AV1300 (TL-WPA8631P / TL-PA8010P) PIB handling — issue #108.

The numbers here are not invented: every checksum value below is what tpPLC
actually wrote in the LED captures supplied with that issue.
"""
import struct
from unittest import TestCase
from unittest.mock import patch

from custom_components.powerline import homeplug as _MODULE
from custom_components.powerline.homeplug.av1300 import (
    AV1300_LED_OFFSETS,
    AV1300_LED_OFF,
    AV1300_LED_ON,
    AV1300_PIB_SIZE,
)

HomeplugAV = _MODULE.HomeplugAV
qca_pib_set_byte = _MODULE.qca_pib_set_byte
CK0, CK1 = _MODULE.QCA_CKSUM_OFFSETS          # 0x0374, 0x03BC
MAC = "3C:52:A1:A9:1C:4F"                     # the TL-PA8010P from the capture


def _pib(led_value: int, ck0_byte: int, ck1_byte: int) -> bytearray:
    """A PIB with the LED table set and the two checksum bytes seeded."""
    buf = bytearray(AV1300_PIB_SIZE)
    for off in AV1300_LED_OFFSETS:
        buf[off] = led_value
    buf[CK0 + 3] = ck0_byte                   # LED offsets are all o % 4 == 3
    buf[CK1 + 3] = ck1_byte
    return buf


class TestAv1300LedBytes(TestCase):
    """Flipping the LED table must reproduce tpPLC's bytes exactly."""

    def test_switch_off_matches_capture(self) -> None:
        # Capture "LED switch off": table 0x00 -> 0x01, and the two checksum
        # bytes went 0x61 -> 0x60 and 0x9E -> 0x9F.
        buf = _pib(AV1300_LED_ON, 0x61, 0x9E)
        for off in AV1300_LED_OFFSETS:
            qca_pib_set_byte(buf, off, AV1300_LED_OFF)
        self.assertEqual(0x60, buf[CK0 + 3])
        self.assertEqual(0x9F, buf[CK1 + 3])

    def test_switch_on_matches_capture(self) -> None:
        # Capture "LED switch on": 0x01 -> 0x00, checksums 0x0C -> 0x0D and
        # 0xF3 -> 0xF2.
        buf = _pib(AV1300_LED_OFF, 0x0C, 0xF3)
        for off in AV1300_LED_OFFSETS:
            qca_pib_set_byte(buf, off, AV1300_LED_ON)
        self.assertEqual(0x0D, buf[CK0 + 3])
        self.assertEqual(0xF2, buf[CK1 + 3])

    def test_eleven_led_bytes_and_nothing_else(self) -> None:
        buf = _pib(AV1300_LED_ON, 0x61, 0x9E)
        before = bytes(buf)
        for off in AV1300_LED_OFFSETS:
            qca_pib_set_byte(buf, off, AV1300_LED_OFF)
        changed = {i for i in range(len(buf)) if buf[i] != before[i]}
        # 11 LED bytes + the two checksum bytes, exactly as in the capture
        self.assertEqual(set(AV1300_LED_OFFSETS) | {CK0 + 3, CK1 + 3}, changed)
        self.assertEqual(11, len(AV1300_LED_OFFSETS))


class TestAv1300Geometry(TestCase):
    """Guards the mistake that made the first hardware test fail.

    The LED table sits beyond the generic PIB, so if the size ever falls back
    to QCA_PIB_SIZE the LED bytes are silently never written — while QoS keeps
    working, because its offset is inside even the small PIB. That asymmetry is
    exactly what was observed on hardware, so it is worth a test.
    """

    def test_led_table_lies_beyond_the_generic_pib(self) -> None:
        self.assertGreater(min(AV1300_LED_OFFSETS), _MODULE.QCA_PIB_SIZE)

    def test_led_table_fits_inside_the_av1300_pib(self) -> None:
        self.assertLess(max(AV1300_LED_OFFSETS), AV1300_PIB_SIZE)

    def test_qos_is_inside_every_pib_size(self) -> None:
        self.assertLess(_MODULE.QCA_QOS_OFFSET + 1, _MODULE.QCA_PIB_SIZE)


class TestAv1300LedState(TestCase):
    """State reads must never guess."""

    def test_reads_on_and_off(self) -> None:
        hp = HomeplugAV("eth0")
        self.assertIs(True, hp.led_state_av1300(bytes(_pib(AV1300_LED_ON, 0, 0))))
        self.assertIs(False, hp.led_state_av1300(bytes(_pib(AV1300_LED_OFF, 0, 0))))

    def test_mixed_or_short_pib_is_unknown(self) -> None:
        hp = HomeplugAV("eth0")
        buf = _pib(AV1300_LED_ON, 0, 0)
        buf[AV1300_LED_OFFSETS[0]] = AV1300_LED_OFF     # inconsistent table
        self.assertIsNone(hp.led_state_av1300(bytes(buf)))
        self.assertIsNone(hp.led_state_av1300(b"\x00" * 16))


class TestAv1300SizeProbe(TestCase):
    """The size probe must only ever claim a bigger PIB when it is certain."""

    def _hp(self, readable_below: int | None):
        """An adapter that answers reads below ``readable_below`` and no others."""
        hp = HomeplugAV("eth0")

        def fake_chunk(dst, mac, offset, clen):
            if readable_below is None or offset + clen > readable_below:
                return None
            return b"\x00" * clen

        return hp, patch.object(hp, "_qca_read_chunk", side_effect=fake_chunk)

    def test_detects_av1300(self) -> None:
        hp, p = self._hp(AV1300_PIB_SIZE)
        with p:
            self.assertEqual(AV1300_PIB_SIZE, hp._pib_size(MAC))
            self.assertTrue(hp.is_av1300(MAC))

    def test_generic_adapter_stays_generic(self) -> None:
        hp, p = self._hp(_MODULE.QCA_PIB_SIZE)
        with p:
            self.assertEqual(_MODULE.QCA_PIB_SIZE, hp._pib_size(MAC))
            self.assertFalse(hp.is_av1300(MAC))

    def test_adapter_answering_everything_is_not_promoted(self) -> None:
        # A device that answers any offset proves nothing about its size — the
        # probe must fall back rather than write a wrong length.
        hp2 = HomeplugAV("eth0")
        with patch.object(hp2, "_qca_read_chunk",
                          side_effect=lambda d, m, o, c: b"\x00" * c):
            self.assertEqual(_MODULE.QCA_PIB_SIZE, hp2._pib_size(MAC))
            self.assertFalse(hp2.is_av1300(MAC))

    def test_probe_failure_falls_back(self) -> None:
        hp = HomeplugAV("eth0")
        with patch.object(hp, "_qca_read_chunk", side_effect=OSError("boom")):
            self.assertEqual(_MODULE.QCA_PIB_SIZE, hp._pib_size(MAC))

    def test_result_is_remembered(self) -> None:
        hp, p = self._hp(AV1300_PIB_SIZE)
        with p as chunk:
            hp._pib_size(MAC)
            calls = chunk.call_count
            hp._pib_size(MAC)
            self.assertEqual(calls, chunk.call_count)   # no second probe


class TestAv1300Qos(TestCase):
    """QoS needs no AV1300-specific constants — same field, same values."""

    def test_shares_the_generic_qos_field(self) -> None:
        buf = bytearray(AV1300_PIB_SIZE)
        # "audio_video" as written in the capture: 0x42 0xFA at 0x0ADC
        struct.pack_into("<H", buf, _MODULE.QCA_QOS_OFFSET,
                         _MODULE.QCA_QOS_VALUES["audio_video"])
        self.assertEqual(0x42, buf[_MODULE.QCA_QOS_OFFSET])
        self.assertEqual(0xFA, buf[_MODULE.QCA_QOS_OFFSET + 1])


class TestCcoAndVendor(TestCase):
    """The two cosmetic gaps reported alongside the LED test in #108."""

    # One real VS_NW_INFO.CNF payload; the CCo it names is 3C:52:A1:A9:1C:4F.
    FRAME = bytes.fromhex(
        "0139a0000000b052000052000001788862a27e690a00000f0a0000000000"
        "3c52a1a91c4f120000000200000000005ce931550c9205221e00d41ad141"
        "9c8cb8002200a40000003c52a1a91c4f1214140098e7f4ecdfb65a022200"
        "b6020000")

    def _framed(self) -> bytes:
        eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x88E1)
        return eth + self.FRAME

    def test_cco_is_read_from_the_confirm(self) -> None:
        # Without this the topology view calls every adapter's role "unknown".
        self.assertEqual("3C:52:A1:A9:1C:4F",
                         _MODULE.parse_qca_nw_info_cco(self._framed()))

    def test_short_or_empty_frame_yields_no_cco(self) -> None:
        eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x88E1)
        self.assertIsNone(_MODULE.parse_qca_nw_info_cco(eth + b"\x00" * 20))
        self.assertIsNone(_MODULE.parse_qca_nw_info_cco(eth + b"\x00" * 60))

    def test_known_vendor_and_neutral_fallback(self) -> None:
        from custom_components.powerline.const import MANUFACTURER, vendor_for_mac
        self.assertEqual("TP-Link", vendor_for_mac("3C:52:A1:A9:1C:4F"))
        self.assertEqual("AVM", vendor_for_mac("5c:49:79:11:22:33"))
        self.assertEqual(MANUFACTURER, vendor_for_mac("00:11:22:33:44:55"))
        self.assertEqual(MANUFACTURER, vendor_for_mac(""))
