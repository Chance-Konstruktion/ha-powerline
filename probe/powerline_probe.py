#!/usr/bin/env python3
"""Powerline probe: read HomePlug AV adapters for a Home Assistant in another VLAN.

HomePlug management is raw layer 2 and never routed. Run this on any Linux box
(a Pi is plenty) whose network port is in the same segment as the powerline
adapters -- usually the trunk's native VLAN. It talks HomePlug there and serves
the results as JSON over HTTP; the integration's "Remote probe" setup reads it.

Read-only on purpose: there is no endpoint that writes to an adapter.

    sudo python3 powerline_probe.py --port 8765 --token-file /etc/powerline-probe.token

Needs root or CAP_NET_RAW. Standard library only; the HomePlug code is the
integration's own ``homeplug`` package, found next to this file in a checkout
of the repository (or wherever ``--homeplug-dir`` points).
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROBE_VERSION = "1"
DEFAULT_PORT = 8765
# Clamp the discovery window a client may ask for: long enough for slow
# adapters, short enough that a request cannot park the probe for minutes.
MIN_TIMEOUT, MAX_TIMEOUT = 1.0, 15.0

_LOGGER = logging.getLogger("powerline_probe")


def load_homeplug(homeplug_dir: Path):
    """Import the integration's ``homeplug`` package from ``homeplug_dir``."""
    sys.path.insert(0, str(homeplug_dir))
    import homeplug  # noqa: PLC0415 - path is only known at runtime

    return homeplug


def links_as_list(plc_links: dict) -> list:
    """``{(src, dst): rates}`` -> JSON-friendly ``[[src, dst, rates], ...]``."""
    return [[src, dst, dict(rates)] for (src, dst), rates in plc_links.items()]


class Probe:
    """One HomeplugAV instance, used by one request at a time."""

    def __init__(self, hp, token: str | None) -> None:
        self.hp = hp
        self.token = token
        self.lock = threading.Lock()

    def authorized(self, header: str | None) -> bool:
        if not self.token:
            return True
        expected = f"Bearer {self.token}"
        return hmac.compare_digest((header or "").encode(), expected.encode())

    def info(self) -> dict:
        return {"probe": "powerline", "version": PROBE_VERSION,
                "interface": self.hp.interface}

    def discover(self, timeout: float) -> dict:
        timeout = min(max(timeout, MIN_TIMEOUT), MAX_TIMEOUT)
        with self.lock:
            devices = self.hp.discover(timeout)
            links = links_as_list(getattr(self.hp, "plc_links", {}))
        return {"devices": devices, "plc_links": links}

    def states(self, macs: list[str]) -> dict:
        with self.lock:
            return self.hp.query_device_states(macs)


def make_handler(probe: Probe):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"powerline-probe/{PROBE_VERSION}"

        def log_message(self, fmt, *args):  # route through logging
            _LOGGER.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 - http.server naming
            if not probe.authorized(self.headers.get("Authorization")):
                self._send(401, {"error": "unauthorized"})
                return
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/v1/info":
                    self._send(200, probe.info())
                elif url.path == "/v1/discover":
                    timeout = float(query.get("timeout", ["5"])[0])
                    self._send(200, probe.discover(timeout))
                elif url.path == "/v1/states":
                    raw = query.get("macs", [""])[0]
                    macs = [m.strip().upper() for m in raw.split(",") if m.strip()]
                    self._send(200, probe.states(macs))
                else:
                    self._send(404, {"error": "not found"})
            except ValueError:
                self._send(400, {"error": "bad request"})
            except Exception:  # noqa: BLE001 - one bad poll must not kill the probe
                _LOGGER.exception("Request %s failed", url.path)
                self._send(500, {"error": "probe error"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interface", default=None,
                        help="network interface towards the adapters (default: auto)")
    parser.add_argument("--token-file", type=Path, default=None,
                        help="file holding a shared secret; clients must send it")
    parser.add_argument("--homeplug-dir", type=Path,
                        default=here.parent / "custom_components" / "powerline")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = args.token_file.read_text(encoding="utf-8").strip() if args.token_file else None
    homeplug = load_homeplug(args.homeplug_dir)
    if not homeplug.is_available():
        _LOGGER.error("No raw socket access: run as root or grant CAP_NET_RAW")
        return 1

    probe = Probe(homeplug.HomeplugAV(args.interface), token)
    if not token:
        _LOGGER.warning("Running without --token-file: anyone who reaches the "
                        "port can read the adapter list")
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(probe))
    _LOGGER.info("Powerline probe on %s:%d, interface %s",
                 args.bind, args.port, probe.hp.interface)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
