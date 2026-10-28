"""Correlate selected power sensors with per-adapter Powerline rates.

All time-series data is read from Home Assistant Recorder at analysis time.
The module deliberately keeps no cache or persistent state so correlation
results follow the same retention and exclusion rules as HA history.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from statistics import median
from typing import Any

from .const import (
    CONSUMER_MIN_OVERLAPPING_SAMPLES,
    CONSUMER_MIN_RATE_DROP_PERCENT,
    CONSUMER_MIN_SAMPLES_PER_STATE,
    CONSUMER_POWER_THRESHOLD_W,
)


def _parts(state: Any) -> tuple[float, Any, dict[str, Any]] | None:
    """Return timestamp, raw state and attributes for HA State or dict data."""
    if isinstance(state, dict):
        raw = state.get("state", state.get("s"))
        attributes = state.get("attributes", state.get("a", {})) or {}
        timestamp = (
            state.get("last_updated")
            or state.get("last_changed")
            or state.get("lu")
            or state.get("lc")
            or state.get("timestamp")
        )
    else:
        raw = getattr(state, "state", None)
        attributes = getattr(state, "attributes", {}) or {}
        timestamp = getattr(state, "last_updated", None) or getattr(
            state, "last_changed", None
        )

    if timestamp is None:
        return None
    try:
        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(ts):
        return None
    return ts, raw, attributes


def _number(raw: Any) -> float | None:
    """Parse a finite numeric sensor state; unavailable/unknown become None."""
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if isfinite(value) else None


def _power_to_watts(value: float, attributes: dict[str, Any]) -> float | None:
    """Normalize supported Home Assistant power units to watts."""
    device_class = attributes.get("device_class")
    if device_class not in (None, "power"):
        return None

    unit = attributes.get("unit_of_measurement", "W")
    if unit in ("W", "watt", "watts", None, ""):
        return value
    if unit in ("kW", "kilowatt", "kilowatts"):
        return value * 1000
    if unit in ("MW", "megawatt", "megawatts"):
        return value * 1_000_000
    if unit in ("mW", "milliwatt", "milliwatts"):
        return value / 1000
    # An unrecognized unit cannot safely be compared with a watt threshold.
    return None


def recorder_samples(
    states: list[Any] | None, *, power: bool = False
) -> list[tuple[float, float | None]]:
    """Convert Recorder states to sorted (Unix timestamp, numeric value) points."""
    points: list[tuple[float, float | None]] = []
    for state in states or []:
        parts = _parts(state)
        if parts is None:
            continue
        timestamp, raw, attributes = parts
        value = _number(raw)
        if power and value is not None:
            value = _power_to_watts(value, attributes)
        points.append((timestamp, value))
    points.sort(key=lambda point: point[0])
    return points


def build_link_rate_samples(
    states_by_entity: dict[str, list[Any]], entity_ids: list[str]
) -> list[tuple[float, float | None]]:
    """Read route-specific link rates from Recorder-backed sensor states.

    The route sensor is force-updated while consumer correlation is enabled,
    so repeated steady readings remain observations. The latest rate is held
    until the next poll; invalid or unavailable samples remain missing.
    """
    events: list[tuple[float, str, float | None]] = []
    for entity_id in entity_ids:
        for timestamp, value in recorder_samples(
            states_by_entity.get(entity_id, [])
        ):
            events.append((timestamp, entity_id, value))
    if not events:
        return []

    events.sort(key=lambda event: (event[0], event[1]))
    current: dict[str, float | None] = {}
    output: list[tuple[float, float | None]] = []
    index = 0
    while index < len(events):
        timestamp = events[index][0]
        # Sensors updated in the same minute describe one coordinator poll.
        minute = int(timestamp // 60)
        group: list[tuple[float, str, float | None]] = []
        while index < len(events) and int(events[index][0] // 60) == minute:
            group.append(events[index])
            index += 1
        for _, entity_id, value in group:
            current[entity_id] = value
        available = [value for value in current.values() if value is not None]
        if available:
            output.append((max(event[0] for event in group), median(available)))
    return output


def correlate_power_and_rate(
    power_samples: list[tuple[float, float | None]],
    rate_samples: list[tuple[float, float | None]],
    *,
    threshold_w: float = CONSUMER_POWER_THRESHOLD_W,
) -> dict[str, int] | None:
    """Detect a material rate drop while a consumer is drawing above threshold.

    Each rate point is paired with the latest known power state at or before
    its timestamp. A hint requires at least eight overlapping observations,
    including three with the consumer active and three inactive; without both
    regimes there is no comparison baseline.
    """
    if not power_samples or not rate_samples:
        return None

    power = sorted(power_samples, key=lambda point: point[0])
    rates = sorted(rate_samples, key=lambda point: point[0])
    power_index = 0
    current_power: float | None = None
    active_rates: list[float] = []
    inactive_rates: list[float] = []

    for timestamp, rate in rates:
        while power_index < len(power) and power[power_index][0] <= timestamp:
            current_power = power[power_index][1]
            power_index += 1
        if current_power is None or rate is None or rate < 0:
            continue
        if current_power > threshold_w:
            active_rates.append(rate)
        else:
            inactive_rates.append(rate)

    overlaps = len(active_rates) + len(inactive_rates)
    if (
        overlaps < CONSUMER_MIN_OVERLAPPING_SAMPLES
        or len(active_rates) < CONSUMER_MIN_SAMPLES_PER_STATE
        or len(inactive_rates) < CONSUMER_MIN_SAMPLES_PER_STATE
    ):
        return None

    baseline = median(inactive_rates)
    active = median(active_rates)
    if baseline <= 0:
        return None
    drop_percent = round(100 * (baseline - active) / baseline)
    if drop_percent < CONSUMER_MIN_RATE_DROP_PERCENT:
        return None

    return {
        "threshold_w": round(threshold_w),
        "baseline_rate": round(baseline),
        "active_rate": round(active),
        "drop_percent": drop_percent,
        "overlapping_samples": overlaps,
    }


def fetch_recorder_correlations(
    hass: Any,
    start_time: datetime,
    end_time: datetime,
    entity_ids: list[str],
    power_sensor_ids: list[str],
    edge_specs: list[dict[str, Any]],
    sensor_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Read HA Recorder history and return consumer/link correlations.

    Call this function through hass.async_add_executor_job: the Recorder
    database query and the subsequent time-series analysis are synchronous
    and must not run on Home Assistant's event loop.
    """
    if not entity_ids or not power_sensor_ids or not edge_specs:
        return []

    # Import in the worker so integrations/tests that have no Recorder never
    # need the module merely to load the Powerline integration.
    from homeassistant.components.recorder import history

    states_by_entity = history.get_significant_states(
        hass,
        start_time,
        end_time,
        entity_ids=entity_ids,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )

    power_samples_by_sensor = {
        sensor_id: recorder_samples(states_by_entity.get(sensor_id, []), power=True)
        for sensor_id in power_sensor_ids
    }
    correlations: list[dict[str, Any]] = []
    for edge in edge_specs:
        rate_samples = build_link_rate_samples(
            states_by_entity, edge["entity_ids"]
        )
        if not rate_samples:
            continue
        for sensor_id, power_samples in power_samples_by_sensor.items():
            correlation = correlate_power_and_rate(power_samples, rate_samples)
            if correlation is None:
                continue
            correlations.append(
                {
                    "type": "consumer_correlation",
                    "source": edge["source"],
                    "destination": edge["destination"],
                    "sensor_id": sensor_id,
                    "sensor_name": sensor_names.get(sensor_id, sensor_id),
                    **correlation,
                }
            )
    return correlations
