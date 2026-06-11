"""Confirmation/indication parsers and PIB checksum helpers."""
import struct

from .const import (
    ETH_HDR,
    HPAV_MME_HDR,
    MX_MME_HDR,
    QCA_CKSUM_OFFSETS,
    _LOGGER,
)
from .frames import mac_to_str

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
    # Accept a partial reply (one direction may be 0 / idle); the caller fills a
    # 0 from the peer's complementary direction. The raw field is the firmware's
    # average PHY rate; tpPLC displays floor(raw * 21/16), so apply the same
    # factor (verified: 124->162, 140->183, 141->185, 142->186).
    if tx > 5000 or rx > 5000 or (tx == 0 and rx == 0):
        return None
    return (tx * 21 // 16, rx * 21 // 16)


def qca_pib_checksum(pib: bytes) -> bytes:
    """The 4-byte PIB checksum the write-open carries to *apply* the change.

    It is the standard open-plc-utils ``checksum32``: the complement of the
    32-bit XOR-fold of the whole PIB, stored little-endian. Verified against
    every captured open command on both adapters (LED, QoS, power saving,
    start). Sending a wrong value makes the adapter store the bytes but never
    apply them (close returns status ``31 00 30`` instead of ``00 00 00``).
    """
    fold = 0
    for i in range(0, len(pib) - 3, 4):
        fold ^= struct.unpack_from("<I", pib, i)[0]
    return struct.pack("<I", (~fold) & 0xFFFFFFFF)


def qca_pib_set_byte(buf: bytearray, offset: int, value: int) -> None:
    """Set a PIB byte and keep the two QCA XOR checksums valid.

    A byte at offset ``o`` folds into checksum byte ``o % 4`` of both fields
    (0x0374, 0x03BC) — verified across QoS and power-saving captures, so this
    reproduces tpPLC's checksum bytes exactly.
    """
    old = buf[offset]
    if old == value:
        return
    buf[offset] = value
    idx = offset & 3
    for coff in QCA_CKSUM_OFFSETS:
        buf[coff + idx] ^= old ^ value

__all__ = [
    "decode_phy_rate",
    "parse_discover_cnf",
    "parse_mx_discover_cnf",
    "parse_mx_get_param_cnf",
    "parse_mx_nw_info_cnf",
    "parse_mx_nw_stats_cnf",
    "parse_mx_status_ind",
    "parse_qca_nw_info_cnf",
    "parse_qca_nw_stats_cnf",
    "qca_pib_checksum",
    "qca_pib_set_byte",
]
