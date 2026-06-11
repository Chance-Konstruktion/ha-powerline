"""LED, QoS and power-saving actuation."""
import struct
import time

from .const import (
    MX_APPLY_CNF,
    MX_APPLY_REQ,
    PARAM_LED_AUX,
    PARAM_LED_OPTIONS,
    PARAM_POWER_STANDBY,
    PARAM_POWER_STANDBY_AUX,
    PARAM_QOS_PRIORITY_MAP,
    QCA_LED_OFFSETS,
    QCA_PIB_CHUNK,
    QCA_PIB_SIZE,
    QCA_POWERSAVE_BYTES,
    QCA_POWERSAVE_PROBE,
    QCA_QOS_OFFSET,
    QCA_QOS_VALUES,
    _LOGGER,
    _MX_ACTION_OK,
    _MX_APPLY_OK,
    _locked,
)
from .frames import build_mx_frame, build_mx_set_param, mac_to_bytes
from .parsers import qca_pib_set_byte

class ControlMixin:
    """LED, QoS and power-saving actuation."""

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

        # The adapter acked the open, every data chunk and the close — that is
        # the same confirmation tpPLC relies on (its captures show no verify
        # read either). The device can keep serving the OLD PIB image for many
        # seconds after the write, so a read-back here produces false failures
        # (hardware-confirmed: writes reported "not confirmed" had actually
        # persisted). Do one quick read purely for logging.
        dst = mac_to_bytes(mac)
        cstart = (min(QCA_LED_OFFSETS) // QCA_PIB_CHUNK) * QCA_PIB_CHUNK
        clen = min(QCA_PIB_CHUNK, QCA_PIB_SIZE - cstart)
        time.sleep(0.4)
        chunk = self._qca_read_chunk(dst, mac, cstart, clen)
        verified = bool(chunk) and all(
            chunk[o - cstart] == value for o in QCA_LED_OFFSETS)
        _LOGGER.info("QCA LED %s written on %s (all chunks acked%s)",
                     "ON" if on else "OFF", mac,
                     ", read-back verified" if verified
                     else "; device still serving old image")
        return True

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

            if self._chipset == "qualcomm":
                return self._set_power_saving_qualcomm(mac, on)
            if self._chipset in ("broadcom", "unknown"):
                if self._set_power_saving_broadcom(mac, on):
                    return True
                # An "unknown" chipset might be QCA (no MEDIAXTREAM reply).
                return self._set_power_saving_qualcomm(mac, on)

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
        qca_pib_set_byte(buf, QCA_QOS_OFFSET, new & 0xFF)
        qca_pib_set_byte(buf, QCA_QOS_OFFSET + 1, (new >> 8) & 0xFF)

        if not self._qca_write_pib(mac, bytes(buf)):
            return False

        # All chunks + close were acked — that is the confirmation tpPLC itself
        # relies on. The device may keep serving the old PIB image for many
        # seconds, so a read-back produces false failures (hardware-confirmed:
        # a "not confirmed" QoS write had actually persisted). Quick read for
        # logging only.
        dst = mac_to_bytes(mac)
        cstart = (QCA_QOS_OFFSET // QCA_PIB_CHUNK) * QCA_PIB_CHUNK
        clen = min(QCA_PIB_CHUNK, QCA_PIB_SIZE - cstart)
        time.sleep(0.4)
        chunk = self._qca_read_chunk(dst, mac, cstart, clen)
        verified = bool(chunk) and len(chunk) > QCA_QOS_OFFSET - cstart + 1 and \
            struct.unpack_from("<H", chunk, QCA_QOS_OFFSET - cstart)[0] == new
        _LOGGER.info("QCA QoS '%s' written on %s (all chunks acked%s)",
                     priority, mac,
                     ", read-back verified" if verified
                     else "; device still serving old image")
        return True

    def _set_power_saving_qualcomm(self, mac: str, on: bool) -> bool:
        """Set power saving on a QCA (AV500) adapter via PIB read-modify-write.

        Writes the captured power-saving bytes (off = all zero) and maintains
        the two XOR checksums via qca_pib_set_byte. Reproduces tpPLC's bytes.
        """
        pib = self._qca_read_pib(mac)
        if not pib or len(pib) != QCA_PIB_SIZE:
            _LOGGER.debug("QCA power saving: could not read PIB from %s", mac)
            return False

        buf = bytearray(pib)
        for off, on_val in QCA_POWERSAVE_BYTES.items():
            qca_pib_set_byte(buf, off, on_val if on else 0x00)

        if not self._qca_write_pib(mac, bytes(buf)):
            return False

        # All chunks + close acked = accepted (see _set_led_qualcomm). The
        # read-back is informational only — the device can serve the old PIB
        # image for a while. (Hardware-confirmed: power saving visibly
        # throttled the link even when the read-back still showed old bytes.)
        probe = QCA_POWERSAVE_PROBE
        expected = 0x01 if on else 0x00
        dst = mac_to_bytes(mac)
        cstart = (probe // QCA_PIB_CHUNK) * QCA_PIB_CHUNK
        clen = min(QCA_PIB_CHUNK, QCA_PIB_SIZE - cstart)
        time.sleep(0.4)
        chunk = self._qca_read_chunk(dst, mac, cstart, clen)
        verified = bool(chunk) and len(chunk) > probe - cstart and \
            chunk[probe - cstart] == expected
        _LOGGER.info("QCA power saving %s written on %s (all chunks acked%s)",
                     "ON" if on else "OFF", mac,
                     ", read-back verified" if verified
                     else "; device still serving old image")
        return True

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
