"""Unit tests for coordinator LED executor handling."""

import types
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

# conftest.py installs all HA stubs before this module is collected.
from custom_components.powerline.coordinator import TpLinkPowerlineCoordinator


class _FakeHass:
    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result
        self._exc = exc

    async def async_add_executor_job(self, func, *args):
        if self._exc:
            raise self._exc
        return self._result


class TestCoordinatorLed(IsolatedAsyncioTestCase):
    async def test_async_set_led_uses_executor_job_result(self):
        fake = types.SimpleNamespace(
            hass=_FakeHass(result=True),
            hp=types.SimpleNamespace(set_led=MagicMock(return_value=True)),
            led_states={},
        )

        result = await TpLinkPowerlineCoordinator.async_set_led(fake, "AA:BB:CC:DD:EE:FF", True)

        self.assertTrue(result)
        self.assertTrue(fake.led_states.get("AA:BB:CC:DD:EE:FF"))

    async def test_async_set_led_returns_false_on_exception(self):
        fake = types.SimpleNamespace(
            hass=_FakeHass(exc=RuntimeError("executor failed")),
            hp=types.SimpleNamespace(set_led=MagicMock(return_value=True)),
            led_states={},
        )

        result = await TpLinkPowerlineCoordinator.async_set_led(fake, "AA:BB:CC:DD:EE:FF", False)

        self.assertFalse(result)

    async def test_async_set_led_returns_false_on_timeout(self):
        import asyncio

        class _TimeoutHass:
            async def async_add_executor_job(self, func, *args):
                await asyncio.sleep(100)

        fake = types.SimpleNamespace(
            hass=_TimeoutHass(),
            hp=types.SimpleNamespace(set_led=MagicMock()),
            led_states={},
        )

        # Patch LED_SET_TIMEOUT to something tiny so the test is fast
        import custom_components.powerline.coordinator as coord_mod
        original = coord_mod.LED_SET_TIMEOUT
        coord_mod.LED_SET_TIMEOUT = 0.01
        try:
            result = await TpLinkPowerlineCoordinator.async_set_led(fake, "AA:BB:CC:DD:EE:FF", True)
        finally:
            coord_mod.LED_SET_TIMEOUT = original

        self.assertFalse(result)
        # State must NOT be updated on timeout
        self.assertNotIn("AA:BB:CC:DD:EE:FF", fake.led_states)


class TestCoordinatorAllLeds(IsolatedAsyncioTestCase):
    async def test_applies_to_every_adapter_and_refreshes(self):
        fake = types.SimpleNamespace(
            devices={"AA:BB:CC:DD:EE:01": {}, "AA:BB:CC:DD:EE:02": {}},
            led_states={},
            async_set_led=AsyncMock(return_value=True),
            async_update_listeners=MagicMock(),
        )

        ok = await TpLinkPowerlineCoordinator.async_set_all_leds(fake, True)

        self.assertTrue(ok)
        self.assertEqual(2, fake.async_set_led.await_count)
        fake.async_set_led.assert_any_await("AA:BB:CC:DD:EE:01", True)
        fake.async_set_led.assert_any_await("AA:BB:CC:DD:EE:02", True)
        fake.async_update_listeners.assert_called_once()

    async def test_returns_false_and_skips_refresh_without_adapters(self):
        fake = types.SimpleNamespace(
            devices={},
            led_states={},
            async_set_led=AsyncMock(return_value=True),
            async_update_listeners=MagicMock(),
        )

        ok = await TpLinkPowerlineCoordinator.async_set_all_leds(fake, False)

        self.assertFalse(ok)
        fake.async_set_led.assert_not_awaited()
        fake.async_update_listeners.assert_not_called()

    async def test_returns_true_if_any_adapter_applies(self):
        fake = types.SimpleNamespace(
            devices={"AA:BB:CC:DD:EE:01": {}, "AA:BB:CC:DD:EE:02": {}},
            led_states={},
            async_set_led=AsyncMock(side_effect=[False, True]),
            async_update_listeners=MagicMock(),
        )

        ok = await TpLinkPowerlineCoordinator.async_set_all_leds(fake, True)

        self.assertTrue(ok)
        fake.async_update_listeners.assert_called_once()
