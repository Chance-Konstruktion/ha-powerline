"""TP-Link AV1300 (TL-WPA8631P, TL-PA8010P) support.

These adapters speak the Qualcomm side of the protocol — discovery, rates and
the chunked module-op PIB access (``0xA0B0``/``0xA0B1``) are all identical to
the QCA7420 path. Two things differ, and both were reverse-engineered from
tpPLC captures of three adapters (TL-WPA8631P v3 + v4, TL-PA8010P v4) supplied
in issue #108:

  1. The PIB is **20888 bytes** (``0x5198``) instead of the generic 9072
     (``QCA_PIB_SIZE``) or AVM's 9796. Reading only the first 9072 bytes would
     truncate it and produce a wrong open length and checksum, which the
     firmware rejects — the same failure mode FRITZ!Powerline had.

  2. The **LED table sits elsewhere**: 11 enable bytes at
     ``AV1300_LED_OFFSETS`` rather than ``QCA_LED_OFFSETS``.

Everything else is already right. **QoS needs no new constants at all** — the
captures write the same 2-byte field at ``QCA_QOS_OFFSET`` with the same four
values this integration already uses, on all three adapters. Likewise the
checksum rule: every LED and QoS write in the captures folds its delta into
``0x0374``/``0x03BC`` exactly the way ``qca_pib_set_byte`` does it, so that
function is used unchanged.

**Power saving is not implemented here.** The captures contain no power-saving
write, and the AV500 byte table (``QCA_POWERSAVE_BYTES``) is not verified at
these offsets — writing it blind could hit unrelated fields.

**LED on the TL-WPA8631P models does not work over powerline at all.** Those
have a web interface, and tpPLC drives their LED with an HTTP ``POST
/userRpm/appPost`` to the adapter's IP instead of a management frame (confirmed
by a capture in #108). Only the TL-PA8010P — the one without a web UI — gets
its LED toggled over layer 2. That is a property of the hardware, not a gap
here: an adapter with a web UI is outside what this integration talks to.
"""
from .const import (
    QCA_PIB_SIZE,
    _LOGGER,
)
from .frames import mac_to_bytes
from .fritz import AVM_PIB_SIZE
from .parsers import qca_pib_set_byte

# Verified PIB length of TL-WPA8631P v3/v4 and TL-PA8010P v4 (#108). All three
# adapters read back exactly this many bytes.
AV1300_PIB_SIZE = 0x5198        # 20888 bytes

# LED-enable bytes: 0x00 = LEDs on, 0x01 = off. Ten of them sit in an 8-byte
# raster (0x2587…0x25CF), one stands apart at 0x255F. Derived from a tpPLC
# LED-off and LED-on capture of the TL-PA8010P: exactly these eleven bytes
# change, plus the two checksum bytes that qca_pib_set_byte maintains.
AV1300_LED_OFFSETS = (0x255F, 0x2587, 0x258F, 0x2597, 0x259F, 0x25A7,
                      0x25AF, 0x25B7, 0x25BF, 0x25C7, 0x25CF)
AV1300_LED_ON = 0x00
AV1300_LED_OFF = 0x01

# Probe window: read this many bytes just below a candidate PIB end.
_PROBE = 16


class Av1300Mixin:
    """AV1300 PIB size detection and LED writes."""

    def _pib_size(self, mac: str) -> int:
        """The adapter's real PIB length, probed once and remembered.

        There is nothing to identify these adapters by: ``VS_SW_VER`` comes back
        all zeros on AV1300, so there is no firmware string and no model name —
        the PIB length itself is the distinguishing feature.

        The probe is deliberately conservative. A candidate size is only
        accepted when a read just **below** it succeeds *and* a read **at** it
        fails; an adapter that answers every offset therefore matches nothing
        and keeps the generic size. That way a working AV500 can never be
        talked into a wrong, larger PIB by a probe that reads too eagerly.
        """
        key = mac.upper()
        cache = getattr(self, "_pib_size_by_mac", None)
        if cache is None:
            cache = self._pib_size_by_mac = {}
        if key in cache:
            return cache[key]

        size = QCA_PIB_SIZE
        for candidate in (AV1300_PIB_SIZE, AVM_PIB_SIZE):
            if candidate <= QCA_PIB_SIZE:
                continue
            try:
                bigger = (self._pib_readable(mac, candidate - _PROBE)
                          and not self._pib_readable(mac, candidate))
            except Exception:      # noqa: BLE001 - a probe must never throw
                _LOGGER.debug("PIB size probe failed on %s; assuming the "
                              "generic size", mac, exc_info=True)
                break
            if bigger:
                size = candidate
                break
        if size != QCA_PIB_SIZE:
            _LOGGER.info("PIB on %s is %d bytes (not the generic %d)",
                         mac, size, QCA_PIB_SIZE)
        cache[key] = size
        return size

    def _pib_readable(self, mac: str, offset: int) -> bool:
        """True if the adapter returns data for a short read at ``offset``."""
        chunk = self._qca_read_chunk(mac_to_bytes(mac), mac, offset, _PROBE)
        return bool(chunk) and len(chunk) == _PROBE

    def is_av1300(self, mac: str) -> bool:
        """True if this adapter carries the 20888-byte AV1300 PIB."""
        return bool(mac) and self._pib_size(mac) == AV1300_PIB_SIZE

    def _set_led_av1300(self, mac: str, on: bool) -> bool:
        """Toggle the LEDs on an AV1300 adapter via a full-size PIB RMW.

        Reads the adapter's own 20888-byte PIB, flips the eleven LED-enable
        bytes through ``qca_pib_set_byte`` (which keeps the two section
        checksums valid) and writes it back — the same sequence tpPLC sends.
        """
        size = self._pib_size(mac)
        pib = self._qca_read_pib(mac, size=size)
        if not pib or len(pib) != size:
            _LOGGER.debug("AV1300 LED: could not read %d-byte PIB from %s "
                          "(got %s)", size, mac, len(pib) if pib else 0)
            return False

        # Safety net, same as the FRITZ path: the LED table must currently hold
        # nothing but the two known values. Anything else means these offsets
        # are not the LED table on this firmware — refuse instead of guessing.
        current = {pib[o] for o in AV1300_LED_OFFSETS}
        if not current <= {AV1300_LED_ON, AV1300_LED_OFF}:
            _LOGGER.warning("AV1300 LED: unexpected LED-table bytes %s on %s; "
                            "aborting (unverified model?)",
                            sorted(current), mac)
            return False

        value = AV1300_LED_ON if on else AV1300_LED_OFF
        buf = bytearray(pib)
        for o in AV1300_LED_OFFSETS:
            qca_pib_set_byte(buf, o, value)
        if not self._qca_write_pib(mac, bytes(buf), close_retries=8):
            return False
        _LOGGER.info("AV1300 LED %s written on %s", "ON" if on else "OFF", mac)
        return True

    def led_state_av1300(self, pib: bytes) -> bool | None:
        """Read the LED state out of an AV1300 PIB, or None if unreadable."""
        if not pib or len(pib) < AV1300_LED_OFFSETS[-1] + 1:
            return None
        values = {pib[o] for o in AV1300_LED_OFFSETS}
        if values == {AV1300_LED_ON}:
            return True
        if values == {AV1300_LED_OFF}:
            return False
        return None


__all__ = [
    "AV1300_LED_OFFSETS",
    "AV1300_LED_OFF",
    "AV1300_LED_ON",
    "AV1300_PIB_SIZE",
    "Av1300Mixin",
]
