"""Synthetic Recorder-history tests for consumer/link correlation."""

from custom_components.powerline.alert_translations import alert_notification_text
from custom_components.powerline.consumer_analysis import (
    correlate_power_and_rate,
    recorder_samples,
)


def _profile(power_w: float = 600, baseline_rate: float = 600, active_rate: float = 150):
    """Create 12 aligned two-minute samples: six idle, then six loaded."""
    power = [(index * 120.0, 0.0 if index < 6 else power_w) for index in range(12)]
    rates = [
        (index * 120.0, baseline_rate if index < 6 else active_rate)
        for index in range(12)
    ]
    return power, rates


def test_consumer_above_threshold_correlates_with_a_clear_link_drop():
    power, rates = _profile()

    result = correlate_power_and_rate(power, rates)

    assert result is not None
    assert result["threshold_w"] == 500
    assert result["baseline_rate"] == 600
    assert result["active_rate"] == 150
    assert result["drop_percent"] == 75
    assert result["overlapping_samples"] == 12


def test_flat_rate_does_not_produce_a_consumer_hint():
    power, rates = _profile(baseline_rate=500, active_rate=480)

    assert correlate_power_and_rate(power, rates) is None


def test_samples_below_the_minimum_overlap_are_ignored():
    power = [(index * 120.0, 0.0 if index < 3 else 600.0) for index in range(7)]
    rates = [(index * 120.0, 600.0 if index < 3 else 100.0) for index in range(7)]

    assert correlate_power_and_rate(power, rates) is None


def test_no_inactive_baseline_means_no_hint():
    power = [(index * 120.0, 900.0) for index in range(12)]
    rates = [(index * 120.0, 100.0) for index in range(12)]

    assert correlate_power_and_rate(power, rates) is None


def test_recorder_power_states_are_normalized_to_watts():
    states = [
        {
            "timestamp": 1,
            "state": "0.6",
            "attributes": {"device_class": "power", "unit_of_measurement": "kW"},
        },
        {
            "timestamp": 2,
            "state": "350",
            "attributes": {"device_class": "power", "unit_of_measurement": "W"},
        },
        {
            "timestamp": 3,
            "state": "unknown",
            "attributes": {"device_class": "power", "unit_of_measurement": "W"},
        },
    ]

    assert recorder_samples(states, power=True) == [
        (1.0, 600.0),
        (2.0, 350.0),
        (3.0, None),
    ]



def test_consumer_correlation_notification_is_translated_in_both_languages():
    values = {
        "sensor_name": "Washing machine",
        "source": "Basement",
        "destination": "Office",
        "threshold_w": 500,
        "drop_percent": 75,
        "baseline_rate": 600,
        "active_rate": 150,
    }
    english = alert_notification_text("en-US", "consumer_correlation", **values)
    german = alert_notification_text(
        "de-DE",
        "consumer_correlation",
        **{**values, "sensor_name": "Waschmaschine"},
    )

    assert "75%" in english["message"]
    assert "Washing machine" in english["message"]
    assert "75 %" in german["message"]
    assert "Waschmaschine" in german["message"]
