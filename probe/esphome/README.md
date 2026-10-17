# Powerline probe on an ESP32 (ESPHome)

The same read-only probe as [`powerline_probe.py`](../powerline_probe.py), as an
ESPHome external component. A small ESP32 board with a wired Ethernet port
plugged into the powerline adapters' segment talks HomePlug there and serves
the result as JSON over HTTP. Home Assistant's **Remote probe** setup reads it
exactly like the Python probe: no change in the integration.

```
 powerline adapters ── switch port (adapters' segment) ── ESP32 (Ethernet) ─┐
                                                                            │ HTTP :8765 (token)
 Home Assistant ◄───────────────────────────────────────────────────────────┘
```

Use it when HA cannot reach the adapters on layer 2 (own IoT VLAN, HAOS without
a raw socket on the right port) and you would rather not keep a Pi running. It
is read-only on purpose: there is no code in it that writes a setting to an
adapter, so LED, power saving, QoS and restart are not available through it.

## Boards

Any ESP32 with a wired Ethernet PHY works. Pick the `ethernet:` block of your
board and keep the rest of the example.

| Board | Status |
|-------|--------|
| WT32-ETH01 (LAN8720) | reference board, tested on hardware (next to a Bluetooth proxy on the same chip), [`powerline-probe-wt32-eth01.yaml`](powerline-probe-wt32-eth01.yaml) |
| LilyGO T-Internet-POE | untested, pins from the public pinout |
| Olimex ESP32-POE | untested, pins from the public pinout |

```yaml
# WT32-ETH01
esp32:
  board: wt32-eth01
ethernet:
  type: LAN8720
  mdc_pin: GPIO23
  mdio_pin: GPIO18
  clk:
    pin: GPIO0
    mode: CLK_EXT_IN
  phy_addr: 1
  power_pin: GPIO16
```

```yaml
# LilyGO T-Internet-POE (untested)
esp32:
  board: esp32dev
ethernet:
  type: LAN8720
  mdc_pin: GPIO23
  mdio_pin: GPIO18
  clk:
    pin: GPIO17
    mode: CLK_OUT
  phy_addr: 0
  power_pin: GPIO12
```

```yaml
# Olimex ESP32-POE (untested)
esp32:
  board: esp32-poe
ethernet:
  type: LAN8720
  mdc_pin: GPIO23
  mdio_pin: GPIO18
  clk:
    pin: GPIO17
    mode: CLK_OUT
  phy_addr: 0
  power_pin: GPIO12
```

SPI Ethernet modules (W5500 and friends) are not covered. Wi-Fi is no option
either: HomePlug needs the wire.

## 1. Build and flash

**Into an existing ESPHome device** (ESPHome dashboard / HA add-on): no clone
needed, ESPHome pulls the component from this repo. Add to the device's YAML:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/Chance-Konstruktion/ha-powerline
      ref: main
      path: probe/esphome/components
    components: [powerline_probe]

powerline_probe:
  port: 8765
  token: !secret powerline_probe_token
```

**Or standalone from a clone:**

The component needs the **esp-idf** framework (the Arduino framework is
rejected at validation). Copy the example, then give it a token:

```bash
cd probe/esphome
cp secrets.example.yaml secrets.yaml      # edit powerline_probe_token
esphome run powerline-probe-wt32-eth01.yaml
```

The example keeps `ota:` and `safe_mode:` so a bad build can always be
replaced over the network. `api:` has no encryption key there; add one if the
board is reachable by anything you do not trust.

## 2. Home Assistant

*Settings → Devices & services → Add integration → Powerline Network*, pick
**remote_probe** as interface, enter `http://<esp-ip>:8765` and the token.

Check it by hand first if you like:

```bash
curl -H "Authorization: Bearer <token>" http://<esp-ip>:8765/v1/info
curl -H "Authorization: Bearer <token>" "http://<esp-ip>:8765/v1/discover?timeout=5"
```

## Options

```yaml
powerline_probe:
  port: 8765                          # default 8765, separate from web_server on 80
  token: !secret powerline_probe_token  # clients must send "Authorization: Bearer <token>"
  interface: eth0                     # accepted for parity with the Python probe, ignored
```

Without a token anyone who reaches the port can read the adapter list; the
log says so at boot.

## How it works, and what differs from the Python probe

- Endpoints, query parameters, status codes and JSON are those of
  `powerline_probe.py` (`/v1/info`, `/v1/discover?timeout=`, `/v1/states?macs=`;
  `timeout` clamped to 1..15 s; 401 / 400 / 404 / 500 as JSON). The HomePlug logic
  is a C++ port of `custom_components/powerline/homeplug` (discovery, rates,
  CCo, firmware, LED / QoS / power-saving state); only the read path was ported.
- Requests are served one at a time, as the Python probe's lock does. A full
  discovery takes several seconds; the first `/v1/states` for a Qualcomm
  adapter probes its PIB size and can take longer.
- Raw frames use ESP-IDF's **L2 TAP** (`/dev/net/tap`, one descriptor per
  EtherType 0x88E1 and 0x8912, open only while a request runs). The component
  enables `CONFIG_ESP_NETIF_L2_TAP` itself. IP traffic is unaffected.
- A malformed `timeout` or MAC list answers 400 (also `nan`); other HTTP methods
  are refused by the ESP-IDF server instead of answering 501.
- `esp_http_server` limits the request URI to 512 bytes, roughly 25 MACs per
  `/v1/states` call.

## Tests without hardware

`tests/run_compat.py` compiles the HomePlug engine with a host C++ compiler and
replays scripted wire conversations (including real captured frames from the
test suite) through both it and the Python implementation; the JSON must match:

```bash
python probe/esphome/tests/run_compat.py
```

It does not exercise the ESP-IDF side (L2 TAP, HTTP server); that part has only
been compiled, not run on a board.
