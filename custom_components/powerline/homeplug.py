"""
HomePlug AV Layer 2 Communication - Dual Protocol Support.

Supports BOTH protocol stacks:
  1. HomePlug AV (Ethertype 0x88E1) - Standard MMEs, Qualcomm vendor-specific
  2. MEDIAXTREAM (Ethertype 0x8912) - Broadcom/Gigle proprietary

TP-Link TL-PA7017 (BCM60355) uses MEDIAXTREAM for all vendor commands.
Only CC_DISCOVER_LIST (0x0014) works on 0x88E1 for Broadcom chips.
All other commands must go through 0x8912 with Gigle OUI 00:1f:84.

Protocol auto-detection: tries MEDIAXTREAM first (most common for modern
TP-Link adapters), falls back to Qualcomm vendor-specific on 0x88E1.

Reference: github.com/serock/pla-util (Ada, GPL-3, tested with TL-PA7017)
Reference: github.com/jbit/powerline (Rust, Broadcom + QCA support)
Reference: peanball.net/2023/08/powerline-monitoring/ (TL-PA7017 monitoring)

Requires: CAP_NET_RAW (root or setcap cap_net_raw+ep)
"""

import asyncio
import functools
import logging
import os
import random
import socket
import struct
import threading
import time
import zlib
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _locked(method):
    """Serialize a HomeplugAV method on self._lock.

    discover() and the control commands (set_led, set_power_saving, ...) each
    open and close the shared raw sockets. When a poll and a switch command run
    in different executor threads at the same time, one closes self._sock_mx
    while the other is mid-_send_recv -> 'NoneType has no attribute settimeout'.
    Serializing every socket-using entry point prevents that.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper

# ── Ethertypes ──
ETHERTYPE_HPAV = 0x88E1          # Standard HomePlug AV
ETHERTYPE_MEDIAXTREAM = 0x8912   # Broadcom / Gigle / MEDIAXTREAM

BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"

# ── OUIs ──
QCA_OUI   = b"\x00\xB0\x52"     # Qualcomm/Atheros
GIGLE_OUI = b"\x00\x1f\x84"     # Gigle Semiconductor (Broadcom PLC)

# ── HomePlug AV Standard MMEs (0x88E1, ALL chipsets) ──
CC_DISCOVER_LIST_REQ = 0x0014
CC_DISCOVER_LIST_CNF = 0x0015

# ── MEDIAXTREAM MMEs (0x8912, Broadcom BCM60xxx only) ──
# MMTYPE values verified against serock/mediaxtream-dissector (the Wireshark
# Mediaxtream dissector) and serock/pla-util. Earlier values for NW_STATS
# (0xA034) and STATION_INFO (0xA080) were wrong and never got a response —
# that is the main reason TX/RX rates always read 0.
MX_DISCOVER_REQ       = 0xA070
MX_DISCOVER_CNF       = 0xA071
MX_NW_INFO_REQ        = 0xA028  # Network Info (station list)
MX_NW_INFO_CNF        = 0xA029
MX_NW_STATS_REQ       = 0xA02C  # Network Stats (avg PHY rates) = pla-util get-network-stats
MX_NW_STATS_CNF       = 0xA02D
MX_GET_PARAM_REQ      = 0xA05C  # Get Parameter
MX_GET_PARAM_CNF      = 0xA05D
MX_SET_PARAM_REQ      = 0xA058  # Set Parameter (LED, power saving, NMK, HFID, ...)
MX_SET_PARAM_CNF      = 0xA059
MX_APPLY_REQ          = 0xA020  # Apply/commit settings (sent by tpPLC after writes)
MX_APPLY_CNF          = 0xA021
MX_SET_KEY_REQ        = 0xA018  # Set Key (legacy)
MX_SET_KEY_CNF        = 0xA019
MX_LINK_STATS_REQ     = 0xA032  # Link Stats (undocumented fallback)
MX_LINK_STATS_CNF     = 0xA033
MX_GET_STATION_REQ    = 0xA04C  # Station Info = pla-util get-station-info
MX_GET_STATION_CNF    = 0xA04D
# 0xA058/0xA059 used to be modelled as an opaque "action" command. It is
# actually Set Parameter — these aliases keep older call sites working.
MX_ACTION_REQ         = MX_SET_PARAM_REQ
MX_ACTION_CNF         = MX_SET_PARAM_CNF
MX_ACTION_ALT_CNF     = 0xA069  # Alternative confirmation seen on some BCM firmware
MX_STATUS_IND         = 0x6046  # Periodic status indication (TX/RX rates, every 2-5s)

# Valid confirmations for Set Parameter (0xA058).
# 0x6046 is a passive status broadcast every 2-5s regardless of any request,
# so accepting it as an ACK produces false positives. 0xA019 / 0xA05D / 0xA071
# are responses to other requests; seeing those instead of a Set Parameter CNF
# means the adapter silently ignored our write.
_MX_ACTION_OK = frozenset((
    MX_SET_PARAM_CNF,   # 0xA059 - Set Parameter confirmation
    MX_ACTION_ALT_CNF,  # 0xA069 - alternative confirmation (BCM firmware)
))
_MX_APPLY_OK = frozenset((MX_APPLY_CNF,))  # 0xA021 - Apply/commit confirmation

# ── Qualcomm Vendor-Specific MMEs (0x88E1 + QCA OUI 00:B0:52) ──
# Values from qca/open-plc-utils mme/qualcomm.h (the canonical reference).
VS_SW_VER_REQ        = 0xA000;  VS_SW_VER_CNF        = 0xA001  # firmware version
VS_NW_INFO_REQ       = 0xA038;  VS_NW_INFO_CNF       = 0xA039  # network info (+ PHY rates)
VS_LNK_STATS_REQ     = 0xA030;  VS_LNK_STATS_CNF     = 0xA031  # per-link statistics
VS_NW_INFO_STATS_REQ = 0xA074;  VS_NW_INFO_STATS_CNF = 0xA075  # extended network info/stats
VS_RD_MOD_REQ        = 0xA024;  VS_RD_MOD_CNF        = 0xA025  # read module (PIB/MAC) — read-only
# VS_WR_MOD (0xA020) / VS_MOD_NVM (0xA028) write the PIB. Module codes:
# VS_MODULE_MAC=1<<0, VS_MODULE_PIB=1<<1, VS_MODULE_FORCE=1<<4. Intentionally
# NOT used here — a bad PIB write can lose the network key / brick the adapter.
# VS_SET_LED_BEHAVIOR (0xA094) exists as a constant in qualcomm.h but has no
# implementation in open-plc-utils (no payload struct), so we can't use it.
VS_SET_LED_BEHAVIOR  = 0xA094

# QCA module operation (chunked PIB read/write) used by the QCA7420 firmware.
# Reverse-engineered from a tpPLC capture (PROTOCOL.md §9): MMV=0x00, MMTYPE
# 0xA0B0(req)/0xA0B1(cnf), OUI 00:b0:52, no FMI. tpPLC reads the whole PIB in
# 1400-byte chunks (op 0x0100), then writes it back (open 0x0110, data 0x0111,
# close 0x0112). LED on/off only flips a 10-byte LED-behavior table; we do a
# read-modify-write so every other byte (network key, MAC, ...) is written back
# untouched.
VS_MOD_OP_REQ = 0xA0B0
VS_MOD_OP_CNF = 0xA0B1
QCA_PIB_SIZE  = 0x2370          # 9072 bytes (write-open total length)
QCA_PIB_CHUNK = 0x0578          # 1400-byte transfer chunks
# LED-behavior table: 0x01 = LED off, 0x00 = LED on (confirmed by diffing the
# off/on/off capture; no checksum elsewhere changes for the LED toggle).
QCA_LED_OFFSETS = (0x1ED5, 0x1EFD, 0x1F05, 0x1F1D, 0x1F25,
                   0x1F2D, 0x1F45, 0x1F4D, 0x1F55, 0x1F6D)
# QoS priority is a 2-byte value in the PIB. Changing it also updates two
# XOR-checksum fields; because the checksum is XOR-linear we maintain it by
# XOR-ing the value delta into each field (no need to recompute from scratch).
# Confirmed by diffing internet/gaming/audio_video/voip captures (QCA7420):
# e.g. internet->gaming flips 0x0ADE 0000->FA41 and the checksums by the same
# delta (0x0376 51F7->ABB6, 0x03BE B276->4837) — reproduced byte-for-byte.
QCA_QOS_OFFSET = 0x0ADE
QCA_QOS_CKSUM_OFFSETS = (0x0376, 0x03BE)
QCA_QOS_VALUES = {
    "internet":    0x0000,
    "gaming":      0xFA41,
    "audio_video": 0xFA42,
    "voip":        0xFA43,
}
# Captured 32-byte module-op header templates; variable fields are patched in:
#   read : len@17(LE), off@19(LE)
#   open : token@13(LE), totlen@22(LE), checksum@26(LE u32)
#   data : token@13(LE), len@22(LE), off@24(LE), data@26
#   close: token@13(LE)
_QCA_HDR_READ  = bytes.fromhex("0000000001000012000000000002700000780500000000000000000000000000")
_QCA_HDR_OPEN  = bytes.fromhex("000000000110008500000000002348000001027000007023000089c580ea0000")
_QCA_HDR_DATA  = bytes.fromhex("000000000111008f050000000023480000000270000078050000000001000100")
_QCA_HDR_CLOSE = bytes.fromhex("0000000001120014000000000023480000000000000000000000000000000000")
# 0xA048 was used by older builds as "VS_NW_STATS" but is not in qualcomm.h and
# never got a confirmed response; kept only so existing call sites still resolve.
VS_NW_STATS_REQ = 0xA048;  VS_NW_STATS_CNF = 0xA049

# ── Constants ──
ETH_HDR = 14
HPAV_MME_HDR = 5    # Version(1) + MMType(2) + FragInfo(2)
MX_MME_HDR = 9      # Version(1) + MMType(2) + FragInfo(2) + OUI(3) + SeqNum(1)
ETH_MIN = 60

# ── MEDIAXTREAM Get/Set Parameter IDs ──
# Verified against serock/mediaxtream-dissector. These are the same IDs used
# by both Get Parameter (0xA05C, read) and Set Parameter (0xA058, write).
PARAM_MANUFACTURER_HFID = 0x0001
PARAM_USER_HFID         = 0x0025
PARAM_MANUFACTURER_DAK1 = 0x0009
PARAM_USER_NMK          = 0x0024
PARAM_POWER_STANDBY     = 0x0029  # Power Manager Standby: low 15 bits = timeout(s), bit 0x8000 = enabled
PARAM_POWER_STANDBY_AUX = 0x0074  # Companion param tpPLC clears when disabling power saving
PARAM_LED_CONTROL       = 0x003E  # LED control (read-only on TL-PA7017, always 0)
PARAM_LED_OPTIONS       = 0x003F  # LED options — bit 0x10 of byte 3 = LED enabled
PARAM_LED_AUX           = 0x0095  # Undocumented LED companion param (tpPLC writes it too)
PARAM_QOS_PRIORITY_MAP  = 0x0069  # QoS priority mapping table (~1000 bytes)


# ══════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════

def mac_to_str(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in b)

def mac_to_bytes(s: str) -> bytes:
    return bytes.fromhex(s.replace(":", "").replace("-", "").replace(" ", ""))

def _find_interface() -> str | None:
    """Find the best Ethernet interface for HomePlug AV.

    Prioritizes physical Ethernet (eth*, en*) over other interfaces.
    Skips virtual/container interfaces.
    """
    try:
        ifaces = os.listdir("/sys/class/net/")
    except OSError:
        return None

    skip_prefixes = ("lo", "veth", "docker", "br-", "vir", "wl", "ww", "tun", "tap")
    # Prefer eth*/en* (physical Ethernet), then anything else
    prefer = []
    fallback = []
    for iface in sorted(ifaces):
        if iface.startswith(skip_prefixes):
            continue
        try:
            with open(f"/sys/class/net/{iface}/operstate") as f:
                if f.read().strip() not in ("up", "unknown"):
                    continue
        except OSError:
            continue
        if iface.startswith(("eth", "en")):
            prefer.append(iface)
        else:
            fallback.append(iface)

    return (prefer or fallback or [None])[0]

def get_iface_mac(iface: str) -> bytes:
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            return mac_to_bytes(f.read().strip())
    except (OSError, ValueError):
        return b"\x00" * 6


# ══════════════════════════════════════════════════════════
#  Frame Builders
# ══════════════════════════════════════════════════════════

def build_hpav_frame(dst: bytes, src: bytes, mmtype: int,
                     payload: bytes = b"") -> bytes:
    """Build standard HomePlug AV frame (Ethertype 0x88E1)."""
    frame = (
        dst + src
        + struct.pack("!H", ETHERTYPE_HPAV)
        + struct.pack("<BHH", 0x01, mmtype, 0x0000)
        + payload
    )
    return frame.ljust(ETH_MIN, b"\x00")

def build_qca_frame(dst: bytes, src: bytes, mmtype: int,
                    payload: bytes = b"") -> bytes:
    """Build Qualcomm vendor-specific frame (0x88E1 + QCA OUI)."""
    return build_hpav_frame(dst, src, mmtype, QCA_OUI + payload)

def build_qca_mod_frame(dst: bytes, src: bytes, payload: bytes) -> bytes:
    """Build a QCA module-operation frame (0x88E1, MMTYPE 0xA0B0, OUI 00:b0:52).

    Unlike build_qca_frame() this uses MMV=0x00 and NO fragmentation field —
    that is exactly what the QCA7420 module read/write protocol uses on the wire.
    """
    frame = (
        dst + src
        + struct.pack("!H", ETHERTYPE_HPAV)
        + struct.pack("<BH", 0x00, VS_MOD_OP_REQ)
        + QCA_OUI
        + payload
    )
    return frame.ljust(ETH_MIN, b"\x00")

def build_mx_frame(dst: bytes, src: bytes, mmtype: int, seq: int = 1,
                   payload: bytes = b"", version: int = 0x02) -> bytes:
    """Build MEDIAXTREAM frame (Ethertype 0x8912 + Gigle OUI).

    Wire format:
      Eth: DST(6) + SRC(6) + Type(2) = 14 bytes
      MME: Version(1) + MMType(2 LE) + FragInfo(2) + OUI(3) + SeqNum(1) = 9 bytes
      Payload: variable
    """
    frame = (
        dst + src
        + struct.pack("!H", ETHERTYPE_MEDIAXTREAM)
        + struct.pack("<BHH", version, mmtype, 0x0000)
        + GIGLE_OUI
        + struct.pack("<B", seq)
        + payload
    )
    return frame.ljust(ETH_MIN, b"\x00")


def build_mx_set_param(dst: bytes, src: bytes, param_id: int, value: bytes,
                       octets_per_element: int = 1, seq: int = 1,
                       version: int = 0x02) -> bytes:
    """Build a MEDIAXTREAM Set Parameter (0xA058) frame.

    Payload layout (serock/mediaxtream-dissector):
      ParamID(2 LE) + OctetsPerElement(1) + NumElements(2 LE) + Value(N)
    """
    num_elements = max(1, len(value) // octets_per_element)
    payload = (
        struct.pack("<H", param_id)
        + struct.pack("<B", octets_per_element)
        + struct.pack("<H", num_elements)
        + value
    )
    return build_mx_frame(dst, src, MX_SET_PARAM_REQ, seq=seq,
                          payload=payload, version=version)


# ══════════════════════════════════════════════════════════
#  Parsers
# ══════════════════════════════════════════════════════════

def parse_discover_cnf(data: bytes) -> list[dict]:
    """Parse CC_DISCOVER_LIST.CNF (0x0015) from 0x88E1."""
    stations = []
    off = ETH_HDR + HPAV_MME_HDR
    if len(data) < off + 1:
        return stations
    n = data[off]; off += 1
    for _ in range(n):
        if off + 12 > len(data):
            break
        mac = mac_to_str(data[off:off+6])
        tei = data[off+6]
        same_nw = data[off+7] == 1
        off += 12
        stations.append({
            "mac": mac, "plcmac": mac,
            "tei": tei, "same_network": same_nw
        })
    return stations

def parse_mx_discover_cnf(data: bytes) -> dict | None:
    """Parse MEDIAXTREAM Discover.CNF (0xa071) from 0x8912.

    Payload after MX header: Interface(1) + HFID_Len(1) + HFID(N)
    """
    off = ETH_HDR + MX_MME_HDR
    payload = data[off:] if len(data) > off else b""
    if len(payload) < 2:
        return None
    iface_type = payload[0]  # 0x01=MII1 (Ethernet), 0x02=PLC
    hfid_len = payload[1]
    hfid = ""
    if hfid_len > 0 and len(payload) >= 2 + hfid_len:
        hfid = payload[2:2+hfid_len].decode("ascii", errors="ignore").rstrip("\x00")
    return {
        "interface": "ethernet" if iface_type == 0x01 else "plc",
        "hfid": hfid,
    }

def parse_mx_nw_info_cnf(data: bytes) -> dict:
    """Parse MEDIAXTREAM Network Info.CNF (0xa029) from 0x8912.

    Based on pla-util wiki get-network-info:
      NumNetworks(1) + [NID(7)+SNID(1)+TEI(1)+Role(1)+CCo_MAC(6)+...] +
      NumStations(1) + [STA_MAC(6)+TEI(1)+Bridge_MAC(6)+TX(2LE)+RX(2LE)]
    """
    result = {"networks": [], "stations": []}
    off = ETH_HDR + MX_MME_HDR
    payload = data[off:] if len(data) > off else b""
    _LOGGER.debug("MX NW_INFO payload (%d bytes): %s",
                  len(payload), payload[:80].hex())

    if len(payload) < 1:
        return result

    num_nw = payload[0]; p = 1
    for _ in range(num_nw):
        if p + 17 > len(payload):
            break
        nid = payload[p:p+7].hex()
        snid = payload[p+7]
        tei = payload[p+8]
        role = payload[p+9]
        cco_mac = mac_to_str(payload[p+10:p+16])
        # Byte 16 may be security level or backup CCo flag
        p += 17
        result["networks"].append({
            "nid": nid, "snid": snid, "tei": tei,
            "role": role, "cco_mac": cco_mac
        })
        _LOGGER.debug("  Net: NID=%s CCo=%s Role=%d", nid, cco_mac, role)

    if p >= len(payload):
        return result

    # Normally a station count byte follows network blocks.
    # Some Broadcom firmware responses omit it and append station-like
    # entries directly, so we support both layouts.
    remaining = len(payload) - p
    parse_implicit = False
    if remaining > 0:
        num_sta = payload[p]
        expected_min = p + 1 + (num_sta * 13)
        if num_sta == 0 and remaining >= 6 and payload[p:p+6] != b"\x00" * 6:
            parse_implicit = True
        elif num_sta > 0 and expected_min <= len(payload):
            p += 1
            _LOGGER.debug("  Stations: %d", num_sta)
            for i in range(num_sta):
                if p + 13 > len(payload):
                    break
                sta_mac = mac_to_str(payload[p:p+6])
                sta_tei = payload[p+6]
                bridge_mac = mac_to_str(payload[p+7:p+13])
                tx = 0
                rx = 0
                # Try 2-byte LE rates after bridge MAC
                if p + 17 <= len(payload):
                    tx = struct.unpack("<H", payload[p+13:p+15])[0]
                    rx = struct.unpack("<H", payload[p+15:p+17])[0]
                    p += 17
                elif p + 15 <= len(payload):
                    # 1-byte rates (multiply by 2 for PHY rate)
                    tx = payload[p+13] * 2
                    rx = payload[p+14] * 2
                    p += 15
                else:
                    p += 13
                _LOGGER.debug("  Sta[%d]: %s bridge=%s TX=%d RX=%d", i, sta_mac, bridge_mac, tx, rx)
                result["stations"].append({
                    "mac": sta_mac,
                    "plcmac": sta_mac,
                    "tei": sta_tei,
                    "tx_rate": tx,
                    "rx_rate": rx,
                })
            return result
        else:
            parse_implicit = True

    if parse_implicit:
        _LOGGER.debug("  Stations: implicit layout")
        i = 0
        while p + 6 <= len(payload):
            raw_mac = payload[p:p+6]
            if raw_mac == b"\x00" * 6:
                break
            sta_mac = mac_to_str(raw_mac)
            result["stations"].append({"mac": sta_mac, "plcmac": sta_mac, "tx_rate": 0, "rx_rate": 0})
            _LOGGER.debug("  Sta[%d]: %s (implicit)", i, sta_mac)
            i += 1
            # Undocumented Broadcom payloads often use 13-byte station blocks.
            # If fewer bytes remain, just advance by MAC length to avoid loops.
            step = 13 if p + 13 <= len(payload) else 6
            p += step

    return result

def parse_mx_get_param_cnf(data: bytes) -> bytes:
    """Parse MEDIAXTREAM Get Parameter.CNF (0xa05d).

    Confirmed format from a tpPLC capture (TL-PA7017):
      OctetsPerElement(1) + NumElements(2 LE) + Value(OctetsPerElement*NumElements)
    e.g. 01 4000 <64 bytes> = HFID string; 02 0100 4700 = a 2-byte value 0x0047.
    """
    off = ETH_HDR + MX_MME_HDR
    payload = data[off:] if len(data) > off else b""
    if len(payload) < 3:
        return b""
    octets = payload[0]
    num = struct.unpack("<H", payload[1:3])[0]
    if octets in (1, 2, 4) and 0 < num <= 2000:
        end = 3 + octets * num
        if end <= len(payload):
            return payload[3:end]
    # Fallback: best-effort, skip the 3-byte header.
    return payload[3:]

def decode_phy_rate(raw: int) -> int:
    """Decode a MEDIAXTREAM PHY rate (Mbps) from its 16-bit LE field.

    Confirmed on TL-PA7017 (BCM60355) across two link types: the rate is the
    low 12 bits, the top nibble is a status/flag field (0x8xxx on the AV500
    link, 0x4xxx on the AV1000<->AV1000 link). e.g.
      0x819D -> 413 Mbps (AV500 link)
      0x4223 -> 547 Mbps (AV1000<->AV1000 link)
    Masking only the top bit (0x8000) was wrong: it left 0x4223 as 16931.
    """
    return raw & 0x0FFF

def parse_mx_nw_stats_cnf(data: bytes) -> list[dict]:
    """Parse MEDIAXTREAM Network Stats.CNF — extract PHY rates.

    Format: NumStations(1) + [DA(6) + AvgTX(2 LE) + AvgRX(2 LE)] per station.
    Each rate's top nibble (0xF000) is a status field; decode_phy_rate() keeps
    only the low 12 bits.
    """
    stations = []
    off = ETH_HDR + MX_MME_HDR
    payload = data[off:] if len(data) > off else b""
    _LOGGER.debug("MX NW_STATS payload (%d bytes): %s",
                  len(payload), payload[:60].hex())

    if len(payload) < 1:
        return stations
    n = payload[0]; p = 1
    for _ in range(n):
        if p + 10 > len(payload):
            break
        mac = mac_to_str(payload[p:p+6])
        tx = decode_phy_rate(struct.unpack("<H", payload[p+6:p+8])[0])
        rx = decode_phy_rate(struct.unpack("<H", payload[p+8:p+10])[0])
        p += 10
        stations.append({"mac": mac, "plcmac": mac, "tx_rate": tx, "rx_rate": rx})
    return stations

def parse_mx_status_ind(data: bytes) -> dict | None:
    """Parse MEDIAXTREAM periodic status indication (0x6046).

    The adapter broadcasts this every 2-5 seconds on 0x8912.
    Payload (after MX header):
      Bytes 0-3: status flags / device state
      Bytes 4-5 (LE): TX rate / 2 (multiply by 2 for PHY rate in Mbps)
      Bytes 6-7 (LE): RX rate / 2 (multiply by 2 for PHY rate in Mbps)
      Bytes 8+: additional state (LED, QoS, power saving indicators)
    """
    off = ETH_HDR + MX_MME_HDR
    payload = data[off:] if len(data) > off else b""
    if len(payload) < 8:
        return None
    src = mac_to_str(data[6:12])
    tx_raw = struct.unpack("<H", payload[4:6])[0]
    rx_raw = struct.unpack("<H", payload[6:8])[0]
    result = {
        "mac": src, "plcmac": src,
        "tx_rate": tx_raw * 2,
        "rx_rate": rx_raw * 2,
    }
    # Log extended payload for state analysis (first time per session)
    if len(payload) > 8:
        _LOGGER.debug("0x6046 full payload from %s (%d bytes): %s",
                      src, len(payload), payload.hex())
    return result


def parse_qca_nw_stats_cnf(data: bytes) -> list[dict]:
    """Parse Qualcomm VS_NW_STATS.CNF (0xA049) from 0x88E1."""
    stations = []
    off = ETH_HDR + HPAV_MME_HDR + 3  # Skip QCA OUI
    if len(data) < off + 1:
        return stations
    n = data[off]; off += 1
    for _ in range(n):
        if off + 10 > len(data):
            break
        mac = mac_to_str(data[off:off+6])
        tx = struct.unpack("<H", data[off+6:off+8])[0]
        rx = struct.unpack("<H", data[off+8:off+10])[0]
        off += 10
        stations.append({"mac": mac, "plcmac": mac, "tx_rate": tx, "rx_rate": rx})
    return stations


def parse_qca_nw_info_cnf(data: bytes) -> tuple[int, int] | None:
    """Parse the responder's PHY link rate from QCA VS_NW_INFO.CNF (0xA039).

    Confirmed on QCA7420 (2-adapter network, captured from tpPLC): the
    responder's average PHY data rates to its peer are the **last two 4-byte
    little-endian** values of the confirm — TX at end-8, RX at end-4 (Mbit/s).
    e.g. tail ``...7c000000 8c000000`` => TX=124, RX=140. Returns (tx, rx) or
    None if the values are out of range. The full payload is logged at debug.
    """
    payload = data[ETH_HDR:]
    _LOGGER.debug("QCA VS_NW_INFO payload (%d bytes): %s",
                  len(payload), payload.hex())
    if len(payload) < 8:
        return None
    tx = struct.unpack_from("<I", payload, len(payload) - 8)[0]
    rx = struct.unpack_from("<I", payload, len(payload) - 4)[0]
    if 1 <= tx <= 5000 and 1 <= rx <= 5000:
        # The raw field is the firmware's average PHY data rate; tpPLC displays
        # floor(raw * 21/16). Apply the same factor so HA matches the app
        # (verified: 124->162, 140->183, 141->185, 142->186).
        return (tx * 21 // 16, rx * 21 // 16)
    return None


# ══════════════════════════════════════════════════════════
#  Main Class
# ══════════════════════════════════════════════════════════

class HomeplugAV:
    """Dual-protocol HomePlug AV communication.

    Opens TWO raw sockets:
      - 0x88E1 for standard HomePlug AV (CC_DISCOVER_LIST works everywhere)
      - 0x8912 for MEDIAXTREAM/Broadcom (NW_INFO, GET_PARAM, etc.)

    Auto-detects chipset based on which protocol responds.
    """

    def __init__(self, interface: str | None = None):
        self.interface = interface or _find_interface()
        self._sock_hpav: socket.socket | None = None
        self._sock_mx: socket.socket | None = None
        self._src_mac = b"\x00" * 6
        self._seq = 1
        self._chipset = "unknown"  # "broadcom" or "qualcomm"
        self._led_success_macs: set[str] = set()
        # MACs whose firmware/model we already tried — avoids re-querying
        # (and timing out on) device info every single poll.
        self._info_attempted: set[str] = set()
        # Serializes the socket-using public methods across executor threads.
        self._lock = threading.RLock()

    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq % 255) + 1
        return s

    def _open_socket(self, ethertype: int, retries: int = 2) -> socket.socket:
        """Open a raw socket with retry on transient errors."""
        if not self.interface:
            raise OSError("No Ethernet interface found")
        last_err: Exception | None = None
        for attempt in range(1 + retries):
            try:
                s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                                  socket.htons(ethertype))
                s.bind((self.interface, ethertype))
                self._src_mac = get_iface_mac(self.interface)
                return s
            except OSError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    _LOGGER.debug("Socket open retry %d for 0x%04X: %s",
                                  attempt + 1, ethertype, e)
        raise last_err  # type: ignore[misc]

    def _open_hpav(self) -> socket.socket:
        if self._sock_hpav:
            return self._sock_hpav
        self._sock_hpav = self._open_socket(ETHERTYPE_HPAV)
        return self._sock_hpav

    def _open_mx(self) -> socket.socket:
        if self._sock_mx:
            return self._sock_mx
        self._sock_mx = self._open_socket(ETHERTYPE_MEDIAXTREAM)
        return self._sock_mx

    def _close(self):
        for attr in ("_sock_hpav", "_sock_mx"):
            s = getattr(self, attr, None)
            if s:
                try:
                    s.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    def _send_recv(self, sock: socket.socket, frame: bytes,
                   timeout: float = 3.0,
                   expected_src: str | None = None,
                   stop_on: frozenset[int] | None = None,
                   ) -> list[tuple[int, str, bytes]]:
        """Send frame, collect responses until timeout.

        If expected_src is given (unicast command), drop frames that do not
        originate from that MAC. This prevents unrelated background traffic
        (e.g. 0x6046 status broadcasts from other adapters) from being
        misinterpreted as a response to our request.

        If stop_on is given, return as soon as a response with one of those
        MMTYPEs is received. The 0x8912 bus carries heavy background traffic
        (0xA070 beacons, 0x6046 status), so without this every control command
        would block for the full timeout — three sequential LED writes then
        exceed the coordinator's 10s budget and the switch reports a failure.
        """
        sock.settimeout(timeout)
        sock.send(frame)
        results = []
        expect = expected_src.upper() if expected_src else None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock.settimeout(max(0.05, deadline - time.monotonic()))
                data = sock.recv(4096)
                if len(data) < ETH_HDR + 3:
                    continue
                mmtype = struct.unpack("<H", data[ETH_HDR+1:ETH_HDR+3])[0]
                src = mac_to_str(data[6:12])
                if expect is not None and src.upper() != expect:
                    continue
                results.append((mmtype, src, data))
                if stop_on is not None and mmtype in stop_on:
                    break
            except socket.timeout:
                break
            except OSError:
                break
        return results

    def _listen(self, sock: socket.socket,
                timeout: float = 3.0) -> list[tuple[int, str, bytes]]:
        """Listen without sending."""
        results = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sock.settimeout(max(0.05, deadline - time.monotonic()))
                data = sock.recv(4096)
                if len(data) < ETH_HDR + 3:
                    continue
                mmtype = struct.unpack("<H", data[ETH_HDR+1:ETH_HDR+3])[0]
                src = mac_to_str(data[6:12])
                results.append((mmtype, src, data))
            except socket.timeout:
                break
            except OSError:
                break
        return results

    def _new_dev(self, mac: str) -> dict:
        return {"mac": mac, "plcmac": mac, "model": "",
                "firmware_ver": "", "tx_rate": 0, "rx_rate": 0}

    def _annotate_capabilities(self, devices: dict[str, dict]) -> None:
        """Attach capability hints per adapter for diagnostics."""
        for mac, dev in devices.items():
            dev["chipset"] = self._chipset
            dev["capabilities"] = {
                "supports_standard_discovery": True,
                "supports_vendor_mx": self._chipset == "broadcom",
                "supports_vendor_qca": self._chipset == "qualcomm",
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
                    qca = True
                    rates = parse_qca_nw_info_cnf(data)
                    if rates and src in devices:
                        devices[src]["tx_rate"], devices[src]["rx_rate"] = rates
                        found = True
                        _LOGGER.info("VS_NW_INFO: %s TX=%d RX=%d",
                                     src, rates[0], rates[1])
        if qca:
            if not found:
                _LOGGER.info(
                    "QCA VS_NW_INFO answered but no PHY rate parsed "
                    "(idle link or unconfirmed layout). Use Diagnose for raw bytes.")
            return found

        # ── A: MX NW_STATS (0xA02C) — primary Broadcom rate method ──
        # This is the dedicated PHY rate request for Broadcom chipsets.
        # Unicast to each adapter, then broadcast as fallback.
        _LOGGER.debug("Trying MX NW_STATS (0xA02C) unicast...")
        for mac in list(devices.keys()):
            dst = mac_to_bytes(mac)
            frame = build_mx_frame(dst, self._src_mac,
                                   MX_NW_STATS_REQ,
                                   seq=self._next_seq())
            for mmtype, src, data in self._send_recv(
                    self._sock_mx, frame, 2.0):
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

            if self._chipset in ("broadcom", "unknown"):
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

            if self._chipset in ("qualcomm", "unknown"):
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

    # ── State Query ───────────────────────────────────────

    @_locked
    def query_device_states(self, macs: list[str]) -> dict[str, dict]:
        """Query LED, QoS, and power saving state from each adapter.

        Returns {mac: {"led": bool|None, "qos": str|None, "power_saving": bool|None}}.

        Read via Get Parameter (0xA05C): LED from param 0x003F (LED Options,
        byte 3 bit 0x10), power saving from param 0x0029 (bit 0x8000), and QoS
        by matching the priority-map (0x0069) CAP bytes to a known mode. Anything
        that cannot be parsed confidently stays None so the coordinator keeps its
        default instead of showing a guess.
        """
        states: dict[str, dict] = {mac: {"led": None, "qos": None,
                                          "power_saving": None} for mac in macs}
        try:
            self._open_mx()
        except (PermissionError, OSError) as e:
            _LOGGER.debug("Cannot open MX socket for state query: %s", e)
            return states
        try:
            for mac in macs:
                # LED state lives in LED Options (0x003F): byte 3, bit 0x10.
                # tpPLC capture: ...01 12 = on, ...01 02 = off.
                led_opt = self._get_param_value(mac, PARAM_LED_OPTIONS)
                if led_opt and len(led_opt) >= 4:
                    states[mac]["led"] = bool(led_opt[3] & 0x10)
                # Power saving = bit 0x8000 of the 0x0029 standby value.
                ps_val = self._get_param_value(mac, PARAM_POWER_STANDBY)
                if ps_val and len(ps_val) >= 2:
                    standby = struct.unpack("<H", ps_val[0:2])[0]
                    states[mac]["power_saving"] = bool(standby & 0x8000)
                # QoS = match the priority map's CAP bytes (0x0069) to a mode.
                qos_table = self._get_param_value(mac, PARAM_QOS_PRIORITY_MAP)
                if qos_table and len(qos_table) > self._QOS_CAP_OFFSETS[-1]:
                    caps = bytes(qos_table[o] for o in self._QOS_CAP_OFFSETS)
                    for mode, pattern in self._QOS_CAP_MAP.items():
                        if caps == pattern:
                            states[mac]["qos"] = mode
                            break
        finally:
            self._close()
        return states

    def _get_param_value(self, mac: str, param_id: int,
                         timeout: float = 1.5) -> bytes | None:
        """Read a parameter via Get Parameter (0xA05C). Returns value or None."""
        dst = mac_to_bytes(mac)
        frame = build_mx_frame(dst, self._src_mac, MX_GET_PARAM_REQ,
                               seq=self._next_seq(),
                               payload=struct.pack("<H", param_id))
        for mmtype, src, data in self._send_recv(
                self._sock_mx, frame, timeout, expected_src=mac,
                stop_on=frozenset((MX_GET_PARAM_CNF,))):
            if mmtype != MX_GET_PARAM_CNF:
                continue
            val = parse_mx_get_param_cnf(data)
            if val:
                return val
        return None

    def _parse_state_from_param(self, state: dict, param_id: int,
                                 val: bytes) -> None:
        """Try to extract LED/QoS/PS state from a GET_PARAM response.

        Note: GET_PARAM IDs for device settings are not yet confirmed.
        0x0040 returns 00210001 on TL-PA7017 regardless of LED state --
        it's NOT the LED state. All values are logged for future analysis.
        """
        # Currently no confirmed state mapping -- just log for analysis.
        # Once correct param IDs are identified via Wireshark captures
        # of tpPLC reading state, add mappings here.
        pass

    def _parse_state_from_status(self, state: dict, data: bytes) -> None:
        """Try to extract state bits from 0x6046 status indication payload."""
        off = ETH_HDR + MX_MME_HDR
        payload = data[off:] if len(data) > off else b""
        if len(payload) < 12:
            return

        # Bytes 0-3 often contain status flags
        flags = struct.unpack("<I", payload[0:4])[0]
        _LOGGER.debug("0x6046 status flags: 0x%08X, extra bytes: %s",
                      flags, payload[8:min(len(payload), 24)].hex())

        # Common Broadcom patterns in 0x6046 status:
        # Bit patterns for LED/PS state vary by firmware.
        # Log for now, will refine once patterns are confirmed.
        if len(payload) >= 16:
            # Some firmware versions include LED state in byte 8 or 12
            byte8 = payload[8]
            byte12 = payload[12] if len(payload) > 12 else 0
            _LOGGER.debug("0x6046 state candidates: byte8=0x%02X byte12=0x%02X",
                          byte8, byte12)

    # ── LED Control ──────────────────────────────────────

    # LED control is a MEDIAXTREAM *Set Parameter* (0xA058) sequence, confirmed
    # byte-for-byte from a tpPLC capture on TL-PA7017 (BCM60355). Toggling the
    # LED writes TWO parameters and then commits with an Apply (0xA020):
    #   1. param 0x0095 (2-byte): 0x0000 = on,            0x0047 = off
    #   2. param 0x003F (4-byte LED Options): 02 a0 01 12 = on, 02 a0 01 02 = off
    #      (byte 3 bit 0x10 is the LED-enabled flag)
    #   3. Apply 0xA020 (empty) -> 0xA021
    # The previous build only sent a single (mis-framed) 0x0095 write and no
    # Apply, so nothing happened. param 0x003E exists but is read-only here.
    _LED_AUX_VALUE   = {True: bytes.fromhex("0000"),     False: bytes.fromhex("4700")}
    _LED_OPTS_VALUE  = {True: bytes.fromhex("02a00112"), False: bytes.fromhex("02a00102")}

    # ── QCA (AV500) LED via PIB read-modify-write ──────────
    # See PROTOCOL.md §9. EXPERIMENTAL: the write-open carries a whole-PIB
    # checksum we cannot reproduce offline; if the firmware validates it the
    # write is a harmless no-op (the read-back below then reports failure).

    def _qca_read_chunk(self, dst: bytes, mac: str, offset: int,
                        clen: int) -> bytes | None:
        """Read one PIB chunk via module-op read (0xA0B0, op 0x0100)."""
        hdr = bytearray(_QCA_HDR_READ[:21])
        struct.pack_into("<H", hdr, 17, clen)
        struct.pack_into("<H", hdr, 19, offset)
        frame = build_qca_mod_frame(dst, self._src_mac, bytes(hdr))
        for mmtype, src, data in self._send_recv(
                self._sock_hpav, frame, 2.0, expected_src=mac,
                stop_on=frozenset((VS_MOD_OP_CNF,))):
            if mmtype != VS_MOD_OP_CNF:
                continue
            pl = data[ETH_HDR + 6:]            # after MMV+MMTYPE+OUI
            # Read CONFIRM packs offset/data one byte earlier than the write
            # REQUEST: offset@23, data@25 (verified against the capture).
            if len(pl) < 25:
                continue
            if struct.unpack_from("<H", pl, 23)[0] != offset:
                continue
            return pl[25:25 + clen]
        return None

    def _qca_read_pib(self, mac: str) -> bytes | None:
        """Read the full PIB (chunked). Returns QCA_PIB_SIZE bytes or None."""
        dst = mac_to_bytes(mac)
        pib = bytearray()
        offset = 0
        while offset < QCA_PIB_SIZE:
            clen = min(QCA_PIB_CHUNK, QCA_PIB_SIZE - offset)
            chunk = self._qca_read_chunk(dst, mac, offset, clen)
            if not chunk or len(chunk) < clen:
                _LOGGER.debug("QCA PIB read failed at 0x%04X (got %s)",
                              offset, len(chunk) if chunk else 0)
                return None
            pib += chunk[:clen]
            offset += clen
        return bytes(pib)

    def _qca_mod_ack(self, dst: bytes, mac: str, payload: bytes) -> bool:
        """Send a module-op frame and wait for its 0xA0B1 confirmation."""
        frame = build_qca_mod_frame(dst, self._src_mac, payload)
        for mmtype, src, data in self._send_recv(
                self._sock_hpav, frame, 2.0, expected_src=mac,
                stop_on=frozenset((VS_MOD_OP_CNF,))):
            if mmtype == VS_MOD_OP_CNF:
                return True
        return False

    def _qca_write_pib(self, mac: str, pib: bytes) -> bool:
        """Write the full PIB back: open -> data chunks -> close."""
        dst = mac_to_bytes(mac)
        token = struct.pack("<H", random.randint(1, 0xFFFE))

        op = bytearray(_QCA_HDR_OPEN)
        op[13:15] = token
        struct.pack_into("<H", op, 22, len(pib))
        struct.pack_into("<I", op, 26, zlib.crc32(pib) & 0xFFFFFFFF)
        if not self._qca_mod_ack(dst, mac, bytes(op)):
            _LOGGER.debug("QCA PIB write: no ack to open from %s", mac)
            return False

        offset = 0
        while offset < len(pib):
            clen = min(QCA_PIB_CHUNK, len(pib) - offset)
            hdr = bytearray(_QCA_HDR_DATA[:26])
            hdr[13:15] = token
            struct.pack_into("<H", hdr, 22, clen)
            struct.pack_into("<H", hdr, 24, offset)
            if not self._qca_mod_ack(dst, mac, bytes(hdr) + pib[offset:offset + clen]):
                _LOGGER.debug("QCA PIB write: no ack at 0x%04X from %s", offset, mac)
                return False
            offset += clen

        cl = bytearray(_QCA_HDR_CLOSE)
        cl[13:15] = token
        return self._qca_mod_ack(dst, mac, bytes(cl))

    def _set_led_qualcomm(self, mac: str, on: bool) -> bool:
        """Toggle the AV500 LED by flipping the 10-byte LED table in the PIB."""
        pib = self._qca_read_pib(mac)
        if not pib or len(pib) != QCA_PIB_SIZE:
            _LOGGER.debug("QCA LED: could not read PIB from %s "
                          "(non-QCA adapter or no response)", mac)
            return False

        # Safety guard: the LED table must currently look like the captured one
        # (all bytes 0x00/0x01). If not, the offsets may not apply to this
        # firmware — refuse to write rather than risk corrupting the PIB.
        current = {pib[o] for o in QCA_LED_OFFSETS}
        if not current <= {0x00, 0x01}:
            _LOGGER.warning("QCA LED: unexpected LED-table bytes %s on %s; "
                            "aborting to avoid PIB corruption",
                            sorted(current), mac)
            return False

        value = 0x00 if on else 0x01
        buf = bytearray(pib)
        for o in QCA_LED_OFFSETS:
            buf[o] = value
        if not self._qca_write_pib(mac, bytes(buf)):
            return False

        # Verify by re-reading the chunk that holds the LED table.
        dst = mac_to_bytes(mac)
        cstart = (min(QCA_LED_OFFSETS) // QCA_PIB_CHUNK) * QCA_PIB_CHUNK
        clen = min(QCA_PIB_CHUNK, QCA_PIB_SIZE - cstart)
        chunk = self._qca_read_chunk(dst, mac, cstart, clen)
        if chunk and all(chunk[o - cstart] == value for o in QCA_LED_OFFSETS):
            _LOGGER.info("QCA LED %s confirmed on %s",
                         "ON" if on else "OFF", mac)
            return True
        _LOGGER.warning("QCA LED %s: write sent but not confirmed on %s "
                        "(firmware may validate the PIB checksum)",
                        "ON" if on else "OFF", mac)
        return False

    def _set_led_broadcom(self, mac: str, on: bool) -> bool:
        """Set LED via the captured tpPLC Set Parameter + Apply sequence."""
        dst = mac_to_bytes(mac)
        state = "ON" if on else "OFF"

        # 1) LED companion parameter 0x0095 (2-byte value).
        f1 = build_mx_set_param(dst, self._src_mac, PARAM_LED_AUX,
                                self._LED_AUX_VALUE[on], octets_per_element=2,
                                seq=self._next_seq())
        resp1 = self._send_recv(self._sock_mx, f1, 2.0, expected_src=mac,
                                stop_on=_MX_ACTION_OK)
        # No reply at all to the first write means this adapter does not speak
        # MEDIAXTREAM (e.g. a Qualcomm AV500). Bail out now instead of running
        # the remaining writes + Apply into their full timeouts.
        if not resp1:
            _LOGGER.debug("LED %s: %s did not answer Set Parameter "
                          "(non-Broadcom adapter?)", state, mac)
            return False

        # 2) LED Options 0x003F (4-byte value) — the actual enable flag.
        f2 = build_mx_set_param(dst, self._src_mac, PARAM_LED_OPTIONS,
                                self._LED_OPTS_VALUE[on], octets_per_element=4,
                                seq=self._next_seq())
        resp2 = self._send_recv(self._sock_mx, f2, 2.0, expected_src=mac,
                                stop_on=_MX_ACTION_OK)
        opts_ok = any(m in _MX_ACTION_OK for m, _, _ in resp2)

        # 3) Apply / commit (0xA020) -> 0xA021.
        f3 = build_mx_frame(dst, self._src_mac, MX_APPLY_REQ, seq=self._next_seq())
        resp3 = self._send_recv(self._sock_mx, f3, 2.0, expected_src=mac,
                                stop_on=_MX_APPLY_OK)
        apply_ok = any(m == MX_APPLY_CNF for m, _, _ in resp3)

        if opts_ok or apply_ok:
            _LOGGER.info("LED %s for %s (opts_cnf=%s apply_cnf=%s)",
                         state, mac, opts_ok, apply_ok)
            return True
        seen = [f"0x{m:04X}" for m, _, _ in (resp2 + resp3)]
        _LOGGER.debug("LED %s: no Set Parameter/Apply CNF from %s (got %s)",
                      state, mac, seen or "nothing")
        return False

    def _set_power_saving_broadcom(self, mac: str, on: bool) -> bool:
        """Set power saving via Set Parameter 0xA058 / param 0x0029 + Apply.

        Confirmed from a tpPLC capture (TL-PA7017): param 0x0029 is a 16-bit
        value whose low 15 bits are the standby timeout (seconds) and whose top
        bit (0x8000) is the power-saving enabled flag — same flag scheme as the
        PHY rate field. tpPLC also clears companion param 0x0074 when disabling.
          OFF: 0x0029 = 0x012C (300s, flag clear) + 0x0074 = 0
          ON : 0x0029 = 0x812C (300s, flag set)
        """
        dst = mac_to_bytes(mac)
        # Preserve the configured standby timeout; default to tpPLC's 300 s.
        timeout = 300
        cur = self._get_param_value(mac, PARAM_POWER_STANDBY)
        if cur and len(cur) >= 2:
            t = struct.unpack("<H", cur[0:2])[0] & 0x7FFF
            if t:
                timeout = t
        value = (timeout & 0x7FFF) | (0x8000 if on else 0)

        f1 = build_mx_set_param(dst, self._src_mac, PARAM_POWER_STANDBY,
                                struct.pack("<H", value), octets_per_element=2,
                                seq=self._next_seq())
        resp1 = self._send_recv(self._sock_mx, f1, 2.0, expected_src=mac,
                                stop_on=_MX_ACTION_OK)
        set_ok = any(m in _MX_ACTION_OK for m, _, _ in resp1)
        # No reply -> not a MEDIAXTREAM (Broadcom) adapter; don't run the rest.
        if not resp1:
            _LOGGER.debug("Power saving %s: %s did not answer Set Parameter "
                          "(non-Broadcom adapter?)", "ON" if on else "OFF", mac)
            return False

        if not on:
            faux = build_mx_set_param(dst, self._src_mac, PARAM_POWER_STANDBY_AUX,
                                      b"\x00", octets_per_element=1,
                                      seq=self._next_seq())
            self._send_recv(self._sock_mx, faux, 2.0, expected_src=mac,
                            stop_on=_MX_ACTION_OK)

        f2 = build_mx_frame(dst, self._src_mac, MX_APPLY_REQ, seq=self._next_seq())
        resp2 = self._send_recv(self._sock_mx, f2, 2.0, expected_src=mac,
                                stop_on=_MX_APPLY_OK)
        apply_ok = any(m == MX_APPLY_CNF for m, _, _ in resp2)

        if set_ok or apply_ok:
            _LOGGER.info("Power saving %s for %s (standby=%ds set_cnf=%s apply_cnf=%s)",
                         "ON" if on else "OFF", mac, timeout, set_ok, apply_ok)
            return True
        _LOGGER.debug("Power saving %s: no Set Parameter/Apply CNF from %s",
                      "ON" if on else "OFF", mac)
        return False

    @_locked
    def set_led(self, mac: str, on: bool, timeout: float = 2.0) -> bool:
        """Set LED on a specific adapter (by MAC)."""
        try:
            try:
                self._open_hpav()
                self._open_mx()
            except (PermissionError, OSError):
                return False

            # Try Broadcom MEDIAXTREAM first (most common for modern TP-Link)
            if self._chipset in ("broadcom", "unknown"):
                if self._set_led_broadcom(mac, on):
                    self._led_success_macs.add(mac.upper())
                    return True
                # Retry once after short delay (adapter may be busy)
                time.sleep(0.5)
                if self._set_led_broadcom(mac, on):
                    self._led_success_macs.add(mac.upper())
                    return True

            # Qualcomm (QCA / AV500): LED lives in the PIB. We do a careful
            # read-modify-write that flips only the 10-byte LED table and writes
            # every other byte back untouched (see _set_led_qualcomm).
            if self._chipset in ("qualcomm", "unknown"):
                if self._set_led_qualcomm(mac, on):
                    self._led_success_macs.add(mac.upper())
                    return True

            _LOGGER.warning(
                "LED: no response from %s. "
                "LED control may not be supported via Layer 2.", mac)
            return False
        except Exception as err:
            _LOGGER.exception("LED control exception for %s: %s", mac, err)
            return False
        finally:
            self._close()

    @_locked
    def set_power_saving(self, mac: str, on: bool) -> bool:
        """Set power saving mode on a specific adapter (by MAC)."""
        try:
            try:
                self._open_hpav()
                self._open_mx()
            except (PermissionError, OSError):
                return False

            if self._chipset in ("broadcom", "unknown"):
                return self._set_power_saving_broadcom(mac, on)

            _LOGGER.warning("Power saving not supported for chipset %s", self._chipset)
            return False
        except Exception as err:
            _LOGGER.exception("Power saving exception for %s: %s", mac, err)
            return False
        finally:
            self._close()

    # ── QoS Priority Control ────────────────────────────

    # QoS priority is a Broadcom "priority mapping" table (Set Parameter, param
    # 0x0069), confirmed from a tpPLC capture on TL-PA7017. tpPLC reads the
    # ~1000-byte table (Get Parameter 0xA05C), rewrites the 8 channel-access-
    # priority (CAP) bytes and writes it back (0xA058) — no Apply needed.
    # CAP encoding: 0x18=CAP0 (low) .. 0x78=CAP3 (high), step 0x20.
    # The 8 CAP bytes sit at these offsets within the table value, and each
    # mode writes this exact 8-byte pattern (captured byte-for-byte):
    _QOS_CAP_OFFSETS = (2, 27, 52, 77, 102, 127, 152, 177)
    _QOS_CAP_MAP = {
        "internet":    bytes((0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18)),
        "audio_video": bytes((0x58, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78)),
        "gaming":      bytes((0x38, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78)),
        "voip":        bytes((0x78, 0x18, 0x18, 0x38, 0x58, 0x58, 0x78, 0x78)),
    }

    def _set_qos_broadcom(self, mac: str, priority: str) -> bool:
        """Set QoS priority by read-modify-writing the 0x0069 priority map."""
        caps = self._QOS_CAP_MAP.get(priority)
        if caps is None:
            _LOGGER.error("Unknown QoS priority: %s", priority)
            return False

        table = self._get_param_value(mac, PARAM_QOS_PRIORITY_MAP)
        if not table or len(table) <= self._QOS_CAP_OFFSETS[-1]:
            _LOGGER.debug("QoS: could not read priority map from %s (got %s bytes)",
                          mac, len(table) if table else 0)
            return False

        buf = bytearray(table)
        for off, cap in zip(self._QOS_CAP_OFFSETS, caps):
            buf[off] = cap

        dst = mac_to_bytes(mac)
        frame = build_mx_set_param(dst, self._src_mac, PARAM_QOS_PRIORITY_MAP,
                                   bytes(buf), octets_per_element=1,
                                   seq=self._next_seq())
        for mmtype, src, data in self._send_recv(
                self._sock_mx, frame, 2.5, expected_src=mac, stop_on=_MX_ACTION_OK):
            if mmtype in _MX_ACTION_OK:
                _LOGGER.info("QoS priority set to '%s' for %s", priority, mac)
                return True

        _LOGGER.warning("QoS: no confirmation from %s for priority '%s'", mac, priority)
        return False

    def _set_qos_qualcomm(self, mac: str, priority: str) -> bool:
        """Set QoS on a QCA (AV500) adapter via PIB read-modify-write.

        Writes the 2-byte QoS value at QCA_QOS_OFFSET and maintains the two
        XOR checksums by XOR-ing the value delta into them (see PROTOCOL.md).
        Reads the device's own PIB first, so the checksum stays correct without
        recomputing it from scratch.
        """
        new = QCA_QOS_VALUES.get(priority)
        if new is None:
            _LOGGER.error("Unknown QoS priority: %s", priority)
            return False
        pib = self._qca_read_pib(mac)
        if not pib or len(pib) != QCA_PIB_SIZE:
            _LOGGER.debug("QCA QoS: could not read PIB from %s", mac)
            return False

        buf = bytearray(pib)
        old = struct.unpack_from("<H", buf, QCA_QOS_OFFSET)[0]
        if old == new:
            _LOGGER.info("QCA QoS already '%s' on %s", priority, mac)
            return True
        delta = old ^ new
        struct.pack_into("<H", buf, QCA_QOS_OFFSET, new)
        for coff in QCA_QOS_CKSUM_OFFSETS:
            cur = struct.unpack_from("<H", buf, coff)[0]
            struct.pack_into("<H", buf, coff, cur ^ delta)

        if not self._qca_write_pib(mac, bytes(buf)):
            return False

        # Verify by re-reading the chunk holding the QoS value.
        dst = mac_to_bytes(mac)
        cstart = (QCA_QOS_OFFSET // QCA_PIB_CHUNK) * QCA_PIB_CHUNK
        clen = min(QCA_PIB_CHUNK, QCA_PIB_SIZE - cstart)
        chunk = self._qca_read_chunk(dst, mac, cstart, clen)
        if chunk and struct.unpack_from("<H", chunk, QCA_QOS_OFFSET - cstart)[0] == new:
            _LOGGER.info("QCA QoS '%s' confirmed on %s", priority, mac)
            return True
        _LOGGER.warning("QCA QoS '%s': write not confirmed on %s", priority, mac)
        return False

    @_locked
    def set_qos_priority(self, mac: str, priority: str) -> bool:
        """Set QoS priority on a specific adapter (by MAC)."""
        try:
            try:
                self._open_hpav()
                self._open_mx()
            except (PermissionError, OSError):
                return False

            if self._chipset == "qualcomm":
                return self._set_qos_qualcomm(mac, priority)
            if self._chipset in ("broadcom", "unknown"):
                if self._set_qos_broadcom(mac, priority):
                    return True
                # An "unknown" chipset might be QCA (no MEDIAXTREAM reply).
                return self._set_qos_qualcomm(mac, priority)

            _LOGGER.warning("QoS not supported for chipset %s", self._chipset)
            return False
        except Exception as err:
            _LOGGER.exception("QoS exception for %s: %s", mac, err)
            return False
        finally:
            self._close()

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


# ══════════════════════════════════════════════════════════
#  Async Wrappers
# ══════════════════════════════════════════════════════════

async def async_discover(interface: str | None = None,
                         timeout: float = 5.0) -> list[dict]:
    hp = HomeplugAV(interface)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, hp.discover, timeout)

async def async_diagnose(interface: str | None = None,
                         timeout: float = 10.0) -> str:
    hp = HomeplugAV(interface)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, hp.diagnose, timeout)

def find_interface() -> str | None:
    return _find_interface()

def is_available() -> bool:
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                          socket.htons(ETHERTYPE_HPAV))
        s.close()
        return True
    except (PermissionError, OSError):
        return False
