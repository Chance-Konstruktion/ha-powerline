"""Interface selection: candidate scan and the config flow's interface step."""

import asyncio

import pytest

from custom_components.powerline import config_flow as cf
from custom_components.powerline.const import CONF_INTERFACE, INTERFACE_AUTO
from custom_components.powerline.homeplug import frames


class FakeHass:
    """Runs executor jobs inline — the flow only needs the awaitable."""

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _fake_sysfs(monkeypatch, ifaces: dict[str, str]) -> None:
    """Pretend /sys/class/net contains ``ifaces`` mapping name -> operstate."""
    monkeypatch.setattr(frames.os, "listdir", lambda path: list(ifaces))

    real_open = open

    def fake_open(path, *args, **kwargs):
        prefix, _, rest = str(path).partition("/sys/class/net/")
        if prefix == "" and rest.endswith("/operstate"):
            name = rest[: -len("/operstate")]
            if name not in ifaces:
                raise OSError("no such interface")
            import io

            return io.StringIO(ifaces[name] + "\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(frames, "open", fake_open, raising=False)


def test_candidate_interfaces_order_ethernet_first(monkeypatch):
    _fake_sysfs(
        monkeypatch,
        {
            "lo": "up",
            "docker0": "up",
            "hassio": "up",
            "veth1234": "up",
            "enp0s19": "up",
            "enp0s18": "up",
            "usb0": "unknown",
        },
    )
    # eth*/en* first (alphabetically), then the rest; loopback, docker/veth
    # and the HAOS-internal hassio bridge never show up as candidates.
    assert frames.list_candidate_interfaces() == ["enp0s18", "enp0s19", "usb0"]


def test_candidate_interfaces_skip_down_links(monkeypatch):
    _fake_sysfs(monkeypatch, {"enp0s18": "down", "enp0s19": "up"})
    assert frames.list_candidate_interfaces() == ["enp0s19"]


def test_find_interface_is_first_candidate(monkeypatch):
    _fake_sysfs(monkeypatch, {"enp0s18": "up", "enp0s19": "up"})
    assert frames._find_interface() == "enp0s18"


def test_find_interface_none_when_no_candidates(monkeypatch):
    _fake_sysfs(monkeypatch, {"lo": "up"})
    assert frames._find_interface() is None


@pytest.fixture
def flow(monkeypatch):
    """A config flow with discovery/permission checks stubbed out."""
    monkeypatch.setattr(cf, "is_available", lambda: True)
    monkeypatch.setattr(cf, "list_interfaces", lambda: ["enp0s18", "enp0s19"])
    monkeypatch.setattr(cf, "find_interface", lambda: "enp0s18")

    probed: list[str] = []

    class FakeHomeplugAV:
        def __init__(self, interface):
            self.interface = interface

        def discover(self, timeout):
            probed.append(self.interface)
            # Only the VLAN leg actually has adapters on it.
            if self.interface == "enp0s19":
                return [{"mac": "AA:BB:CC:DD:EE:FF"}]
            return []

    monkeypatch.setattr(cf, "HomeplugAV", FakeHomeplugAV)

    handler = cf.TpLinkPowerlineConfigFlow()
    handler.hass = FakeHass()
    handler.probed = probed
    return handler


def _felder(data_schema):
    """Die Feldabbildung eines Formulars.

    Ist voluptuous echt installiert, steckt sie in ``.schema``; baut die
    conftest es nach, ist das Formular selbst schon die Abbildung. Der
    Test soll beides koennen -- er ist einmal daran zerbrochen, dass
    voluptuous inzwischen wirklich mitinstalliert wird.
    """
    return getattr(data_schema, "schema", data_schema)


def _auswahl(pruefer):
    """Die erlaubten Werte eines ``vol.In`` -- echt oder nachgebaut."""
    return list(getattr(pruefer, "container", pruefer))


def test_form_offers_auto_plus_detected_interfaces(flow):
    result = asyncio.run(flow.async_step_user())

    assert result["type"] == "form"
    felder = _felder(result["data_schema"])
    key = next(k for k in felder if k == CONF_INTERFACE)
    assert _auswahl(felder[key]) == [INTERFACE_AUTO, "enp0s18", "enp0s19"]


def test_auto_uses_find_interface(flow):
    result = asyncio.run(
        flow.async_step_user({CONF_INTERFACE: INTERFACE_AUTO})
    )

    assert flow.probed == ["enp0s18"]
    # No adapters on the auto-picked NIC — the issue's exact symptom.
    assert result["type"] == "form"
    assert result["errors"] == {"base": "no_devices_found"}


def test_explicit_interface_is_probed_and_stored(flow):
    result = asyncio.run(flow.async_step_user({CONF_INTERFACE: "enp0s19"}))

    assert flow.probed == ["enp0s19"]
    assert result["type"] == "form"
    assert result["step_id"] == "confirm"

    entry = asyncio.run(flow.async_step_confirm({}))
    assert entry["type"] == "create_entry"
    assert entry["data"]["interface"] == "enp0s19"
    assert entry["data"]["device_count"] == 1


def test_unique_id_follows_selected_interface(flow):
    asyncio.run(flow.async_step_user({CONF_INTERFACE: "enp0s19"}))
    assert flow.unique_id == "powerline_enp0s19"
