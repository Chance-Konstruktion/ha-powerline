"""Privacy-safe downloadable diagnostics for Powerline Network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# async_redact_data applies these keys recursively, including inside config
# entries and raw protocol samples. Topology and poll records are also rebuilt
# below, because MACs can occur as values or dictionary keys, not only as
# values under a key named "mac".
TO_REDACT = {
    "mac",
    "plcmac",
    "cco_mac",
    "source_mac",
    "destination_mac",
    "responder",
    "peer",
    "ip",
    "ip_addr",
    "ip_address",
    "ipv4",
    "ipv6",
    "host",
    "hostname",
    "interface",
    "url",
    "probe_url",
    "token",
    "probe_token",
    "access_token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "key",
    "serial",
    "serial_number",
    "serialnumber",
    "username",
    "email",
    "address",
    "latitude",
    "longitude",
    "location",
    "name",
    "alias",
}

MAX_POLL_HISTORY = 20
MAX_RATE_SAMPLES = 200


def _integration_version() -> str | None:
    """Read the version from the integration manifest."""
    try:
        manifest = json.loads(
            Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    version = manifest.get("version")
    return str(version) if version is not None else None


def _identity(value: Any) -> str | None:
    """Canonicalise an identity for use only inside this diagnostic export."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _adapter_ids(
    devices: dict[str, Any],
    topology: dict[str, Any],
    polls: list[dict[str, Any]],
    plc_links: Any,
) -> dict[str, str]:
    """Map hardware identifiers to anonymous, dump-local adapter IDs."""
    macs: set[str] = {_identity(mac) for mac in devices if _identity(mac)}
    for node in topology.get("nodes", []) if isinstance(topology, dict) else []:
        if isinstance(node, dict) and (mac := _identity(node.get("mac"))):
            macs.add(mac)
    for poll in polls:
        for device in poll.get("devices", []) if isinstance(poll, dict) else []:
            if isinstance(device, dict) and (mac := _identity(device.get("mac"))):
                macs.add(mac)
    if isinstance(plc_links, dict):
        for pair in plc_links:
            if isinstance(pair, (tuple, list)):
                macs.update(mac for part in pair if (mac := _identity(part)))
    return {mac: f"adapter_{index:02d}" for index, mac in enumerate(sorted(macs), 1)}


def _adapter_id(value: Any, ids: dict[str, str]) -> str | None:
    identity = _identity(value)
    return ids.get(identity) if identity else None


def _safe_adapters(
    devices: dict[str, Any], ids: dict[str, str], default_chipset: Any
) -> list[dict[str, Any]]:
    """Project adapter data onto fields useful for debugging and safe to share."""
    result: list[dict[str, Any]] = []
    for mac, device in sorted(devices.items()):
        if not isinstance(device, dict):
            continue
        result.append(
            {
                "id": _adapter_id(mac, ids),
                "model": device.get("model") or "",
                "firmware": device.get("firmware_ver") or device.get("firmware") or "",
                "chipset": device.get("chipset") or default_chipset or "unknown",
                "tx_rate": device.get("tx_rate", 0),
                "rx_rate": device.get("rx_rate", 0),
                "online": bool(device.get("_online", device.get("online", True))),
                "role": device.get("role") or "",
                "capability": device.get("capability"),
                "capabilities": device.get("capabilities")
                if isinstance(device.get("capabilities"), dict)
                else {},
            }
        )
    return result


def _safe_edge(edge: Any, ids: dict[str, str]) -> dict[str, Any] | None:
    """Copy an edge without exposing its source or destination MAC."""
    if not isinstance(edge, dict):
        return None
    source = _adapter_id(edge.get("source"), ids)
    destination = _adapter_id(edge.get("destination"), ids)
    if source is None or destination is None:
        return None
    return {
        "source": source,
        "destination": destination,
        "tx_phy_rate": edge.get("tx_phy_rate", 0),
        "rx_phy_rate": edge.get("rx_phy_rate", 0),
        "average_rate": edge.get("average_rate", 0),
        "link_quality": edge.get("link_quality"),
        "estimated": bool(edge.get("estimated", False)),
        "timestamp": edge.get("timestamp"),
    }


def _safe_topology(topology: Any, ids: dict[str, str]) -> dict[str, Any]:
    """Return topology and analysis with all hardware addresses pseudonymised."""
    if not isinstance(topology, dict):
        topology = {}
    nodes: list[dict[str, Any]] = []
    for node in topology.get("nodes", []):
        if not isinstance(node, dict):
            continue
        adapter_id = _adapter_id(node.get("mac"), ids)
        if adapter_id is None:
            continue
        nodes.append(
            {
                "id": adapter_id,
                "model": node.get("model") or "",
                "manufacturer": node.get("manufacturer") or "",
                "firmware": node.get("firmware") or "",
                "chipset": node.get("chipset") or "",
                "role": node.get("role") or "",
                "online": bool(node.get("online", True)),
                "last_update": node.get("last_update"),
            }
        )

    edges = [
        safe_edge
        for edge in topology.get("edges", [])
        if (safe_edge := _safe_edge(edge, ids)) is not None
    ]
    analysis_source = topology.get("analysis")
    if not isinstance(analysis_source, dict):
        analysis_source = {}
    analysis = {
        "worst_link": _safe_edge(analysis_source.get("worst_link"), ids),
        "best_adapter": _adapter_id(analysis_source.get("best_adapter"), ids),
        "offline_adapters": [
            adapter_id
            for item in analysis_source.get("offline_adapters", [])
            if (adapter_id := _adapter_id(item, ids)) is not None
        ],
        "new_adapters": [
            adapter_id
            for item in analysis_source.get("new_adapters", [])
            if (adapter_id := _adapter_id(item, ids)) is not None
        ],
    }
    if "most_unstable_link" in analysis_source:
        analysis["most_unstable_link"] = _safe_edge(
            analysis_source.get("most_unstable_link"), ids
        )
    return {"nodes": nodes, "edges": edges, "analysis": analysis}


def _safe_plc_links(plc_links: Any, ids: dict[str, str]) -> list[dict[str, Any]]:
    """Flatten and pseudonymise the HomePlug directed-link table."""
    result: list[dict[str, Any]] = []
    if not isinstance(plc_links, dict):
        return result
    for pair, rates in sorted(plc_links.items(), key=lambda item: str(item[0])):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        responder = _adapter_id(pair[0], ids)
        peer = _adapter_id(pair[1], ids)
        if responder is None or peer is None or not isinstance(rates, dict):
            continue
        result.append(
            {
                "responder": responder,
                "peer": peer,
                "tx_rate": rates.get("tx_rate", 0),
                "rx_rate": rates.get("rx_rate", 0),
            }
        )
    return result


def _safe_poll_history(
    history: list[dict[str, Any]], ids: dict[str, str]
) -> list[dict[str, Any]]:
    """Keep recent poll outcomes while replacing each MAC with a local ID."""
    result: list[dict[str, Any]] = []
    for poll in history[-MAX_POLL_HISTORY:]:
        if not isinstance(poll, dict):
            continue
        devices: list[dict[str, Any]] = []
        for device in poll.get("devices", []):
            if not isinstance(device, dict):
                continue
            adapter_id = _adapter_id(device.get("mac"), ids)
            if adapter_id is None:
                continue
            devices.append(
                {
                    "id": adapter_id,
                    "status": device.get("status"),
                    "error": device.get("error"),
                    "timed_out": bool(device.get("timed_out", False)),
                    "online": bool(device.get("online", True)),
                    "missed_polls": device.get("missed_polls", 0),
                    "model": device.get("model") or "",
                    "firmware": device.get("firmware") or "",
                    "chipset": device.get("chipset") or "",
                    "tx_rate": device.get("tx_rate", 0),
                    "rx_rate": device.get("rx_rate", 0),
                }
            )
        result.append(
            {
                "timestamp": poll.get("timestamp"),
                "status": poll.get("status"),
                "discovered_count": poll.get("discovered_count", 0),
                "error_type": poll.get("error_type"),
                "timed_out": bool(poll.get("timed_out", False)),
                "state_query_error_type": poll.get("state_query_error_type"),
                "devices": devices,
            }
        )
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with identifiers and connection details removed."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {
            "integration": {"domain": DOMAIN, "version": _integration_version()},
            "error": "coordinator not loaded",
        }

    hp = getattr(coordinator, "hp", None)
    devices = getattr(coordinator, "devices", {}) or {}
    coordinator_data = getattr(coordinator, "data", None)
    if not isinstance(coordinator_data, dict):
        coordinator_data = {}
    topology = coordinator_data.get("topology", {})
    polls = list(getattr(coordinator, "poll_history", []) or [])
    plc_links = getattr(hp, "plc_links", {}) if hp is not None else {}
    ids = _adapter_ids(devices, topology, polls, plc_links)

    samples = list(getattr(hp, "rate_samples", []) or []) if hp is not None else []
    samples = samples[-MAX_RATE_SAMPLES:]
    capability = [
        sample
        for sample in samples
        if isinstance(sample, dict) and sample.get("capability_bit")
    ]

    entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    options = async_redact_data(dict(entry.options), TO_REDACT)

    return {
        "integration": {"domain": DOMAIN, "version": _integration_version()},
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": entry_data,
            "options": options,
        },
        "chipset": getattr(hp, "_chipset", None) if hp is not None else None,
        "adapters": _safe_adapters(
            devices, ids, getattr(hp, "_chipset", None) if hp is not None else None
        ),
        "topology": _safe_topology(topology, ids),
        "plc_links": _safe_plc_links(plc_links, ids),
        "poll_history": _safe_poll_history(polls, ids),
        "rate_samples": async_redact_data(samples, TO_REDACT),
        "rate_sample_summary": {
            "total": len(samples),
            "capability_flagged": len(capability),
            "usable": len(samples) - len(capability),
            "implausible_over_1200": sorted(
                {
                    sample["rate"]
                    for sample in samples
                    if isinstance(sample, dict) and (sample.get("rate") or 0) > 1200
                }
            ),
        },
    }
