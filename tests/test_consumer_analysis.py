"""Synthetic Recorder-history tests for consumer/link correlation."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from custom_components.powerline.alert_translations import alert_notification_text
from custom_components.powerline.coordinator import TpLinkPowerlineCoordinator
from custom_components.powerline.consumer_analysis import (
    correlate_power_and_rate,
    recorder_samples,
)


def _profile(
    power_w: float = 600,
    baseline_rate: float = 600,
    active_rate: float = 150,
    days: int = 3,
):
    """Create six idle and six loaded rate samples on each UTC day."""
    power = []
    rates = []
    for day in range(days):
        start = float(day * 86400)
        power.extend([(start + 3600, 0.0), (start + 7200, power_w)])
        rates.extend(
            (start + 3600 + index * 120, baseline_rate)
            for index in range(6)
        )
        rates.extend(
            (start + 7200 + index * 120, active_rate)
            for index in range(6)
        )
    return power, rates


def test_consumer_above_threshold_correlates_with_a_clear_link_drop():
    power, rates = _profile()

    result = correlate_power_and_rate(power, rates)

    assert result is not None
    assert result["threshold_w"] == 500
    assert result["baseline_rate"] == 600
    assert result["active_rate"] == 150
    assert result["drop_percent"] == 75
    assert result["overlapping_samples"] == 36
    assert result["days_observed"] == 3


def test_short_single_day_coincidence_does_not_produce_a_hint():
    power, rates = _profile(days=1)

    assert len(rates) >= 8
    assert correlate_power_and_rate(power, rates) is None


def test_two_days_of_repeated_samples_are_not_enough():
    power, rates = _profile(days=2)

    assert len(rates) >= 8
    assert correlate_power_and_rate(power, rates) is None


def test_every_observed_day_needs_both_load_states():
    power, rates = _profile(days=3)
    # On the middle day only one active-state rate was observed. The other
    # two days still contain enough samples overall, but only two days qualify.
    rates = [
        (timestamp, rate)
        for timestamp, rate in rates
        if not (
            int(timestamp // 86400) == 1
            and timestamp >= 86400 + 7200 + 120
        )
    ]

    assert correlate_power_and_rate(power, rates) is None


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
        "days_observed": 3,
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
    assert "3 UTC-Kalendertagen" in german["message"]
    assert "3 UTC calendar days" in english["message"]


class _FakeRegistry:
    """Registry stub keyed the way er.async_get_entity_id looks entries up."""

    def __init__(self):
        # A foreign integration happens to carry the same unique_id — this is
        # exactly what the old full-registry scan matched (#9 K6).
        self.entities = {
            "sensor.foreign_thief": SimpleNamespace(
                unique_id="plc_link_A_B_rate", entity_id="sensor.foreign_thief"
            ),
        }
        self._by_key = {
            ("sensor", "powerline", "plc_link_A_B_rate"):
                "sensor.plc_link_a_b_rate",
        }

    def async_get_entity_id(self, domain, platform, unique_id):
        return self._by_key.get((domain, platform, unique_id))


class TestRateEntityLookup(IsolatedAsyncioTestCase):
    """The consumer correlation resolves link-rate entities from the registry."""

    async def test_rate_entities_resolve_by_platform_not_by_unique_id_alone(self):
        """(#9 K6) The lookup must ask the entity registry for
        (domain, platform, unique_id) instead of scanning every registry
        entry of every integration on the event loop: a foreign integration
        with the same unique_id must not be matched."""
        coord = TpLinkPowerlineCoordinator.__new__(TpLinkPowerlineCoordinator)
        coord.power_sensors = ["sensor.washing_machine"]
        coord._last_consumer_analysis = None
        coord._consumer_alerted = set()
        coord.hass = SimpleNamespace(
            async_add_executor_job=lambda func, *args: func(*args),
            states=SimpleNamespace(get=lambda entity_id: None),
        )

        captured = {}

        def fake_fetch(hass, start, end, entity_ids, power_ids, edge_specs, names):
            captured["edge_specs"] = edge_specs
            return []

        topology = {"edges": [{"source": "A", "destination": "B"}]}
        with patch(
            "custom_components.powerline.coordinator.fetch_recorder_correlations",
            fake_fetch,
        ), patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=_FakeRegistry(),
        ):
            alerts = await coord._async_check_consumer_correlations(topology)

        assert alerts == []
        # The registry-backed, platform-correct entity is used, not the
        # foreign integration's entry that carries the same unique_id.
        assert captured["edge_specs"][0]["entity_ids"] == [
            "sensor.plc_link_a_b_rate"
        ]
