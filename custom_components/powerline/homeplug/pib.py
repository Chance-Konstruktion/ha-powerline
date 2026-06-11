"""QCA AV500 PIB access (chunked module-op read/write)."""
import random
import struct

from .const import (
    ETH_HDR,
    QCA_PIB_CHUNK,
    QCA_PIB_SIZE,
    VS_MOD_OP_CNF,
    _LOGGER,
    _QCA_HDR_CLOSE,
    _QCA_HDR_OPEN,
    _QCA_HDR_READ,
)
from .frames import build_qca_mod_frame, mac_to_bytes
from .parsers import qca_pib_checksum

class QcaPibMixin:
    """QCA AV500 PIB read / write (module-op, read-modify-write)."""

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
            # Read CONFIRM layout (verified against tpPLC captures, both
            # adapters): offset@23 (u32 LE), payload data starts at byte 27.
            if len(pl) < 27:
                continue
            if struct.unpack_from("<I", pl, 23)[0] != offset:
                continue
            return pl[27:27 + clen]
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

    def _qca_mod_send(self, dst: bytes, mac: str, payload: bytes) -> bytes | None:
        """Send a module-op frame; return the 0xA0B1 response payload or None."""
        frame = build_qca_mod_frame(dst, self._src_mac, payload)
        for mmtype, src, data in self._send_recv(
                self._sock_hpav, frame, 2.0, expected_src=mac,
                stop_on=frozenset((VS_MOD_OP_CNF,))):
            if mmtype == VS_MOD_OP_CNF:
                return data[ETH_HDR + 6:]            # payload after MMV+MMTYPE+OUI
        return None

    def _qca_mod_ack(self, dst: bytes, mac: str, payload: bytes) -> bool:
        """Send a module-op frame and wait for its 0xA0B1 confirmation."""
        return self._qca_mod_send(dst, mac, payload) is not None

    def _qca_write_pib(self, mac: str, pib: bytes) -> bool:
        """Write the full PIB back: open -> data chunks -> close."""
        dst = mac_to_bytes(mac)
        token = struct.pack("<H", random.randint(1, 0xFFFE))

        op = bytearray(_QCA_HDR_OPEN)
        op[13:15] = token
        struct.pack_into("<I", op, 22, len(pib))
        # The write-open carries a 4-byte PIB checksum the adapter validates to
        # *activate* (not just store) the change: the complement of the PIB's
        # 32-bit XOR-fold (open-plc-utils checksum32). It is computed per PIB,
        # so it is correct for every adapter — the previous fixed-key formula
        # only matched the one adapter it was cracked from, and others rejected
        # the apply with close status 31 00 30.
        op[26:30] = qca_pib_checksum(pib)
        open_resp = self._qca_mod_send(dst, mac, bytes(op))
        if open_resp is None:
            _LOGGER.debug("QCA PIB write: no ack to open from %s", mac)
            return False
        _LOGGER.info("QCA write: open resp from %s = %s", mac, open_resp[:24].hex())

        # Data frame wire layout (verified byte-for-byte against tpPLC, both
        # adapters): op 0x0111, a 16-bit "payload+23" length at byte 7, the
        # token at 12, the chunk length at 22, a 32-bit offset at 24, and the
        # chunk data starting at byte 28.
        offset = 0
        while offset < len(pib):
            clen = min(QCA_PIB_CHUNK, len(pib) - offset)
            hdr = bytearray(28)
            hdr[4:6] = b"\x01\x11"
            struct.pack_into("<H", hdr, 7, clen + 23)
            hdr[13:15] = token            # token format on the wire is 00 XX XX 00
            hdr[18:22] = b"\x02\x70\x00\x00"
            struct.pack_into("<H", hdr, 22, clen)
            struct.pack_into("<I", hdr, 24, offset)
            if not self._qca_mod_ack(dst, mac, bytes(hdr) + pib[offset:offset + clen]):
                _LOGGER.debug("QCA PIB write: no ack at 0x%04X from %s", offset, mac)
                return False
            offset += clen

        cl = bytearray(_QCA_HDR_CLOSE)
        cl[13:15] = token
        close_resp = self._qca_mod_send(dst, mac, bytes(cl))
        if close_resp is None:
            _LOGGER.debug("QCA PIB write: no ack to close from %s", mac)
            return False
        # The close applies the write. A healthy apply returns all-zero status;
        # some adapters reject the apply with a non-zero code (e.g. 31 00 30) --
        # confirmed on hardware: that adapter's LED/QoS/power-saving never
        # change. Treat a non-zero status as a real failure.
        applied = close_resp[:3] == b"\x00\x00\x00"
        if applied:
            _LOGGER.info("QCA write applied on %s (close ok)", mac)
        else:
            _LOGGER.warning("QCA write REJECTED by %s: close status %s "
                            "(adapter refused to apply the PIB; try power-cycling "
                            "it, and disable power saving first)",
                            mac, close_resp[:6].hex())
        return applied
