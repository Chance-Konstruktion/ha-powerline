# Powerline Network Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Chance-Konstruktion/ha-tp-link-powerline)](https://github.com/Chance-Konstruktion/ha-tp-link-powerline/releases)
[![License: MIT](https://img.shields.io/github/license/Chance-Konstruktion/ha-tp-link-powerline)](LICENSE)

Home Assistant Integration for **Powerline / dLAN adapters** (TP-Link, FRITZ!Powerline, devolo, etc.) -- works with pure PLC adapters **without WiFi and without IP address**!

Communicates directly via **HomePlug AV** (Layer 2, Ethertype `0x88E1`) and **MEDIAXTREAM** (Ethertype `0x8912`, Broadcom) -- exactly like the official tpPLC app.

## Features

> **Current status:** Only the **Online/Offline status** (Connected / Disconnected) is confirmed to work reliably. All other features listed below are implemented but **not yet verified** to work correctly on real hardware. Use them with caution and report your findings.

- **Auto-Discovery** -- finds all Powerline adapters automatically via Layer 2
- **Online Status** per adapter (BinarySensor with `device_class: connectivity`) ✅ **Working**
- **TX/RX Data Rates** per adapter (Mbit/s PHY Rate) via passive monitoring (0x6046) ⚠️ *Unverified*
- **Adapter Count** (online + total) ⚠️ *Unverified*
- **Firmware Version** and model detection per adapter ⚠️ *Unverified*
- **LED Control** per adapter (on/off via MEDIAXTREAM 0xA058) ⚠️ *Unverified*
- **Power Saving Mode** per adapter (on/off, Broadcom only) ⚠️ *Unverified*
- **QoS Priority** per adapter (Gaming, VoIP, Audio/Video, Internet) ⚠️ *Unverified*
- **Diagnostic Button** -- full protocol scan with raw frame dump to logs ⚠️ *Unverified*
- **Dynamic Discovery** -- new adapters appear automatically within one poll cycle
- **Dual Protocol** -- auto-detects Broadcom (MEDIAXTREAM) vs. Qualcomm chipsets

## Supported Hardware

| Adapter | Chipset | Status |
|---------|---------|--------|
| TP-Link TL-PA7017 | Broadcom BCM60355 | Tested (Discovery) |
| TP-Link AV1000 | Broadcom | Tested (Discovery, Rates) |
| FRITZ!Powerline AV500 | Broadcom | Tested (Discovery) |
| devolo dLAN | Varies | Discovery works, features depend on chipset |
| Other HomePlug AV adapters | QCA / Broadcom | Discovery works on all, vendor features vary |

## Requirements

**Raw Socket access** (`CAP_NET_RAW`) + **Ethernet cable** (WiFi cannot send Layer 2 HomePlug AV frames!)

### Docker
```yaml
services:
  homeassistant:
    cap_add:
      - NET_RAW
    network_mode: host
```

### HAOS
Should work out of the box (host network mode is default).

### Python venv
```bash
sudo setcap cap_net_raw+ep $(readlink -f $(which python3))
```

## Installation

### HACS (Recommended)
1. Open HACS in Home Assistant
2. Search for **"Powerline Network"**
3. Install and restart Home Assistant
4. Go to **Settings** > **Devices & Services** > **Add Integration** > **"Powerline Network"**

### Manual
1. Copy `custom_components/powerline` to your `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings** > **Devices & Services** > **Add Integration** > **"Powerline Network"**
4. Click **Next** -- adapters are discovered automatically

## Entities

### Network Overview (virtual device)
| Entity | Type | Description |
|--------|------|-------------|
| TX Total | Sensor | Sum of TX rates of all adapters (Mbit/s) |
| RX Total | Sensor | Sum of RX rates of all adapters (Mbit/s) |
| Adapters Online | Sensor | Number of currently reachable adapters |
| Adapters Total | Sensor | Total number of ever-seen adapters |
| Diagnose | Button | Runs full protocol diagnostic scan |

### Per Adapter (each adapter becomes its own device)
| Entity | Type | Description | Default |
|--------|------|-------------|---------|
| TX Rate | Sensor | PHY TX Rate in Mbit/s | Enabled |
| RX Rate | Sensor | PHY RX Rate in Mbit/s | Enabled |
| Status | Binary Sensor | Online / Offline (connectivity) | Enabled |
| LED | Switch | LED on/off control | Disabled |
| Power Saving | Switch | Power saving mode on/off | Disabled |
| QoS Priority | Select | Traffic priority (Gaming/VoIP/A-V/Internet) | Disabled |

> LED, Power Saving, and QoS are **disabled by default** because they require Broadcom chipsets and may not work on all adapters. Enable them manually in the entity settings if your adapter supports them.

## Configuration

The scan interval is configurable under:
**Settings > Devices & Services > Powerline Network > Configure**

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Scan interval | 120s | 10--600s | Discovery + rate polling interval |

> Rates are also received **passively** via 0x6046 status indications (every 2--5 seconds from the adapter), so the scan interval mainly affects device discovery.

## How It Works

```
Home Assistant (Ethernet, CAP_NET_RAW)
     |
     | Raw Ethernet Frames
     |
     |-- 0x88E1 (HomePlug AV) --> CC_DISCOVER_LIST (all chipsets)
     |-- 0x8912 (MEDIAXTREAM)  --> MX_DISCOVER, LED, QoS, Rates (Broadcom)
     |
     +-- Adapter #1 (e.g. TL-PA7017, Broadcom BCM60355)
     |        Power Line
     +-- Adapter #2
```

### Protocol Details
| Function | MME Type | Direction | Description |
|----------|----------|-----------|-------------|
| Discovery | 0x0014/0x0015 | Bidirectional | CC_DISCOVER_LIST (all chipsets) |
| Broadcom Detection | 0xA070/0xA071 | Bidirectional | MEDIAXTREAM Discover |
| Passive Rates | 0x6046 | From adapter | Periodic TX/RX status (every 2--5s) |
| LED Control | 0xA058/0xA059 | Bidirectional | MEDIAXTREAM Action Command |
| Power Saving | 0xA058/0xA059 | Bidirectional | Two-frame sequence |
| QoS Priority | 0xA058/0xA059 | Bidirectional | Short + long frame sequence |
| Firmware Info | 0xA05C/0xA05D | Bidirectional | GET_PARAM (User HFID) |

## Troubleshooting

### Debug Logging
```yaml
logger:
  logs:
    custom_components.powerline: debug
```

### Common Issues

| Problem | Solution |
|---------|----------|
| "Raw socket access not available" | Add `CAP_NET_RAW` capability (see Requirements) |
| "No Powerline adapters found" | Check Ethernet cable connection (WiFi does not work!) |
| "No suitable network interface" | Ensure Ethernet interface is up |
| LED/QoS/Power Saving not working | Enable the entity first; only works on Broadcom chipsets |
| Duplicate devices after update | Remove integration, restart HA, re-add (auto-migration handles most cases) |

### Diagnostic Button
Press the **Diagnose** button entity to run a full protocol scan. Results are written to the Home Assistant log and include:
- All discovered devices with firmware/model
- Raw frame responses from all protocol tests
- Current LED/QoS/Power Saving states
- Passive rate monitoring results

### Wireshark
For deep protocol analysis, capture with filter:
```
eth.type == 0x88e1 || eth.type == 0x8912
```

## Bug Reports

Please use the [bug report template](https://github.com/Chance-Konstruktion/ha-tp-link-powerline/issues/new?template=bug_report.yml) and include:
- Home Assistant version + integration version
- Adapter model(s) + firmware
- Debug logs from `custom_components.powerline`
- Comparison with the Windows **tpPLC** app (if available)

## License

[MIT](LICENSE) -- Copyright 2026 Chance-Konstruktion

## Acknowledgments

- [pla-util](https://github.com/serock/pla-util) -- Ada HomePlug AV utility (protocol reference)
- [powerline](https://github.com/jbit/powerline) -- Rust Broadcom + QCA support (protocol reference)
- [peanball.net](https://peanball.net/2023/08/powerline-monitoring/) -- TL-PA7017 monitoring guide
