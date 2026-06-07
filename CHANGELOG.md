# Changelog

All notable changes to **Powerline Network** (ha-tp-link-powerline) are documented here.

## [Unreleased]

### Fixed
- **TX/RX rates always 0** -- the MEDIAXTREAM Network Stats MMTYPE was wrong (`0xA034`); corrected to **`0xA02C/0xA02D`** (the `pla-util get-network-stats` command). Station Info corrected from `0xA080` to **`0xA04C/0xA04D`**. Values verified against `serock/mediaxtream-dissector` and `serock/pla-util`.
- **Absurd PHY rates (~33000 Mbps)** -- the rate field's top bit (`0x8000`) is a link-active flag, not part of the value. It is now masked off (`decode_phy_rate()`), confirmed against a real TL-PA7017 capture (`0x81A6` -> 422 Mbps).
- **Config flow button labelled "OK"** -- the discover/confirm steps use an empty form, so Home Assistant showed a generic "OK" button that did not match the "Click Submit" text. Added an explicit per-step `submit` label ("Submit" / "Absenden").
- **LED control did nothing** -- `0xA058` is **Set Parameter**, not an opaque "action" command. LED is now written as a structured Set Parameter to param **`0x003E` (LED Control)** with value `0x01`/`0x00`, instead of a hand-captured 30-byte blob that never carried the correct parameter id.
- **Power saving did nothing** -- now written via Set Parameter to param **`0x0029` (Power Manager Standby Timeout)**.

### Added
- `build_mx_set_param()` helper implementing the documented Set Parameter payload layout (ParamID + OctetsPerElement + NumElements + Value).
- **State read-back** -- `query_device_states()` now reads the real LED and power-saving state via Get Parameter (`0xA05C`, params `0x003E` / `0x0029`) instead of always returning defaults.

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
