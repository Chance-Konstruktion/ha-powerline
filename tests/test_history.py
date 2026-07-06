"""Unit tests for the topology history (link rates over time)."""

from datetime import datetime, timedelta, timezone

from custom_components.powerline.history import TopologyHistory

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def _topology(avg, online=True):
    return {
        "nodes": [
            {"mac": MAC_A, "online": online},
            {"mac": MAC_B, "online": True},
        ],
        "edges": [
            {"source": MAC_A, "destination": MAC_B, "average_rate": avg},
        ],
    }


def _fill(history, hours, avg, end=NOW, step_minutes=2):
    """Record `hours` of samples with the given average rate, ending at `end`."""
    steps = int(hours * 60 / step_minutes)
    for i in range(steps, 0, -1):
        history.record(_topology(avg), now=end - timedelta(minutes=i * step_minutes))


def test_recent_series_returns_raw_samples():
    history = TopologyHistory()
    for i in range(5):
        history.record(_topology(500 + i), now=NOW - timedelta(minutes=10 - i * 2))

    series = history.series(MAC_A, MAC_B, hours=1, now=NOW)

    assert len(series) == 5
    assert series[-1]["avg"] == 504


def test_bucket_series_aggregates_with_min_max():
    history = TopologyHistory()
    # Two samples in the same 15-min bucket
    base = datetime(2026, 7, 6, 12, 1, tzinfo=timezone.utc)
    history.record(_topology(400), now=base)
    history.record(_topology(600), now=base + timedelta(minutes=4))

    series = history.series(MAC_A, MAC_B, hours=24, now=base + timedelta(hours=1))

    assert len(series) == 1
    assert series[0]["avg"] == 500
    assert series[0]["min"] == 400
    assert series[0]["max"] == 600


def test_recent_samples_pruned_after_one_hour():
    history = TopologyHistory()
    history.record(_topology(500), now=NOW - timedelta(hours=2))
    history.record(_topology(510), now=NOW)

    assert len(history.series(MAC_A, MAC_B, hours=1, now=NOW)) == 1
    # The old sample still lives in the bucket tier
    assert len(history.series(MAC_A, MAC_B, hours=24, now=NOW)) == 2


def test_baseline_excludes_ongoing_slowdown():
    history = TopologyHistory()
    _fill(history, hours=6, avg=600, end=NOW - timedelta(minutes=30))
    # Last 30 minutes: collapsed rate — must NOT drag the baseline down
    _fill(history, hours=0.5, avg=100, end=NOW)

    baseline = history.baseline(MAC_A, MAC_B, now=NOW)

    assert baseline is not None
    assert baseline > 500


def test_instability_flat_vs_jumpy():
    flat = TopologyHistory()
    _fill(flat, hours=4, avg=500)
    assert flat.instability(MAC_A, MAC_B, now=NOW) == 0

    jumpy = TopologyHistory()
    base = NOW - timedelta(hours=4)
    for i in range(120):
        avg = 800 if i % 2 else 150
        jumpy.record(_topology(avg), now=base + timedelta(minutes=i * 2))
    value = jumpy.instability(MAC_A, MAC_B, now=NOW)
    assert value is not None and value > 0.2

    worst = jumpy.most_unstable(now=NOW)
    assert worst["source"] == MAC_A
    assert worst["destination"] == MAC_B
    assert worst["instability"] == value


def test_online_ratio_counts_offline_windows():
    history = TopologyHistory()
    # online 12h, offline 6h, online again 6h
    history.record(_topology(500), now=NOW - timedelta(hours=24))
    history.record(_topology(500, online=False), now=NOW - timedelta(hours=12))
    history.record(_topology(500), now=NOW - timedelta(hours=6))

    ratio = history.online_ratio(MAC_A, hours=24, now=NOW)

    assert ratio is not None
    assert 0.7 < ratio < 0.8  # 18 of 24 hours online


def test_persistence_roundtrip():
    history = TopologyHistory()
    _fill(history, hours=2, avg=450)

    restored = TopologyHistory()
    restored.restore(history.as_dict())

    assert restored.series(MAC_A, MAC_B, hours=24, now=NOW) == history.series(
        MAC_A, MAC_B, hours=24, now=NOW
    )
    assert restored.online_ratio(MAC_A, hours=24, now=NOW) == history.online_ratio(
        MAC_A, hours=24, now=NOW
    )
