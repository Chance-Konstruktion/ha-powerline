# Changelog

All notable changes to **Powerline Network** (ha-tp-link-powerline) are documented here.

## [Unreleased]

### Fixed
- **Switches reported "timed out" even though the command worked** -- a control command issued while a ~24 s poll held the shared lock waited longer than the 10 s budget, so the UI showed a failure although the adapter had accepted the command. Raised the control timeout to 30 s and shortened the poll (see below).
- **Slow polls (~24 s)** -- firmware/model is now queried only once per adapter per session instead of every poll (it timed out repeatedly on adapters that don't answer), and passive `0x6046` listening was cut from 6 s to 2 s since the active `NW_STATS` query is the reliable path. This also shortens how long the shared lock is held, so switches respond faster.
- **Crash / adapters appearing to "reset" when toggling a switch during a poll** -- the poll (`discover`) and control commands shared one `HomeplugAV` instance and each opened/closed the same raw sockets. Run concurrently in different executor threads, one closed a socket while the other was mid-receive (`'NoneType' object has no attribute 'settimeout'`), failing the update and briefly making all entities unavailable. All socket-using methods are now serialized on a lock.
- **Only one adapter showed a connection speed** -- `NW_STATS` reports the PHY rate against the *peer* MAC, so in a 2-adapter setup only one device got a value. The link rate is now mirrored onto the responding adapter too, so both endpoints show a speed.
- **LED/control switching failed in Home Assistant** -- `_send_recv()` collected responses for the *full* timeout, and the 0x8912 bus is never idle (constant 0xA070 beacons), so each of the three sequential LED writes blocked for 2 s. With the retry, one toggle exceeded the coordinator's 10 s budget and the switch reported a failure. `_send_recv()` now returns immediately once the expected confirmation (`stop_on`) arrives, making LED/power-saving/state-read commands near-instant.
- **TX/RX rates always 0** -- the MEDIAXTREAM Network Stats MMTYPE was wrong (`0xA034`); corrected to **`0xA02C/0xA02D`** (the `pla-util get-network-stats` command). Station Info corrected from `0xA080` to **`0xA04C/0xA04D`**. Values verified against `serock/mediaxtream-dissector` and `serock/pla-util`.
- **Absurd PHY rates (~33000 Mbps)** -- the rate field's top bit (`0x8000`) is a link-active flag, not part of the value. It is now masked off (`decode_phy_rate()`), confirmed against a real TL-PA7017 capture (`0x81A6` -> 422 Mbps).
- **Config flow button labelled "OK"** -- the discover/confirm steps use an empty form, so Home Assistant showed a generic "OK" button that did not match the "Click Submit" text. Added an explicit per-step `submit` label ("Submit" / "Absenden").
- **LED control did nothing** -- reverse-engineered the exact tpPLC sequence from a Wireshark capture (TL-PA7017). Toggling the LED is two `Set Parameter` writes (param **`0x0095`** and **`0x003F` "LED Options"**, byte 3 bit `0x10` = enabled) followed by an **Apply (`0xA020`)**. The old build sent a single mis-framed write and no apply, so nothing happened.
- **Get Parameter responses parsed wrong** -- the confirmation format is `OctetsPerElement(1) + NumElements(2 LE) + Value` (no parameter-id echo). Fixed `parse_mx_get_param_cnf()`, which also makes firmware/model strings and LED state read-back reliable.
- **Power saving did nothing** -- reverse-engineered from a tpPLC capture (TL-PA7017). Param **`0x0029`** is a 16-bit value: low 15 bits = standby timeout (s), top bit **`0x8000` = power-saving enabled** (same flag scheme as the PHY rate). Toggling writes `0x0029` (with/without the flag, timeout preserved), clears companion param `0x0074` on disable, then commits with **Apply (`0xA020`)**. The old 4-byte write with no flag/apply did nothing. Read-back now uses the `0x8000` bit too.

### Added
- `build_mx_set_param()` helper implementing the documented Set Parameter payload layout (ParamID + OctetsPerElement + NumElements + Value).
- **State read-back** -- `query_device_states()` now reads the real LED (param `0x003F`, bit `0x10`) and power-saving state via Get Parameter (`0xA05C`) instead of always returning defaults.

## [4.2.0] -- 2026-03-31

### Added
- **QoS Priority Select** -- per-adapter traffic priority (Gaming, VoIP, Audio/Video, Internet) via MEDIAXTREAM 0xA058 two-frame sequence
- **Power Saving Switch** -- per-adapter power saving mode on/off (Broadcom only)
- **Passive Rate Monitoring** -- TX/RX rates via 0x6046 status indications (every 2--5s from adapter, no polling needed)
- **Diagnostic Button** -- full protocol scan with raw frame dump to logs, including LED/QoS/Power Saving state
- **Dynamic Discovery** -- new adapters appear automatically within one poll cycle via `register_new_device_callback()`
- **Dual Protocol Auto-Detection** -- automatically detects Broadcom (MEDIAXTREAM) vs. Qualcomm chipsets
- **German translations** (`de.json`) for all entities and config flow
- **Entity translations** via `translation_key` for all platforms (sensor, binary_sensor, switch, select, button)
- TX Total / RX Total sensors (sum of all adapter rates)
- Adapters Online / Adapters Total sensors

### Changed
- Scan interval now configurable via Options Flow (10--600s, default 120s) without restart
- Improved rate fetching: passive 0x6046 first (6s), then active fallback methods
- Entity names use `translation_key` pattern instead of hardcoded strings
- Diagnostic button now logs integration state (LED, QoS, Power Saving) before protocol scan

### Fixed
- Duplicate device entries after reinstallation (automatic cleanup of stale devices)
- Options Flow `AttributeError` on modern HA versions (read-only `config_entry` property)
- Config flow 500 error on HA < 2024.11 (`single_config_entry` removed)
- `ConfigFlowResult` import fallback for HA < 2024.4

## [4.1.0] -- 2026-03-15

### Added
- **LED Control Switch** -- per-adapter LED on/off via MEDIAXTREAM 0xA058/0xA059
- **Binary Sensor** for online status (`device_class: connectivity`) replacing old text sensor
- Firmware version and model detection per adapter (via 0xA05C GET_PARAM)
- Device info with manufacturer, model, firmware, suggested area

### Changed
- Status entity migrated from text sensor to binary sensor (automatic migration removes old entity)
- Improved discovery reliability with socket retry logic (2 retries, exponential backoff)
- Better network interface selection (prioritizes eth*/en* interfaces)

### Fixed
- Socket timeout handling on slow networks
- MAC normalization with LRU cache for performance

## [4.0.0] -- 2026-03-01

### Added
- Initial release as HACS custom integration
- **Auto-Discovery** of all Powerline adapters via HomePlug AV Layer 2 (CC_DISCOVER_LIST 0x0014/0x0015)
- **MEDIAXTREAM Discovery** for Broadcom chipsets (0xA070/0xA071)
- Per-adapter TX/RX rate sensors (Mbit/s PHY Rate)
- Per-adapter online status sensor
- Config flow with automatic adapter detection
- Raw Ethernet socket communication (AF_PACKET, Ethertype 0x88E1 + 0x8912)
- Support for TP-Link, FRITZ!Powerline, devolo, and other HomePlug AV adapters

### Requirements
- Home Assistant 2024.1.0+
- CAP_NET_RAW capability
- Ethernet connection (WiFi cannot send Layer 2 HomePlug AV frames)

[4.2.0]: https://github.com/Chance-Konstruktion/ha-tp-link-powerline/releases/tag/v4.2.0
[4.1.0]: https://github.com/Chance-Konstruktion/ha-tp-link-powerline/releases/tag/v4.1.0
[4.0.0]: https://github.com/Chance-Konstruktion/ha-tp-link-powerline/releases/tag/v4.0.0
