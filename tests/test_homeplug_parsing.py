"""Unit tests for MEDIAXTREAM parser edge-cases."""

import importlib.util
from pathlib import Path
from unittest import TestCase

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

MX_MME_HDR = _MODULE.MX_MME_HDR
ETH_HDR = _MODULE.ETH_HDR
parse_mx_nw_info_cnf = _MODULE.parse_mx_nw_info_cnf
parse_mx_status_ind = _MODULE.parse_mx_status_ind
parse_mx_nw_stats_cnf = _MODULE.parse_mx_nw_stats_cnf
parse_mx_get_param_cnf = _MODULE.parse_mx_get_param_cnf
decode_phy_rate = _MODULE.decode_phy_rate
parse_qca_nw_info_cnf = _MODULE.parse_qca_nw_info_cnf
mac_to_bytes = _MODULE.mac_to_bytes
HomeplugAV = _MODULE.HomeplugAV
import struct


class TestQcaNwInfo(TestCase):
    """QCA VS_NW_INFO.CNF (0xA039) PHY-rate parsing (4-byte LE tail)."""

    def _frame(self, body: bytes) -> bytes:
        eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x88E1)
        return eth + b"\x01\x39\xa0\x00\x00\xb0\x52" + body

    def test_extracts_tail_rates(self) -> None:
        # Real capture tail: TX=124 (0x7c), RX=140 (0x8c) as 4-byte LE.
        body = bytes.fromhex("00003a000001") + b"\x00" * 40 \
            + struct.pack("<II", 124, 140)
        self.assertEqual(parse_qca_nw_info_cnf(self._frame(body)), (124, 140))

    def test_idle_link_yields_none(self) -> None:
        body = b"\x00" * 60
        self.assertIsNone(parse_qca_nw_info_cnf(self._frame(body)))


class TestMirrorLinkRate(TestCase):
    """The PLC link rate should appear on both endpoints, not just the peer."""

    def test_responder_gets_the_link_rate(self) -> None:
        devices = {
            "B0:19:21:F5:DB:A7": {"mac": "B0:19:21:F5:DB:A7", "tx_rate": 0, "rx_rate": 0},
            "EC:08:6B:54:FE:E3": {"mac": "EC:08:6B:54:FE:E3", "tx_rate": 422, "rx_rate": 274},
        }
        # B0:19:21 responded reporting peer EC:08:6B with 422/274.
        HomeplugAV._mirror_link_rate(devices, "B0:19:21:F5:DB:A7",
                                     "EC:08:6B:54:FE:E3", 422, 274)
        self.assertEqual(422, devices["B0:19:21:F5:DB:A7"]["tx_rate"])
        self.assertEqual(274, devices["B0:19:21:F5:DB:A7"]["rx_rate"])

    def test_does_not_overwrite_existing_rate(self) -> None:
        devices = {"X": {"mac": "X", "tx_rate": 100, "rx_rate": 50}}
        HomeplugAV._mirror_link_rate(devices, "X", "Y", 999, 999)
        self.assertEqual(100, devices["X"]["tx_rate"])  # own rate wins


class TestDeviceInfoCaching(TestCase):
    """Device info must not be re-queried every poll once attempted."""

    def test_skips_already_attempted_with_firmware(self) -> None:
        from unittest.mock import patch
        hp = HomeplugAV("eth0")
        hp._sock_mx = object()
        hp._sock_hpav = object()
        devices = {"AA:BB:CC:DD:EE:FF": {"mac": "AA:BB:CC:DD:EE:FF",
                                         "firmware_ver": "v1", "model": "m"}}
        hp._info_attempted.add("AA:BB:CC:DD:EE:FF")
        with patch.object(hp, "_send_recv") as sr:
            hp._fetch_device_info(devices)
        sr.assert_not_called()


class TestMediaXtreamParsing(TestCase):
    """Tests for undocumented Broadcom payload formats."""

    def test_parse_mx_nw_info_cnf_supports_implicit_station_layout(self) -> None:
        # Network block (17 bytes): NID(7)+SNID(1)+TEI(1)+Role(1)+CCo(6)+reserved(1)
        network_block = bytes.fromhex(
            "83789fb4d88b0f"  # NID
            "0f"              # SNID
            "02"              # TEI
            "04"              # Role
            "ec086b54fee3"    # CCo MAC
            "00"              # Reserved
        )

        # No explicit station count byte; station entries start directly.
        station_1 = bytes.fromhex("b01921f5dba7") + (b"\x00" * 7)
        station_2 = bytes.fromhex("aabbccddeeff") + (b"\x00" * 7)
        payload = b"\x01" + network_block + station_1 + station_2

        frame = (b"\x00" * (ETH_HDR + MX_MME_HDR)) + payload
        parsed = parse_mx_nw_info_cnf(frame)

        self.assertEqual(1, len(parsed["networks"]))
        self.assertEqual(2, len(parsed["stations"]))
        self.assertEqual("B0:19:21:F5:DB:A7", parsed["stations"][0]["mac"])
        self.assertEqual("AA:BB:CC:DD:EE:FF", parsed["stations"][1]["mac"])

    def test_decode_phy_rate_masks_link_flag(self) -> None:
        # AV500 link (top nibble 0x8)
        self.assertEqual(413, decode_phy_rate(0x819D))
        self.assertEqual(422, decode_phy_rate(0x81A6))
        self.assertEqual(274, decode_phy_rate(0x8112))
        # AV1000<->AV1000 link (top nibble 0x4) — real capture, real 547/545
        self.assertEqual(547, decode_phy_rate(0x4223))
        self.assertEqual(545, decode_phy_rate(0x4221))
        self.assertEqual(554, decode_phy_rate(0x422A))

    def test_parse_mx_nw_stats_cnf_av1000_link(self) -> None:
        # Real capture (2x AV1000): TX=0x4223 RX=0x4221 -> 547 / 545.
        payload = bytes.fromhex("01b01921f5e0dc2342214200000000")
        frame = (b"\x00" * (ETH_HDR + MX_MME_HDR)) + payload
        stations = parse_mx_nw_stats_cnf(frame)
        self.assertEqual(1, len(stations))
        self.assertEqual("B0:19:21:F5:E0:DC", stations[0]["mac"])
        self.assertEqual(547, stations[0]["tx_rate"])
        self.assertEqual(545, stations[0]["rx_rate"])

    def test_parse_mx_nw_stats_cnf_real_capture(self) -> None:
        # Real TL-PA7017 (BCM60355) capture: 1 station, TX=0x81A6 RX=0x8112,
        # high bit is a link-active flag -> 422 / 274 Mbps.
        payload = bytes.fromhex("01ec086b54fee3a681128100000000")
        frame = (b"\x00" * (ETH_HDR + MX_MME_HDR)) + payload
        stations = parse_mx_nw_stats_cnf(frame)

        self.assertEqual(1, len(stations))
        self.assertEqual("EC:08:6B:54:FE:E3", stations[0]["mac"])
        self.assertEqual(422, stations[0]["tx_rate"])
        self.assertEqual(274, stations[0]["rx_rate"])

    def test_parse_mx_get_param_cnf_hfid_string(self) -> None:
        # Real capture: octets=1, num=0x40 (64), value = HFID string.
        payload = bytes.fromhex("014000") + b"tpver_701E14_190426_901".ljust(64, b"\x00")
        frame = (b"\x00" * (ETH_HDR + MX_MME_HDR)) + payload
        val = parse_mx_get_param_cnf(frame)
        self.assertTrue(val.startswith(b"tpver_701E14_190426_901"))

    def test_parse_mx_get_param_cnf_led_options(self) -> None:
        # Real capture: octets=4, num=1, value=02a00112 (LED on, bit 0x10 set).
        payload = bytes.fromhex("04010002a00112") + b"\x00" * 20
        frame = (b"\x00" * (ETH_HDR + MX_MME_HDR)) + payload
        val = parse_mx_get_param_cnf(frame)
        self.assertEqual(bytes.fromhex("02a00112"), val)
        self.assertTrue(val[3] & 0x10)  # LED enabled

    def test_parse_mx_status_ind_extracts_rates(self) -> None:
        payload = b"\x02\x46\x04\x00" + b"\x05\x00\x06\x00"
        src_mac = bytes.fromhex("b01921f5dba7")
        frame = (b"\x00" * 6) + src_mac + (b"\x00" * (ETH_HDR - 12 + MX_MME_HDR)) + payload

        parsed = parse_mx_status_ind(frame)

        assert parsed is not None
        self.assertEqual("B0:19:21:F5:DB:A7", parsed["mac"])
        self.assertEqual(10, parsed["tx_rate"])
        self.assertEqual(12, parsed["rx_rate"])
        self.assertNotIn("led_on", parsed)
