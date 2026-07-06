"""DataUpdateCoordinator for Powerline Network.

Uses HomePlug AV raw Ethernet (Layer 2) -- no IP needed.
Discovers new devices every poll cycle (default 120s).
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, TOPOLOGY_EVENT, get_mac, normalize_mac
from .topology import TopologyManager

_LOGGER = logging.getLogger(__name__)

# Control commands (LED, power saving, QoS) are serialized with the poll on a
# shared lock, so a command issued mid-poll must wait for the poll to finish.
# This budget must comfortably exceed a full poll, otherwise a command that
# actually succeeds gets reported as a timeout failure in the UI.
LED_SET_TIMEOUT = 30.0


class TpLinkPowerlineCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls Powerline adapters via HomePlug AV Layer 2."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        interface: str | None,
        initial_devices: list[dict[str, Any]],
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        from .homeplug import HomeplugAV

        self.hp = HomeplugAV(interface)
        self.interface = interface or self.hp.interface
        self.devices: dict[str, dict[str, Any]] = {}
        self._known_macs: set[str] = set()
        self._new_device_callbacks: list[Callable[[list[dict[str, Any]]], None]] = []
        # Each platform shares the set of MACs it has already created entities
        # for. forget_device() clears a MAC from all of them so a rediscovered
        # adapter gets fresh entities instead of being silently skipped.
        self._tracked_mac_sets: list[set[str]] = []
        self.led_states: dict[str, bool] = {}
        self.power_saving_states: dict[str, bool] = {}
        self.qos_states: dict[str, str] = {}
        self.topology = TopologyManager()

        self._states_queried = False

        # Index initial devices by MAC (states will be queried on first update)
        for dev in initial_devices:
            mac = get_mac(dev)
            if mac:
                self.devices[mac] = dev
                self._known_macs.add(mac)

        super().__init__(
            hass, _LOGGER,
            name=f"{DOMAIN}_homeplug",
            update_interval=timedelta(seconds=scan_interval),
        )

    def register_new_device_callback(self, cb: Callable[[list[dict[str, Any]]], None]) -> None:
        """Register callback for when new devices are discovered."""
        self._new_device_callbacks.append(cb)

    def register_tracked_macs(self, tracked: set[str]) -> None:
        """Register a platform's set of already-created MACs.

        ``forget_device`` clears the MAC from every registered set, so an
        adapter that was deleted from the UI but is still plugged in gets its
        entities created again on the next poll.
        """
        self._tracked_mac_sets.append(tracked)

    def forget_device(self, mac: str) -> None:
        """Drop all in-memory state for a single adapter.

        Called when the user deletes a device from the UI. Wipes the cached
        device, its known-MAC marker and its LED/QoS/power-saving state, and
        clears it from every platform's tracked-MAC set. The adapter only
        stays gone for good if it is genuinely offline (wrongly detected,
        swapped or unplugged) -- a still-present adapter is rediscovered on
        the next poll, which is the correct behaviour for hardware that's
        really there.
        """
        mac = normalize_mac(mac)
        self.devices.pop(mac, None)
        self._known_macs.discard(mac)
        self.led_states.pop(mac, None)
        self.power_saving_states.pop(mac, None)
        self.qos_states.pop(mac, None)
        for tracked in self._tracked_mac_sets:
            tracked.discard(mac)

    def adapter_online(self, mac: str) -> bool:
        """Whether an adapter answered the most recent poll.

        Per-adapter entities use this for their ``available`` property, so an
        offline adapter's sensors/controls show "unavailable" instead of stale
        values. Unknown MACs (not yet polled) default to online.
        """
        dev = self.devices.get(mac)
        return bool(dev.get("_online", True)) if dev else False

    async def _async_update_data(self) -> dict[str, Any]:
        """Full discovery + stats every poll cycle."""
        try:
            discovered = await self.hass.async_add_executor_job(
                self.hp.discover, 5.0
            )

            new_devices: list[dict[str, Any]] = []

            for dev in discovered:
                mac = get_mac(dev)
                if not mac:
                    continue

                if mac in self.devices:
                    self.devices[mac].update(dev)
                else:
                    self.devices[mac] = dev
                    self.led_states.setdefault(mac, True)
                    self.power_saving_states.setdefault(mac, False)
                    self.qos_states.setdefault(mac, "internet")
                    _LOGGER.info("New Powerline adapter discovered: %s (FW: %s)",
                                 mac, dev.get("firmware_ver", "?"))

                if mac not in self._known_macs:
                    self._known_macs.add(mac)
                    new_devices.append(dev)

            # Query device states (LED, QoS, Power Saving) from adapters
            if not self._states_queried and self.devices:
                try:
                    queried = await self.hass.async_add_executor_job(
                        self.hp.query_device_states, list(self.devices.keys())
                    )
                    for mac, state in queried.items():
                        if state.get("led") is not None:
                            self.led_states[mac] = state["led"]
                            _LOGGER.info("Initial LED state for %s: %s",
                                         mac, "ON" if state["led"] else "OFF")
                        else:
                            self.led_states.setdefault(mac, True)
                        if state.get("qos") is not None:
                            self.qos_states[mac] = state["qos"]
                            _LOGGER.info("Initial QoS state for %s: %s",
                                         mac, state["qos"])
                        else:
                            self.qos_states.setdefault(mac, "internet")
                        if state.get("power_saving") is not None:
                            self.power_saving_states[mac] = state["power_saving"]
                            _LOGGER.info("Initial Power Saving state for %s: %s",
                                         mac, "ON" if state["power_saving"] else "OFF")
                        else:
                            self.power_saving_states.setdefault(mac, False)
                    self._states_queried = True
                except Exception:
                    _LOGGER.debug("State query failed, using defaults", exc_info=True)
                    for mac in self.devices:
                        self.led_states.setdefault(mac, True)
                        self.power_saving_states.setdefault(mac, False)
                        self.qos_states.setdefault(mac, "internet")
                    self._states_queried = True

            # Ensure all devices have state entries
            for mac in self.devices:
                self.led_states.setdefault(mac, True)
                self.power_saving_states.setdefault(mac, False)
                self.qos_states.setdefault(mac, "internet")

            # Mark devices not seen in this scan
            seen_macs = {get_mac(d) for d in discovered}
            for mac in self.devices:
                self.devices[mac]["_online"] = mac in seen_macs

            # Notify platforms about new devices so they create entities
            if new_devices:
                _LOGGER.info("Notifying platforms about %d new device(s)", len(new_devices))
                for cb in self._new_device_callbacks:
                    try:
                        cb(new_devices)
                    except Exception:
                        _LOGGER.exception("Error in new device callback")

            # Build output data
            online_devices = {m: d for m, d in self.devices.items() if d.get("_online", True)}

            plc_rates: dict[str, dict[str, int]] = {}
            for mac, dev in self.devices.items():
                plc_rates[mac] = {
                    "tx": dev.get("tx_rate", 0),
                    "rx": dev.get("rx_rate", 0),
                }

            # Weakest/slowest link: the lowest per-adapter link rate among online
            # adapters that actually report one. A single number summing all rates
            # is meaningless; the slowest link is what actually limits the network.
            links: list[tuple[int, str]] = []
            for mac, dev in online_devices.items():
                rates = [r for r in (dev.get("tx_rate", 0), dev.get("rx_rate", 0)) if r > 0]
                if rates:
                    links.append((min(rates), mac))
            slowest = min(links) if links else None

            total = len(self.devices)
            online = len(online_devices)
            # Network has a problem if nothing is reachable, or a known adapter
            # that we have seen before is currently offline.
            network_problem = online == 0 or online < total

            topology = self.topology.update(self.devices)
            for event in self.topology.drain_events():
                self.hass.bus.async_fire(TOPOLOGY_EVENT, event)

            return {
                "online": online > 0,
                "plc_devices": self.devices,
                "plc_device_count": online,
                "plc_device_count_total": total,
                "plc_rates": plc_rates,
                "slowest_link": slowest[0] if slowest else None,
                "slowest_link_mac": slowest[1] if slowest else None,
                "network_problem": network_problem,
                "topology": topology,
            }

        except Exception as err:
            _LOGGER.debug("Error polling Powerline adapters: %s", err)
            raise UpdateFailed(f"HomePlug AV error: {err}") from err

    async def async_set_led(self, mac: str, on: bool) -> bool:
        """Set LED on a specific adapter (by MAC)."""
        try:
            result = await asyncio.wait_for(
                self.hass.async_add_executor_job(self.hp.set_led, mac, on),
                timeout=LED_SET_TIMEOUT,
            )
            if result:
                self.led_states[mac] = on
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("LED control timed out for %s", mac)
            return False
        except Exception:
            _LOGGER.exception("LED control crashed for %s (on=%s)", mac, on)
            return False

    async def async_restart_adapter(self, mac: str) -> bool:
        """Reboot an adapter (soft restart via VS_RS_DEV)."""
        try:
            result = await asyncio.wait_for(
                self.hass.async_add_executor_job(self.hp.restart, mac),
                timeout=LED_SET_TIMEOUT,
            )
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("Restart timed out for %s", mac)
            return False
        except Exception:
            _LOGGER.exception("Restart crashed for %s", mac)
            return False

    async def async_set_all_leds(self, on: bool) -> bool:
        """Turn every adapter's LED on/off at once (tpPLC "all LEDs" buttons).

        Reuses the per-adapter ``async_set_led`` so each adapter takes its own
        chipset path. A QCA (AV500) adapter can *apply* the write but drop the
        close confirmation, so ``async_set_led`` may report False even though the
        LED physically changed (Broadcom/AV1000 acks reliably, hence it was fine).
        Because this is a deliberate "set them all" action — like tpPLC's button —
        we reflect the requested state for every adapter and log how many
        explicitly confirmed. The real state is re-read on the next restart.
        """
        macs = list(self.devices.keys())
        if not macs:
            _LOGGER.warning("All LEDs %s: no adapters known yet",
                            "on" if on else "off")
            return False
        confirmed = 0
        for mac in macs:
            if await self.async_set_led(mac, on):
                confirmed += 1
            # async_set_led already set led_states on a confirmed write; mirror
            # the requested state for the rest too (apply-but-no-ack is common
            # on QCA), so the dashboard matches the physical LEDs.
            self.led_states[mac] = on
        _LOGGER.info("All LEDs %s: %d/%d adapters confirmed the write",
                     "on" if on else "off", confirmed, len(macs))
        self.async_update_listeners()
        return True

    async def async_set_power_saving(self, mac: str, on: bool) -> bool:
        """Set power saving mode on a specific adapter (by MAC)."""
        try:
            result = await asyncio.wait_for(
                self.hass.async_add_executor_job(self.hp.set_power_saving, mac, on),
                timeout=LED_SET_TIMEOUT,
            )
            if result:
                self.power_saving_states[mac] = on
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("Power saving control timed out for %s", mac)
            return False
        except Exception:
            _LOGGER.exception("Power saving control crashed for %s (on=%s)", mac, on)
            return False

    async def async_set_qos_priority(self, mac: str, priority: str) -> bool:
        """Set QoS priority on a specific adapter (by MAC)."""
        try:
            result = await asyncio.wait_for(
                self.hass.async_add_executor_job(self.hp.set_qos_priority, mac, priority),
                timeout=LED_SET_TIMEOUT,
            )
            if result:
                self.qos_states[mac] = priority
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("QoS control timed out for %s", mac)
            return False
        except Exception:
            _LOGGER.exception("QoS control crashed for %s (priority=%s)", mac, priority)
            return False
