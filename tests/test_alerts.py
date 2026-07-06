"""Unit tests for smart topology alerts."""

from datetime import datetime, timedelta, timezone

from custom_components.powerline.alerts import TopologyAlerts
from custom_components.powerline.history import TopologyHistory

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
