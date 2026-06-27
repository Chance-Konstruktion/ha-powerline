# Changelog

All notable changes to **Powerline Network** (ha-powerline) are documented here.

## [Unreleased]

## [260627] - 2026-06-27

### Added
- **Dedicated HACS info page (`info.md`).** HACS now renders a purpose-built,
  pure-Markdown info page instead of the HTML-heavy README (`render_readme` is
  disabled). Same content — highlights, hardware matrix, entities, configuration,
  requirements and troubleshooting — but rendered cleanly in the HACS frontend.

### Fixed
- **Corrected `iot_class` to `local_polling`.** The integration polls adapters
  via a `DataUpdateCoordinator`, so it is a polling integration, not push. Also
  declared the component `loggers` in the manifest (best practice).

## [260617] - 2026-06-17

### Changed
- **Switched to calendar-based release numbers (`YYMMDD`).** Releases are now
  versioned by date (e.g. `260617` = 2026-06-17) instead of SemVer. This is
  easier to read at a glance and sorts chronologically. It also fixes update
  detection for anyone still on the never-published **`4.x` pre-release**:
  SemVer `0.x` versions were *lower* than the stranded `4.2.0`, so HACS offered
  no update. A `YYMMDD` number is numerically greater than `4.2.0`, so the
  update is detected again. The Git tag for each release must match the
  `manifest.json` version (e.g. tag `260617`).

### Added
- **Delete a single adapter from the UI.** Each adapter is already its own
  device; now the integration implements `async_remove_config_entry_device`, so
  every adapter device gets a **Delete** button. You no longer have to remove and
  re-add the whole integration to clear out a wrongly detected adapter or one you
  swapped/replaced. A deleted adapter is forgotten from the coordinator and
  dropped from the stored device list, so it doesn't reappear after a restart.
  The **Powerline Network** overview device can't be deleted (it represents the
  integration itself), and an adapter that is still plugged in and reachable is
  rediscovered on the next poll — unplug it first to remove it for good.

## [0.3.0] - 2026-06-14

### Added
- **LED control now works on FRITZ!Powerline (AVM QCA7420).** AVM adapters
  (e.g. 510E) use a QCA7420 chip but ship "Custom" firmware with a **larger PIB**
  (9796 B vs the 9072 B the generic QCA path assumed) and an **AVM-specific LED
  table** (7 enable bytes at `0x1ED3 … 0x1F23`). The previous code wrote only the
  first 9072 bytes with a wrong open length/checksum, which the firmware rejected
  (`close status 31 00 5d`). A new `homeplug/fritz.py` module reads/writes the
  adapter's **real PIB size**, flips the AVM LED offsets (keeping the section
  checksums valid), and retries the asynchronous close — reconstructed
  **byte-for-byte** from a capture of the AVM app. Detection is by OUI or an
  `AVM`/`FRITZ` firmware/HFID marker. Discovery, status and PHY rates are
  unaffected.

- **Restart button for FRITZ!Powerline.** A per-adapter **Restart** button
  reboots the adapter via the QCA reset MME (`VS_RS_DEV`, `0xA01C` → `0xA01D`),
  captured from the FRITZ!Powerline app's restart action (empty request,
  MMV=0x00, QCA OUI). Soft reboot, not a factory reset; the adapter drops offline
  for a few seconds and the next poll picks it back up. Only created for AVM
  adapters for now.

### Changed
- **No QoS or power-saving entities for FRITZ!Powerline.** Per the FRITZ!
  Powerline app, these adapters (e.g. 510E) only expose LED, restart and reset —
  there is no QoS or power-saving option. The integration no longer creates a
  QoS selector or a power-saving switch for AVM adapters (detected by OUI or an
  AVM/FRITZ model/firmware string); the LED switch is still created.

### Documentation
- **PROTOCOL.md §9b** documents the FRITZ!Powerline LED path (real PIB size, AVM
  LED offsets, checksum folding, async close) and what restart/reset still need.
- **README** now lists FRITZ!Powerline in the hardware matrix and is honest about
  its scope: LED works (byte-verified against the AVM app capture), QoS/power
  saving are intentionally absent, and restart/reset are still to come.

## [0.2.6] - 2026-06-13

### Fixed
- **Mixed-network PHY rates no longer show 0 Mbit/s on one end.** In a mixed
  Broadcom + Qualcomm network the QCA "2-adapter symmetric mirror" still fired
  and gave the Broadcom adapter a *guessed* rate, which then blocked its real
  `NW_STATS` reading — so the two ends of the same link disagreed (e.g. AV1000
  TX=300/RX=0 vs AV500 TX=385/RX=11). The symmetric mirror is now restricted to
  **pure-QCA** networks; in a mixed network the real `NW_STATS` link rate is
  applied consistently to **both** ends. Pure-Broadcom and pure-QCA behaviour is
  unchanged.

## [0.2.5] - 2026-06-13

### Changed
- **Docs: mark AV500 (QCA7420) as verified everywhere and remove stale claims.**
  The Supported-Hardware matrix now shows the QCA7420 LED/QoS/power-saving column
  as ✅ verified, and the notes/troubleshooting/platform docstrings no longer say
  Qualcomm control is "not offered / Broadcom-only / too risky" — it's verified on
  two AV500s. Fixed a dead `PROTOCOL.md` §9 anchor in the README.

## [0.2.4] - 2026-06-13

### Fixed
- **Offline adapters now show "unavailable" instead of stale rates.** When an
  adapter is unplugged it is correctly flagged offline, but its TX/RX rate
  sensors kept displaying the last-seen speed. Every *per-adapter* entity (TX/RX
  rate, LED, Power Saving, QoS) now reports `unavailable` while the adapter is
  offline — HA greys it out and leaves a clean gap in history, instead of logging
  a misleading value. The connectivity binary sensor stays available so it can
  report "Disconnected", and the network-wide sensors are unaffected.
- **Options-flow translation:** the *Scan interval* field label now localizes
  correctly — it was under `options.data` instead of `options.step.init.data`
  (caught by the new hassfest CI), so it never showed the translated label.

### Internal
- **CI added**: `validate.yml` (HACS + hassfest), `tests.yml` (pytest on every
  push/PR), and `release.yml` (auto-builds and attaches `powerline.zip` on each
  release, per `hacs.json`).
- Added `issue_tracker` to `manifest.json` (required by HACS validation).
- Removed the unused `Preview.html` dev helper; expanded `.gitignore`.

## [0.2.3] - 2026-06-12

### Fixed
- **"All LEDs Off/On" now matches the physical LEDs on QCA (AV500) adapters.**
  A QCA adapter can *apply* a LED write but drop the close confirmation, so the
  per-adapter call reported failure even though the LED changed — the dashboard
  switch stayed on while the LED was physically off (Broadcom/AV1000 acks
  reliably, so it was unaffected). The bulk buttons now reflect the requested
  state for every adapter (like tpPLC) instead of trusting the flaky per-write
  ack. Individual LED switches are unchanged (they stay honest about a failed
  write).

## [0.2.2] - 2026-06-12

### Changed
- **Docs & labels match the tpPLC wording.** README now describes each control
  (LED on/off; Power Saving = lower power draw when the attached device has been
  off/unplugged ~5 min; QoS = pick the highest-priority traffic type) and lists
  the **All LEDs On/Off** buttons. Corrected the stale "LED/QoS/power-saving are
  Broadcom-only" notes — these are verified on **Broadcom and Qualcomm** now.
- **QoS option labels** renamed to the tpPLC terms: *Internet*, *Online Games*
  (de *Onlinespiele*), *Audio / Video* (de *Audio oder Video*), *Voice over IP*.
  Option keys are unchanged, so existing automations keep working.

## [0.2.1] - 2026-06-12

### Added
- **"All LEDs On" / "All LEDs Off" buttons** on the network device — mirrors the
  two buttons in tpPLC. Each loops the existing per-adapter LED control, so it
  works across mixed networks (every adapter takes its own chipset path), then
  refreshes the per-adapter LED switches. No new protocol/capture needed.

## [0.2.0] - 2026-06-12

This release marks **AV500 (Qualcomm QCA7420) control as verified on real
hardware**: LED, QoS and power saving now apply on **two different AV500
adapters** with no factory reset, completing dual-chipset (Broadcom + Qualcomm)
support.

### Highlights
- **AV500 LED / QoS / power saving confirmed working on two adapters.** The
  universal open checksum (0.1.11) was the missing piece — both `54:FE:E3` and
  `55:09:3F` now apply PIB writes and report their correct state, no reset needed.
- **PIB writes are safe.** Control is a read-modify-write of the adapter's *own*
  PIB carrying the universal `~xorfold32` open checksum; the frames are
  byte-identical to tpPLC and a rejected write is detected (close status
  `31 00 30`) and reverted — it cannot half-apply or brick an adapter. Raw
  `VS_WR_MOD` / `VS_MOD_NVM` are never sent. See `PROTOCOL.md` §9.

### Fixed
- **Mixed networks (Broadcom AV1000 + Qualcomm AV500 together) now work.** The
  chipset used to be detected once for the whole instance, so a network that
  contained AV500s forced *every* adapter onto the Qualcomm path: an AV1000 then
  failed to apply LED / QoS / power saving, mis-read its state, and showed no PHY
  rate. Chipset is now tracked **per adapter (per MAC)** — control, state reads
  and rate fetching each branch on the individual adapter's protocol, and a
  not-yet-identified adapter tries both. Rate fetching no longer stops at the
  first Qualcomm reply: in a mixed network it also runs the Broadcom rate methods
  for the non-QCA adapters.

### Changed
- Docs refreshed for the release: `README.md` (AV500 verified, safety note,
  roadmap), `PROTOCOL.md` §9 (control implemented & verified), and the QCA PIB
  code comments. `manifest.json` → `0.2.0`.

Includes everything from 0.1.6 – 0.1.12: QCA power-saving control, the
generalized then **universal** open checksum, corrected PIB offsets, real-state
reads, and the mixin-based `homeplug/` package refactor.

## [0.1.12] - 2026-06-11

### Changed
- **Internal refactor: the ~2,000-line `homeplug.py` is now a mixin-based package
  (`homeplug/`).** It splits into `const` / `frames` / `parsers`, a
  `_HomeplugBase` transport (dual raw sockets, send/recv, framing) and focused
  mixins — `DiscoveryMixin`, `StateMixin`, `QcaPibMixin`, `ControlMixin`,
  `DiagnosticsMixin` — composed into the same `HomeplugAV` facade. Purely
  structural: every function and constant is byte-identical (verified by AST),
  the public API (`from .homeplug import HomeplugAV, find_interface,
  is_available, async_discover, async_diagnose`) is unchanged, and all 56 tests
  pass without changes to their assertions.

## [0.1.11] - 2026-06-11

### Fixed
- **QCA writes are now accepted by *every* adapter (universal open checksum).**
  The write-open command carries a 4-byte checksum the adapter validates before
  *applying* a write. The previous value — the `0x0376` section checksum XOR a
  fixed key (`91 cb ab 39`) — had been cracked from a single adapter and did not
  generalize: a second AV500 (`55:09:3F`) rejected every write with close status
  `31 00 30` while its twin (`54:FE:E3`) applied them, even though tpPLC drove
  both fine. The real value is the open-plc-utils `checksum32`: the bitwise
  complement of a 32-bit XOR-fold over the **whole PIB** (little-endian words),
  stored little-endian. It is now computed directly from the PIB being written
  (`qca_pib_checksum()`) and reproduces tpPLC's bytes for both adapters.
- **Corrected all QCA PIB offsets to the true tpPLC offsets.** The read parser
  read the chunk payload at `pl[25]` and the write builder placed it at `pl[26]`
  — both shifted `+2` from the real offsets (data at `pl[27]`/`pl[28]`). Because
  the read and write shifts *cancel* for the PIB payload, one adapter appeared to
  work; only the whole-PIB open checksum exposed the error. All field offsets are
  now the true offsets, verified byte-identical against captures from **both**
  adapters: LED `0x1ED3…0x1F6B`, QoS `0x0ADC`, power saving `0x2141/0x2142/0x21EA/
  0x2264/0x2273`, section checksums `0x0374`/`0x03BC` (XOR-fold into byte `o % 4`).
- **Real QCA state (LED/QoS/power saving) is read from the corrected offsets**, so
  Home Assistant shows the adapter's actual current state on both adapters.

## [0.1.10] - 2026-06-11

### Fixed
- **A rejected QCA write is now reported as failed.** The close (apply) response
  carries a status: a healthy apply is all-zero, but some adapters reject it
  with a non-zero code (`31 00 30`) and then never change the LED/QoS/power
  saving — confirmed on hardware (one AV500 applied, its twin rejected every
  write while tpPLC worked on both). We now treat a non-zero close status as a
  real failure (logged with a hint to power-cycle the adapter / disable power
  saving) instead of pretending success.
- **Real QCA state is now read from the PIB.** On Qualcomm adapters the LED, QoS
  and power-saving state is read directly from the PIB (LED table, `0x0ADE`,
  `0x21EC`), so Home Assistant shows the adapter's actual current state instead
  of a default guess.

## [0.1.9] - 2026-06-11

### Diagnostics
- Log the adapter's **open and close responses** for a QCA PIB write. The bytes
  are stored and read-back-verified but the AV500 doesn't apply LED/power-saving
  at runtime, so this captures the device's status code (e.g. a non-zero
  `31 00 30` vs the healthy `00 00 00`) to find why the change isn't activated.

## [0.1.8] - 2026-06-10

### Fixed
- **QCA writes were stored but never applied (LED/QoS/power-saving did nothing).**
  The write-open command carries a 4-byte PIB checksum the adapter validates to
  *activate* the change; we were sending `crc32` (wrong), so the AV500 accepted
  and stored the bytes (read-back even "verified") but never applied them — the
  LED wouldn't toggle, etc. Cracked the real value from captures: it equals the
  `0x0376` checksum field **XOR a fixed key** (`91 cb ab 39`), matching LED, QoS,
  power-saving and start captures exactly. Now computed correctly.
- **Rate showed 0 when one direction was idle.** `VS_NW_INFO` often reports only
  one direction; the parser now accepts a partial reply and fills the missing
  direction from the peer (so HA shows e.g. ~168 instead of 0).

## [0.1.7] - 2026-06-10

### Fixed
- **QCA LED/QoS/power-saving falsely reported as failed.** Hardware logs proved
  the PIB writes were accepted and persisted (a "failed" QoS write was still on
  the device later; power saving visibly throttled the link), but the AV500 can
  keep serving the *old* PIB image for many seconds, so the read-back
  verification timed out, reported failure, and Home Assistant reverted the
  toggle — making working controls feel dead. Success is now determined the
  same way tpPLC does it: **open + every data chunk + close acknowledged**.
  The read-back remains as an informational log line only.

## [0.1.6] - 2026-06-10

### Added
- **Qualcomm (AV500) power-saving control.** Decoded from tpPLC captures: power
  saving sets 5 PIB bytes (`0x2143`=08, `0x2144`=96, `0x21EC`=01, `0x2266`=01,
  `0x2275`=02; off = all zero) plus the two XOR checksums. Generalized the
  checksum maintenance into one rule — a byte at offset `o` folds into checksum
  byte `(o % 4) XOR 2` of both fields — verified against two independent
  power-saving captures (predicted delta `01 08 97 02` == actual) and reused for
  QoS. Implemented as a PIB read-modify-write with retried read-back.

## [0.1.5] - 2026-06-10

### Fixed
- **QCA LED/QoS write "not confirmed".** The adapter commits the PIB a moment
  after the close, so the immediate read-back could still read the old value and
  report failure even though the write (verified byte-correct + acked) actually
  applied. The read-back now retries a few times with a short delay.
- **One adapter showing 0 Mbps.** A QCA `VS_NW_INFO` reply occasionally reports
  one direction as 0. In a 2-adapter network the link is symmetric, so a peer's
  rate is now mirrored (swapped) onto an adapter that got none.

## [0.1.4] - 2026-06-10

### Added
- **Qualcomm (AV500) QoS control.** Decoded from tpPLC captures: the QoS mode is
  a 2-byte PIB value (`0x0ADE`: internet `0x0000`, gaming `0xFA41`, audio/video
  `0xFA42`, VoIP `0xFA43`). Setting it also updates two **XOR checksums**
  (`0x0376`, `0x03BE`); since the checksum is XOR-linear we maintain it by
  XOR-ing the value delta — verified to reproduce the captured bytes exactly.
  Implemented as a PIB read-modify-write with read-back confirmation.

### Fixed
- **QCA PHY rate now matches tpPLC.** The raw `VS_NW_INFO` field is the firmware
  average PHY rate; tpPLC displays `floor(raw * 21/16)`. The integration applies
  the same factor (verified: 124→162, 140→183, 141→185, 142→186).

## [0.1.3] - 2026-06-09

### Fixed
- **QCA (AV500) PHY-rate decoding.** Decoded from a full tpPLC-start capture:
  the `VS_NW_INFO` (`0xA039`) confirm carries the responder's average PHY data
  rates as the **last two 4-byte little-endian** values (TX at end-8, RX at
  end-4, Mbit/s). 0.1.2's best-effort 2-byte heuristic is replaced by this exact
  parse, verified against the capture (e.g. `…7c000000 8c000000` → TX 124 / RX
  140, mirrored on the peer). PLC rates fluctuate, so the integration shows the
  current link rate, which can differ from a momentary tpPLC reading.

## [0.1.2] - 2026-06-09

### Fixed
- **Qualcomm (AV500) rates always 0 + ~50s polls.** The rate query used the
  wrong MME (`0xA048`, which the QCA7420 ignores), so the chipset stayed
  `unknown` and every poll spent ~50s timing out on Broadcom (`0x8912`) methods.
  Now the poll tries the **correct `VS_NW_INFO` (`0xA038`)** MME (which the
  AV500 answers), marks the chipset `qualcomm`, parses the per-station PHY
  rates, and **skips the Broadcom methods entirely** on a QCA network (poll
  drops from ~50s to a few seconds). Rate parsing is best-effort and
  range-validated; the full response is logged for refinement.

### Changed
- **Diagnose** now dumps full frames (256 bytes instead of 60) so large QCA
  responses (`VS_NW_INFO`/`VS_NW_INFO_STATS`) are fully visible.

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
