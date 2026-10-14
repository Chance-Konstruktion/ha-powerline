"""Remote probe client: HomePlug AV through a probe in another network segment.

HomePlug management frames are raw layer 2 and are never routed. When Home
Assistant lives in its own (IoT) VLAN and the powerline adapters sit on a trunk
in another VLAN, HA cannot reach them at all -- discovery comes back with zero
devices. The probe (``probe/powerline_probe.py``) runs on any Linux box in the
adapters' segment, talks HomePlug there and serves the results as JSON over
HTTP. ``RemoteHomeplug`` mirrors the parts of ``HomeplugAV`` the coordinator
uses, so nothing else has to know whether the adapters are local or remote.

The probe is read-only on purpose: it is reachable from another VLAN, so it
must not be a way to reboot adapters or change their settings. Control calls
return False, exactly like an adapter that did not confirm the write.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

_LOGGER = logging.getLogger(__name__)

# The probe runs a full discovery per request (about 5 s), so give it room.
REQUEST_TIMEOUT = 30.0


class ProbeError(Exception):
    """The probe could not be reached or answered with an error."""


def normalize_probe_url(url: str) -> str:
    """``host:port`` or a full URL -> ``http://host:port`` without trailing slash."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    return url


class RemoteHomeplug:
    """Drop-in for ``HomeplugAV`` that asks a remote probe instead of the wire."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = normalize_probe_url(url)
        self.token = token or None
        # Shown wherever the local variant shows its network interface.
        self.interface = f"probe:{self.url}"
        self.plc_links: dict[tuple[str, str], dict[str, int]] = {}

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(f"{self.url}{path}")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise ProbeError(f"probe answered HTTP {err.code}") from err
        except (urllib.error.URLError, OSError, ValueError) as err:
            raise ProbeError(f"probe not reachable: {err}") from err

    def discover(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Run discovery on the probe; also refreshes ``plc_links``."""
        data = self._get(f"/v1/discover?timeout={float(timeout)}")
        links: dict[tuple[str, str], dict[str, int]] = {}
        for entry in data.get("plc_links", []):
            src, dst, rates = entry
            links[(str(src).upper(), str(dst).upper())] = dict(rates)
        self.plc_links = links
        return list(data.get("devices", []))

    def query_device_states(self, macs: list[str]) -> dict[str, dict[str, Any]]:
        """LED / QoS / power-saving state as read by the probe."""
        query = ",".join(macs)
        return dict(self._get(f"/v1/states?macs={query}"))

    def ping(self) -> dict[str, Any]:
        """Probe identity and version, used by the config flow."""
        return dict(self._get("/v1/info"))

    # -- control: deliberately not forwarded (see module docstring) ---------

    def _read_only(self, what: str, mac: str) -> bool:
        _LOGGER.warning("%s for %s ignored: the remote probe is read-only", what, mac)
        return False

    def set_led(self, mac: str, on: bool) -> bool:
        return self._read_only("LED control", mac)

    def set_power_saving(self, mac: str, on: bool) -> bool:
        return self._read_only("Power saving control", mac)

    def set_qos_priority(self, mac: str, priority: str) -> bool:
        return self._read_only("QoS control", mac)

    def restart(self, mac: str) -> bool:
        return self._read_only("Restart", mac)
