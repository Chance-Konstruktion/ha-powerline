"""MAC / interface helpers and HomePlug / MEDIAXTREAM frame builders."""
import os
import struct

from .const import (
    ETHERTYPE_HPAV,
    ETHERTYPE_MEDIAXTREAM,
    ETH_MIN,
    GIGLE_OUI,
    MX_SET_PARAM_REQ,
    QCA_OUI,
    VS_MOD_OP_REQ,
)

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

def build_qca_vs0_frame(dst: bytes, src: bytes, mmtype: int,
                        payload: bytes = b"") -> bytes:
    """Build a QCA vendor frame with MMV=0x00 and NO fragmentation field.

    Some QCA7420 MMEs (the module read/write 0xA0B0 and the reset 0xA01C) use
    MMV=0x00 with no FMI on the wire, unlike build_qca_frame() (MMV=0x01 + FMI).
    """
    frame = (
        dst + src
        + struct.pack("!H", ETHERTYPE_HPAV)
        + struct.pack("<BH", 0x00, mmtype)
        + QCA_OUI
        + payload
    )
    return frame.ljust(ETH_MIN, b"\x00")

def build_qca_mod_frame(dst: bytes, src: bytes, payload: bytes) -> bytes:
    """Build a QCA module-operation frame (0x88E1, MMTYPE 0xA0B0, OUI 00:b0:52)."""
    return build_qca_vs0_frame(dst, src, VS_MOD_OP_REQ, payload)

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

__all__ = [
    "build_hpav_frame",
    "build_mx_frame",
    "build_mx_set_param",
    "build_qca_frame",
    "build_qca_mod_frame",
    "build_qca_vs0_frame",
    "get_iface_mac",
    "mac_to_bytes",
    "mac_to_str",
]
