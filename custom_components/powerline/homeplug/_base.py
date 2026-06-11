"""Shared transport: dual raw sockets, send/recv, framing."""
import socket
import struct
import threading
import time

from .const import ETHERTYPE_HPAV, ETHERTYPE_MEDIAXTREAM, ETH_HDR, _LOGGER
from .frames import _find_interface, get_iface_mac, mac_to_str

class _HomeplugBase:
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
