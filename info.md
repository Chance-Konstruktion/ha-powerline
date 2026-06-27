# ⚡ Powerline for Home Assistant

**Monitor & control your HomePlug AV / AV2 powerline adapters — no IP, no WiFi, just Layer 2.**

Talks **directly** to pure PLC adapters over raw Ethernet (HomePlug AV `0x88E1` + Broadcom MEDIAXTREAM `0x8912`) — exactly like the official *tpPLC* app, but native in Home Assistant. Works with adapters that have **no IP address and no web UI**.

✅ **Verified end-to-end on TP-Link AV1000 (TL-PA7017, BCM60355) and AV500 (QCA7420)** — discovery, TX/RX rates, LED, power saving **and** QoS all confirmed on real hardware, including on **two** AV500 adapters.

> 🛡️ **PIB writes are safe by design.** AV500 LED / QoS / power-saving changes are applied with a *read-modify-write of the adapter's **own** PIB* — never a hard-coded image — carrying the same **universal open checksum** tpPLC uses (`~xorfold32` over the whole PIB). The frames are byte-identical to tpPLC and confirmed applying on two different adapters, and a rejected write is detected from the close status and reverted. Toggling these settings will **not brick** an adapter.

---

## 🤔 Why this exists

**Most powerline adapters have no IP, no app API, no web page — so how do you see them in Home Assistant?** You speak their native Layer-2 language. This integration does exactly that, straight over the Ethernet cable.

| | |
|---|---|
| 🔌 **No IP needed** | Pure PLC adapters (no WiFi, no web UI) are invisible to normal integrations. This one finds and reads them at **Layer 2**. |
| 🧪 **Verified, not guessed** | Every vendor command was **reverse-engineered from Wireshark** captures of the official tpPLC app and **tested on real AV1000 hardware** — LED, power saving and QoS all confirmed. See `PROTOCOL.md`. |
| 🎯 **Honest about chipsets** | Feature support depends on the **chipset** (and on vendor firmware — AVM FRITZ!Powerline gets its own module), and the docs say so plainly — no fake "supported" checkmarks. |

---

## ✨ Highlights

| Feature | Feature |
|---|---|
| 🔎 **Auto-Discovery** — finds every adapter via Layer-2 broadcast | 🟢 **Online status** — per-adapter connectivity binary sensor |
| 📈 **TX/RX PHY rates** — real Mbit/s, shown on both link ends | 💡 **LED control** — toggle the adapter LEDs (Broadcom + Qualcomm) |
| 🔋 **Power saving** — standby mode on/off (Broadcom + Qualcomm) | 🚦 **QoS priority** — Internet / Online Games / Audio-Video / VoIP |
| 🧩 **Dual protocol** — auto-detects Broadcom vs Qualcomm | 🛠️ **Diagnostic button** — full protocol scan to the log |

> 💡 **Online status + rates work on every HomePlug AV/AV2 chipset.** LED, power saving and QoS work on **both Broadcom** (MEDIAXTREAM `Set Parameter`) **and Qualcomm** (AV500-class, via a safe PIB read-modify-write) — verified on real hardware. See the feature matrix below.

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

No router, no cloud, no IP — the integration opens raw `AF_PACKET` sockets and exchanges HomePlug management messages directly with the adapters. Full wire details live in **`PROTOCOL.md`**.

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

> ⚠️ **Requires `CAP_NET_RAW` + a wired Ethernet path to a powerline adapter.** WiFi cannot send Layer-2 HomePlug frames. See Requirements below.

---

## 🧰 Supported Hardware

| Adapter / Chipset | Status & rates | LED | Power Save · QoS |
|---|:---:|:---:|:---:|
| TP-Link **AV1000** / TL-PA7017 — Broadcom BCM60355 | ✅ **verified** | ✅ **verified** | ✅ **verified** |
| Other **Broadcom** (MEDIAXTREAM) adapters | ✅ | ✅ *(expected)* | ✅ *(expected)* |
| Qualcomm **QCA7420** (AV500-class) | ✅ **verified** | ✅ **verified** *(via PIB)* | ✅ **verified** *(via PIB)* |
| **FRITZ!Powerline** (AVM QCA7420, e.g. 510E) | ✅ | ✅ *(see note)* | — *(not on device)* |
| devolo dLAN · misc HomePlug AV/AV2 | ✅ | depends on chipset | depends on chipset |

> ✅ = tested & confirmed on real hardware. Verified end-to-end — discovery, TX/RX rates, LED, power saving and QoS — on the **AV1000 (TL-PA7017)** (Broadcom, since 0.1) and on **two AV500 / QCA7420** adapters (Qualcomm, 0.2).

> 🟦 **FRITZ!Powerline (AVM):** these adapters use a QCA7420 chip but ship AVM's own "Custom" firmware, so they get a **dedicated module** (`homeplug/fritz.py`). Discovery, online status and PHY rates work like any QCA adapter. **LED on/off** is implemented via AVM's larger PIB (9796 B) and AVM-specific LED offsets — it was reconstructed **byte-for-byte from a capture of the FRITZ!Powerline app** (the generic QCA path failed only because of the wrong PIB size/offsets). **QoS and power saving are not offered** because the device itself has no such setting (the FRITZ!Powerline app only exposes LED, restart and reset). A **Restart** button is provided (soft reboot via `VS_RS_DEV`); factory reset is not implemented yet. See `PROTOCOL.md` §9b.

> ℹ️ On **Qualcomm** adapters, LED/QoS/power-saving live inside the device's *Parameter Information Block*. We change them exactly the way the vendor app does — a **read-modify-write of the adapter's own PIB** carrying the universal open checksum — verified byte-identical to tpPLC on two adapters, so it applies safely (a rejected write is detected and reverted, never half-applied). Details in `PROTOCOL.md` §9.

---

## 📋 Entities created

### Network overview (virtual device)

| Entity | Type | Description |
|--------|------|-------------|
| Adapters Online | Sensor | Reachable adapters (`total` ever-seen as attribute) |
| Slowest Link | Sensor | Weakest link rate in the network (Mbit/s) — the actual bottleneck |
| Network Problem | Binary Sensor (`problem`) | On when a known adapter is offline |
| Diagnose | Button | Runs a full protocol scan to the log |
| All LEDs On / Off | Button | Turns every adapter's LED on or off at once (like tpPLC) |

### Per adapter

| Entity | Type | Default |
|--------|------|---------|
| TX Rate / RX Rate | Sensor | Enabled |
| Status | Binary Sensor (`connectivity`) | Enabled |
| LED | Switch | Disabled |
| Power Saving | Switch | Disabled |
| QoS Priority | Select | Disabled |
| Restart | Button (`restart`) | FRITZ!Powerline only |

**What the controls do** (mirrors the tpPLC app):

- **LED** — turn the adapter's LEDs on or off.
- **Power Saving** — reduces the adapter's power consumption when the connected device has been switched off or unplugged for ~5 minutes.
- **QoS Priority** — choose the traffic type to give the highest priority: **Internet**, **Online Games**, **Audio / Video**, or **Voice over IP**.

LED / Power Saving / QoS are disabled by default — enable them per entity. They work on both Broadcom and Qualcomm (AV500) adapters. On **FRITZ!Powerline (AVM)** only the **LED** switch is created — those devices have no QoS or power-saving setting, so those entities are deliberately omitted.

---

## ⚙️ Configuration

**Settings → Devices & Services → Powerline → Configure**

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Scan interval | 120 s | 10–600 s | Discovery + rate polling interval |

**Removing a single adapter.** Each adapter is its own device, so you don't have to delete and re-add the whole integration to get rid of one. Open the adapter's device page (**Settings → Devices & Services → Powerline → the adapter**) and use the **Delete** button in the device menu. This is handy for an adapter that was wrongly detected, or one you've swapped out / replaced — the old entry and all its entities are removed, and it won't come back after a restart. The **Powerline Network** overview device can't be deleted (it represents the integration itself). Note: an adapter that is *still plugged in and reachable* will be rediscovered on the next poll — unplug it first, then delete it.

---

## 📦 Requirements

**Raw socket access (`CAP_NET_RAW`)** + a **wired Ethernet** path to an adapter.

**Docker**

```yaml
services:
  homeassistant:
    cap_add:
      - NET_RAW
    network_mode: host
```

**HAOS** — works out of the box (host networking is the default).

**Python venv**

```bash
sudo setcap cap_net_raw+ep $(readlink -f $(which python3))
```

---

## 🩺 Troubleshooting

Enable debug logging first:

```yaml
logger:
  logs:
    custom_components.powerline: debug
```

| Symptom | Fix |
|---|---|
| **"Raw socket access not available"** | Add the `CAP_NET_RAW` capability — see Requirements above. |
| **"No Powerline adapters found"** | Check the **Ethernet cable** (WiFi can't carry HomePlug frames) and that the adapters are plugged in and paired. |
| **LED / Power Saving / QoS do nothing** | These work on both **Broadcom** (MEDIAXTREAM) and **Qualcomm** (QCA, via the PIB) adapters — verified on AV1000 and AV500. Make sure the adapter is **online** (an offline adapter shows its controls as *unavailable*); other/older chipsets or firmware may not expose these controls. On **FRITZ!Powerline (AVM)** only **LED** is available. |
| **Deep protocol analysis (Wireshark)** | Filter on `eth.type == 0x88e1 || eth.type == 0x8912`, capture the official tpPLC app performing an action and compare with `PROTOCOL.md`. |

---

## 🤝 Contributing

PRs welcome — especially Wireshark captures from new adapters. Validate with:

```bash
python -m compileall custom_components/powerline
python -m pytest tests/
```

See `CONTRIBUTING.md` and the wire-level reference in `PROTOCOL.md`.

## 📄 License

MIT — © 2026 Chance-Konstruktion
