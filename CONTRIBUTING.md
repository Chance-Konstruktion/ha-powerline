# Contributing

Thanks for your interest in **ha-tp-link-powerline**!

## Project Status

This integration is **stable** and supports the following features:

- Auto-discovery of all HomePlug AV adapters (Layer 2)
- TX/RX rate monitoring (passive 0x6046 + active polling)
- LED control, Power Saving, QoS Priority (Broadcom MEDIAXTREAM)
- Binary sensor for connectivity status
- Full diagnostic scan with raw frame logging

## Reporting Bugs

Please use the [bug report template](https://github.com/Chance-Konstruktion/ha-tp-link-powerline/issues/new?template=bug_report.yml) and include:

- Home Assistant version + integration version
- Adapter model(s) + firmware version
- Reproducible steps
- Debug logs from `custom_components.powerline`
- Comparison with the Windows **tpPLC** app (if available)

## Debug Logging

```yaml
logger:
  logs:
    custom_components.powerline: debug
```

## Wireshark (optional but very helpful)

For protocol analysis (LED, QoS, Power Saving, rate issues):

1. Start Wireshark on the Ethernet interface where adapters are visible
2. Set display filter:
   ```
   eth.type == 0x88e1 || eth.type == 0x8912
   ```
3. Perform the action (e.g. toggle LED in tpPLC)
4. Save capture as `.pcapng`
5. Anonymize sensitive data (MAC addresses, serial numbers) before uploading

### Key Ethertypes
| Ethertype | Protocol | Usage |
|-----------|----------|-------|
| `0x88E1` | HomePlug AV | Discovery (0x0014/0x0015), standard MMEs |
| `0x8912` | MEDIAXTREAM | Broadcom: LED, QoS, Power Saving, Rates, Discovery |

### Key MME Types
| MME | Direction | Function |
|-----|-----------|----------|
| 0x0014/0x0015 | Bidirectional | CC_DISCOVER_LIST (all chipsets) |
| 0xA070/0xA071 | Bidirectional | MEDIAXTREAM Discover (Broadcom) |
| 0xA058/0xA059 | Bidirectional | MEDIAXTREAM Action (LED, QoS, Power Saving) |
| 0xA05C/0xA05D | Bidirectional | GET_PARAM (firmware, model) |
| 0x6046 | From adapter | Passive TX/RX status indication |

## Pull Requests

Small, focused PRs are preferred:

1. Describe the problem or feature briefly
2. Keep changes scoped and well-separated
3. Validate with `python -m compileall custom_components/powerline`
4. For protocol changes: include log excerpts or Wireshark captures
5. Update translations (`strings.json`, `en.json`, `de.json`) if adding user-visible strings

## Development Setup

```bash
git clone https://github.com/Chance-Konstruktion/ha-tp-link-powerline.git
cd ha-tp-link-powerline

# Validate syntax
python -m compileall custom_components/powerline

# Test in HA dev container or copy to config/custom_components/
```

## Architecture

```
custom_components/powerline/
  __init__.py       -- Entry setup, migration, device cleanup
  config_flow.py    -- Config + Options flow
  const.py          -- Constants, MAC normalization
  coordinator.py    -- DataUpdateCoordinator, state management
  homeplug.py       -- Raw socket Layer 2 communication (HomePlug AV + MEDIAXTREAM)
  sensor.py         -- TX/RX rate sensors, network overview sensors
  binary_sensor.py  -- Connectivity binary sensor
  switch.py         -- LED + Power Saving switches
  select.py         -- QoS Priority select
  button.py         -- Diagnostic button
  strings.json      -- Base translations
  translations/     -- en.json, de.json
```
