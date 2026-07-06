"""Unit tests for the Powerline topology graph model."""

from datetime import datetime, timedelta, timezone

from custom_components.powerline.topology import TopologyManager

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"
MAC_C = "AA:BB:CC:DD:EE:03"

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def _dev(mac, tx=0, rx=0, online=True, **extra):
    return {"mac": mac, "tx_rate": tx, "rx_rate": rx, "_online": online, **extra}


# ── Nodes ──────────────────────────────────────────────────


def test_node_payload_fields():
    manager = TopologyManager()
    topology = manager.update(
        {
            MAC_A: _dev(
                MAC_A,
                model="TL-PA7017",
                firmware_ver="1.2.3",
                chipset="broadcom",
                cco_mac=MAC_A,
            ),
            MAC_B: _dev(MAC_B),
        },
        now=NOW,
    )

    node_a, node_b = topology["nodes"]
    assert node_a["mac"] == MAC_A
    assert node_a["model"] == "TL-PA7017"
    assert node_a["firmware"] == "1.2.3"
    assert node_a["chipset"] == "broadcom"
    assert node_a["role"] == "CCo"
    assert node_a["online"] is True
    assert node_a["last_update"] == NOW.isoformat()
    assert node_b["role"] == "Station"


def test_role_unknown_without_cco_info():
    manager = TopologyManager()
    topology = manager.update({MAC_A: _dev(MAC_A)}, now=NOW)
    assert topology["nodes"][0]["role"] == "unknown"


# ── Edges: measured pairwise links (tier 1) ────────────────


def test_measured_links_become_edges():
    manager = TopologyManager()
    topology = manager.update(
        {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)},
        links={(MAC_A, MAC_B): {"tx_rate": 720, "rx_rate": 610}},
        now=NOW,
    )

    assert len(topology["edges"]) == 1
    edge = topology["edges"][0]
    assert edge["source"] == MAC_A
    assert edge["destination"] == MAC_B
    assert edge["tx_phy_rate"] == 720
    assert edge["rx_phy_rate"] == 610
    assert edge["average_rate"] == 665
    assert edge["link_quality"] == "yellow"
    assert edge["estimated"] is False


def test_reverse_measurement_is_normalised_to_source():
    manager = TopologyManager()
    # B measured the link: B→A 610, B←A 720. On the edge (A, B) that is
    # tx (A→B) = 720 and rx = 610.
    topology = manager.update(
        {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)},
        links={(MAC_B, MAC_A): {"tx_rate": 610, "rx_rate": 720}},
        now=NOW,
    )

    edge = topology["edges"][0]
    assert edge["source"] == MAC_A
    assert edge["tx_phy_rate"] == 720
    assert edge["rx_phy_rate"] == 610


def test_both_directions_merge_keeping_higher_reading():
    manager = TopologyManager()
    topology = manager.update(
        {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)},
        links={
            (MAC_A, MAC_B): {"tx_rate": 700, "rx_rate": 0},
            (MAC_B, MAC_A): {"tx_rate": 600, "rx_rate": 690},
        },
        now=NOW,
    )

    edge = topology["edges"][0]
    assert edge["tx_phy_rate"] == 700  # max(700, reverse rx 690)
    assert edge["rx_phy_rate"] == 600  # max(0, reverse tx 600)


def test_links_to_offline_adapters_are_ignored():
    manager = TopologyManager()
    topology = manager.update(
        {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B, online=False)},
        links={(MAC_A, MAC_B): {"tx_rate": 500, "rx_rate": 500}},
        now=NOW,
    )
    assert topology["edges"] == []


# ── Edges: fallbacks (tiers 2 and 3) ───────────────────────


def test_two_adapters_fall_back_to_device_rates():
    manager = TopologyManager()
    topology = manager.update(
        {MAC_A: _dev(MAC_A, tx=413, rx=395), MAC_B: _dev(MAC_B, tx=395, rx=413)},
        now=NOW,
    )

    assert len(topology["edges"]) == 1
    edge = topology["edges"][0]
    assert edge["tx_phy_rate"] == 413
    assert edge["rx_phy_rate"] == 395
    assert edge["estimated"] is False


def test_star_fallback_is_marked_estimated_and_uses_peer_rates():
    manager = TopologyManager()
    topology = manager.update(
        {
            MAC_A: _dev(MAC_A, tx=900, rx=900, cco_mac=MAC_B),
            MAC_B: _dev(MAC_B, tx=100, rx=110, cco_mac=MAC_B),
            MAC_C: _dev(MAC_C, tx=220, rx=230),
        },
        now=NOW,
    )

    # Star from the CCo (B): edges B–A and B–C, each with the PEER's rates —
    # not one adapter's rates copied onto every edge.
    edges = {(e["source"], e["destination"]): e for e in topology["edges"]}
    assert set(edges) == {(MAC_A, MAC_B), (MAC_B, MAC_C)}
    assert all(e["estimated"] for e in edges.values())
    assert edges[(MAC_A, MAC_B)]["tx_phy_rate"] == 900
    assert edges[(MAC_B, MAC_C)]["rx_phy_rate"] == 220  # C's tx, seen from B


# ── Offline handling and retention ─────────────────────────


def test_vanished_adapter_goes_offline_then_expires():
    manager = TopologyManager(offline_retention_seconds=3600)
    manager.update({MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)}, now=NOW)

    later = NOW + timedelta(minutes=5)
    topology = manager.update({MAC_A: _dev(MAC_A)}, now=later)
    offline = [n for n in topology["nodes"] if n["mac"] == MAC_B]
    assert offline and offline[0]["online"] is False

    expired = NOW + timedelta(hours=2)
    topology = manager.update({MAC_A: _dev(MAC_A)}, now=expired)
    assert [n["mac"] for n in topology["nodes"]] == [MAC_A]


def test_offline_flag_in_inventory_marks_node_offline():
    manager = TopologyManager()
    manager.update({MAC_A: _dev(MAC_A)}, now=NOW)
    topology = manager.update(
        {MAC_A: _dev(MAC_A, online=False)}, now=NOW + timedelta(minutes=2)
    )
    assert topology["nodes"][0]["online"] is False


# ── Events ─────────────────────────────────────────────────


def test_adapter_and_connection_events():
    manager = TopologyManager()
    manager.update(
        {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)},
        links={(MAC_A, MAC_B): {"tx_rate": 500, "rx_rate": 500}},
        now=NOW,
    )
    events = manager.drain_events()
    kinds = [e["event"] for e in events]
    assert kinds.count("adapter_online") == 2
    assert "connection_added" in kinds

    # B vanishes: adapter_offline + connection_lost
    manager.update({MAC_A: _dev(MAC_A)}, now=NOW + timedelta(minutes=2))
    kinds = [e["event"] for e in manager.drain_events()]
    assert "adapter_offline" in kinds
    assert "connection_lost" in kinds

    # B returns: adapter_online again
    manager.update(
        {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)}, now=NOW + timedelta(minutes=4)
    )
    kinds = [e["event"] for e in manager.drain_events()]
    assert "adapter_online" in kinds


def test_link_rate_change_events_are_noise_filtered():
    manager = TopologyManager()
    devices = {MAC_A: _dev(MAC_A), MAC_B: _dev(MAC_B)}
    manager.update(
        devices, links={(MAC_A, MAC_B): {"tx_rate": 500, "rx_rate": 500}}, now=NOW
    )
    manager.drain_events()

    # 2% jitter within the same quality class: no event
    manager.update(
        devices,
        links={(MAC_A, MAC_B): {"tx_rate": 510, "rx_rate": 510}},
        now=NOW + timedelta(minutes=2),
    )
    assert manager.drain_events() == []

    # Big drop: link_rate_changed fires with old and new average
    manager.update(
        devices,
        links={(MAC_A, MAC_B): {"tx_rate": 120, "rx_rate": 120}},
        now=NOW + timedelta(minutes=4),
    )
    events = manager.drain_events()
    assert len(events) == 1
    assert events[0]["event"] == "link_rate_changed"
    assert events[0]["previous_average"] == 510
    assert events[0]["average"] == 120
    assert events[0]["link_quality"] == "red"


# ── Analysis ───────────────────────────────────────────────


def test_analysis_worst_link_best_adapter_and_lists():
    manager = TopologyManager()
    topology = manager.update(
        {
            MAC_A: _dev(MAC_A),
            MAC_B: _dev(MAC_B),
            MAC_C: _dev(MAC_C, online=False),
        },
        links={
            (MAC_A, MAC_B): {"tx_rate": 800, "rx_rate": 800},
        },
        now=NOW,
    )

    analysis = topology["analysis"]
    assert analysis["worst_link"]["source"] == MAC_A
    assert analysis["best_adapter"] in (MAC_A, MAC_B)
    assert analysis["offline_adapters"] == [MAC_C]
    assert analysis["new_adapters"] == [MAC_A, MAC_B, MAC_C]


def test_link_quality_thresholds():
    quality = TopologyManager._link_quality
    assert quality(701) == "green"
    assert quality(700) == "yellow"
    assert quality(400) == "yellow"
    assert quality(399) == "orange"
    assert quality(150) == "orange"
    assert quality(149) == "red"
