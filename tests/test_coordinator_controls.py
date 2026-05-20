"""Unit tests for coordinator power-saving and QoS control methods."""

import asyncio
import types
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

# conftest.py installs all HA stubs before this module is collected.
import custom_components.powerline.coordinator as coord_mod
from custom_components.powerline.coordinator import TpLinkPowerlineCoordinator

MAC = "AA:BB:CC:DD:EE:FF"


class _FakeHass:
    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result
        self._exc = exc

    async def async_add_executor_job(self, func, *args):
        if self._exc:
            raise self._exc
        return self._result


class _SlowHass:
    """Simulates an executor that never returns (triggers timeout)."""

    async def async_add_executor_job(self, func, *args):
        await asyncio.sleep(100)


def _fake(hass, power_saving_states=None, qos_states=None, led_states=None):
    return types.SimpleNamespace(
        hass=hass,
        hp=MagicMock(),
        power_saving_states=power_saving_states if power_saving_states is not None else {},
        qos_states=qos_states if qos_states is not None else {},
        led_states=led_states if led_states is not None else {},
    )


# ---------------------------------------------------------------------------
# async_set_power_saving
# ---------------------------------------------------------------------------

class TestCoordinatorPowerSaving(IsolatedAsyncioTestCase):
    async def test_set_power_saving_returns_true_and_updates_state(self):
        fake = _fake(_FakeHass(result=True))
        result = await TpLinkPowerlineCoordinator.async_set_power_saving(fake, MAC, True)

        self.assertTrue(result)
        self.assertTrue(fake.power_saving_states[MAC])

    async def test_set_power_saving_off_updates_state(self):
        fake = _fake(_FakeHass(result=True), power_saving_states={MAC: True})
        result = await TpLinkPowerlineCoordinator.async_set_power_saving(fake, MAC, False)

        self.assertTrue(result)
        self.assertFalse(fake.power_saving_states[MAC])

    async def test_set_power_saving_does_not_update_state_on_failure(self):
        fake = _fake(_FakeHass(result=False), power_saving_states={MAC: True})
        result = await TpLinkPowerlineCoordinator.async_set_power_saving(fake, MAC, False)

        self.assertFalse(result)
        # State must stay unchanged when adapter rejected the command
        self.assertTrue(fake.power_saving_states[MAC])

    async def test_set_power_saving_returns_false_on_exception(self):
        fake = _fake(_FakeHass(exc=RuntimeError("broken")))
        result = await TpLinkPowerlineCoordinator.async_set_power_saving(fake, MAC, True)

        self.assertFalse(result)
        self.assertNotIn(MAC, fake.power_saving_states)

    async def test_set_power_saving_returns_false_on_timeout(self):
        fake = _fake(_SlowHass())
        original = coord_mod.LED_SET_TIMEOUT
        coord_mod.LED_SET_TIMEOUT = 0.01
        try:
            result = await TpLinkPowerlineCoordinator.async_set_power_saving(fake, MAC, True)
        finally:
            coord_mod.LED_SET_TIMEOUT = original

        self.assertFalse(result)
        self.assertNotIn(MAC, fake.power_saving_states)


# ---------------------------------------------------------------------------
# async_set_qos_priority
# ---------------------------------------------------------------------------

class TestCoordinatorQosPriority(IsolatedAsyncioTestCase):
    async def test_set_qos_returns_true_and_updates_state(self):
        fake = _fake(_FakeHass(result=True))
        result = await TpLinkPowerlineCoordinator.async_set_qos_priority(fake, MAC, "gaming")

        self.assertTrue(result)
        self.assertEqual(fake.qos_states[MAC], "gaming")

    async def test_set_qos_switches_priority(self):
        fake = _fake(_FakeHass(result=True), qos_states={MAC: "internet"})
        result = await TpLinkPowerlineCoordinator.async_set_qos_priority(fake, MAC, "voip")

        self.assertTrue(result)
        self.assertEqual(fake.qos_states[MAC], "voip")

    async def test_set_qos_does_not_update_state_on_failure(self):
        fake = _fake(_FakeHass(result=False), qos_states={MAC: "internet"})
        result = await TpLinkPowerlineCoordinator.async_set_qos_priority(fake, MAC, "gaming")

        self.assertFalse(result)
        self.assertEqual(fake.qos_states[MAC], "internet")

    async def test_set_qos_returns_false_on_exception(self):
        fake = _fake(_FakeHass(exc=OSError("socket error")))
        result = await TpLinkPowerlineCoordinator.async_set_qos_priority(fake, MAC, "gaming")

        self.assertFalse(result)
        self.assertNotIn(MAC, fake.qos_states)

    async def test_set_qos_returns_false_on_timeout(self):
        fake = _fake(_SlowHass())
        original = coord_mod.LED_SET_TIMEOUT
        coord_mod.LED_SET_TIMEOUT = 0.01
        try:
            result = await TpLinkPowerlineCoordinator.async_set_qos_priority(fake, MAC, "gaming")
        finally:
            coord_mod.LED_SET_TIMEOUT = original

        self.assertFalse(result)
        self.assertNotIn(MAC, fake.qos_states)
