# Powerline probe on a Raspberry Pi (Zero 2 W and up)

The probe lets Home Assistant read powerline adapters that sit in a network
segment HA cannot reach on layer 2 (other VLAN, other building, HAOS without
access to the right port). It is **read-only** and needs nothing but Python.

```
 powerline adapters ── switch port (adapters' segment) ── USB-Ethernet ─┐
                                                                       Pi
 Home Assistant ◄──────────── HTTP :8765 (token) ─────────── WiFi or 2nd port ─┘
```

## What you need

- Raspberry Pi Zero 2 W (or any Pi / Linux SBC)
- USB-Ethernet adapter (on the Zero via an OTG adapter on the **USB** port, not PWR)
- A switch port in the **same segment as the powerline adapters**
  (usually the trunk's native/untagged VLAN)
- A way back to Home Assistant: WiFi, or the same cable if HA can route to it

## 1. Flash the SD card

Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)**, Bookworm or newer
(ships Python 3.11; the probe needs 3.10+). In the Imager settings set
hostname (e.g. `plprobe`), user, SSH and WiFi.

## 2. Find the interface

Boot with the USB-Ethernet adapter plugged in, SSH in, then:

```bash
ip -br link
```

The USB adapter is usually `eth0` (sometimes `enx…`). `wlan0` is WiFi.
Check that the adapters answer on it — with the probe in step 4 running in
debug mode you will see them; no extra tools needed.

## 3. Install

```bash
sudo apt install -y git
sudo git clone https://github.com/Chance-Konstruktion/ha-powerline.git /opt/ha-powerline
head -c 32 /dev/urandom | base64 | sudo tee /etc/powerline-probe.token >/dev/null
sudo chmod 600 /etc/powerline-probe.token
```

## 4. First test run

Replace `eth0` with the name from step 2:

```bash
sudo python3 /opt/ha-powerline/probe/powerline_probe.py --interface eth0 --port 8765 --token-file /etc/powerline-probe.token --debug
```

The log shows the adapters it found. Stop it with Ctrl+C.

## 5. Run as a service

```bash
sudo tee /etc/systemd/system/powerline-probe.service >/dev/null <<'EOF'
[Unit]
Description=Powerline probe for Home Assistant
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/ha-powerline/probe/powerline_probe.py --interface eth0 --port 8765 --token-file /etc/powerline-probe.token
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now powerline-probe
```

## 6. Home Assistant

1. Update the integration to **261016** or newer (HACS).
2. *Settings → Devices & services → Add integration → Powerline Network*.
3. Interface **remote_probe**, URL `http://plprobe.local:8765`
   (or the Pi's IP), token: the content of `/etc/powerline-probe.token`:

   ```bash
   sudo cat /etc/powerline-probe.token
   ```

Only HTTP from HA to the Pi must be allowed; no VLAN gets bridged.
Sensors, topology and history work; LED, power saving, QoS and restart are
not available through the probe.

## Updating

```bash
sudo git -C /opt/ha-powerline pull
sudo systemctl restart powerline-probe
```

## If no adapters show up

- Wrong interface or wrong switch port: the frames are layer 2 and never cross
  a router or WiFi.
- Run step 4 with `--debug` and attach the log to a GitHub issue.
