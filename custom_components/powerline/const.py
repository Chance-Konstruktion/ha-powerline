"""Constants for Powerline Network integration (HomePlug AV / MEDIAXTREAM)."""

from functools import lru_cache
from typing import Any

DOMAIN = "powerline"
MANUFACTURER = "Powerline"

# Vendor per MAC prefix. Deliberately short: only prefixes seen on hardware
# this integration was actually tested against, because a half-remembered OUI
# table that mislabels someone's adapter is worse than the neutral fallback.
# Adapters report no vendor name of their own (VS_SW_VER is empty on several
# models), so without this every device shows up as plain "Powerline".
OUI_VENDORS = {
    # TP-Link — the three AV1300 adapters from issue #108 ...
    "9C:A2:F4": "TP-Link",
    "5C:E9:31": "TP-Link",
    "3C:52:A1": "TP-Link",
    # ... and the AV1000 / AV500 units this integration was developed on
    "EC:08:6B": "TP-Link",
    "B0:19:21": "TP-Link",
    # AVM FRITZ!Powerline (same list as homeplug/fritz.py)
    "5C:49:79": "AVM",
    "9C:C7:A6": "AVM",
    "38:10:D5": "AVM",
    "C0:25:06": "AVM",
    "E0:28:6D": "AVM",
    "00:04:0E": "AVM",
}


def vendor_for_mac(mac: str) -> str:
    """Vendor name for a MAC, or the neutral fallback when unknown."""
    return OUI_VENDORS.get((mac or "")[:8].upper(), MANUFACTURER)

# Polling interval (seconds)
DEFAULT_SCAN_INTERVAL = 120
CONF_SCAN_INTERVAL = "scan_interval"
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 600

# Platforms
PLATFORMS = ["sensor", "binary_sensor", "switch", "select", "button"]

# QoS priority options
QOS_PRIORITY_GAMING = "gaming"
QOS_PRIORITY_VOIP = "voip"
QOS_PRIORITY_AV = "audio_video"
QOS_PRIORITY_INTERNET = "internet"
QOS_OPTIONS = [QOS_PRIORITY_GAMING, QOS_PRIORITY_VOIP, QOS_PRIORITY_AV, QOS_PRIORITY_INTERNET]

# Network-wide device identifier
NETWORK_DEVICE_ID = "powerline_network"
NETWORK_DEVICE_NAME = "Powerline Network"

# Topology (mesh graph) support
TOPOLOGY_EVENT = "powerline_topology_event"
# The whole frontend/ directory is served here, so the panel module can
# import the card module with a relative path.
FRONTEND_BASE_URL = "/powerline_frontend"
TOPOLOGY_CARD_URL = f"{FRONTEND_BASE_URL}/powerline-topology-card.js"
TOPOLOGY_PANEL_URL = f"{FRONTEND_BASE_URL}/powerline-topology-panel.js"

# Sidebar panel (toggleable via the options flow)
CONF_SIDEBAR_PANEL = "sidebar_panel"
DEFAULT_SIDEBAR_PANEL = True
PANEL_URL_PATH = "powerline"

# Smart topology alerts (persistent notifications, toggleable)
CONF_TOPOLOGY_ALERTS = "topology_alerts"
DEFAULT_TOPOLOGY_ALERTS = True

# Spatial Hub provider (https://gitlab.schanz.ipv64.net/chance-konstruktion/ha-spatial-hub).
# Registration is free when the hub is absent -- it is a dict in hass.data
# that nobody reads -- so this defaults to on. Users who want the adapters
# off their floor plan can switch it off in the options flow.
CONF_SPATIAL_HUB = "spatial_hub"
DEFAULT_SPATIAL_HUB = True
PROVIDER_ID = "powerline"
PROVIDER_LAYER_ID = "network_powerline"


@lru_cache(maxsize=128)
def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format."""
    return mac.upper().strip()


def get_mac(dev: dict[str, Any]) -> str:
    """Extract and normalize MAC address from a device dict."""
    raw = dev.get("mac") or dev.get("plcmac") or ""
    return normalize_mac(raw) if raw else ""

# Config flow: network interface selection
CONF_INTERFACE = "interface"
INTERFACE_AUTO = "auto"
