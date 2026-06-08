<div align="center">

<img src="assets/banner.svg" alt="Powerline for Home Assistant" width="100%">

# ⚡ Powerline for Home Assistant

**Monitor & control your HomePlug AV / AV2 powerline adapters — no IP, no WiFi, just Layer 2.**

Talks **directly** to pure PLC adapters over raw Ethernet (HomePlug AV `0x88E1` + Broadcom MEDIAXTREAM `0x8912`) — exactly like the official *tpPLC* app, but native in Home Assistant. Works with adapters that have **no IP address and no web UI**.

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Integration-03A9F4.svg)](https://www.home-assistant.io/)
[![Release](https://img.shields.io/badge/release-0.1.0-22D3EE.svg)](https://github.com/Chance-Konstruktion/ha-powerline/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-22D3EE.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/Protocol-reverse--engineered-F59E0B.svg)](PROTOCOL.md)

✅ **Verified end-to-end on TP-Link AV1000 (TL-PA7017, BCM60355)** — discovery, TX/RX rates, LED, power saving **and** QoS all confirmed working on real hardware.

**[Quick Start](#-quick-start)** · **[Features](#-highlights)** · **[How it works](#-how-it-works)** · **[Protocol](PROTOCOL.md)** · **[Troubleshooting](#-troubleshooting)**

</div>

---

## 🤔 Why this exists

**Most powerline adapters have no IP, no app API, no web page — so how do you see them in Home Assistant?** You speak their native Layer-2 language. This integration does exactly that, straight over the Ethernet cable.

<table>
<tr>
<td width="33%" valign="top">

### 🔌 No IP needed
Pure PLC adapters (no WiFi, no web UI) are invisible to normal integrations. This one finds and reads them at **Layer 2**.

</td>
<td width="33%" valign="top">

### 🧪 Verified, not guessed
Every vendor command was **reverse-engineered from Wireshark** captures of the official tpPLC app and **tested on real AV1000 hardware** — LED, power saving and QoS all confirmed. See [`PROTOCOL.md`](PROTOCOL.md).

</td>
<td width="33%" valign="top">

### 🎯 Honest about chipsets
Feature support depends on the **chipset**, and the docs say so plainly — no fake "supported" checkmarks.

</td>
</tr>
</table>

---

## ✨ Highlights

<table>
<tr>
<td width="50%">🔎 <b>Auto-Discovery</b> — finds every adapter via Layer-2 broadcast</td>
<td width="50%">🟢 <b>Online status</b> — per-adapter connectivity binary sensor</td>
</tr>
<tr>
<td>📈 <b>TX/RX PHY rates</b> — real Mbit/s, shown on both link ends</td>
<td>💡 <b>LED control</b> — toggle the adapter LED (Broadcom)</td>
</tr>
<tr>
<td>🔋 <b>Power saving</b> — standby mode on/off (Broadcom)</td>
<td>🚦 <b>QoS priority</b> — Gaming / VoIP / A-V / Internet (Broadcom)</td>
</tr>
<tr>
<td>🧩 <b>Dual protocol</b> — auto-detects Broadcom vs Qualcomm</td>
<td>🛠️ <b>Diagnostic button</b> — full protocol scan to the log</td>
</tr>
</table>

> 💡 **Online status + rates work on every HomePlug AV/AV2 chipset.** LED, power saving and QoS are **Broadcom-only** (they use safe MEDIAXTREAM `Set Parameter` writes). See the [feature matrix](#-supported-hardware).

---

## 🧭 How it works

```
Home Assistant  (Ethernet · CAP_NET_RAW)
      │  raw Layer-2 frames
      ├── 0x88E1  HomePlug AV  → CC_DISCOVER_LIST   (all chipsets)
      └── 0x8912  MEDIAXTREAM  → rates · LED · QoS  (Broadcom)
      │
   ┌──┴───────── power line ─────────────┐
 Adapter A  ◀───  PHY link (Mbit/s)  ───▶  Adapter B
```

No router, no cloud, no IP — the integration opens raw `AF_PACKET` sockets and
exchanges HomePlug management messages directly with the adapters. Full wire
details live in **[`PROTOCOL.md`](PROTOCOL.md)**.

---

## 🚀 Quick Start

**1. Install via HACS** (Custom repository → Integration)

```text
HACS → ⋮ → Custom repositories
Repository: Chance-Konstruktion/ha-powerline
Category:   Integration
```

**2. Restart Home Assistant**, then add the integration:

```text
Settings → Devices & Services → Add Integration → "Powerline"
```

**3. Click *Submit*** — adapters are discovered automatically. Done.

> ⚠️ **Requires `CAP_NET_RAW` + a wired Ethernet path to a powerline adapter.**
> WiFi cannot send Layer-2 HomePlug frames. See [Requirements](#-requirements).

---

## 🧰 Supported Hardware

| Adapter / Chipset | Status & rates | LED · Power Save · QoS |
|---|:---:|:---:|
| TP-Link **AV1000** / TL-PA7017 — Broadcom BCM60355 | ✅ **verified** | ✅ **verified** |
| Other **Broadcom** (MEDIAXTREAM) adapters | ✅ | ✅ *(expected)* |
| Qualcomm **QCA7420** (AV500-class) & other QCA | ✅ | 🚧 *(planned 0.2 — see note)* |
| FRITZ!Powerline · devolo dLAN · misc HomePlug AV/AV2 | ✅ | depends on chipset |

> ✅ = tested & confirmed on real hardware. The **AV1000 (TL-PA7017)** is the
> reference device: discovery, TX/RX rates, LED, power saving and QoS are all
> verified end-to-end in 0.1.

> ℹ️ On **Qualcomm** adapters, LED/QoS/power-saving live inside the device's
> *Parameter Information Block* and the vendor app only changes them via a full
> PIB read-modify-write — too risky to replicate, so those controls aren't
> offered there. Details + capture recipe in [`PROTOCOL.md` §9](PROTOCOL.md#9--qualcomm-qca--av500--current-state).

<details>
<summary><b>📋 Entities created</b></summary>

### Network overview (virtual device)
| Entity | Type | Description |
|--------|------|-------------|
| Adapters Online | Sensor | Reachable adapters (`total` ever-seen as attribute) |
| Slowest Link | Sensor | Weakest link rate in the network (Mbit/s) — the actual bottleneck |
| Network Problem | Binary Sensor (`problem`) | On when a known adapter is offline |
| Diagnose | Button | Runs a full protocol scan to the log |

### Per adapter
| Entity | Type | Default |
|--------|------|---------|
| TX Rate / RX Rate | Sensor | Enabled |
| Status | Binary Sensor (`connectivity`) | Enabled |
| LED | Switch | Disabled |
| Power Saving | Switch | Disabled |
| QoS Priority | Select | Disabled |

LED / Power Saving / QoS are disabled by default — enable them per entity if your
adapter is Broadcom-based.
</details>

<details>
<summary><b>⚙️ Configuration</b></summary>

**Settings → Devices & Services → Powerline → Configure**

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Scan interval | 120 s | 10–600 s | Discovery + rate polling interval |
</details>

---

## 📦 Requirements

**Raw socket access (`CAP_NET_RAW`)** + a **wired Ethernet** path to an adapter.

<details>
<summary><b>Docker</b></summary>

```yaml
services:
  homeassistant:
    cap_add:
      - NET_RAW
    network_mode: host
```
</details>

<details>
<summary><b>HAOS</b></summary>

Works out of the box (host networking is the default).
</details>

<details>
<summary><b>Python venv</b></summary>

```bash
sudo setcap cap_net_raw+ep $(readlink -f $(which python3))
```
</details>

---

## 🩺 Troubleshooting

Enable debug logging first:

```yaml
logger:
  logs:
    custom_components.powerline: debug
```

<details>
<summary>"Raw socket access not available"</summary>

Add the `CAP_NET_RAW` capability — see [Requirements](#-requirements).
</details>

<details>
<summary>"No Powerline adapters found"</summary>

Check the **Ethernet cable** (WiFi can't carry HomePlug frames) and that the
adapters are plugged in and paired.
</details>

<details>
<summary>LED / Power Saving / QoS do nothing</summary>

These are **Broadcom-only**. On a Qualcomm (QCA) adapter the switch will fail
quickly by design — see the [feature matrix](#-supported-hardware).
</details>

<details>
<summary>Deep protocol analysis (Wireshark)</summary>

```text
eth.type == 0x88e1 || eth.type == 0x8912
```

Capture the official tpPLC app performing an action and compare with
[`PROTOCOL.md`](PROTOCOL.md).
</details>

---

## 🗺️ Roadmap

- [x] **0.1 — Broadcom / AV1000 (verified):** discovery, TX/RX rates, LED, power saving, QoS — all confirmed on TL-PA7017.
- [ ] **0.2 — Qualcomm (QCA / AV500) control:** LED, power saving, QoS via a safe, minimal PIB write. Driven by tpPLC captures from real QCA hardware ([recipe](PROTOCOL.md#9--qualcomm-qca--av500--current-state)).
- [ ] **0.2 — rates between two same-chipset adapters:** `NW_STATS` reports the rate against the *peer*, so a link is mirrored onto the responder. Two AV500s (or any pair where neither answers `NW_STATS`) can still show no rate — being addressed alongside the AV500 work.
- [ ] **G.hn powerline** *(maybe someday)* — G.hn (ITU-T G.9960/61, e.g. devolo Magic) is a **separate, incompatible** standard and would need its own module. On the wishlist for if/when suitable adapter hardware is available to capture and test.

---

## 🤝 Contributing

PRs welcome — especially Wireshark captures from new adapters. Validate with:

```bash
python -m compileall custom_components/powerline
python -m pytest tests/
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the wire-level reference in [`PROTOCOL.md`](PROTOCOL.md).

## 📄 License

[MIT](LICENSE) — © 2026 Chance-Konstruktion

## 🙏 Acknowledgments

- [`serock/mediaxtream-dissector`](https://github.com/serock/mediaxtream-dissector) · [`serock/pla-util`](https://github.com/serock/pla-util) · [`jbit/powerline`](https://github.com/jbit/powerline) · [`qca/open-plc-utils`](https://github.com/qca/open-plc-utils)
- [peanball.net powerline monitoring guide](https://peanball.net/2023/08/powerline-monitoring/)

<div align="center">
<sub>Built for pure-PLC adapters that nobody else talks to · ⚡ Layer 2 all the way down</sub>
</div>
