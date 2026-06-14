"""AVM FRITZ!Powerline (QCA7420 'Custom' firmware) support.

FRITZ!Powerline adapters use a Qualcomm QCA7420 chip, but ship AVM's own
"Custom" firmware. That firmware differs from the generic open-plc-utils / QCA
reference firmware in two ways that matter for control:

  1. The PIB is a DIFFERENT SIZE. A captured FRITZ!Powerline 510E PIB
     (firmware 1.5.0.2-24) is 9796 bytes (0x2644); the generic AV500 PIB the
     rest of this integration assumes is 9072 bytes (0x2370, ``QCA_PIB_SIZE``).
     The chunked PIB read in ``pib.py`` reads exactly ``QCA_PIB_SIZE`` bytes,
     so on a FRITZ adapter it returns the FIRST 9072 bytes of a 9796-byte
     image and *passes* the size check — a read-modify-write would then write
     only those 9072 bytes back, TRUNCATING the real PIB. The fixed
     LED / QoS / power-saving byte offsets also point at the wrong fields in
     AVM's layout.

  2. The firmware REFUSES the PIB apply. A real 510E acks the module-op open
     and every data chunk, but rejects the close/apply with status
     ``31 00 5d 00 00 00`` (logged as "QCA write REJECTED"). The AVM app never
     rewrites the PIB to toggle the LED; it uses dedicated AVM vendor MMEs
     (0xA06C / 0xA0D0 / 0xA200, observed but not yet fully decoded — see
     PROTOCOL.md §"FRITZ!Powerline").

Both points mean the generic QCA read-modify-write must NEVER run on a FRITZ
adapter: at best it is rejected, at worst (if a future firmware accepted it)
it would corrupt the PIB. Until the AVM vendor MMEs are decoded, this module's
job is to *detect* AVM adapters and keep the unsafe path away from them.
"""
from .const import _LOGGER

# AVM GmbH OUIs seen on FRITZ! hardware. The 510E in the reference capture is
# 5C:49:79. This list only needs the prefixes used by AVM powerline / router
# products; any AVM adapter on an OUI not listed here still falls through to
# the firmware-string detection below.
AVM_OUIS = frozenset({
    "5C:49:79",  # AVM GmbH (FRITZ!Powerline 510E, ...)
    "9C:C7:A6",  # AVM GmbH
    "38:10:D5",  # AVM GmbH
    "C0:25:06",  # AVM GmbH
    "E0:28:6D",  # AVM GmbH
    "00:04:0E",  # AVM GmbH (legacy)
})

# Substrings (upper-cased) in a QCA VS_SW_VER / HFID string that identify AVM
# "Custom" firmware, e.g. "$MAC-QCA7420-1.5.0.26-02-20200114-CS" or HFIDs like
# "FRITZ!Powerline 510E" / "AVM Powerline 510E".
AVM_FW_MARKERS = ("FRITZ", "AVM")

# AVM FRITZ!Powerline adapters expose a smaller feature set than the generic
# QCA/Broadcom adapters: only LED, restart and reset (per the FRITZ!Powerline
# app). They have NO QoS and NO power-saving controls, so those entities must
# not be created for them.
AVM_SUPPORTS_QOS = False
AVM_SUPPORTS_POWER_SAVING = False


def is_avm_mac(mac: str) -> bool:
    """True if a MAC's OUI belongs to AVM."""
    return bool(mac) and mac.upper()[0:8] in AVM_OUIS


def is_avm_device(mac: str, dev: dict | None = None) -> bool:
    """True if an adapter is AVM, by OUI or an AVM/FRITZ marker in its strings.

    ``dev`` is a discovery device dict (``model`` / ``firmware_ver``); either
    may carry "FRITZ!Powerline ..." or "AVM ...". Usable from the entity layer,
    which has no HomeplugAV instance.
    """
    if is_avm_mac(mac):
        return True
    if not dev:
        return False
    blob = f"{dev.get('model', '')} {dev.get('firmware_ver', '')}".upper()
    return any(marker in blob for marker in AVM_FW_MARKERS)


class FritzMixin:
    """Detect AVM FRITZ!Powerline adapters and keep unsafe PIB writes away."""

    def note_firmware(self, mac: str, firmware: str) -> None:
        """Remember a firmware/HFID string so is_fritz() can use it later."""
        if mac and firmware:
            self._fw_hint[mac.upper()] = firmware

    def is_fritz(self, mac: str, firmware: str | None = None) -> bool:
        """True if this adapter is an AVM FRITZ!Powerline (QCA7420/Custom).

        Detected by AVM OUI, or by an AVM/FRITZ marker in a firmware/HFID
        string (either passed in or remembered via ``note_firmware``).
        """
        if not mac:
            return False
        if is_avm_mac(mac):
            return True
        fw = (firmware or self._fw_hint.get(mac.upper(), "")).upper()
        return any(marker in fw for marker in AVM_FW_MARKERS)

    def _reject_fritz_pib_write(self, mac: str, what: str) -> None:
        """Log the standard reason a PIB write is suppressed on FRITZ."""
        _LOGGER.warning(
            "%s: %s is an AVM FRITZ!Powerline (QCA7420 'Custom' firmware). "
            "Its PIB size/layout differ from the generic QCA image and its "
            "firmware rejects PIB writes, so Layer-2 %s control is not yet "
            "supported. The generic PIB write is suppressed to avoid "
            "corrupting the adapter. See PROTOCOL.md for the AVM-MME roadmap.",
            what, mac, what)
