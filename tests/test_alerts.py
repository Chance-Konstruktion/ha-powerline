"""Unit tests for topology alerts and conservative cause hints."""

import time
from datetime import datetime, timedelta, timezone

from custom_components.powerline.alert_translations import alert_notification_text
from custom_components.powerline.alerts import TopologyAlerts
from custom_components.powerline.history import BUCKET_SECONDS, TopologyHistory, edge_key

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"
MAC_C = "AA:BB:CC:DD:EE:03"

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


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


def _edge(source, destination, average, **extra):
    return {
        "source": source,
        "destination": destination,
        "average_rate": average,
        **extra,
    }


def _history_for_profiles(profiles, hours=24, end=NOW):
    """Record steady synthetic rates at 15-minute intervals."""
    history = TopologyHistory()
    for i in range(hours * 4, 0, -1):
        when = end - timedelta(minutes=i * 15)
        edges = [
            {"source": source, "destination": destination, "average_rate": rate}
            for source, destination, rate in profiles
        ]
        history.record({"edges": edges}, now=when)
    return history


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
    # First check: everything is "new" -> suppressed
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


def test_repeated_daily_dips_suggest_a_consumer():
    history = TopologyHistory()
    for i in range(8 * 24 * 4, 0, -1):
        when = NOW - timedelta(minutes=i * 15)
        # A recurring one-hour dip surrounded by a stable daily baseline.
        rate = 100 if when.hour == 8 else 600
        history.record(
            {"edges": [{"source": MAC_A, "destination": MAC_B, "average_rate": rate}]},
            now=when,
        )

    alerts = TopologyAlerts()
    fired = alerts.check(
        {"edges": [_edge(MAC_A, MAC_B, 600)]}, history, [], now=NOW
    )
    assert any(
        alert.get("cause") == "recurring_load" for alert in fired
    )
    # The same active pattern should not produce a fresh alert every poll.
    assert alerts.check(
        {"edges": [_edge(MAC_A, MAC_B, 600)]},
        history,
        [],
        now=NOW + timedelta(minutes=2),
    ) == []


def test_persistently_weak_route_is_compared_with_healthy_peers():
    profiles = [
        (MAC_A, MAC_B, 100),
        (MAC_A, MAC_C, 600),
        (MAC_B, MAC_C, 500),
    ]
    history = _history_for_profiles(profiles)
    edges = [
        _edge(MAC_A, MAC_B, 100),
        _edge(MAC_A, MAC_C, 600),
        _edge(MAC_B, MAC_C, 500),
    ]

    fired = TopologyAlerts().check({"edges": edges}, history, [], now=NOW)
    assert any(
        alert.get("cause") == "isolated_weak_link"
        and alert["source"] == MAC_A
        and alert["destination"] == MAC_B
        for alert in fired
    )


def test_directional_tx_rx_asymmetry_requires_thirty_minutes():
    history = _history_for_profiles([(MAC_A, MAC_B, 300)])
    edge = _edge(
        MAC_A,
        MAC_B,
        300,
        tx_phy_rate=100,
        rx_phy_rate=500,
    )
    alerts = TopologyAlerts()

    assert not any(
        alert.get("cause") == "directional_asymmetry"
        for alert in alerts.check({"edges": [edge]}, history, [], now=NOW)
    )
    assert not any(
        alert.get("cause") == "directional_asymmetry"
        for alert in alerts.check(
            {"edges": [edge]}, history, [], now=NOW + timedelta(minutes=29)
        )
    )
    fired = alerts.check(
        {"edges": [edge]}, history, [], now=NOW + timedelta(minutes=30)
    )
    hint = next(
        alert for alert in fired if alert.get("cause") == "directional_asymmetry"
    )
    assert hint["tx"] == 100
    assert hint["rx"] == 500


def test_estimated_link_never_reports_directional_asymmetry():
    history = _history_for_profiles([(MAC_A, MAC_B, 300)])
    edge = _edge(
        MAC_A,
        MAC_B,
        300,
        tx_phy_rate=100,
        rx_phy_rate=500,
        estimated=True,
    )
    alerts = TopologyAlerts()

    for moment in (NOW, NOW + timedelta(minutes=45)):
        fired = alerts.check({"edges": [edge]}, history, [], now=moment)
        assert not any(
            alert.get("cause") == "directional_asymmetry" for alert in fired
        )


def test_no_cause_hint_without_an_observed_pattern():
    history = _history_for_profiles([(MAC_A, MAC_B, 100)])
    edge = _edge(MAC_A, MAC_B, 100, tx_phy_rate=100, rx_phy_rate=100)

    fired = TopologyAlerts().check({"edges": [edge]}, history, [], now=NOW)
    assert not any(alert["type"] == "link_cause" for alert in fired)


def test_cause_hints_are_suppressed_until_enough_history_exists():
    history = TopologyHistory()
    edge = _edge(
        MAC_A,
        MAC_B,
        100,
        tx_phy_rate=100,
        rx_phy_rate=100,
    )
    for i in (2, 1):
        history.record(
            {"edges": [{"source": MAC_A, "destination": MAC_B, "average_rate": 100}]},
            now=NOW - timedelta(minutes=i * 15),
        )

    fired = TopologyAlerts().check({"edges": [edge]}, history, [], now=NOW)
    assert not any(alert["type"] == "link_cause" for alert in fired)


def test_cause_notifications_have_german_and_english_text():
    german = alert_notification_text(
        "de-DE", "recurring_load", source="Adapter A", destination="Adapter B"
    )
    english = alert_notification_text(
        "en-US", "recurring_load", source="Adapter A", destination="Adapter B"
    )

    assert "wiederkehrende Störung" in german["title"]
    assert "recurring interference" in english["title"]


def test_check_twelve_adapters_with_thirty_days_history_under_100ms():
    """Regression benchmark for the worst-case fully connected 12-adapter graph."""
    adapters = [f"adapter-{i:02d}" for i in range(12)]
    edges = [
        _edge(
            source,
            destination,
            500,
            tx_phy_rate=500,
            rx_phy_rate=500,
        )
        for i, source in enumerate(adapters)
        for destination in adapters[i + 1 :]
    ]
    last_bucket = int(NOW.timestamp() // BUCKET_SECONDS) * BUCKET_SECONDS
    first_bucket = last_bucket - 30 * 24 * 60 * 60 + BUCKET_SECONDS
    bucket_starts = range(first_bucket, last_bucket + 1, BUCKET_SECONDS)
    assert len(bucket_starts) == 30 * 24 * 4

    history = TopologyHistory()
    history.restore(
        {
            "buckets": {
                edge_key(edge["source"], edge["destination"]): {
                    str(start): [500, 1, 500, 500] for start in bucket_starts
                }
                for edge in edges
            },
            "online": {},
        }
    )

    started = time.perf_counter()
    TopologyAlerts().check({"edges": edges}, history, [], now=NOW)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1, f"12-adapter, 30-day alert check took {elapsed:.3f}s"
