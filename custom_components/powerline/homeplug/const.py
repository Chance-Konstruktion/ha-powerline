"""Protocol constants, IDs and the @_locked serialization helper."""
import functools
import logging

_LOGGER = logging.getLogger("custom_components.powerline.homeplug")

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
# NOT used — a bad raw PIB write could lose the network key / brick the adapter.
# Control instead goes through the chunked module-op path (0xA0B0, below): a
# read-modify-write of the adapter's own PIB with the universal open checksum,
# which the firmware rejects cleanly if malformed (never half-applied).
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
# LED-behavior table: each LED is an 8-byte descriptor and byte[3] is the
# enable flag (0x01 = on, 0x00 = off). tpPLC's "LED off" toggles the enable
# byte of these 10 activity LEDs (the power LED stays on). Offsets verified
# byte-for-byte against tpPLC writes to BOTH adapters; the LED region is
# outside the 0x0374/0x03BC checksum coverage, so toggling it leaves those
# fields untouched (exactly what tpPLC does).
QCA_LED_OFFSETS = (0x1ED3, 0x1EFB, 0x1F03, 0x1F1B, 0x1F23,
                   0x1F2B, 0x1F43, 0x1F4B, 0x1F53, 0x1F6B)
# Config changes (QoS, power saving) also update two XOR-checksum fields. The
# checksum is XOR-linear: a PIB byte at offset o folds into checksum byte
# (o % 4) of BOTH fields. Verified across every QoS and power-saving capture
# on both adapters. We maintain the checksum by XOR-ing each changed byte's
# delta in at the right position — no need to know the algorithm, and it
# reproduces tpPLC's bytes exactly.
QCA_CKSUM_OFFSETS = (0x0374, 0x03BC)
# QoS priority = 2-byte value at 0x0ADC.
QCA_QOS_OFFSET = 0x0ADC
QCA_QOS_VALUES = {
    "internet":    0x0000,
    "gaming":      0xFA41,
    "audio_video": 0xFA42,
    "voip":        0xFA43,
}
# Power saving: these PIB bytes hold the captured "on" values; "off" = all zero.
QCA_POWERSAVE_BYTES = {0x2141: 0x08, 0x2142: 0x96,
                       0x21EA: 0x01, 0x2264: 0x01, 0x2273: 0x02}
QCA_POWERSAVE_PROBE = 0x21EA     # one of the bytes above, used for read-back
# Captured 32-byte module-op header templates; variable fields are patched in:
#   read : len@17(LE), off@19(LE)
#   open : token@13(LE), totlen@22(LE u32), checksum@26(LE u32)
#   close: token@13(LE)
# The data frame is built directly in _qca_write_pib (its header length depends
# on the chunk, and the data must sit at byte 28 — see the wire layout there).
_QCA_HDR_READ  = bytes.fromhex("0000000001000012000000000002700000780500000000000000000000000000")
_QCA_HDR_OPEN  = bytes.fromhex("000000000110008500000000002348000001027000007023000089c580ea0000")
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

__all__ = [
    "BROADCAST_MAC",
    "CC_DISCOVER_LIST_CNF",
    "CC_DISCOVER_LIST_REQ",
    "ETHERTYPE_HPAV",
    "ETHERTYPE_MEDIAXTREAM",
    "ETH_HDR",
    "ETH_MIN",
    "GIGLE_OUI",
    "HPAV_MME_HDR",
    "MX_ACTION_ALT_CNF",
    "MX_ACTION_CNF",
    "MX_ACTION_REQ",
    "MX_APPLY_CNF",
    "MX_APPLY_REQ",
    "MX_DISCOVER_CNF",
    "MX_DISCOVER_REQ",
    "MX_GET_PARAM_CNF",
    "MX_GET_PARAM_REQ",
    "MX_GET_STATION_CNF",
    "MX_GET_STATION_REQ",
    "MX_LINK_STATS_CNF",
    "MX_LINK_STATS_REQ",
    "MX_MME_HDR",
    "MX_NW_INFO_CNF",
    "MX_NW_INFO_REQ",
    "MX_NW_STATS_CNF",
    "MX_NW_STATS_REQ",
    "MX_SET_KEY_CNF",
    "MX_SET_KEY_REQ",
    "MX_SET_PARAM_CNF",
    "MX_SET_PARAM_REQ",
    "MX_STATUS_IND",
    "PARAM_LED_AUX",
    "PARAM_LED_CONTROL",
    "PARAM_LED_OPTIONS",
    "PARAM_MANUFACTURER_DAK1",
    "PARAM_MANUFACTURER_HFID",
    "PARAM_POWER_STANDBY",
    "PARAM_POWER_STANDBY_AUX",
    "PARAM_QOS_PRIORITY_MAP",
    "PARAM_USER_HFID",
    "PARAM_USER_NMK",
    "QCA_CKSUM_OFFSETS",
    "QCA_LED_OFFSETS",
    "QCA_OUI",
    "QCA_PIB_CHUNK",
    "QCA_PIB_SIZE",
    "QCA_POWERSAVE_BYTES",
    "QCA_POWERSAVE_PROBE",
    "QCA_QOS_OFFSET",
    "QCA_QOS_VALUES",
    "VS_LNK_STATS_CNF",
    "VS_LNK_STATS_REQ",
    "VS_MOD_OP_CNF",
    "VS_MOD_OP_REQ",
    "VS_NW_INFO_CNF",
    "VS_NW_INFO_REQ",
    "VS_NW_INFO_STATS_CNF",
    "VS_NW_INFO_STATS_REQ",
    "VS_NW_STATS_CNF",
    "VS_NW_STATS_REQ",
    "VS_RD_MOD_CNF",
    "VS_RD_MOD_REQ",
    "VS_SET_LED_BEHAVIOR",
    "VS_SW_VER_CNF",
    "VS_SW_VER_REQ",
]
