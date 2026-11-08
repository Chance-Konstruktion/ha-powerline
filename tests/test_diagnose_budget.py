"""(#9 K2) diagnose() honors its timeout parameter.

button.py passes ``timeout=8.0`` expecting a bound; the parameter was never
used and a scan with two adapters ran ~90 s of fixed windows (3 s per test,
0.6 s per scanned parameter, 3 s per passive listen). The budget is now
split as a fair share per probe, so every probe still runs — each gets a
real (if short) window — and the whole scan finishes within the caller's
timeout.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from custom_components.powerline import homeplug as _MODULE

HomeplugAV = _MODULE.HomeplugAV
CC_DISCOVER_LIST_CNF = _MODULE.CC_DISCOVER_LIST_CNF

MACS = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]


class TestDiagnoseBudget(TestCase):
    def test_every_probe_runs_and_the_total_fits_the_timeout(self):
        windows = []

        def wire(sock, *args, **kwargs):
            if args and isinstance(args[0], (bytes, bytearray)):
                timeout = args[1] if len(args) > 1 else kwargs.get("timeout", 3.0)
                frame = args[0]
            else:
                timeout = args[0] if args else kwargs.get("timeout", 3.0)
                frame = None
            windows.append(timeout)
            if frame is not None and frame[15:17] == b"\x14\x00":
                return [(CC_DISCOVER_LIST_CNF, mac, b"") for mac in MACS]
            return []

        hp = HomeplugAV("eth0")
        hp._sock_hpav = MagicMock()
        hp._sock_mx = MagicMock()
        hp._send_recv = wire
        hp._listen = wire
        with patch(
            "custom_components.powerline.homeplug.diagnostics.get_iface_mac",
            return_value=b"\x02" * 6,
        ):
            report = hp.diagnose(timeout=8.0)

        self.assertTrue(report)
        # 1 discover + 18 tests (3 broadcast + 8 unicast + 2 GET_PARAM + 5
        # QCA) + 48 scanned parameters + 2 passive listens: nothing skipped.
        self.assertEqual(69, len(windows))
        # Before the fix the windows were fixed (2.0 + 18×3.0 + 48×0.6 +
        # 2×3.0 = 90.8 s); now they sum to the caller's timeout.
        self.assertLessEqual(sum(windows), 8.0 + 0.5, f"windows: {windows}")
        self.assertGreater(min(windows), 0.0)
