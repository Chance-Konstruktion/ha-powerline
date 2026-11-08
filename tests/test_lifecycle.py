"""Lifecycle tests (#9 K3, K8): the options update listener, setup, unload.

The unit harness keeps Home Assistant at stub level (tests/conftest.py), so
these tests exercise exactly the integration glue: how the entry listener
reacts, what lands in hass.data, and what gets registered.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

import custom_components.powerline as integration
from custom_components.powerline.const import (
    CONF_SCAN_INTERVAL,
    CONF_SPATIAL_HUB,
    CONF_TOPOLOGY_ALERTS,
    DOMAIN,
)


class _FakeHass:
    def __init__(self):
        self.data = {}


def _coordinator():
    return SimpleNamespace(
        update_interval=None,
        power_sensors=[],
        _consumer_alerted=set(),
        _last_consumer_analysis=None,
        alerts_enabled=True,
    )


def _entry(options=None, entry_id="entry-1"):
    entry = SimpleNamespace(entry_id=entry_id, data={}, options=dict(options or {}))
    entry.unload_hooks = []

    def async_on_unload(hook):
        entry.unload_hooks.append(hook)

    entry.async_on_unload = async_on_unload
    return entry


class TestOptionsListener(IsolatedAsyncioTestCase):
    async def test_options_update_applies_values_live(self):
        hass = _FakeHass()
        coordinator = _coordinator()
        hass.data[DOMAIN] = {"entry-1": coordinator}
        entry = _entry({
            CONF_SCAN_INTERVAL: 45,
            CONF_TOPOLOGY_ALERTS: False,
            CONF_SPATIAL_HUB: False,
        })

        await integration._async_options_updated(hass, entry)

        self.assertEqual(timedelta(seconds=45), coordinator.update_interval)
        self.assertFalse(coordinator.alerts_enabled)

    async def test_options_update_for_unloaded_entry_is_ignored(self):
        """(#9 K3) Options/data changes arriving for a not-loaded entry (e.g.
        a device deletion via async_update_entry triggers the same listener)
        must not raise a KeyError — HA only logs it, nothing gets applied."""
        hass = _FakeHass()
        hass.data[DOMAIN] = {}  # entry is not loaded

        await integration._async_options_updated(hass, _entry())

    async def test_options_update_before_first_setup_is_ignored(self):
        """(#9 K3) DOMAIN missing from hass.data entirely (listener fired
        before any setup) must be equally harmless."""
        hass = _FakeHass()

        await integration._async_options_updated(hass, _entry())
