"""(#9 M5) The discovery worst case must fit the remote probe's client timeout.

``RemoteHomeplug`` aborts a /v1/discover request after ``REQUEST_TIMEOUT``
(30 s); two failed polls in a row flip every entity to "unavailable". The
fixed per-adapter fallback cascade in ``_fetch_rates`` plus the info/role
queries burned 60+ s on a silent network with only three adapters — a budget
now bounds them. Networks that answer return earlier by design (``found``
-> return), so nothing that ever answered is lost.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from custom_components.powerline import homeplug as _MODULE
from custom_components.powerline.homeplug.discovery import DISCOVERY_RATE_BUDGET
from custom_components.powerline.remote import REQUEST_TIMEOUT

HomeplugAV = _MODULE.HomeplugAV
CC_DISCOVER_LIST_REQ = _MODULE.CC_DISCOVER_LIST_REQ
CC_DISCOVER_LIST_CNF = _MODULE.CC_DISCOVER_LIST_CNF
VS_NW_INFO_CNF = _MODULE.VS_NW_INFO_CNF
mac_to_bytes = _MODULE.mac_to_bytes

MACS = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]

# Real QCA VS_NW_INFO.CNF payloads from a three-adapter capture (#108),
# same fixtures as test_homeplug_parsing.py.
NW_INFO_FRAMES = {
    "9C:A2:F4:B0:E7:C0":
        "0139a0000000b052000052000001788862a27e690a00000f08000000000"
        "05ce931550c92020000000200000000005ce931550c920226200068ec8a"
        "0f7194c3002200b70000003c52a1a91c4f0a141400c84d4421e5e065022"
        "200c5020000",
    "3C:52:A1:A9:1C:4F":
        "0139a0000000b052000052000001788862a27e690a00000f0a000000000"
        "05ce931550c92020000000200000000005ce931550c92021c1c00d41ad1"
        "419c8c73012200280100009ca2f4b0e7c0081a14009009d088e643c5022"
        "20065020000",
    "5C:E9:31:55:0C:92":
        "0139a0000000b052000052000001788862a27e690a00000f02000000000"
        "25ce931550c92020000000200000000009ca2f4b0e7c00800000090"
        "09d088e643b7002200c30000003c52a1a91c4f0a000000c84d4421e5"
        "e02801220073010000",
}


def _eth(src: str, payload: bytes) -> bytes:
    return b"\xaa" * 6 + mac_to_bytes(src) + b"\x88\xe1" + payload


class TestDiscoveryBudget(TestCase):
    def _hp(self) -> HomeplugAV:
        hp = HomeplugAV("eth0")
        hp._sock_hpav = MagicMock()
        hp._sock_mx = MagicMock()
        return hp

    def test_silent_worst_case_fits_the_client_timeout(self):
        """A silent three-adapter network must not outlast the probe client."""
        clock = {"now": 0.0}

        def wire(sock, *args, **kwargs):
            # _send_recv(sock, frame, timeout) and _listen(sock, timeout)
            # both land here; every window consumes its full timeout.
            if args and isinstance(args[0], (bytes, bytearray)):
                timeout = args[1] if len(args) > 1 else kwargs.get("timeout", 3.0)
                frame = args[0]
            else:
                timeout = args[0] if args else kwargs.get("timeout", 3.0)
                frame = None
            clock["now"] += timeout
            if frame is not None and frame[15:17] == b"\x14\x00":
                return [(CC_DISCOVER_LIST_CNF, mac, b"") for mac in MACS]
            return []

        hp = self._hp()
        hp._send_recv = wire
        hp._listen = wire
        with patch.object(hp, "_open_hpav"), patch.object(hp, "_open_mx"), \
             patch.object(hp, "_close"), \
             patch("custom_components.powerline.homeplug.discovery.time",
                   SimpleNamespace(monotonic=lambda: clock["now"])):
            devices = hp.discover(5.0)

        self.assertEqual(3, len(devices))
        # The probe client gives up after REQUEST_TIMEOUT; the old fixed
        # cascade ran ~60 s of windows here.
        self.assertLessEqual(
            clock["now"], REQUEST_TIMEOUT - 3,
            f"discovery worst case took {clock['now']} s (budget "
            f"{DISCOVERY_RATE_BUDGET} s + preamble) — the remote "
            "probe client would abort")

    def test_answering_network_still_gets_its_rates(self):
        """A QCA network that answers gets its rates — the budget only cuts
        the silent tail, never a network that responds."""
        clock = {"now": 0.0}

        def wire(sock, *args, **kwargs):
            if args and isinstance(args[0], (bytes, bytearray)):
                timeout = args[1] if len(args) > 1 else kwargs.get("timeout", 3.0)
                frame = args[0]
            else:
                timeout = args[0] if args else kwargs.get("timeout", 3.0)
                frame = None
            clock["now"] += timeout
            if frame is not None and frame[15:17] == b"\x38\xa0":
                dst = frame[0:6].hex(":").upper()
                body = bytes.fromhex(NW_INFO_FRAMES[dst])
                return [(VS_NW_INFO_CNF, dst, _eth(dst, body))]
            return []

        hp = self._hp()
        hp._send_recv = wire
        hp._listen = wire
        devices = {mac: {"mac": mac} for mac in NW_INFO_FRAMES}
        with patch("custom_components.powerline.homeplug.discovery.time",
                   SimpleNamespace(monotonic=lambda: clock["now"])):
            found = hp._fetch_rates(devices, clock["now"]
                                    + DISCOVERY_RATE_BUDGET)

        self.assertTrue(found)
        for mac, dev in devices.items():
            with self.subTest(mac=mac):
                self.assertGreater(dev.get("tx_rate", 0), 0)
                self.assertGreater(dev.get("rx_rate", 0), 0)
