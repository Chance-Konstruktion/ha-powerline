"""Adapter discovery and PHY-rate collection."""
import struct

from .const import (
    BROADCAST_MAC,
    CC_DISCOVER_LIST_CNF,
    CC_DISCOVER_LIST_REQ,
    ETH_HDR,
    HPAV_MME_HDR,
    MX_DISCOVER_CNF,
    MX_DISCOVER_REQ,
    MX_GET_PARAM_CNF,
    MX_GET_PARAM_REQ,
    MX_GET_STATION_CNF,
    MX_GET_STATION_REQ,
    MX_LINK_STATS_CNF,
    MX_LINK_STATS_REQ,
    MX_MME_HDR,
    MX_NW_INFO_CNF,
    MX_NW_INFO_REQ,
    MX_NW_STATS_CNF,
    MX_NW_STATS_REQ,
    MX_STATUS_IND,
    PARAM_MANUFACTURER_HFID,
    PARAM_USER_HFID,
    VS_NW_INFO_CNF,
    VS_NW_INFO_REQ,
    VS_NW_STATS_CNF,
    VS_NW_STATS_REQ,
    VS_SW_VER_CNF,
    VS_SW_VER_REQ,
    _LOGGER,
    _locked,
)
from .frames import build_hpav_frame, build_mx_frame, build_qca_frame, mac_to_bytes
from .parsers import (
    parse_discover_cnf,
    parse_mx_discover_cnf,
    parse_mx_get_param_cnf,
    parse_mx_nw_info_cnf,
    parse_mx_nw_stats_cnf,
    parse_mx_status_ind,
    parse_qca_nw_info_cnf,
    parse_qca_nw_stats_cnf,
)

class DiscoveryMixin:
    """Adapter discovery and PHY-rate collection."""

    def _new_dev(self, mac: str) -> dict:
        return {"mac": mac, "plcmac": mac, "model": "",
                "firmware_ver": "", "tx_rate": 0, "rx_rate": 0}

    def _note_link(self, responder: str, peer: str, tx: int, rx: int) -> None:
        """Record one directed link measurement responder→peer.

        NW_STATS-style confirms list the responder's PHY rate towards each
        peer station, so (responder, peer) pairs are real pairwise link data
        — the basis for the topology graph's edges.
        """
        if not responder or not peer or responder.upper() == peer.upper():
            return
        if tx <= 0 and rx <= 0:
            return
        self.plc_links[(responder.upper(), peer.upper())] = {
            "tx_rate": tx, "rx_rate": rx,
        }

    def _annotate_capabilities(self, devices: dict[str, dict]) -> None:
        """Attach capability hints per adapter for diagnostics."""
        for mac, dev in devices.items():
            cs = self._mac_chipset(mac)
            dev["chipset"] = cs if cs != "unknown" else self._chipset
            dev["capabilities"] = {
                "supports_standard_discovery": True,
                "supports_vendor_mx": cs in ("broadcom", "unknown"),
                "supports_vendor_qca": cs in ("qualcomm", "unknown"),
                "supports_rate_polling": (
                    dev.get("tx_rate", 0) > 0 or dev.get("rx_rate", 0) > 0
                ),
                "supports_led_control": mac.upper() in self._led_success_macs,
            }

    # ── Discovery ──────────────────────────────────────────

    @_locked
    def discover(self, timeout: float = 5.0) -> list[dict]:
        try:
            self._open_hpav()
            self._open_mx()
        except PermissionError:
            _LOGGER.error("HomePlug AV requires root or CAP_NET_RAW.")
            return []
        except OSError as e:
            _LOGGER.error("Cannot open raw socket: %s", e)
            return []

        devices: dict[str, dict] = {}
        self.plc_links = {}

        # Step 1: CC_DISCOVER_LIST on 0x88E1 (works on ALL chipsets)
        frame = build_hpav_frame(BROADCAST_MAC, self._src_mac,
                                 CC_DISCOVER_LIST_REQ)
        for mmtype, src, data in self._send_recv(self._sock_hpav, frame, min(timeout, 3.0)):
            if mmtype == CC_DISCOVER_LIST_CNF:
                devices.setdefault(src, self._new_dev(src))
                for sta in parse_discover_cnf(data):
                    m = sta["mac"]
                    devices.setdefault(m, self._new_dev(m))
                    devices[m]["same_network"] = sta.get("same_network", True)
        _LOGGER.debug("CC_DISCOVER_LIST (0x88E1): %d devices", len(devices))

        # Step 2: MEDIAXTREAM Discover on 0x8912 (Broadcom only)
        frame = build_mx_frame(BROADCAST_MAC, self._src_mac, MX_DISCOVER_REQ,
                               seq=self._next_seq())
        for mmtype, src, data in self._send_recv(self._sock_mx, frame, 2.0):
            if mmtype == MX_DISCOVER_CNF:
                self._chipset = "broadcom"
                self._mark_chipset(src, "broadcom")
                devices.setdefault(src, self._new_dev(src))
                info = parse_mx_discover_cnf(data)
                if info:
                    if info.get("hfid"):
                        devices[src]["model"] = info["hfid"]
                    devices[src]["_interface"] = info.get("interface", "")
                _LOGGER.debug("MX Discover: %s iface=%s hfid=%s",
                              src,
                              info.get("interface") if info else "?",
                              info.get("hfid") if info else "?")

        if self._chipset == "broadcom":
            _LOGGER.info("Broadcom chipset detected (BCM60xxx)")
        else:
            _LOGGER.info("No MEDIAXTREAM responses; trying Qualcomm path")

        # Step 3: Get TX/RX rates
        self._fetch_rates(devices)

        # Step 4: Get firmware/model info
        self._fetch_device_info(devices)

        # Step 5: Network role (CCo MAC) for the topology graph
        self._fetch_network_roles(devices)
        self._annotate_capabilities(devices)

        self._close()
        _LOGGER.info("HomePlug AV: %d adapters (chipset=%s)",
                     len(devices), self._chipset)
        for m, d in devices.items():
            _LOGGER.debug("  %s  TX=%d RX=%d  FW=%s  Model=%s",
                          m, d.get("tx_rate", 0), d.get("rx_rate", 0),
                          d.get("firmware_ver", ""), d.get("model", ""))
        return list(devices.values())

    # ── Passive Rate Monitoring ─────────────────────────────

    @_locked
    def get_passive_rates(self, timeout: float = 6.0) -> dict[str, dict[str, int]]:
        """Listen passively for 0x6046 status indications (Broadcom).

        The adapter broadcasts TX/RX rates every 2-5 seconds.
        Returns {mac: {"tx_rate": int, "rx_rate": int}}.
        """
        try:
            self._open_mx()
        except (PermissionError, OSError) as e:
            _LOGGER.debug("Cannot open MX socket for passive rates: %s", e)
            return {}

        rates: dict[str, dict[str, int]] = {}
        try:
            for mmtype, src, data in self._listen(self._sock_mx, timeout):
                if mmtype == MX_STATUS_IND:
                    info = parse_mx_status_ind(data)
                    if info and (info["tx_rate"] > 0 or info["rx_rate"] > 0):
                        rates[info["mac"]] = {
                            "tx_rate": info["tx_rate"],
                            "rx_rate": info["rx_rate"],
                        }
                        _LOGGER.debug("0x6046 passive: %s TX=%d RX=%d",
                                      info["mac"], info["tx_rate"], info["rx_rate"])
        finally:
            self._close()
        return rates

    # ── Rate Fetching ─────────────────────────────────────

    @staticmethod
    def _mirror_link_rate(devices: dict, responder: str, peer: str,
                          tx: int, rx: int) -> None:
        """A PLC link rate belongs to both endpoints.

        NW_STATS lists only the peer station, so the responding adapter would
        otherwise show 0. In a typical 2-adapter setup that means only one
        device reports a speed. Mirror the rate onto the responder too (only if
        it has none yet, so a directly reported rate always wins).
        """
        if responder and peer and responder != peer and responder in devices:
            d = devices[responder]
            if d.get("tx_rate", 0) == 0 and d.get("rx_rate", 0) == 0:
                d["tx_rate"] = tx
                d["rx_rate"] = rx

    def _fetch_rates(self, devices: dict) -> bool:
        found = False

        # Note: even a single adapter can report its own PHY rate to other
        # peers on the powerline (e.g. passive 0x6046 status indications,
        # or NW_STATS if it has ever linked). So we always attempt.

        # ── P: Passive 0x6046 listening (Broadcom) ──
        # Some adapters broadcast rates every 2-5s. Keep this short: the active
        # NW_STATS query below is the reliable path and listening 6s on every
        # poll just lengthens the time the shared lock is held.
        _LOGGER.debug("Trying passive 0x6046 listening (2s)...")
        for mmtype, src, data in self._listen(self._sock_mx, 2.0):
            if mmtype == MX_STATUS_IND:
                info = parse_mx_status_ind(data)
                if not info:
                    continue
                m = info["mac"]
                devices.setdefault(m, self._new_dev(m))
                self._mark_chipset(m, "broadcom")  # 0x6046 is Broadcom-only
                if info["tx_rate"] > 0 or info["rx_rate"] > 0:
                    devices[m]["tx_rate"] = info["tx_rate"]
                    devices[m]["rx_rate"] = info["rx_rate"]
                    found = True
                    _LOGGER.info("0x6046 passive: %s TX=%d RX=%d",
                                 m, info["tx_rate"], info["rx_rate"])

        if found:
            self._chipset = "broadcom"
            return True

        # ── Q: Qualcomm VS_NW_INFO (0xA038) — the correct QCA rate/topology MME.
        # The QCA7420 answers this (confirmed via Diagnose); the old 0xA048 does
        # not. If an adapter replies, this is a Qualcomm network: read the PHY
        # rates here and SKIP the slow Broadcom (0x8912) methods below — they
        # only time out (~40s) on a QCA network.
        _LOGGER.debug("Trying QCA VS_NW_INFO (0xA038) on 0x88E1...")
        qca = False
        macs = list(devices.keys())
        for mac in macs:
            dst = mac_to_bytes(mac)
            frame = build_qca_frame(dst, self._src_mac, VS_NW_INFO_REQ)
            for mmtype, src, data in self._send_recv(
                    self._sock_hpav, frame, 1.5, expected_src=mac,
                    stop_on=frozenset((VS_NW_INFO_CNF,))):
                if mmtype == VS_NW_INFO_CNF:
                    self._chipset = "qualcomm"
                    self._mark_chipset(src, "qualcomm")
                    qca = True
                    rates = parse_qca_nw_info_cnf(data)
                    if rates and src in devices:
                        devices[src]["tx_rate"], devices[src]["rx_rate"] = rates
                        found = True
                        _LOGGER.info("VS_NW_INFO: %s TX=%d RX=%d",
                                     src, rates[0], rates[1])
        if qca:
            # Adapters that did NOT answer QCA may be Broadcom (e.g. an AV1000
            # next to AV500s). Only take the pure-QCA shortcut if EVERY adapter is
            # QCA; a mixed network falls through to the Broadcom rate methods below
            # (whose unicast loops skip the QCA adapters, so no extra timeout).
            non_qca = [m for m in devices if self._mac_chipset(m) != "qualcomm"]
            if not non_qca:
                # Pure QCA. In a 2-adapter link the rate is symmetric: a
                # VS_NW_INFO reply often reports only one direction, so fill each
                # 0 from the peer's complementary value (both ends then agree).
                if len(devices) == 2:
                    ms = list(devices)
                    a, b = devices[ms[0]], devices[ms[1]]
                    ab = a.get("tx_rate", 0) or b.get("rx_rate", 0)
                    ba = a.get("rx_rate", 0) or b.get("tx_rate", 0)
                    if ab or ba:
                        a["tx_rate"], a["rx_rate"] = ab, ba
                        b["tx_rate"], b["rx_rate"] = ba, ab
                        found = True
                if not found:
                    _LOGGER.info(
                        "QCA VS_NW_INFO answered but no PHY rate parsed "
                        "(idle link or unconfirmed layout). Use Diagnose for raw bytes.")
                return found
            # Mixed network: do NOT apply the QCA symmetric mirror — it would
            # guess a rate for the non-QCA adapter and then block its real
            # NW_STATS reading, leaving the two ends inconsistent (one stuck at
            # 0 Mbit/s). Fall through so NW_STATS fills the link rate on both ends.
            _LOGGER.debug("Mixed network: %d non-QCA adapter(s) — also trying "
                          "Broadcom rate methods", len(non_qca))

        # ── A: MX NW_STATS (0xA02C) — primary Broadcom rate method ──
        # This is the dedicated PHY rate request for Broadcom chipsets.
        # Unicast to each adapter, then broadcast as fallback.
        _LOGGER.debug("Trying MX NW_STATS (0xA02C) unicast...")
        for mac in list(devices.keys()):
            if self._mac_chipset(mac) == "qualcomm":
                continue  # QCA adapter — answered VS_NW_INFO, skip MEDIAXTREAM
            dst = mac_to_bytes(mac)
            frame = build_mx_frame(dst, self._src_mac,
                                   MX_NW_STATS_REQ,
                                   seq=self._next_seq())
            for mmtype, src, data in self._send_recv(
                    self._sock_mx, frame, 2.0):
                if mmtype == MX_NW_STATS_CNF:
                    self._chipset = "broadcom"
                    self._mark_chipset(src, "broadcom")
                    for sta in parse_mx_nw_stats_cnf(data):
                        m = sta["mac"]
                        tx = sta.get("tx_rate", 0)
                        rx = sta.get("rx_rate", 0)
                        if tx > 0 or rx > 0:
                            devices.setdefault(m, self._new_dev(m))
                            devices[m]["tx_rate"] = tx
                            devices[m]["rx_rate"] = rx
                            self._mirror_link_rate(devices, src, m, tx, rx)
                            self._note_link(src, m, tx, rx)
                            found = True
                            _LOGGER.info("NW_STATS unicast: "
                                         "%s TX=%d RX=%d", m, tx, rx)

        if not found:
            _LOGGER.debug("Trying MX NW_STATS (0xA02C) broadcast...")
            frame = build_mx_frame(BROADCAST_MAC, self._src_mac,
                                   MX_NW_STATS_REQ,
                                   seq=self._next_seq())
            for mmtype, src, data in self._send_recv(
                    self._sock_mx, frame, 3.0):
                if mmtype == MX_NW_STATS_CNF:
                    self._chipset = "broadcom"
                    for sta in parse_mx_nw_stats_cnf(data):
                        m = sta["mac"]
                        tx = sta.get("tx_rate", 0)
                        rx = sta.get("rx_rate", 0)
                        if tx > 0 or rx > 0:
                            devices.setdefault(m, self._new_dev(m))
                            devices[m]["tx_rate"] = tx
                            devices[m]["rx_rate"] = rx
                            self._mirror_link_rate(devices, src, m, tx, rx)
                            self._note_link(src, m, tx, rx)
                            found = True
                            _LOGGER.info("NW_STATS broadcast: "
                                         "%s TX=%d RX=%d", m, tx, rx)

        if found:
            return True

        # ── B: MX LINK_STATS (0xA032) UNICAST — per-link rate query ──
        _LOGGER.debug("Trying MX LINK_STATS (0xA032) unicast...")
        for mac in list(devices.keys()):
            dst = mac_to_bytes(mac)
            frame = build_mx_frame(dst, self._src_mac,
                                   MX_LINK_STATS_REQ,
                                   seq=self._next_seq())
            for mmtype, src, data in self._send_recv(
                    self._sock_mx, frame, 2.0):
                if mmtype == MX_LINK_STATS_CNF:
                    self._chipset = "broadcom"
                    for sta in parse_mx_nw_stats_cnf(data):
                        m = sta["mac"]
                        tx = sta.get("tx_rate", 0)
                        rx = sta.get("rx_rate", 0)
                        if tx > 0 or rx > 0:
                            devices.setdefault(m, self._new_dev(m))
                            devices[m]["tx_rate"] = tx
                            devices[m]["rx_rate"] = rx
                            self._note_link(src, m, tx, rx)
                            found = True
                            _LOGGER.info("LINK_STATS: "
                                         "%s TX=%d RX=%d", m, tx, rx)

        if found:
            return True

        # ── C: MX GET_STATION_INFO (0xA04C) UNICAST to each adapter ──
        _LOGGER.debug("Trying MX GET_STATION_INFO (0xA04C) unicast...")
        for mac in list(devices.keys()):
            dst = mac_to_bytes(mac)
            frame = build_mx_frame(dst, self._src_mac,
                                   MX_GET_STATION_REQ,
                                   seq=self._next_seq())
            for mmtype, src, data in self._send_recv(
                    self._sock_mx, frame, 2.0):
                payload = data[ETH_HDR:min(len(data), ETH_HDR+80)]
                _LOGGER.debug("  STATION_INFO from %s: MME=0x%04X "
                              "hex=%s", src, mmtype, payload.hex())
                if mmtype == MX_GET_STATION_CNF:
                    if self._parse_station_rates(data, mac, devices):
                        found = True

        if found:
            return True

        # ── D: MX NW_INFO UNICAST (0xA028) per adapter ──
        _LOGGER.debug("Trying MX NW_INFO (0xA028) UNICAST per adapter...")
        for mac in list(devices.keys()):
            dst = mac_to_bytes(mac)
            frame = build_mx_frame(
                dst, self._src_mac, MX_NW_INFO_REQ,
                seq=self._next_seq(),
                payload=b"\x00\x01")
            for mmtype, src, data in self._send_recv(
                    self._sock_mx, frame, 2.0):
                if mmtype == MX_NW_INFO_CNF:
                    self._chipset = "broadcom"
                    info = parse_mx_nw_info_cnf(data)
                    for sta in info.get("stations", []):
                        m = sta["mac"]
                        tx = sta.get("tx_rate", 0)
                        rx = sta.get("rx_rate", 0)
                        if tx > 0 or rx > 0:
                            devices.setdefault(m, self._new_dev(m))
                            devices[m]["tx_rate"] = tx
                            devices[m]["rx_rate"] = rx
                            self._note_link(src, m, tx, rx)
                            found = True
                            _LOGGER.info("NW_INFO unicast: "
                                         "%s TX=%d RX=%d", m, tx, rx)

        if found:
            return True

        # ── E: MX NW_INFO BROADCAST (0xA028) ──
        _LOGGER.debug("Trying MX NW_INFO (0xA028) broadcast...")
        frame = build_mx_frame(
            BROADCAST_MAC, self._src_mac, MX_NW_INFO_REQ,
            seq=self._next_seq(), payload=b"\x00\x01")
        for mmtype, src, data in self._send_recv(self._sock_mx, frame, 3.0):
            if mmtype == MX_NW_INFO_CNF:
                self._chipset = "broadcom"
                info = parse_mx_nw_info_cnf(data)
                for sta in info.get("stations", []):
                    m = sta["mac"]
                    tx = sta.get("tx_rate", 0)
                    rx = sta.get("rx_rate", 0)
                    if tx > 0 or rx > 0:
                        devices.setdefault(m, self._new_dev(m))
                        devices[m]["tx_rate"] = tx
                        devices[m]["rx_rate"] = rx
                        self._note_link(src, m, tx, rx)
                        found = True

        if found:
            return True

        # ── F: Qualcomm VS_NW_STATS on 0x88E1 (fallback) ──
        _LOGGER.debug("Trying QCA VS_NW_STATS (0xA048) on 0x88E1...")
        frame = build_qca_frame(BROADCAST_MAC, self._src_mac,
                                VS_NW_STATS_REQ)
        for mmtype, src, data in self._send_recv(
                self._sock_hpav, frame, 3.0):
            if mmtype == VS_NW_STATS_CNF:
                self._chipset = "qualcomm"
                for sta in parse_qca_nw_stats_cnf(data):
                    m = sta["mac"]
                    if m in devices:
                        devices[m]["tx_rate"] = sta["tx_rate"]
                        devices[m]["rx_rate"] = sta["rx_rate"]
                        self._note_link(src, m, sta["tx_rate"], sta["rx_rate"])
                        found = True
            elif mmtype not in (0x6046, CC_DISCOVER_LIST_REQ,
                                0xA000):
                _LOGGER.debug("  QCA resp: 0x%04X from %s",
                              mmtype, src)

        if not found:
            num_devs = len(devices)
            if num_devs <= 1:
                _LOGGER.debug(
                    "No TX/RX rates (chipset=%s, %d adapter). "
                    "Rates require at least 2 paired adapters with active PLC link.",
                    self._chipset, num_devs)
            else:
                _LOGGER.info(
                    "No TX/RX rates obtained (chipset=%s, %d adapters). "
                    "Adapters may be idle or firmware does not expose rates. "
                    "Use Diagnose button for raw protocol analysis.",
                    self._chipset, num_devs)
        return found

    def _parse_station_rates(self, data: bytes, queried_mac: str,
                              devices: dict) -> bool:
        """Try to parse PHY rates from GET_STATION_INFO.CNF (0xA081).

        The format is undocumented. Look for MAC addresses of known
        devices followed by rate-like 16-bit values.
        """
        off = ETH_HDR + MX_MME_HDR
        payload = data[off:] if len(data) > off else b""
        _LOGGER.debug("STATION_INFO payload (%d bytes): %s",
                      len(payload), payload[:60].hex())
        found = False
        # Scan for any known MAC in the payload
        for mac in list(devices.keys()):
            mac_bytes = mac_to_bytes(mac)
            idx = payload.find(mac_bytes)
            if idx >= 0 and idx + 10 <= len(payload):
                # Try 16-bit LE rates after the MAC
                tx = struct.unpack("<H", payload[idx+6:idx+8])[0]
                rx = struct.unpack("<H", payload[idx+8:idx+10])[0]
                if 1 < tx < 3000 and 1 < rx < 3000:
                    devices[mac]["tx_rate"] = tx
                    devices[mac]["rx_rate"] = rx
                    _LOGGER.info("STATION_INFO: %s TX=%d RX=%d",
                                 mac, tx, rx)
                    found = True
                else:
                    _LOGGER.debug(
                        "STATION_INFO: found %s at offset %d "
                        "but values TX=%d RX=%d look wrong",
                        mac, idx, tx, rx)
        return found

    # ── Network Roles (CCo detection) ─────────────────────

    def _fetch_network_roles(self, devices: dict) -> None:
        """Learn each network's Central Coordinator via MX NW_INFO (0xA028).

        The confirm's network block carries the CCo MAC, which the topology
        graph uses to mark the root adapter. Queried once per adapter per
        session — roles only change on re-pairing or power cycling, and the
        unicast times out on adapters that don't implement the MME.
        Qualcomm-only adapters don't answer 0x8912 at all, so they are
        skipped; their role stays unknown.
        """
        for mac in list(devices.keys()):
            if self._mac_chipset(mac) == "qualcomm":
                continue
            if mac in self._roles_attempted:
                # Keep the previously learned value visible on the fresh dict.
                continue
            self._roles_attempted.add(mac)
            dst = mac_to_bytes(mac)
            frame = build_mx_frame(dst, self._src_mac, MX_NW_INFO_REQ,
                                   seq=self._next_seq(), payload=b"\x00\x01")
            for mmtype, src, data in self._send_recv(self._sock_mx, frame, 1.5):
                if mmtype != MX_NW_INFO_CNF:
                    continue
                info = parse_mx_nw_info_cnf(data)
                for nw in info.get("networks", []):
                    cco = (nw.get("cco_mac") or "").upper()
                    if cco and cco != "00:00:00:00:00:00":
                        self._cco_by_mac[src.upper()] = cco
                        _LOGGER.debug("NW_INFO roles: %s reports CCo=%s",
                                      src, cco)
        for mac, dev in devices.items():
            cco = self._cco_by_mac.get(mac.upper())
            if cco:
                dev["cco_mac"] = cco

    # ── Device Info ───────────────────────────────────────

    def _fetch_device_info(self, devices: dict):
        for mac in list(devices.keys()):
            # Firmware/model rarely change and the queries are slow (and time
            # out on adapters that don't answer). Try each adapter only once
            # per session instead of every poll.
            if mac in self._info_attempted and devices[mac].get("firmware_ver"):
                continue
            self._info_attempted.add(mac)
            dst = mac_to_bytes(mac)
            cs = self._mac_chipset(mac)

            if cs in ("broadcom", "unknown"):
                # MX Get Parameter: Manufacturer HFID
                if not devices[mac].get("model"):
                    frame = build_mx_frame(
                        dst, self._src_mac, MX_GET_PARAM_REQ,
                        seq=self._next_seq(),
                        payload=struct.pack("<H", PARAM_MANUFACTURER_HFID))
                    for mmtype, src, data in self._send_recv(
                            self._sock_mx, frame, 1.5):
                        if mmtype == MX_GET_PARAM_CNF:
                            val = parse_mx_get_param_cnf(data)
                            hfid = val.decode("ascii", errors="ignore"
                                              ).strip("\x00").strip()
                            if hfid:
                                devices[mac]["model"] = hfid
                                _LOGGER.debug("MX HFID %s: %s", mac, hfid)

                # MX Get Parameter: User HFID (firmware/name)
                if not devices[mac].get("firmware_ver"):
                    frame = build_mx_frame(
                        dst, self._src_mac, MX_GET_PARAM_REQ,
                        seq=self._next_seq(),
                        payload=struct.pack("<H", PARAM_USER_HFID))
                    for mmtype, src, data in self._send_recv(
                            self._sock_mx, frame, 1.5):
                        if mmtype == MX_GET_PARAM_CNF:
                            val = parse_mx_get_param_cnf(data)
                            ver = val.decode("ascii", errors="ignore"
                                             ).strip("\x00").strip()
                            if ver:
                                devices[mac]["firmware_ver"] = ver

            if cs in ("qualcomm", "unknown"):
                # QCA VS_SW_VER
                if not devices[mac].get("firmware_ver"):
                    frame = build_qca_frame(dst, self._src_mac, VS_SW_VER_REQ)
                    for mmtype, src, data in self._send_recv(
                            self._sock_hpav, frame, 1.5):
                        if mmtype == VS_SW_VER_CNF:
                            off = ETH_HDR + HPAV_MME_HDR + 3
                            if len(data) > off + 3 and data[off] == 0:
                                ver_len = data[off + 2]
                                ver = data[off+3:off+3+ver_len].decode(
                                    "ascii", errors="ignore").rstrip("\x00")
                                devices[mac]["firmware_ver"] = ver
                                # Let FritzMixin recognise AVM "Custom" firmware
                                # (e.g. "...-CS") on AVM OUIs we don't list.
                                self.note_firmware(mac, ver)
