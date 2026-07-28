"""Powerline as a Spatial Hub provider.

These tests never import the hub -- that is the point of the contract.
They assert on the registration dict and the payload it produces, which is
exactly what the hub would see.
"""

import asyncio

import pytest

from custom_components.powerline.spatial import (
    PowerlineSpatialAdapter,
    async_create_provider,
)
from custom_components.powerline.spatial_hub_provider import DATA_PROVIDERS


class FakeHass:
    def __init__(self):
        self.data = {}


class FakeEntry:
    """A ConfigEntry stand-in that records its unload hooks."""

    domain = "powerline"

    def __init__(self):
        self.unload_hooks = []

    def async_on_unload(self, func):
        self.unload_hooks.append(func)

    def unload(self):
        for hook in self.unload_hooks:
            hook()


class FakeCoordinator:
    """Just enough coordinator for the adapter to translate."""

    def __init__(self, topology=None):
        self.data = {"topology": topology or _topology()}
        self.listeners = []
        self.history = self
        self.led_calls = []
        self.restart_calls = []
        self.series_calls = []
        self.renamed = {}

    def adapter_name(self, mac):
        return self.renamed.get(mac, mac)

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def refreshed(self):
        for listener in list(self.listeners):
            listener()

    def series(self, source, destination, hours):
        self.series_calls.append((source, destination, hours))
        return [{"t": "2026-07-25T20:00:00+00:00", "value": 560}]

    async def async_set_led(self, mac, on):
        self.led_calls.append((mac, on))
        return True

    async def async_restart_adapter(self, mac):
        self.restart_calls.append(mac)
        return True


def _topology():
    return {
        "nodes": [
            {"mac": "AA:BB", "name": "Router EG", "online": True, "role": "CCo",
             "model": "TL-PA9020", "firmware": "1.2.3"},
            {"mac": "CC:DD", "name": "Büro", "online": False, "role": "Station"},
        ],
        "edges": [
            {"source": "AA:BB", "destination": "CC:DD", "tx_phy_rate": 560,
             "rx_phy_rate": 540, "average_rate": 550, "link_quality": "yellow",
             "estimated": False},
        ],
    }


@pytest.fixture
def adapter():
    return PowerlineSpatialAdapter(FakeHass(), FakeCoordinator())


def test_registration_declares_itself_to_the_hub():
    hass = FakeHass()
    async_create_provider(hass, FakeEntry(), FakeCoordinator())

    registration = hass.data[DATA_PROVIDERS]["powerline"]
    assert registration["provider_id"] == "powerline"
    assert registration["api_version"] == 1
    assert registration["capabilities"]["edges"] is True
    assert registration["layers"][0]["id"] == "network_powerline"
    assert callable(registration["data"])


def test_unloading_the_entry_withdraws_the_registration():
    """Nobody wires unregister by hand -- the entry's unload does it."""
    hass, entry = FakeHass(), FakeEntry()
    async_create_provider(hass, entry, FakeCoordinator())
    entry.unload()

    assert hass.data[DATA_PROVIDERS] == {}


def test_every_poll_tells_the_hub_to_refetch():
    hass, coordinator = FakeHass(), FakeCoordinator()
    async_create_provider(hass, FakeEntry(), coordinator)

    assert len(coordinator.listeners) == 1, "registration follows the coordinator"


def test_registering_without_a_hub_costs_nothing():
    """No hub installed means nobody reads the dict -- and nothing raises."""
    hass = FakeHass()
    provider = async_create_provider(hass, FakeEntry(), FakeCoordinator())
    provider.async_notify()  # dispatcher signal into the void


def test_nodes_carry_state_icon_and_metadata(adapter):
    nodes = {node["id"]: node for node in adapter.async_data()["nodes"]}

    cco = nodes["AA:BB"]
    assert cco["label"] == "Router EG"
    assert cco["state"] == "online"
    assert cco["icon"] == "mdi:router-network"
    assert cco["metadata"]["firmware"] == "1.2.3"
    assert cco["layer_id"] == "network_powerline"


def test_a_home_assistant_rename_beats_the_wire_name():
    """Otherwise the plan shows a MAC the moment the adapter says nothing."""
    coordinator = FakeCoordinator()
    coordinator.renamed["AA:BB"] = "Wohnzimmer"
    adapter = PowerlineSpatialAdapter(FakeHass(), coordinator)

    nodes = {node["id"]: node for node in adapter.async_data()["nodes"]}
    assert nodes["AA:BB"]["label"] == "Wohnzimmer"


def test_no_name_anywhere_falls_back_to_the_mac():
    coordinator = FakeCoordinator(
        topology={
            "nodes": [{"mac": "11:22", "online": True, "role": "Station"}],
            "edges": [],
        }
    )
    adapter = PowerlineSpatialAdapter(FakeHass(), coordinator)

    nodes = {node["id"]: node for node in adapter.async_data()["nodes"]}
    assert nodes["11:22"]["label"] == "11:22"


def test_offline_adapters_report_offline(adapter):
    nodes = {node["id"]: node for node in adapter.async_data()["nodes"]}
    offline = nodes["CC:DD"]
    assert offline["state"] == "offline"
    assert offline["icon"] == "mdi:lan-disconnect"
    assert offline.get("actions", []) == [], (
        "no actions on an adapter that is gone"
    )


def test_nodes_have_no_position(adapter):
    """Placement is the hub's job -- we genuinely don't know where they are."""
    assert all("position" not in node for node in adapter.async_data()["nodes"])


def test_edges_map_quality_to_the_shared_vocabulary(adapter):
    edge = adapter.async_data()["edges"][0]

    assert (edge["source"], edge["target"]) == ("AA:BB", "CC:DD")
    assert edge["quality"] == "fair", "powerline's yellow tier"
    assert edge["value"] == 550
    assert edge["dashed"] is False
    assert edge["animated"] is True
    assert 2.0 <= edge["width"] <= 8.0
    assert edge["metadata"]["tx_phy_rate"] == 560


def test_estimated_edges_are_dashed():
    topology = _topology()
    topology["edges"][0]["estimated"] = True
    adapter = PowerlineSpatialAdapter(FakeHass(), FakeCoordinator(topology))

    assert adapter.async_data()["edges"][0]["dashed"] is True


def test_edge_id_round_trips_into_a_history_lookup(adapter):
    edge_id = adapter.async_data()["edges"][0]["id"]
    series = adapter.history("edge", edge_id, 24)

    assert adapter.coordinator.series_calls == [("AA:BB", "CC:DD", 24)]
    assert series[0]["value"] == 560


def test_history_is_only_offered_for_edges(adapter):
    assert adapter.history("node", "AA:BB", 24) == []
    assert adapter.history("edge", "no-separator", 24) == []


def test_empty_coordinator_data_yields_an_empty_payload():
    coordinator = FakeCoordinator()
    coordinator.data = None
    adapter = PowerlineSpatialAdapter(FakeHass(), coordinator)

    assert adapter.async_data() == {"nodes": [], "edges": []}


def test_actions_reach_the_coordinator(adapter):
    assert asyncio.run(
        adapter.async_action("node", "AA:BB", "led_on", {})
    ) == {"success": True}
    asyncio.run(adapter.async_action("node", "AA:BB", "led_off", {}))
    asyncio.run(adapter.async_action("node", "AA:BB", "restart", {}))

    assert adapter.coordinator.led_calls == [("AA:BB", True), ("AA:BB", False)]
    assert adapter.coordinator.restart_calls == ["AA:BB"]


def test_unknown_actions_are_rejected(adapter):
    with pytest.raises(ValueError):
        asyncio.run(adapter.async_action("node", "AA:BB", "self_destruct", {}))
    with pytest.raises(ValueError):
        asyncio.run(adapter.async_action("edge", "AA:BB__CC:DD", "led_on", {}))


def test_the_provider_ships_its_own_icons():
    """The hub decides where a node goes; we decide what it looks like."""
    hass = FakeHass()
    async_create_provider(hass, FakeEntry(), FakeCoordinator())
    registration = hass.data[DATA_PROVIDERS]["powerline"]

    assert registration["capabilities"]["custom_icons"] is True
    icons = registration["icon_set"]
    assert icons, "an integration with no icons of its own is anonymous"
    for name, icon in icons.items():
        assert name.startswith("mdi:"), (
            "keyed by the icon a node asks for, so a renderer that ignores "
            "icon sets still draws something sensible"
        )
        assert icon["svg"].startswith("<svg")


def test_every_icon_a_node_asks_for_is_one_we_ship():
    hass = FakeHass()
    async_create_provider(hass, FakeEntry(), FakeCoordinator())
    registration = hass.data[DATA_PROVIDERS]["powerline"]
    wanted = {node["icon"] for node in registration["data"]()["nodes"]}

    assert wanted <= set(registration["icon_set"])


def test_the_popup_can_get_back_to_our_own_panel():
    hass = FakeHass()
    async_create_provider(hass, FakeEntry(), FakeCoordinator())
    registration = hass.data[DATA_PROVIDERS]["powerline"]

    assert registration["panel_url"] == "/powerline"
    assert registration["panel_url"].startswith("/"), "inside this instance"
