"""Comprehensive tests for HomePlug AV parser edge cases."""

from unittest import TestCase

from custom_components.powerline import homeplug as _MODULE

MX_MME_HDR = _MODULE.MX_MME_HDR
ETH_HDR = _MODULE.ETH_HDR
parse_discover_cnf = _MODULE.parse_discover_cnf
parse_mx_discover_cnf = _MODULE.parse_mx_discover_cnf
parse_mx_nw_info_cnf = _MODULE.parse_mx_nw_info_cnf
parse_mx_get_param_cnf = _MODULE.parse_mx_get_param_cnf
decode_phy_rate = _MODULE.decode_phy_rate
parse_qca_nw_info_cnf = _MODULE.parse_qca_nw_info_cnf
parse_qca_nw_info_stations = _MODULE.parse_qca_nw_info_stations
parse_qca_nw_info_cco = _MODULE.parse_qca_nw_info_cco
qca_pib_checksum = _MODULE.qca_pib_checksum
qca_pib_set_byte = _MODULE.qca_pib_set_byte
QCA_CKSUM_OFFSETS = _MODULE.QCA_CKSUM_OFFSETS

import struct

class TestParserEdgeCases(TestCase):
    """Test parser functions with edge cases and malformed input."""

    def _frame(self, body: bytes, ethertype: int = 0x88E1) -> bytes:
        eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", ethertype)
        return eth + b"\x01\x39\xa0\x00\x00\xb0\x52" + body

    def test_parse_discover_cnf_empty(self) -> None:
        """Test empty discover confirmation."""
        frame = self._frame(b"")
        self.assertEqual(parse_discover_cnf(frame), [])

    def test_parse_discover_cnf_truncated(self) -> None:
        """Test truncated discover confirmation."""
        frame = self._frame(b"\x01")  # Only count byte
        self.assertEqual(parse_discover_cnf(frame), [])

    def test_parse_discover_cnf_malformed(self) -> None:
        """Test malformed discover confirmation."""
        frame = self._frame(b"\x01\x00" * 10)  # Count + 10 zero bytes
        # The parser will attempt to parse what it can - 1 station with 12 bytes
        result = parse_discover_cnf(frame)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tei"], 0)
        self.assertTrue(result[0]["same_network"])

    def test_parse_mx_discover_cnf_empty(self) -> None:
        """Test empty MX discover confirmation."""
        frame = self._frame(b"")
        self.assertIsNone(parse_mx_discover_cnf(frame))

    def test_parse_mx_discover_cnf_truncated(self) -> None:
        """Test truncated MX discover confirmation."""
        frame = self._frame(b"\x01")  # Only interface byte
        self.assertIsNone(parse_mx_discover_cnf(frame))

    def test_parse_mx_discover_cnf_malformed(self) -> None:
        """Test malformed MX discover confirmation."""
        frame = self._frame(b"\x01\x01\x00")  # Interface + HFID length + no HFID
        self.assertIsNone(parse_mx_discover_cnf(frame))

    def test_parse_mx_nw_info_cnf_empty(self) -> None:
        """Test empty MX network info confirmation."""
        frame = self._frame(b"")
        self.assertEqual(parse_mx_nw_info_cnf(frame), {"networks": [], "stations": []})

    def test_parse_mx_nw_info_cnf_truncated(self) -> None:
        """Test truncated MX network info confirmation."""
        frame = self._frame(b"\x01")  # Only network count
        self.assertEqual(parse_mx_nw_info_cnf(frame), {"networks": [], "stations": []})

    def test_parse_mx_nw_info_cnf_malformed(self) -> None:
        """Test malformed MX network info confirmation."""
        frame = self._frame(b"\x01\x00" * 10)  # Network count + 10 zero bytes
        # The parser will attempt to parse what it can - 1 network with 17 bytes
        result = parse_mx_nw_info_cnf(frame)
        self.assertEqual(len(result["networks"]), 1)
        self.assertEqual(len(result["stations"]), 0)

    def test_parse_mx_get_param_cnf_empty(self) -> None:
        """Test empty MX get parameter confirmation."""
        frame = self._frame(b"")
        self.assertEqual(parse_mx_get_param_cnf(frame), b"")

    def test_parse_mx_get_param_cnf_truncated(self) -> None:
        """Test truncated MX get parameter confirmation."""
        frame = self._frame(b"\x01")  # Only octets per element
        self.assertEqual(parse_mx_get_param_cnf(frame), b"")

    def test_parse_mx_get_param_cnf_malformed(self) -> None:
        """Test malformed MX get parameter confirmation."""
        frame = self._frame(b"\x01\x01\x00")  # Octets per element + num elements + no value
        self.assertEqual(parse_mx_get_param_cnf(frame), b"")

    def test_decode_phy_rate_edge_cases(self) -> None:
        """Test PHY rate decoding with edge cases."""
        self.assertEqual(decode_phy_rate(0x0000), 0)
        self.assertEqual(decode_phy_rate(0x07FF), 2047)  # Max rate without capability flag
        self.assertEqual(decode_phy_rate(0x0FFF), 0)    # Capability flag set (0x0800) -> 0
        self.assertEqual(decode_phy_rate(0x0800), 0)    # Capability flag
        self.assertEqual(decode_phy_rate(0x08FF), 0)    # Capability flag + max rate

    def test_parse_qca_nw_info_cnf_empty(self) -> None:
        """Test empty QCA network info confirmation."""
        frame = self._frame(b"")
        self.assertIsNone(parse_qca_nw_info_cnf(frame))

    def test_parse_qca_nw_info_cnf_truncated(self) -> None:
        """Test truncated QCA network info confirmation."""
        frame = self._frame(b"\x01")  # Only station count
        self.assertIsNone(parse_qca_nw_info_cnf(frame))

    def test_parse_qca_nw_info_cnf_malformed(self) -> None:
        """Test malformed QCA network info confirmation."""
        frame = self._frame(b"\x01\x00" * 10)  # Station count + 10 zero bytes
        self.assertIsNone(parse_qca_nw_info_cnf(frame))

    def test_parse_qca_nw_info_stations_empty(self) -> None:
        """Test empty QCA network info stations."""
        frame = self._frame(b"")
        self.assertEqual(parse_qca_nw_info_stations(frame, []), {})

    def test_parse_qca_nw_info_stations_truncated(self) -> None:
        """Test truncated QCA network info stations."""
        frame = self._frame(b"\x01")  # Only station count
        self.assertEqual(parse_qca_nw_info_stations(frame, []), {})

    def test_parse_qca_nw_info_stations_malformed(self) -> None:
        """Test malformed QCA network info stations."""
        frame = self._frame(b"\x01\x00" * 10)  # Station count + 10 zero bytes
        self.assertEqual(parse_qca_nw_info_stations(frame, []), {})

    def test_parse_qca_nw_info_cco_empty(self) -> None:
        """Test empty QCA network info CCo."""
        frame = self._frame(b"")
        self.assertIsNone(parse_qca_nw_info_cco(frame))

    def test_parse_qca_nw_info_cco_truncated(self) -> None:
        """Test truncated QCA network info CCo."""
        frame = self._frame(b"\x01")  # Only partial MAC
        self.assertIsNone(parse_qca_nw_info_cco(frame))

    def test_parse_qca_nw_info_cco_malformed(self) -> None:
        """Test malformed QCA network info CCo."""
        frame = self._frame(b"\x00" * 6)  # Zero MAC
        self.assertIsNone(parse_qca_nw_info_cco(frame))

    def test_qca_pib_checksum_empty(self) -> None:
        """Test PIB checksum with empty PIB."""
        # Empty PIB returns complement of 0 = 0xFFFFFFFF
        self.assertEqual(qca_pib_checksum(b""), b"\xff\xff\xff\xff")

    def test_qca_pib_checksum_single_dword(self) -> None:
        """Test PIB checksum with single 4-byte chunk."""
        pib = struct.pack("<I", 0x12345678)
        # fold = 0x12345678, ~fold = 0xEDCBA987
        expected = struct.pack("<I", (~0x12345678) & 0xFFFFFFFF)
        self.assertEqual(qca_pib_checksum(pib), expected)

    def test_qca_pib_checksum_multiple_dwords(self) -> None:
        """Test PIB checksum with multiple 4-byte chunks."""
        pib = struct.pack("<II", 0x11111111, 0x22222222)
        # fold = 0x11111111 ^ 0x22222222 = 0x33333333
        # ~fold = 0xCCCCCCCC
        expected = struct.pack("<I", 0xCCCCCCCC)
        self.assertEqual(qca_pib_checksum(pib), expected)

    def test_qca_pib_checksum_ignores_last_3_bytes(self) -> None:
        """Test that PIB checksum ignores last 3 bytes (not full dword)."""
        pib = struct.pack("<I", 0x11111111) + b"\x22\x33\x44"
        # fold = 0x11111111 (last 3 bytes ignored)
        expected = struct.pack("<I", (~0x11111111) & 0xFFFFFFFF)
        self.assertEqual(qca_pib_checksum(pib), expected)

    def test_qca_pib_set_byte_no_change(self) -> None:
        """Test qca_pib_set_byte with same value (no change)."""
        # Buffer must be large enough to include checksum offsets (0x0374=884, 0x03BC=956)
        buf = bytearray(b"\x00" * 1000)
        qca_pib_set_byte(buf, 10, 0x00)
        self.assertEqual(buf[10], 0x00)
        # Checksums unchanged
        self.assertEqual(buf[QCA_CKSUM_OFFSETS[0]:QCA_CKSUM_OFFSETS[0]+4], b"\x00\x00\x00\x00")

    def test_qca_pib_set_byte_updates_checksums(self) -> None:
        """Test qca_pib_set_byte updates both checksum fields."""
        # Buffer must be large enough to include checksum offsets (0x0374=884, 0x03BC=956)
        buf = bytearray(b"\x00" * 1000)
        # Set byte at offset 10 (idx = 10 & 3 = 2)
        old = buf[10]
        new = 0xFF
        qca_pib_set_byte(buf, 10, new)
        self.assertEqual(buf[10], 0xFF)
        # Both checksum fields at index 2 should be updated
        for coff in QCA_CKSUM_OFFSETS:
            self.assertEqual(buf[coff + 2], old ^ new)

    def test_qca_pib_set_byte_different_offsets(self) -> None:
        """Test qca_pib_set_byte with different offsets."""
        # Buffer must be large enough to include checksum offsets (0x0374=884, 0x03BC=956)
        buf = bytearray(b"\x00" * 1000)
        # Offset 5 -> idx = 1
        qca_pib_set_byte(buf, 5, 0xAA)
        self.assertEqual(buf[QCA_CKSUM_OFFSETS[0] + 1], 0xAA)
        self.assertEqual(buf[QCA_CKSUM_OFFSETS[1] + 1], 0xAA)

        # Offset 7 -> idx = 3
        qca_pib_set_byte(buf, 7, 0x55)
        self.assertEqual(buf[QCA_CKSUM_OFFSETS[0] + 3], 0x55)
        self.assertEqual(buf[QCA_CKSUM_OFFSETS[1] + 3], 0x55)