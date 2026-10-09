"""Diagnostics for the Powerline integration.

The point of this dump is the **raw** rate fields. Both rate bugs found so far
were invisible in decoded output, because a capability value decodes to a
number that looks like a perfectly ordinary link rate:

* #110 - the responder's own record carried its rated PLC capability, which
  decoded to ~2100 and was drawn as a ~2100 Mbit/s link.
* #112 - on an AV2000<->AV2000 pairing *both* ends report the other with the
  same capability value, in a peer record no MAC comparison can catch. The
  discriminator turned out to be **bit 11** of the raw 16-bit field.

Neither was diagnosable from `tx_rate` / `rx_rate` alone. Pinning #112 down
needed debug logging enabled on a live mesh, the `MX NW_STATS payload` hex
lines pulled out of the log and decoded by hand. This file makes that a
one-click download instead: every rate field parsed during the last
`discover()`, with its raw word, flag nibble, bit-11 state and decoded value.

`rate_samples` is recorded before the "is this a usable rate" filter, so
records that are correctly *dropped* as link rates still appear here - which
is exactly what you need to see when a rate goes missing rather than wrong.

Note for anyone sharing a dump: it contains the MAC addresses of your
powerline adapters, because for this integration the MAC *is* the device
identity and the per-peer table cannot be read without it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"error": "coordinator not loaded"}

    hp = getattr(coordinator, "hp", None)

    samples: list[dict[str, Any]] = list(getattr(hp, "rate_samples", []) or [])
    capability = [s for s in samples if s.get("capability_bit")]

    # Directed per-link table, as the topology graph sees it. Tuple keys are
    # not JSON-serialisable, so flatten them.
    links = [
        {"responder": responder, "peer": peer, **rates}
        for (responder, peer), rates in (getattr(hp, "plc_links", {}) or {}).items()
    ]

    adapters = [
        {
            key: dev.get(key)
            for key in (
                "mac", "plcmac", "model", "firmware_ver", "chipset",
                "tx_rate", "rx_rate", "online", "role", "cco", "capability",
            )
            if key in dev
        }
        for dev in (getattr(coordinator, "devices", {}) or {}).values()
    ]

    return {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "chipset": getattr(hp, "_chipset", None),
        "adapters": adapters,
        "plc_links": links,
        "rate_samples": samples,
        "rate_sample_summary": {
            "total": len(samples),
            "capability_flagged": len(capability),
            "usable": len(samples) - len(capability),
            # A rate above this on AV1000/AV2000 hardware means something is
            # still being mis-decoded; it should always be an empty list.
            "implausible_over_1200": sorted(
                {s["rate"] for s in samples if (s.get("rate") or 0) > 1200}
            ),
        },
    }
