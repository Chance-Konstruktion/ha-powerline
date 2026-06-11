"""Read LED / QoS / power-saving state from adapters."""
import struct

from .const import (
    ETH_HDR,
    MX_GET_PARAM_CNF,
    MX_GET_PARAM_REQ,
    MX_MME_HDR,
    PARAM_LED_OPTIONS,
    PARAM_POWER_STANDBY,
    PARAM_QOS_PRIORITY_MAP,
    QCA_LED_OFFSETS,
    QCA_PIB_SIZE,
    QCA_POWERSAVE_PROBE,
    QCA_QOS_OFFSET,
    QCA_QOS_VALUES,
    _LOGGER,
    _locked,
)
from .frames import build_mx_frame, mac_to_bytes
from .parsers import parse_mx_get_param_cnf

class StateMixin:
    """Read LED / QoS / power-saving state from adapters."""

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

        # Qualcomm (QCA / AV500): read the real state straight from the PIB.
        if self._chipset == "qualcomm":
            try:
                self._open_hpav()
            except (PermissionError, OSError):
                return states
            qos_rev = {v: k for k, v in QCA_QOS_VALUES.items()}
            try:
                for mac in macs:
                    pib = self._qca_read_pib(mac)
                    if not pib or len(pib) != QCA_PIB_SIZE:
                        continue
                    if {pib[o] for o in QCA_LED_OFFSETS} <= {0x00, 0x01}:
                        states[mac]["led"] = pib[QCA_LED_OFFSETS[0]] == 0x00
                    qv = struct.unpack_from("<H", pib, QCA_QOS_OFFSET)[0]
                    if qv in qos_rev:
                        states[mac]["qos"] = qos_rev[qv]
                    states[mac]["power_saving"] = pib[QCA_POWERSAVE_PROBE] == 0x01
            finally:
                self._close()
            return states

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
