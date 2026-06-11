"""Human-readable adapter diagnostics."""
import struct

from .const import (
    BROADCAST_MAC,
    CC_DISCOVER_LIST_CNF,
    CC_DISCOVER_LIST_REQ,
    ETH_HDR,
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
    VS_LNK_STATS_REQ,
    VS_NW_INFO_REQ,
    VS_NW_INFO_STATS_REQ,
    VS_NW_STATS_REQ,
    VS_SW_VER_REQ,
    _locked,
)
from .frames import (
    build_hpav_frame,
    build_mx_frame,
    build_qca_frame,
    get_iface_mac,
    mac_to_bytes,
    mac_to_str,
)
from .parsers import (
    parse_discover_cnf,
    parse_mx_discover_cnf,
    parse_mx_get_param_cnf,
    parse_mx_nw_info_cnf,
    parse_mx_nw_stats_cnf,
    parse_mx_status_ind,
)

class DiagnosticsMixin:
    """Human-readable adapter diagnostics."""

    # ── Diagnostics ──────────────────────────────────────

    @_locked
    def diagnose(self, timeout: float = 10.0) -> str:
        src_mac = get_iface_mac(self.interface or "")
        lines = [
            f"Interface: {self.interface}",
            f"Source MAC: {mac_to_str(src_mac)}",
            f"Chipset: {self._chipset}",
            f"Dual sockets: 0x88E1 (HomePlug AV) + 0x8912 (MEDIAXTREAM)",
            "",
        ]
        try:
            self._open_hpav()
            self._open_mx()
        except Exception as e:
            return f"Cannot open sockets: {e}"

        # ── All diagnostic tests ──
        tests = [
            # (label, socket, frame_builder_args)
            ("CC_DISCOVER_LIST (0x0014) on 0x88E1",
             self._sock_hpav,
             build_hpav_frame(BROADCAST_MAC, self._src_mac,
                              CC_DISCOVER_LIST_REQ)),

            ("MX DISCOVER (0xA070) on 0x8912",
             self._sock_mx,
             build_mx_frame(BROADCAST_MAC, self._src_mac,
                            MX_DISCOVER_REQ, seq=self._next_seq())),

            ("MX NW_INFO broadcast (0xA028) on 0x8912",
             self._sock_mx,
             build_mx_frame(BROADCAST_MAC, self._src_mac,
                            MX_NW_INFO_REQ, seq=self._next_seq(),
                            payload=b"\x00\x01")),
        ]

        # Get discovered MACs first for unicast tests
        disc_frame = build_hpav_frame(BROADCAST_MAC, self._src_mac,
                                      CC_DISCOVER_LIST_REQ)
        disc_macs = set()
        for mmtype, src, data in self._send_recv(
                self._sock_hpav, disc_frame, 2.0):
            disc_macs.add(src)
            if mmtype == CC_DISCOVER_LIST_CNF:
                for sta in parse_discover_cnf(data):
                    disc_macs.add(sta["mac"])

        # Add unicast tests for each discovered adapter
        for mac in sorted(disc_macs):
            dst = mac_to_bytes(mac)
            tests.extend([
                (f"MX NW_STATS unicast (0xA02C) → {mac}",
                 self._sock_mx,
                 build_mx_frame(dst, self._src_mac,
                                MX_NW_STATS_REQ,
                                seq=self._next_seq())),

                (f"MX LINK_STATS unicast (0xA032) → {mac}",
                 self._sock_mx,
                 build_mx_frame(dst, self._src_mac,
                                MX_LINK_STATS_REQ,
                                seq=self._next_seq())),

                (f"MX GET_STATION_INFO (0xA04C) → {mac}",
                 self._sock_mx,
                 build_mx_frame(dst, self._src_mac,
                                MX_GET_STATION_REQ,
                                seq=self._next_seq())),

                (f"MX NW_INFO unicast (0xA028) → {mac}",
                 self._sock_mx,
                 build_mx_frame(dst, self._src_mac,
                                MX_NW_INFO_REQ,
                                seq=self._next_seq(),
                                payload=b"\x00\x01")),
            ])

        tests.extend([
            ("MX GET_PARAM Mfg HFID (0xA05C) on 0x8912",
             self._sock_mx,
             build_mx_frame(BROADCAST_MAC, self._src_mac,
                            MX_GET_PARAM_REQ, seq=self._next_seq(),
                            payload=struct.pack("<H",
                                               PARAM_MANUFACTURER_HFID))),

            ("MX GET_PARAM User HFID (0xA05C) on 0x8912",
             self._sock_mx,
             build_mx_frame(BROADCAST_MAC, self._src_mac,
                            MX_GET_PARAM_REQ, seq=self._next_seq(),
                            payload=struct.pack("<H", PARAM_USER_HFID))),

            ("QCA VS_SW_VER (0xA000) on 0x88E1",
             self._sock_hpav,
             build_qca_frame(BROADCAST_MAC, self._src_mac,
                             VS_SW_VER_REQ)),

            # Documented Qualcomm read MMEs (open-plc-utils qualcomm.h). These
            # are the real QCA rate/topology sources and are read-only. Dump the
            # raw responses so a QCA7420 (AV500) capture can be decoded later.
            ("QCA VS_NW_INFO (0xA038) on 0x88E1",
             self._sock_hpav,
             build_qca_frame(BROADCAST_MAC, self._src_mac,
                             VS_NW_INFO_REQ)),

            ("QCA VS_LNK_STATS (0xA030) on 0x88E1",
             self._sock_hpav,
             build_qca_frame(BROADCAST_MAC, self._src_mac,
                             VS_LNK_STATS_REQ)),

            ("QCA VS_NW_INFO_STATS (0xA074) on 0x88E1",
             self._sock_hpav,
             build_qca_frame(BROADCAST_MAC, self._src_mac,
                             VS_NW_INFO_STATS_REQ)),

            # Legacy/unverified guess kept for comparison.
            ("QCA VS_NW_STATS? (0xA048) on 0x88E1",
             self._sock_hpav,
             build_qca_frame(BROADCAST_MAC, self._src_mac,
                             VS_NW_STATS_REQ)),
        ])

        for label, sock, frame in tests:
            lines.append(f"=== {label} ===")
            resps = self._send_recv(sock, frame, 3.0)
            lines.append(f"Responses: {len(resps)}")
            for mmtype, src, data in resps:
                plen = min(len(data), ETH_HDR + 256)
                p = data[ETH_HDR:plen]
                lines.append(
                    f"  MME=0x{mmtype:04X} from={src} "
                    f"len={len(data)} hex={p.hex()}")
                # Decode known types
                if mmtype == CC_DISCOVER_LIST_CNF:
                    for sta in parse_discover_cnf(data):
                        lines.append(
                            f"    > Station: {sta['mac']} "
                            f"same_nw={sta['same_network']}")
                elif mmtype == MX_DISCOVER_CNF:
                    info = parse_mx_discover_cnf(data)
                    if info:
                        lines.append(
                            f"    > iface={info['interface']} "
                            f"hfid={info['hfid']}")
                elif mmtype == MX_NW_INFO_CNF:
                    info = parse_mx_nw_info_cnf(data)
                    for nw in info.get("networks", []):
                        lines.append(
                            f"    > Net: CCo={nw['cco_mac']} "
                            f"Role={nw['role']}")
                    for sta in info.get("stations", []):
                        lines.append(
                            f"    > Sta: {sta['mac']} "
                            f"TX={sta['tx_rate']} RX={sta['rx_rate']}")
                elif mmtype == MX_GET_PARAM_CNF:
                    val = parse_mx_get_param_cnf(data)
                    txt = val.decode("ascii", errors="replace"
                                     ).rstrip("\x00")
                    lines.append(f"    > Value: {txt}")
                elif mmtype in (MX_NW_STATS_CNF, MX_LINK_STATS_CNF):
                    for sta in parse_mx_nw_stats_cnf(data):
                        lines.append(
                            f"    > {sta['mac']} "
                            f"TX={sta['tx_rate']} RX={sta['rx_rate']}")
                elif mmtype == MX_STATUS_IND:
                    info = parse_mx_status_ind(data)
                    if info:
                        lines.append(
                            f"    > Status: TX={info['tx_rate']} "
                            f"RX={info['rx_rate']} Mbps")
                elif mmtype == MX_GET_STATION_CNF:
                    p = data[ETH_HDR+MX_MME_HDR:]
                    lines.append(
                        f"    > STATION_INFO payload ({len(p)}b): "
                        f"{p[:60].hex()}")
            lines.append("")

        # ── GET_PARAM parameter scan (0x0030-0x005F) ──
        if disc_macs:
            first_mac = sorted(disc_macs)[0]
            dst = mac_to_bytes(first_mac)
            lines.append(f"=== GET_PARAM scan 0x0030-0x005F → {first_mac} ===")
            found_params = []
            for pid in range(0x0030, 0x0060):
                frame = build_mx_frame(
                    dst, self._src_mac, MX_GET_PARAM_REQ,
                    seq=self._next_seq(),
                    payload=struct.pack("<H", pid))
                for mmtype, src, data in self._send_recv(
                        self._sock_mx, frame, 0.6):
                    if mmtype == MX_GET_PARAM_CNF:
                        val = parse_mx_get_param_cnf(data)
                        if len(val) >= 1:
                            found_params.append(
                                f"  0x{pid:04X}: {len(val)} bytes "
                                f"= {val[:30].hex()}")
            if found_params:
                lines.extend(found_params)
            else:
                lines.append("  No valid parameters in this range")
            lines.append("")

        # ── Passive listen ──
        for etype_name, sock in [("0x88E1", self._sock_hpav),
                                  ("0x8912", self._sock_mx)]:
            lines.append(f"=== PASSIVE LISTEN {etype_name} (3s) ===")
            passive = self._listen(sock, 3.0)
            lines.append(f"Frames: {len(passive)}")
            for mmtype, src, data in passive:
                p = data[ETH_HDR:min(len(data), ETH_HDR+256)]
                lines.append(
                    f"  MME=0x{mmtype:04X} from={src} hex={p.hex()}")
            # Summary
            types: dict[int, int] = {}
            for mmtype, _, _ in passive:
                types[mmtype] = types.get(mmtype, 0) + 1
            if types:
                lines.append("  Summary:")
                for mt, c in sorted(types.items()):
                    lines.append(f"    0x{mt:04X}: {c}x")
            lines.append("")

        self._close()
        return "\n".join(lines)
