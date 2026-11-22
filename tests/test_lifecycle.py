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
    CONF_PROBE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SIDEBAR_PANEL,
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
    entry.update_listeners = []

    def async_on_unload(hook):
        entry.unload_hooks.append(hook)

    def add_update_listener(listener):
        entry.update_listeners.append(listener)

    entry.async_on_unload = async_on_unload
    entry.add_update_listener = add_update_listener
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


class _SetupHass(_FakeHass):
    """Fake hass with the attributes async_setup_entry touches."""

    def __init__(self):
        super().__init__()
        self.http = SimpleNamespace(register_static_path=lambda *a, **k: None)
        self.forwarded = None
        self.unloaded = None

        async def run_executor_job(func, *args):
            # In real Home Assistant this runs in a thread; the synchronous
            # call is enough to exercise what setup hands to the executor.
            return func(*args)

        self.async_add_executor_job = run_executor_job

        async def forward_entry_setups(entry, platforms):
            self.forwarded = (entry, platforms)

        async def unload_platforms(entry, platforms):
            self.unloaded = (entry, platforms)
            return True

        self.config_entries = SimpleNamespace(
            async_forward_entry_setups=forward_entry_setups,
            async_unload_platforms=unload_platforms,
        )


def _probe_entry(options=None, entry_id="entry-1"):
    """An entry configured against a remote probe (no raw sockets needed)."""
    entry = _entry(options, entry_id)
    entry.data = {
        "interface": None,
        "devices": [],
        "probe_url": "http://probe.invalid",
        "probe_token": "test-token",
    }
    return entry


class TestSetupEntry(IsolatedAsyncioTestCase):
    """(#9 K8) async_setup_entry / async_unload_entry were never exercised."""

    async def _setup(self, hass, entry):
        from unittest.mock import AsyncMock, patch

        from custom_components.powerline.coordinator import (
            TpLinkPowerlineCoordinator,
        )

        with patch.object(
            TpLinkPowerlineCoordinator,
            "async_config_entry_first_refresh",
            new=AsyncMock(),
            create=True,  # the unit stub base has no such method
        ):
            ok = await integration.async_setup_entry(hass, entry)
        return ok

    async def test_setup_entry_registers_and_returns_true(self):
        hass = _SetupHass()
        entry = _probe_entry({
            CONF_SCAN_INTERVAL: 45,
            CONF_SIDEBAR_PANEL: False,
            CONF_SPATIAL_HUB: False,
        })

        ok = await self._setup(hass, entry)

        self.assertTrue(ok)
        # The coordinator is reachable by entry id, the platforms were
        # forwarded and the update listener is registered on the entry.
        self.assertIn(entry.entry_id, hass.data[DOMAIN])
        self.assertEqual((entry, integration.PLATFORMS), hass.forwarded)
        self.assertEqual(1, len(entry.unload_hooks))
        # The websocket commands got registered once.
        self.assertEqual(4, len(hass.registered_websocket_commands))
        # And the card script is injected into the frontend.
        self.assertEqual(1, len(hass.extra_js_urls))

    async def test_setup_preloads_notification_catalogs(self):
        """(#12) Alerts are raised from the poll callback on the event loop,
        so setup must warm the catalog lru_cache in the executor first — the
        first alert may not open a translation file on the loop."""
        from custom_components.powerline import alert_translations

        alert_translations._catalog.cache_clear()
        hass = _SetupHass()
        entry = _probe_entry({
            CONF_SIDEBAR_PANEL: False,
            CONF_SPATIAL_HUB: False,
        })

        await self._setup(hass, entry)

        self.assertEqual(
            len(alert_translations.LOCALES),
            alert_translations._catalog.cache_info().currsize,
        )

    async def test_setup_without_raw_sockets_and_without_probe_fails_cleanly(self):
        """(#9 K8) The setup error path: no raw sockets and no probe must
        return False instead of half-registering anything."""
        from unittest.mock import patch

        hass = _SetupHass()
        entry = _entry()
        entry.data = {"interface": None, "devices": []}

        with patch("custom_components.powerline.is_available", return_value=False):
            ok = await integration.async_setup_entry(hass, entry)

        self.assertFalse(ok)
        self.assertNotIn(DOMAIN, hass.data)
        self.assertIsNone(hass.forwarded)

    async def test_unload_entry_drops_the_coordinator(self):
        hass = _SetupHass()
        entry = _probe_entry({
            CONF_SIDEBAR_PANEL: False,
            CONF_SPATIAL_HUB: False,
        })
        await self._setup(hass, entry)

        ok = await integration.async_unload_entry(hass, entry)

        self.assertTrue(ok)
        self.assertNotIn(entry.entry_id, hass.data[DOMAIN])
        self.assertEqual((entry, integration.PLATFORMS), hass.unloaded)
