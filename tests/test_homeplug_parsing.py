"""Unit tests for MEDIAXTREAM parser edge-cases."""

from unittest import TestCase

from custom_components.powerline import homeplug as _MODULE

MX_MME_HDR = _MODULE.MX_MME_HDR
ETH_HDR = _MODULE.ETH_HDR
parse_mx_nw_info_cnf = _MODULE.parse_mx_nw_info_cnf
parse_mx_status_ind = _MODULE.parse_mx_status_ind
parse_mx_nw_stats_cnf = _MODULE.parse_mx_nw_stats_cnf
parse_mx_get_param_cnf = _MODULE.parse_mx_get_param_cnf
decode_phy_rate = _MODULE.decode_phy_rate
parse_qca_nw_info_cnf = _MODULE.parse_qca_nw_info_cnf
parse_qca_nw_info_stations = _MODULE.parse_qca_nw_info_stations
mac_to_bytes = _MODULE.mac_to_bytes
HomeplugAV = _MODULE.HomeplugAV
import struct


class TestQcaNwInfo(TestCase):
    """QCA VS_NW_INFO.CNF (0xA039) PHY-rate parsing (4-byte LE tail)."""

    def _frame(self, body: bytes) -> bytes:
        eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x88E1)
        return eth + b"\x01\x39\xa0\x00\x00\xb0\x52" + body

    def test_extracts_tail_rates_scaled(self) -> None:
        # Real capture tail: raw TX=124 (0x7c), RX=140 (0x8c) 4-byte LE.
        # tpPLC displays floor(raw*21/16): 124->162, 140->183.
        body = bytes.fromhex("00003a000001") + b"\x00" * 40 \
            + struct.pack("<II", 124, 140)
        self.assertEqual(parse_qca_nw_info_cnf(self._frame(body)), (162, 183))

    def test_idle_link_yields_none(self) -> None:
        body = b"\x00" * 60
        self.assertIsNone(parse_qca_nw_info_cnf(self._frame(body)))


class TestQcaNwInfoStations(TestCase):
    """Per-peer PHY rates from the station list — real AV1300 frames (#108).

    Three TP-Link adapters (TL-WPA8631P v3 + v4, TL-PA8010P v4) captured by a
    user. Every link must come out mirrored from both ends; that symmetry is
    what proves the field offsets are right.
    """

    # from → full 0xA039 payload as logged by Diagnose
    FRAMES = {
        "9C:A2:F4:B0:E7:C0":
            "0139a0000000b052000052000001788862a27e690a00000f08000000000"
            "05ce931550c92020000000200000000005ce931550c920226200068ec8a"
            "0f7194c3002200b70000003c52a1a91c4f0a141400c84d4421e5e065022"
            "200c5020000",
        "3C:52:A1:A9:1C:4F":
            "0139a0000000b052000052000001788862a27e690a00000f0a000000000"
            "05ce931550c92020000000200000000005ce931550c92021c1c00d41ad1"
            "419c8c73012200280100009ca2f4b0e7c0081a14009009d088e643c5022"
            "20065020000",
        "5C:E9:31:55:0C:92":
            "0139a0000000b052000052000001788862a27e690a00000f02000000000"
            "25ce931550c92020000000200000000009ca2f4b0e7c00800000090"
            "09d088e643b7002200c30000003c52a1a91c4f0a000000c84d4421e5"
            "e02801220073010000",
    }
    ALL = list(FRAMES)

    def _frame(self, src: str) -> bytes:
        eth = b"\xaa" * 6 + mac_to_bytes(src) + struct.pack("!H", 0x88E1)
        return eth + bytes.fromhex(self.FRAMES[src])

    def _rates(self, src: str) -> dict:
        peers = [m for m in self.ALL if m != src]
        return parse_qca_nw_info_stations(self._frame(src), peers)

    def test_every_peer_is_found(self) -> None:
        for src in self.ALL:
            with self.subTest(src=src):
                self.assertEqual(len(self._rates(src)), 2)

    def test_links_are_mirrored_between_both_ends(self) -> None:
        for a in self.ALL:
            for b, (tx, rx) in self._rates(a).items():
                with self.subTest(link=f"{a}->{b}"):
                    # what A sends to B is what B receives from A
                    self.assertEqual((tx, rx), self._rates(b)[a][::-1])

    def test_rates_match_the_capture(self) -> None:
        # raw 613/709 scaled the way tpPLC displays them (x21/16)
        self.assertEqual(
            self._rates("9C:A2:F4:B0:E7:C0")["3C:52:A1:A9:1C:4F"],
            (613 * 21 // 16, 709 * 21 // 16))

    def test_unknown_peer_is_omitted(self) -> None:
        rates = parse_qca_nw_info_stations(
            self._frame("9C:A2:F4:B0:E7:C0"), ["00:11:22:33:44:55"])
        self.assertEqual(rates, {})


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

    def test_parse_mx_nw_stats_cnf_skips_responder_self_record(self) -> None:
        # Real TL-PA9020P (AV2000) capture from #110: the first record is the
        # responder itself and carries its rated capability (0x683c -> 2108),
        # not a link rate. Only the real peer (0x40e6/0x40f4 -> 230/244) counts.
        responder = bytes.fromhex("5091e3a0e1c8")
        peer = bytes.fromhex("5091e3a0380a")
        frame = (bytes.fromhex("ffffffffffff") + responder
                 + bytes(ETH_HDR + MX_MME_HDR - 12) + bytes([2])
                 + responder + bytes.fromhex("3c682c68")
                 + peer + bytes.fromhex("e640f440"))
        stations = parse_mx_nw_stats_cnf(frame)

        self.assertEqual(1, len(stations))
        self.assertEqual("50:91:E3:A0:38:0A", stations[0]["mac"].upper())
        self.assertEqual(230, stations[0]["tx_rate"])
        self.assertEqual(244, stations[0]["rx_rate"])

    def test_decode_phy_rate_capability_bit_is_not_a_rate(self) -> None:
        # Bit 11 (0x0800) marks a rated PLC capability, not a measurement. Real
        # TL-PA9020P (AV2000) values: 2292/2092/2105/2108 "Mbps", i.e. the unit's
        # "PLC 2000 Mbps" rating. Reported as 0 = "unknown", the sentinel this
        # module already uses, so peer-mirroring can fill it from the other end.
        self.assertEqual(0, decode_phy_rate(0x68F4))
        self.assertEqual(0, decode_phy_rate(0x682C))
        self.assertEqual(0, decode_phy_rate(0x6839))
        self.assertEqual(0, decode_phy_rate(0x683C))
        # The top nibble is NOT the discriminator: 0x6xxx also carries real rates,
        # and those must survive untouched.
        self.assertEqual(238, decode_phy_rate(0x60EE))
        self.assertEqual(67, decode_phy_rate(0x6043))

    def test_parse_mx_nw_stats_cnf_exposes_raw_rate_fields(self) -> None:
        # Diagnostics needs the raw 16-bit words, not just the decoded rates:
        # a capability record decodes to a plausible number, so only the raw
        # field distinguishes it (see diagnostics.py). Real AV2000 capture.
        responder = bytes.fromhex("98ded0da380a")
        payload = bytes.fromhex(
            "02"
            "ec086b6ae1c8" "f468" "4360"   # 3D: TX capability, RX 67
            "d4d6df585d4e" "5440" "2d40"   # Attic: 84 / 45
        )
        frame = (bytes.fromhex("ffffffffffff") + responder
                 + bytes(ETH_HDR + MX_MME_HDR - 12) + payload)
        by_mac = {s["mac"].upper(): s for s in parse_mx_nw_stats_cnf(frame)}

        three_d = by_mac["EC:08:6B:6A:E1:C8"]
        # Raw preserved verbatim even where the rate is deliberately dropped.
        self.assertEqual(0x68F4, three_d["tx_raw"])
        self.assertEqual(0, three_d["tx_rate"])
        self.assertTrue(three_d["tx_raw"] & 0x0800)
        self.assertEqual(0x6043, three_d["rx_raw"])
        self.assertEqual(67, three_d["rx_rate"])

        attic = by_mac["D4:D6:DF:58:5D:4E"]
        self.assertEqual((0x4054, 0x402D), (attic["tx_raw"], attic["rx_raw"]))
        self.assertEqual((84, 45), (attic["tx_rate"], attic["rx_rate"]))

    def test_note_rate_sample_keeps_dropped_capability_records(self) -> None:
        # The recorder runs BEFORE the usable-rate filter, so a record dropped
        # as a link rate is still visible in diagnostics - the case that is
        # impossible to diagnose from decoded output.
        hp = HomeplugAV()
        hp.rate_samples = []
        hp._note_rate_sample("98:DE:D0:DA:38:0A", "EC:08:6B:6A:E1:C8",
                             0x68F4, 0x6043)
        self.assertEqual(2, len(hp.rate_samples))
        tx, rx = hp.rate_samples

        self.assertEqual("tx", tx["direction"])
        self.assertEqual("0x68F4", tx["raw"])
        self.assertEqual("0x6", tx["flag_nibble"])
        self.assertTrue(tx["capability_bit"])
        self.assertEqual(0, tx["rate"])

        self.assertEqual("rx", rx["direction"])
        self.assertEqual("0x6043", rx["raw"])
        self.assertFalse(rx["capability_bit"])
        self.assertEqual(67, rx["rate"])

        # MACs normalised so responder/peer pairs collate.
        self.assertEqual("98:DE:D0:DA:38:0A", tx["responder"])
        self.assertEqual("EC:08:6B:6A:E1:C8", tx["peer"])

    def test_note_rate_sample_ignores_missing_raw(self) -> None:
        # Non-MX paths (QCA) carry no raw field; nothing should be recorded.
        hp = HomeplugAV()
        hp.rate_samples = []
        hp._note_rate_sample("AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66", None, None)
        self.assertEqual([], hp.rate_samples)

    def test_parse_mx_nw_stats_cnf_av2000_peer_capability(self) -> None:
        # Real capture, AV2000 responder (Shed) listing its three peers. Both AV2000
        # units report the OTHER with the capability value 0x68f4, in a PEER record
        # that no MAC comparison can catch — skipping self-records is not enough.
        # Only the affected direction is dropped: TX becomes unknown (0), while RX
        # on the same station (0x6043 -> 67) and both AV1000 peers stay intact.
        responder = bytes.fromhex("98ded0da380a")
        payload = bytes.fromhex(
            "03"
            "ec086b6ae1c8" "f468" "4360"   # 3D     (AV2000): TX capability, RX 67
            "d4d6df585d4e" "5440" "2d40"   # Attic  (AV1000): 84 / 45
            "d4d6df585095" "af41" "df41"   # Garage (AV1000): 431 / 479
        )
        frame = (bytes.fromhex("ffffffffffff") + responder
                 + bytes(ETH_HDR + MX_MME_HDR - 12) + payload)
        stations = parse_mx_nw_stats_cnf(frame)

        self.assertEqual(3, len(stations))
        by_mac = {s["mac"].upper(): s for s in stations}
        self.assertEqual(0, by_mac["EC:08:6B:6A:E1:C8"]["tx_rate"])
        self.assertEqual(67, by_mac["EC:08:6B:6A:E1:C8"]["rx_rate"])
        self.assertEqual(84, by_mac["D4:D6:DF:58:5D:4E"]["tx_rate"])
        self.assertEqual(45, by_mac["D4:D6:DF:58:5D:4E"]["rx_rate"])
        self.assertEqual(431, by_mac["D4:D6:DF:58:50:95"]["tx_rate"])
        self.assertEqual(479, by_mac["D4:D6:DF:58:50:95"]["rx_rate"])
        # Nothing implausible survives anywhere in the reply.
        self.assertEqual([], [s for s in stations
                              if s["tx_rate"] > 1200 or s["rx_rate"] > 1200])

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
