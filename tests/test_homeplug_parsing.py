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
