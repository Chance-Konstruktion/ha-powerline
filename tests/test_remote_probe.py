"""Remote probe: the probe's HTTP server and the integration's client together.

The probe runs for real on a loopback port with a fake HomeplugAV behind it,
so the JSON round trip (devices, plc_links tuple keys, states, token) is the
one the integration sees in the field.
"""

import asyncio
import importlib.util
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from custom_components.powerline import config_flow as cf
from custom_components.powerline.const import (
    CONF_INTERFACE,
    CONF_PROBE_TOKEN,
    CONF_PROBE_URL,
    INTERFACE_PROBE,
)
from custom_components.powerline.remote import (
    ProbeError,
    RemoteHomeplug,
    normalize_probe_url,
)

_PROBE_PATH = Path(__file__).resolve().parent.parent / "probe" / "powerline_probe.py"
_spec = importlib.util.spec_from_file_location("powerline_probe", _PROBE_PATH)
powerline_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(powerline_probe)

MAC_A = "AA:BB:CC:00:00:01"
MAC_B = "AA:BB:CC:00:00:02"


class FakeHomeplugAV:
    interface = "eth0"

    def __init__(self):
        self.plc_links = {}
        self.timeouts = []
        self.writes = []

    def discover(self, timeout):
        self.timeouts.append(timeout)
        self.plc_links = {(MAC_A, MAC_B): {"tx": 512, "rx": 480}}
        return [{"mac": MAC_A, "firmware_ver": "1.0", "tx_rate": 512, "rx_rate": 480},
                {"mac": MAC_B}]

    def query_device_states(self, macs):
        return {m: {"led": True, "qos": "internet", "power_saving": False} for m in macs}

    def set_led(self, mac, on):  # must never be reached through the probe
        self.writes.append(mac)
        return True


@pytest.fixture
def probe_server():
    """Start a probe on a free loopback port; yields (url, fake_hp, set_token)."""
    fake = FakeHomeplugAV()
    probe = powerline_probe.Probe(fake, token=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), powerline_probe.make_handler(probe))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def set_token(token):
        probe.token = token

    yield url, fake, set_token
    server.shutdown()
    server.server_close()


def test_discover_round_trip_keeps_devices_and_links(probe_server):
    url, fake, _ = probe_server
    hp = RemoteHomeplug(url)

    devices = hp.discover(5.0)

    assert [d["mac"] for d in devices] == [MAC_A, MAC_B]
    assert devices[0]["tx_rate"] == 512
    # plc_links travel as lists and come back keyed by (src, dst) tuples,
    # which is what TopologyManager.update() expects.
    assert hp.plc_links == {(MAC_A, MAC_B): {"tx": 512, "rx": 480}}
    assert fake.timeouts == [5.0]


def test_discover_timeout_is_clamped_on_the_probe(probe_server):
    url, fake, _ = probe_server
    RemoteHomeplug(url).discover(600)
    RemoteHomeplug(url).discover(0)
    assert fake.timeouts == [powerline_probe.MAX_TIMEOUT, powerline_probe.MIN_TIMEOUT]


def test_states_are_read_through_the_probe(probe_server):
    url, _, _ = probe_server
    states = RemoteHomeplug(url).query_device_states([MAC_A])
    assert states == {MAC_A: {"led": True, "qos": "internet", "power_saving": False}}


def test_token_is_required_when_set(probe_server):
    url, _, set_token = probe_server
    set_token("geheim")

    with pytest.raises(ProbeError):
        RemoteHomeplug(url).ping()
    with pytest.raises(ProbeError):
        RemoteHomeplug(url, "falsch").ping()
    assert RemoteHomeplug(url, "geheim").ping()["probe"] == "powerline"


def test_controls_are_not_forwarded(probe_server):
    url, fake, _ = probe_server
    hp = RemoteHomeplug(url)

    assert hp.set_led(MAC_A, False) is False
    assert hp.set_power_saving(MAC_A, True) is False
    assert hp.set_qos_priority(MAC_A, "gaming") is False
    assert hp.restart(MAC_A) is False
    assert fake.writes == []


def test_unreachable_probe_raises_probe_error():
    # Port 9 on loopback: nothing listens there in a test runner.
    with pytest.raises(ProbeError):
        RemoteHomeplug("127.0.0.1:9").ping()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.1.2:8765", "http://192.168.1.2:8765"),
        ("http://probe.lan:8765/", "http://probe.lan:8765"),
        ("  https://probe.lan  ", "https://probe.lan"),
    ],
)
def test_normalize_probe_url(raw, expected):
    assert normalize_probe_url(raw) == expected


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _flow(monkeypatch, raw_socket=True):
    monkeypatch.setattr(cf, "is_available", lambda: raw_socket)
    monkeypatch.setattr(cf, "list_interfaces", lambda: ["enp0s18"])
    handler = cf.TpLinkPowerlineConfigFlow()
    handler.hass = FakeHass()
    return handler


def test_flow_offers_probe_and_creates_probe_entry(monkeypatch, probe_server):
    url, _, set_token = probe_server
    set_token("geheim")
    flow = _flow(monkeypatch)

    form = asyncio.run(flow.async_step_user({CONF_INTERFACE: INTERFACE_PROBE}))
    assert form["step_id"] == "probe"

    confirm = asyncio.run(
        flow.async_step_probe({CONF_PROBE_URL: url, CONF_PROBE_TOKEN: "geheim"})
    )
    assert confirm["step_id"] == "confirm"

    entry = asyncio.run(flow.async_step_confirm({}))
    assert entry["type"] == "create_entry"
    assert entry["data"][CONF_PROBE_URL] == url
    assert entry["data"][CONF_PROBE_TOKEN] == "geheim"
    assert entry["data"]["device_count"] == 2
    assert flow.unique_id == f"powerline_probe_{url}"


def test_flow_without_raw_socket_goes_to_probe(monkeypatch):
    flow = _flow(monkeypatch, raw_socket=False)
    result = asyncio.run(flow.async_step_user())
    assert result["type"] == "form"
    assert result["step_id"] == "probe"


def test_flow_reports_unreachable_probe(monkeypatch):
    flow = _flow(monkeypatch)
    result = asyncio.run(
        flow.async_step_probe({CONF_PROBE_URL: "127.0.0.1:9", CONF_PROBE_TOKEN: ""})
    )
    assert result["errors"] == {"base": "probe_unreachable"}


def test_coordinator_uses_injected_probe(monkeypatch):
    from custom_components.powerline.coordinator import TpLinkPowerlineCoordinator

    sentinel = RemoteHomeplug("127.0.0.1:9")
    coordinator = TpLinkPowerlineCoordinator(FakeHass(), None, [], hp=sentinel)
    assert coordinator.hp is sentinel
    assert coordinator.interface == "probe:http://127.0.0.1:9"
