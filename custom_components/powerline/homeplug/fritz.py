"""AVM FRITZ!Powerline (QCA7420 'Custom' firmware) support.

FRITZ!Powerline adapters use a Qualcomm QCA7420 chip, but ship AVM's own
"Custom" firmware. Two things differ from the generic open-plc-utils / QCA
reference firmware:

  1. The PIB is a DIFFERENT SIZE. A captured FRITZ!Powerline 510E PIB
     (firmware 1.5.0.2-24) is 9796 bytes (0x2644); the generic AV500 PIB the
     rest of this integration assumes is 9072 bytes (0x2370, ``QCA_PIB_SIZE``).
     The generic chunked read/write use ``QCA_PIB_SIZE`` and so on a FRITZ
     adapter would read/write only the first 9072 bytes — TRUNCATING the real
     PIB and writing a wrong open length/checksum, which the firmware rejects
     (close status ``31 00 5d``). FRITZ control therefore uses the adapter's
     real PIB size (``AVM_PIB_SIZE``).

  2. The LED table sits at DIFFERENT offsets. AVM's larger PIB lays out 7 LED
     enable bytes at ``FRITZ_LED_OFFSETS`` (``0x1ED3 … 0x1F23``), not the
     generic ``QCA_LED_OFFSETS``. Writing the generic offsets on a FRITZ would
     hit the wrong fields.

LED on/off is the only PIB control AVM exposes: the FRITZ!Powerline app has no
QoS and no power-saving option, so those entities/writes stay disabled here.
The LED write below is reconstructed byte-for-byte from a capture of the AVM
app toggling the 510E LED — flipping ``FRITZ_LED_OFFSETS`` via
``qca_pib_set_byte`` (which keeps the 0x0374/0x03BC section checksums valid)
reproduces the app's two PIB images exactly, and the universal open checksum
matches what the app sends.
"""
from .const import _LOGGER
from .parsers import qca_pib_set_byte

# Verified PIB size of the FRITZ!Powerline 510E (firmware 1.5.0.2-24).
AVM_PIB_SIZE = 0x2644          # 9796 bytes

# AVM LED-enable bytes: 7 activity LEDs, 0x00 = on, 0x01 = off. These offsets
# are AVM-specific (the generic QCA_LED_OFFSETS differ) and outside the
# 0x0374/0x03BC checksum coverage only partially — qca_pib_set_byte folds each
# change into both section checksums, matching the AVM app byte-for-byte.
FRITZ_LED_OFFSETS = (0x1ED3, 0x1EFB, 0x1F03, 0x1F0B, 0x1F13, 0x1F1B, 0x1F23)
FRITZ_LED_ON = 0x00
FRITZ_LED_OFF = 0x01



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
        """Log why a control write is unavailable on FRITZ (QoS/power-saving)."""
        _LOGGER.warning(
            "%s: %s is an AVM FRITZ!Powerline — the device has no %s control "
            "(the FRITZ!Powerline app only offers LED, restart and reset), so "
            "this write is not performed.", what, mac, what)

    def _set_led_fritz(self, mac: str, on: bool) -> bool:
        """Toggle the LED on an AVM FRITZ!Powerline via a full-size PIB RMW.

        Uses the adapter's real PIB size and AVM-specific LED offsets, flips the
        7 LED-enable bytes via ``qca_pib_set_byte`` (keeping the section
        checksums valid) and writes the whole PIB back. Reconstructed
        byte-for-byte from an AVM-app capture of the 510E; the firmware applies
        it (close status all-zero) but can take a moment, so the close is
        retried.
        """
        pib = self._qca_read_pib(mac, size=AVM_PIB_SIZE)
        if not pib or len(pib) != AVM_PIB_SIZE:
            _LOGGER.debug("FRITZ LED: could not read %d-byte PIB from %s "
                          "(got %s)", AVM_PIB_SIZE, mac,
                          len(pib) if pib else 0)
            return False

        # Safety: the LED table must currently be all 0x00/0x01. If not, this is
        # a different AVM model/firmware whose offsets we haven't verified —
        # refuse rather than risk writing the wrong fields.
        current = {pib[o] for o in FRITZ_LED_OFFSETS}
        if not current <= {FRITZ_LED_ON, FRITZ_LED_OFF}:
            _LOGGER.warning("FRITZ LED: unexpected LED-table bytes %s on %s; "
                            "aborting (unverified AVM model?)",
                            sorted(current), mac)
            return False

        value = FRITZ_LED_ON if on else FRITZ_LED_OFF
        buf = bytearray(pib)
        for o in FRITZ_LED_OFFSETS:
            qca_pib_set_byte(buf, o, value)
        # The AVM firmware acks the close only once the apply finishes, which it
        # retries several times in the reference capture — mirror that.
        if not self._qca_write_pib(mac, bytes(buf), close_retries=8):
            return False
        _LOGGER.info("FRITZ LED %s written on %s", "ON" if on else "OFF", mac)
        return True
