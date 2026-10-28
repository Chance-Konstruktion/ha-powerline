"""Unit tests for smart topology alerts."""

from datetime import datetime, timedelta, timezone

from custom_components.powerline.alerts import TopologyAlerts
from custom_components.powerline.history import TopologyHistory

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"
MAC_C = "AA:BB:CC:DD:EE:03"

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
EDGE_AB = (MAC_A, MAC_B)
EDGE_AC = (MAC_A, MAC_C)
EDGE_BC = (MAC_B, MAC_C)


def _topology(avg, new_adapters=()):
    return {
        "nodes": [
            {"mac": MAC_A, "online": True},
            {"mac": MAC_B, "online": True},
        ],
        "edges": [
            {"source": MAC_A, "destination": MAC_B, "average_rate": avg},
        ],
        "analysis": {"new_adapters": list(new_adapters)},
    }


def _history_with_baseline(avg=600, hours=6, end=NOW):
    history = TopologyHistory()
    for i in range(int(hours * 30), 0, -1):
        history.record(_topology(avg), now=end - timedelta(minutes=i * 2))
    return history


def _network_topology(rates):
    edges = []
    for (source, destination), (tx, rx) in rates.items():
        edges.append(
            {
                "source": source,
                "destination": destination,
                "tx_phy_rate": tx,
                "rx_phy_rate": rx,
                "average_rate": round((tx + rx) / 2),
                "estimated": False,
            }
        )
    return {
        "nodes": [
            {"mac": MAC_A, "online": True},
            {"mac": MAC_B, "online": True},
            {"mac": MAC_C, "online": True},
        ],
        "edges": edges,
        "analysis": {"new_adapters": []},
    }


def _synthetic_history(rate_for, edge_pairs=(EDGE_AB,)):
    """Build four days of quarter-hour buckets plus raw two-minute samples."""
    history = TopologyHistory()

    def record_at(moment):
        edges = []
        for source, destination in edge_pairs:
            tx, rx = rate_for((source, destination), moment)
            edges.append(
                {
                    "source": source,
                    "destination": destination,
                    "tx_phy_rate": tx,
                    "rx_phy_rate": rx,
                    "average_rate": round((tx + rx) / 2),
                    "estimated": False,
                }
            )
        history.record({"nodes": [], "edges": edges}, now=moment)

    moment = NOW - timedelta(hours=96)
    raw_start = NOW - timedelta(hours=1)
    while moment < raw_start:
        record_at(moment)
        moment += timedelta(minutes=15)

    moment = raw_start
    while moment <= NOW:
        record_at(moment)
        moment += timedelta(minutes=2)
    return history


def _fire_slow_alert(history, current_rates, edge_pairs):
    alerts = TopologyAlerts()
    healthy_rates = {pair: (600, 600) for pair in edge_pairs}
    healthy = _network_topology(healthy_rates)
    slow = _network_topology(current_rates)

    # Prime the detector, then sustain the slowdown long enough to alert.
    alerts.check(healthy, history, [], now=NOW - timedelta(minutes=90))
    alerts.check(slow, history, [], now=NOW - timedelta(minutes=70))
    fired = alerts.check(slow, history, [], now=NOW)
    return next(
        alert for alert in fired
        if alert["type"] == "link_slow"
        and alert["source"] == EDGE_AB[0]
        and alert["destination"] == EDGE_AB[1]
    )


def test_slow_link_alert_after_sustained_drop():
    history = _history_with_baseline(600)
    alerts = TopologyAlerts()
    alerts.check(_topology(600), history, [], now=NOW - timedelta(minutes=40))

    # Drop begins: below 60% of baseline, but not yet 30 minutes
    assert alerts.check(_topology(150), history, [], now=NOW - timedelta(minutes=31)) == []
    # Still slow after 30+ minutes: alert fires exactly once
    fired = alerts.check(_topology(150), history, [], now=NOW)
    assert len(fired) == 1
    alert = fired[0]
    assert alert["type"] == "link_slow"
    assert alert["average"] == 150
    assert alert["baseline"] > 500
    assert alert["minutes"] >= 30
    # No repeat while it stays slow
    assert alerts.check(_topology(150), history, [], now=NOW + timedelta(minutes=5)) == []


def test_slow_link_alert_rearms_after_recovery():
    history = _history_with_baseline(600)
    alerts = TopologyAlerts()
    alerts.check(_topology(600), history, [], now=NOW - timedelta(minutes=90))

    alerts.check(_topology(150), history, [], now=NOW - timedelta(minutes=80))
    assert alerts.check(_topology(150), history, [], now=NOW - timedelta(minutes=45))
    # Full recovery clears the state …
    alerts.check(_topology(600), history, [], now=NOW - timedelta(minutes=40))
    # … so a new sustained drop alerts again
    alerts.check(_topology(150), history, [], now=NOW - timedelta(minutes=35))
    assert alerts.check(_topology(150), history, [], now=NOW)


def test_short_dip_does_not_alert():
    history = _history_with_baseline(600)
    alerts = TopologyAlerts()
    alerts.check(_topology(600), history, [], now=NOW - timedelta(minutes=20))

    assert alerts.check(_topology(150), history, [], now=NOW - timedelta(minutes=10)) == []
    assert alerts.check(_topology(600), history, [], now=NOW) == []


def test_adapter_removed_and_new_adapter_alerts():
    history = _history_with_baseline(600)
    alerts = TopologyAlerts()
    # First check: everything is "new" → suppressed
    assert (
        alerts.check(_topology(600, new_adapters=[MAC_A, MAC_B]), history, [], now=NOW)
        == []
    )

    fired = alerts.check(
        _topology(600, new_adapters=[MAC_C]),
        history,
        [{"event": "adapter_removed", "mac": MAC_B, "timestamp": NOW.isoformat()}],
        now=NOW + timedelta(minutes=2),
    )

    kinds = {a["type"]: a for a in fired}
    assert kinds["adapter_removed"]["mac"] == MAC_B
    assert kinds["adapter_new"]["mac"] == MAC_C


def test_daily_time_of_day_dips_add_interference_hint():
    def rate_for(edge, moment):
        if edge == EDGE_AB and moment.hour == 2 and moment.minute == 0:
            return 100, 100
        return 600, 600

    history = _synthetic_history(rate_for)
    alert = _fire_slow_alert(history, {EDGE_AB: (150, 150)}, (EDGE_AB,))
    assert alert["cause"] == "daily_interference"
    assert alert["action"] == "check_noise_sources"


def test_persistently_weak_link_vs_healthy_peers_adds_outlet_hint():
    pairs = (EDGE_AB, EDGE_AC, EDGE_BC)

    def rate_for(edge, moment):
        if edge == EDGE_AB and moment >= NOW - timedelta(hours=8):
            return 100, 100
        if edge == EDGE_AC:
            return 700, 700
        if edge == EDGE_BC:
            return 650, 650
        return 600, 600

    history = _synthetic_history(rate_for, pairs)
    alert = _fire_slow_alert(
        history,
        {EDGE_AB: (100, 100), EDGE_AC: (700, 700), EDGE_BC: (650, 650)},
        pairs,
    )
    assert alert["cause"] == "power_strip_or_filter"
    assert alert["action"] == "use_wall_socket"


def test_persistent_tx_rx_asymmetry_adds_end_interference_hint():
    def rate_for(edge, moment):
        if edge == EDGE_AB and moment >= NOW - timedelta(hours=1):
            return 100, 500
        return 600, 600

    history = _synthetic_history(rate_for)
    alert = _fire_slow_alert(history, {EDGE_AB: (100, 500)}, (EDGE_AB,))
    assert alert["cause"] == "directional_interference"
    assert alert["action"] == "check_both_ends"


def test_persistently_low_balanced_direct_link_adds_phase_hint():
    def rate_for(edge, moment):
        if edge == EDGE_AB and moment >= NOW - timedelta(hours=6):
            return 180, 200
        return 600, 600

    history = _synthetic_history(rate_for)
    alert = _fire_slow_alert(history, {EDGE_AB: (180, 200)}, (EDGE_AB,))
    assert alert["cause"] == "phase_or_rcd"
    assert alert["action"] == "check_circuit_phase"


def test_counterexample_without_recurrent_pattern_has_no_cause_hint():
    history = _synthetic_history(lambda edge, moment: (600, 600))
    alert = _fire_slow_alert(history, {EDGE_AB: (150, 150)}, (EDGE_AB,))
    assert "cause" not in alert
    assert "action" not in alert
