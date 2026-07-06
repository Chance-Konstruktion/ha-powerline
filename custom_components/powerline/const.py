"""Constants for Powerline Network integration (HomePlug AV / MEDIAXTREAM)."""

from functools import lru_cache
from typing import Any

DOMAIN = "powerline"
MANUFACTURER = "Powerline"

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
TOPOLOGY_EVENT = "powerline_topology_event"


@lru_cache(maxsize=128)
def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format."""
    return mac.upper().strip()


def get_mac(dev: dict[str, Any]) -> str:
    """Extract and normalize MAC address from a device dict."""
    raw = dev.get("mac") or dev.get("plcmac") or ""
    return normalize_mac(raw) if raw else ""
