# Changelog

All notable changes to **Powerline Network** (ha-powerline) are documented here.

## [Unreleased]

## [0.1.1] - 2026-06-09

### Added
- **Qualcomm (AV500 / QCA7420) LED control.** Reverse-engineered from a tpPLC
  capture: the LED lives in a 10-byte table in the PIB (`0x01` off / `0x00` on).
  We do a careful read-modify-write over the module protocol (`0xA0B0/0xA0B1`) —
  read the device's real PIB, flip only those 10 bytes, write it back, then
  **verify by read-back**. Every other byte (network key, MAC, …) is preserved.
  The frame builders were validated byte-for-byte against the capture, and the
  read path against the captured PIB.
  - *Caveat:* the write-open carries a whole-PIB checksum we can't reproduce
    offline. If a firmware validates it, the toggle is a harmless no-op (reported
    as failure via the read-back), never a corruption. Tested on real hardware.

### Changed
- **Network overview device overhauled.** The meaningless "TX Total / RX Total"
  sum sensors (which just added unrelated link rates together) and the separate
  "Adapters Total" sensor are **removed**. The overview device now exposes:
  - **Adapters Online** (with the ever-seen total as an attribute),
  - **Slowest Link** — the weakest link rate in the network, i.e. the actual
    bottleneck (with the responsible adapter as an attribute),
  - **Network Problem** — a `problem` binary sensor that turns on when a known
    adapter is offline,
  - and the **Diagnose** button (unchanged).

  *Breaking:* dashboards/automations referencing the old `*_total` / total-rate
  entities must be updated.

### Research
- **Qualcomm (AV500) LED decoded.** A tpPLC capture (QCA7420) shows LED on/off is
  a full-PIB read-modify-write via MME `0xA0B0/0xA0B1`, but only **10 PIB bytes**
  change (`0x01` = off / `0x00` = on at offsets `0x1ED5, 0x1EFD, 0x1F05, 0x1F1D,
  0x1F25, 0x1F2D, 0x1F45, 0x1F4D, 0x1F55, 0x1F6D`) with no checksum/counter churn.
  Documented in `PROTOCOL.md` §9; implementation planned for 0.2.

## [0.1.0] - 2026-06-08

First public release. Native Home Assistant integration for HomePlug AV / AV2
powerline adapters over raw Layer 2 (no IP / WiFi needed).

### Added
- **Auto-discovery** of adapters via `CC_DISCOVER_LIST` (works on every chipset).
- **Per-adapter sensors**: TX/RX PHY rate (Mbit/s) and online status.
- **Broadcom (MEDIAXTREAM) control**: LED switch, power-saving switch, and QoS
  priority select — all reverse-engineered from tpPLC captures and verified on a
  TL-PA7017 (BCM60355).
- **Network overview** entities (total TX/RX, adapters online/total) and a
  **Diagnose** button that dumps a full protocol scan to the log.
- **Qualcomm (QCA / AV500)**: discovery, online status, firmware and rates;
  Diagnose probes the correct QCA read MMEs (`VS_NW_INFO`, `VS_LNK_STATS`,
  `VS_NW_INFO_STATS`). QCA control (LED/QoS/power-saving) is intentionally not
  implemented yet — see `PROTOCOL.md` §9. Planned for 0.2.
- **Documentation**: rewritten README, full Layer-2 reference (`PROTOCOL.md`),
  banner and local preview.

### Note on versioning
- The pre-release `manifest.json` carried an inflated `4.x` version that was
  never published. Reset to **`0.1.0`** for this first real release.

The entries below were developed pre-release and are included in 0.1.0.

### Fixed
- **LED/power-saving on a non-Broadcom adapter hung for ~12 s before failing** -- on a mixed network (e.g. a Qualcomm AV500 alongside Broadcom AV1000s), the MEDIAXTREAM control sequence ran every write into its full timeout on the adapter that does not speak MEDIAXTREAM. It now bails out as soon as the first Set Parameter gets no reply, failing in ~2 s.
- **QoS could be set but not read back** -- `query_device_states()` now derives the current QoS mode by matching the priority-map (`0x0069`) CAP bytes, so the QoS select reflects the adapter's real state instead of a default.
- **Switches reported "timed out" even though the command worked** -- a control command issued while a ~24 s poll held the shared lock waited longer than the 10 s budget, so the UI showed a failure although the adapter had accepted the command. Raised the control timeout to 30 s and shortened the poll (see below).
- **Slow polls (~24 s)** -- firmware/model is now queried only once per adapter per session instead of every poll (it timed out repeatedly on adapters that don't answer), and passive `0x6046` listening was cut from 6 s to 2 s since the active `NW_STATS` query is the reliable path. This also shortens how long the shared lock is held, so switches respond faster.
- **Crash / adapters appearing to "reset" when toggling a switch during a poll** -- the poll (`discover`) and control commands shared one `HomeplugAV` instance and each opened/closed the same raw sockets. Run concurrently in different executor threads, one closed a socket while the other was mid-receive (`'NoneType' object has no attribute 'settimeout'`), failing the update and briefly making all entities unavailable. All socket-using methods are now serialized on a lock.
- **Only one adapter showed a connection speed** -- `NW_STATS` reports the PHY rate against the *peer* MAC, so in a 2-adapter setup only one device got a value. The link rate is now mirrored onto the responding adapter too, so both endpoints show a speed.
- **LED/control switching failed in Home Assistant** -- `_send_recv()` collected responses for the *full* timeout, and the 0x8912 bus is never idle (constant 0xA070 beacons), so each of the three sequential LED writes blocked for 2 s. With the retry, one toggle exceeded the coordinator's 10 s budget and the switch reported a failure. `_send_recv()` now returns immediately once the expected confirmation (`stop_on`) arrives, making LED/power-saving/state-read commands near-instant.
- **TX/RX rates always 0** -- the MEDIAXTREAM Network Stats MMTYPE was wrong (`0xA034`); corrected to **`0xA02C/0xA02D`** (the `pla-util get-network-stats` command). Station Info corrected from `0xA080` to **`0xA04C/0xA04D`**. Values verified against `serock/mediaxtream-dissector` and `serock/pla-util`.
- **Absurd PHY rates (~16900-33000 Mbps)** -- the rate is the **low 12 bits** of the 16-bit field; the top nibble is a status flag (`0x8xxx` on an AV500 link, `0x4xxx` on an AV1000<->AV1000 link). `decode_phy_rate()` now masks `0x0FFF`. Confirmed against real TL-PA7017 captures: `0x819D` -> 413 Mbps and `0x4223` -> 547 Mbps (matching tpPLC). The earlier `0x8000`-only mask left AV1000 links reading ~16900.
- **Config flow button labelled "OK"** -- the discover/confirm steps use an empty form, so Home Assistant showed a generic "OK" button that did not match the "Click Submit" text. Added an explicit per-step `submit` label ("Submit" / "Absenden").
- **LED control did nothing** -- reverse-engineered the exact tpPLC sequence from a Wireshark capture (TL-PA7017). Toggling the LED is two `Set Parameter` writes (param **`0x0095`** and **`0x003F` "LED Options"**, byte 3 bit `0x10` = enabled) followed by an **Apply (`0xA020`)**. The old build sent a single mis-framed write and no apply, so nothing happened.
- **Get Parameter responses parsed wrong** -- the confirmation format is `OctetsPerElement(1) + NumElements(2 LE) + Value` (no parameter-id echo). Fixed `parse_mx_get_param_cnf()`, which also makes firmware/model strings and LED state read-back reliable.
- **Power saving did nothing** -- reverse-engineered from a tpPLC capture (TL-PA7017). Param **`0x0029`** is a 16-bit value: low 15 bits = standby timeout (s), top bit **`0x8000` = power-saving enabled** (same flag scheme as the PHY rate). Toggling writes `0x0029` (with/without the flag, timeout preserved), clears companion param `0x0074` on disable, then commits with **Apply (`0xA020`)**. The old 4-byte write with no flag/apply did nothing. Read-back now uses the `0x8000` bit too.

### Added
- **QoS Priority now actually works (Broadcom)** -- reverse-engineered from a tpPLC capture (TL-PA7017). QoS is the priority-mapping table (param `0x0069`): the integration reads it (Get Parameter), rewrites the 8 channel-access-priority (CAP) bytes for the chosen mode (Internet/Gaming/Audio-Video/VoIP) and writes it back (Set Parameter) — a safe read-modify-write, no flashing. The previous guessed two-frame payloads were ACKed but did nothing.
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
