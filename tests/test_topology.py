"""Unit tests for the Powerline topology graph."""

from datetime import datetime, timezone

from custom_components.powerline.topology import TopologyManager

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"

def test_builds_nodes_and_edges_from_devices():
    manager = TopologyManager()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    topology = manager.update(
        {
            MAC_A: {
                "mac": MAC_A,
                "name": "Wohnzimmer",
                "manufacturer": "TP-Link",
                "model": "TL-PA7017",
                "firmware_ver": "1.2.3",
                "hardware_revision": "v4",
                "role": "CCo",
                "tx_rate": 720,
                "rx_rate": 610,
                "errors": 2,
                "_online": True,
            },
            MAC_B: {"mac": MAC_B, "tx_rate": 610, "rx_rate": 720, "_online": True},
        },
        now=now,
    )

    assert topology == {
        "nodes": [
            {
                "mac": MAC_A,
                "name": "Wohnzimmer",
                "manufacturer": "TP-Link",
                "model": "TL-PA7017",
                "firmware": "1.2.3",
                "hardware_revision": "v4",
                "role": "CCo",
                "online": True,
                "last_update": "2026-07-06T12:00:00+00:00",
            },
            {
                "mac": MAC_B,
                "name": MAC_B,
                "manufacturer": "Powerline",
                "model": "",
                "firmware": "",
                "hardware_revision": "",
                "role": "Station",
                "online": True,
                "last_update": "2026-07-06T12:00:00+00:00",
            },
        ],
        "edges": [
            {
                "source": MAC_A,
                "destination": MAC_B,
                "tx_phy_rate": 720,
                "rx_phy_rate": 610,
                "average_rate": 665,
                "link_quality": "yellow",
                "errors": 2,
                "timestamp": "2026-07-06T12:00:00+00:00",
            }
        ],
        "analysis": {
            "worst_link": {
                "source": MAC_A,
                "destination": MAC_B,
                "tx_phy_rate": 720,
                "rx_phy_rate": 610,
                "average_rate": 665,
                "link_quality": "yellow",
                "errors": 2,
                "timestamp": "2026-07-06T12:00:00+00:00",
            },
            "best_adapter": MAC_A,
            "offline_adapters": [],
            "new_adapters": [MAC_A, MAC_B],
        },
    }


def test_marks_missing_known_devices_offline_and_keeps_last_update():
    manager = TopologyManager()
    first = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    second = datetime(2026, 7, 6, 12, 2, tzinfo=timezone.utc)

    manager.update({MAC_A: {"mac": MAC_A}, MAC_B: {"mac": MAC_B}}, now=first)
    topology = manager.update({MAC_A: {"mac": MAC_A}}, now=second)

    offline = next(node for node in topology["nodes"] if node["mac"] == MAC_B)
    assert offline["online"] is False
    assert offline["last_update"] == "2026-07-06T12:00:00+00:00"


def test_removes_offline_devices_after_expiry():
    manager = TopologyManager(offline_retention_seconds=60)
    first = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    expired = datetime(2026, 7, 6, 12, 2, tzinfo=timezone.utc)

    manager.update({MAC_A: {"mac": MAC_A}, MAC_B: {"mac": MAC_B}}, now=first)
    topology = manager.update({MAC_A: {"mac": MAC_A}}, now=expired)

    assert [node["mac"] for node in topology["nodes"]] == [MAC_A]


def test_detects_new_lost_and_rate_change_events():
    manager = TopologyManager()
    first = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    second = datetime(2026, 7, 6, 12, 1, tzinfo=timezone.utc)
    third = datetime(2026, 7, 6, 12, 2, tzinfo=timezone.utc)

    manager.update({MAC_A: {"mac": MAC_A}, MAC_B: {"mac": MAC_B, "tx_rate": 500}}, now=first)
    manager.update({MAC_A: {"mac": MAC_A}, MAC_B: {"mac": MAC_B, "tx_rate": 650}}, now=second)
    assert manager.events[-1]["event"] == "link_rate_changed"

    manager.update({MAC_A: {"mac": MAC_A}}, now=third)
    event_names = [event["event"] for event in manager.events]
    assert "adapter_offline" in event_names
    assert "connection_lost" in event_names


def test_drain_events_returns_and_clears_pending_events():
    manager = TopologyManager()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    manager.update({MAC_A: {"mac": MAC_A}}, now=now)

    events = manager.drain_events()
    assert [event["event"] for event in events] == ["adapter_online"]
    assert manager.drain_events() == []


def test_topology_analysis_identifies_bottleneck_best_and_offline():
    manager = TopologyManager()
    first = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    second = datetime(2026, 7, 6, 12, 1, tzinfo=timezone.utc)

    manager.update(
        {
            MAC_A: {"mac": MAC_A, "role": "CCo", "tx_rate": 720, "rx_rate": 680},
            MAC_B: {"mac": MAC_B, "tx_rate": 180, "rx_rate": 160},
        },
        now=first,
    )
    topology = manager.update(
        {MAC_A: {"mac": MAC_A, "role": "CCo", "tx_rate": 720, "rx_rate": 680}},
        now=second,
    )

    assert topology["analysis"] == {
        "worst_link": None,
        "best_adapter": MAC_A,
        "offline_adapters": [MAC_B],
        "new_adapters": [],
    }
