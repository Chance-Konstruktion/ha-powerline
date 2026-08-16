"""Spatial Hub provider shim -- copy this file into your integration.

Copy, do not import. The hub may not be installed, may be a different
version, or may be removed while your integration keeps running. This file
therefore has zero imports from ``spatial_hub``: it writes a dict into
``hass.data`` and fires dispatcher signals, both of which cost nothing when
nobody is listening.

The whole integration usually looks like this, inside ``async_setup_entry``::

    from .spatial_hub_provider import spatial_provider

    spatial_provider(
        hass,
        entry,
        name="My Integration",
        icon="mdi:flash",
        data=lambda: ["light.kitchen", "sensor.hallway_temperature"],
        coordinator=coordinator,
    )

That is the complete integration. ``spatial_provider`` registers,
withdraws on unload, and re-notifies the hub on every coordinator update --
you never call register/unregister/notify yourself.

A bare entity id is a full node: Home Assistant already knows its name,
area, icon and state, so the hub fills those in. Use :func:`node` and
:func:`edge` when you have more to say than an entity id.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)

_LOGGER = logging.getLogger(__name__)

# ── Frozen contract strings (must match the hub verbatim) ─────────────
DATA_PROVIDERS = "spatial_hub_providers"
SIGNAL_PROVIDER_REGISTERED = "spatial_hub_provider_registered"
SIGNAL_PROVIDER_REMOVED = "spatial_hub_provider_removed"
SIGNAL_DATA_UPDATED = "spatial_hub_data_updated"
API_VERSION = 1

# Which revision of *this file* you copied. It travels with the
# registration so the hub can tell you, in its diagnostics, that a newer
# one exists -- without this file ever calling home, importing the hub, or
# pinning you to a version of anything.
#
# Bumped only when the shim gains something worth going back for. The
# contract above is frozen; this is not part of it.
#
# 5 -- `anchors`: say what a node is near when you cannot say where it is.
SDK_VERSION = 5

# ── Spatial vocabulary (Specification 1.0) ───────────────────────────
#
# Copied, like everything else in this file. Compare against these rather
# than against a literal: "outside" is the mistake this exists to prevent.
class AreaKind(StrEnum):
    """What an area is. Specification 1.0 § Area Type."""

    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    VIRTUAL = "virtual"


class NodeState(StrEnum):
    """The states every renderer is expected to style. § Node."""

    ONLINE = "online"
    OFFLINE = "offline"
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class EdgeQuality(StrEnum):
    """How good a connection is. § Edge."""

    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"


@callback
def spatial_provider(
    hass: HomeAssistant,
    entry: Any,
    name: str,
    data: Callable[[], Any],
    *,
    provider_id: str | None = None,
    icon: str = "",
    version: str = "",
    capabilities: dict[str, bool] | None = None,
    layers: list[dict[str, Any]] | None = None,
    icon_set: dict[str, Any] | None = None,
    panel_url: str = "",
    history: Callable[..., Any] | None = None,
    action: Callable[..., Any] | None = None,
    coordinator: Any = None,
    signals: Iterable[str] | str | None = None,
) -> SpatialHubProvider:
    """Register with the hub and wire up the whole lifecycle. One call.

    ``entry`` is your ConfigEntry: unregistration is hooked onto its unload,
    so a removed integration leaves no ghost layer behind.

    ``coordinator`` is optional. Pass your DataUpdateCoordinator and the hub
    is told to re-fetch after every refresh -- which is what makes the floor
    plan live without you writing a single push.

    ``signals`` is for integrations that have no DataUpdateCoordinator --
    a UDP listener, an MQTT subscription, anything push-shaped. Name the
    dispatcher signals you already fire when your data changes and the hub
    is told on each of them. They are disconnected on unload with
    everything else::

        signals=[SIGNAL_NODE_DISCOVERED, SIGNAL_NODE_AVAILABILITY]

    ``provider_id`` defaults to your integration's domain, which is exactly
    what you want unless you register more than one provider.

    ``icon_set`` and ``panel_url`` are how your integration keeps its own
    face: the icons your nodes are drawn with, and the panel the hub links
    to from their popups. Both are optional and neither is interpreted --
    the hub decides *where* things are drawn, you decide what they look
    like, and Home Assistant stays the source of the data.
    """
    provider = SpatialHubProvider(
        hass,
        provider_id=provider_id or _domain_of(entry, name),
        name=name,
        data=data,
        icon=icon,
        version=version,
        capabilities=capabilities,
        layers=layers,
        icon_set=icon_set,
        panel_url=panel_url,
        history=history,
        action=action,
    )
    provider.async_register()

    if entry is not None and hasattr(entry, "async_on_unload"):
        entry.async_on_unload(provider.async_unregister)
    if coordinator is not None:
        if hasattr(coordinator, "async_add_listener"):
            # Dropped by async_unregister too, so switching the provider off
            # mid-run stops the chatter as well -- not just unloading.
            provider.async_on_unregister(
                coordinator.async_add_listener(provider.async_notify)
            )
        elif not signals:
            # Silence here would be the cruellest outcome: the plan draws
            # once and then never moves, with nothing anywhere saying why.
            _LOGGER.warning(
                "Spatial Hub: the coordinator passed by %s has no "
                "async_add_listener, so the hub will never hear about "
                "changes. Pass signals=[...] with the dispatcher signals "
                "you already fire, or call provider.async_notify() yourself",
                provider.provider_id,
            )

    if signals:
        if isinstance(signals, str):
            signals = [signals]
        for signal in signals:
            provider.async_on_unregister(
                # Swallow the payload. Dispatcher signals carry whatever
                # their sender felt like sending -- a unit number, a node
                # object, nothing at all -- and the hub does not want to
                # know: it re-fetches. Connecting async_notify directly
                # raises TypeError on any signal that carries something.
                async_dispatcher_connect(
                    hass, signal, lambda *_args: provider.async_notify()
                )
            )

    return provider


def _domain_of(entry: Any, fallback: str) -> str:
    domain = getattr(entry, "domain", None)
    return domain or fallback.lower().replace(" ", "_")


# ── Builders ──────────────────────────────────────────────────────────
#
# Optional: plain dicts work just as well. These exist so a typo in a key
# name is a TypeError at the call site instead of a silently missing label.


def node(
    id: str,  # noqa: A002 - the field really is called id
    *,
    label: str = "",
    entity_id: str | None = None,
    area_id: str | None = None,
    floor_id: str | None = None,
    state: str = "",
    icon: str = "",
    color: str = "",
    position: dict[str, float] | None = None,
    actions: Iterable[dict[str, Any]] = (),
    anchors: Iterable[dict[str, Any]] = (),
    **metadata: Any,
) -> dict[str, Any]:
    """One thing that sits somewhere.

    Leave ``position`` out unless you genuinely know where the device is:
    the hub centres it in its area and the user drags it from there.
    Anything extra you pass lands in the node's metadata and shows up in
    the popup, so ``node("a", tx_rate=560)`` just works.

    ``anchors`` is for the case in between -- you cannot say *where* this
    is, but you can measure what it is *near*. Name your own nodes and how
    strongly, with :func:`anchor`, and the hub places this one between
    them::

        node("tag-4c1f", anchors=[anchor("proxy-kitchen", 0.8),
                                  anchor("proxy-hall", 0.2)])

    The weights are relative and unitless -- only the proportions inside
    one list are ever compared, so derive them from RSSI, LQI, link rate
    or anything else, as long as higher means nearer. The hub resolves
    them against wherever the user actually placed those anchors, which is
    knowledge you do not have and are not given: positions travel one way.
    A node the user has dragged themselves ignores its anchors for good.
    """
    result: dict[str, Any] = {"id": id}
    optional = {
        "label": label,
        "entity_id": entity_id,
        "area_id": area_id,
        "floor_id": floor_id,
        "state": state,
        "icon": icon,
        "color": color,
        "position": position,
    }
    result.update({key: value for key, value in optional.items() if value})
    if actions:
        result["actions"] = list(actions)
    if anchors:
        result["anchors"] = list(anchors)
    if metadata:
        result["metadata"] = metadata
    return result


def anchor(
    id: str,  # noqa: A002 - matches the node field it points at
    weight: float = 1.0,
) -> dict[str, Any]:
    """One node this node is near, and how strongly. Higher means nearer.

    ``id`` is one of *your own* node ids, unnamespaced, exactly as you
    wrote it. You cannot anchor to another provider's node: you have no
    way of knowing it will still be there on the next refresh.

    A weight of zero or below is dropped rather than treated as "very far
    away" -- it is almost always a division that went wrong, and averaging
    with it would move the answer somewhere nobody measured.
    """
    return {"id": id, "weight": float(weight)}


def edge(
    source: str,
    target: str,
    *,
    id: str | None = None,  # noqa: A002
    label: str = "",
    value: float | None = None,
    quality: str = "",
    color: str = "",
    width: float | None = None,
    directed: bool = False,
    dashed: bool = False,
    animated: bool = False,
    **metadata: Any,
) -> dict[str, Any]:
    """A relationship between two nodes: a link, a flow, a pipe, a trail.

    ``quality`` is one of ``good`` / ``fair`` / ``poor`` -- the shared
    vocabulary that lets a renderer colour your edges without knowing what
    they mean.
    """
    result: dict[str, Any] = {
        "id": id or f"{source}__{target}",
        "source": source,
        "target": target,
    }
    optional = {
        "label": label,
        "value": value,
        "quality": quality,
        "color": color,
        "width": width,
    }
    result.update({key: value for key, value in optional.items() if value})
    result.update({"directed": directed, "dashed": dashed, "animated": animated})
    if metadata:
        result["metadata"] = metadata
    return result


def action(
    id: str,  # noqa: A002
    label: str = "",
    icon: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Something the user can trigger; your ``action`` callable runs it."""
    return {"id": id, "label": label or id, "icon": icon, "confirm": confirm}


# ── The registration itself ───────────────────────────────────────────


class SpatialHubProvider:
    """Announces one integration's spatial data to the hub, if present.

    Most integrations never touch this class directly -- use
    :func:`spatial_provider`, which builds it and wires the lifecycle.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        provider_id: str,
        name: str,
        data: Callable[[], Any],
        icon: str = "",
        version: str = "",
        capabilities: dict[str, bool] | None = None,
        layers: list[dict[str, Any]] | None = None,
        icon_set: dict[str, Any] | None = None,
        panel_url: str = "",
        history: Callable[..., Any] | None = None,
        action: Callable[..., Any] | None = None,
    ) -> None:
        self.hass = hass
        self.provider_id = provider_id
        self._registration: dict[str, Any] = {
            "provider_id": provider_id,
            "api_version": API_VERSION,
            "sdk_version": SDK_VERSION,
            "name": name,
            "icon": icon,
            "version": version,
            # Not stated? Infer it from what was actually passed in, so
            # nobody's feature is switched off by a forgotten flag.
            "capabilities": capabilities or {
                "nodes": True,
                "edges": True,
                "popup": True,
                "history": history is not None,
                "actions": action is not None,
                "custom_icons": bool(icon_set),
            },
            "layers": layers or [],
            "icon_set": icon_set or {},
            # Your own view, if you have one. The hub links to it from the
            # popup of any node you produced, so a user who wants your
            # full picture is one click away and comes back afterwards.
            "panel_url": panel_url,
            "data": data,
        }
        if history is not None:
            self._registration["history"] = history
        if action is not None:
            self._registration["action"] = action
        self._detach: list[Callable[[], None]] = []

    @callback
    def async_on_unregister(self, remove: Callable[[], None]) -> None:
        """Run this when the provider withdraws (listeners, subscriptions)."""
        self._detach.append(remove)

    @callback
    def async_register(self) -> None:
        """Publish the registration. Safe whether or not the hub exists."""
        self.hass.data.setdefault(DATA_PROVIDERS, {})[
            self.provider_id
        ] = self._registration
        async_dispatcher_send(
            self.hass, SIGNAL_PROVIDER_REGISTERED, self.provider_id
        )

    @callback
    def async_unregister(self) -> None:
        """Withdraw on unload, so the hub drops the layer immediately."""
        for remove in self._detach:
            remove()
        self._detach.clear()
        self.hass.data.get(DATA_PROVIDERS, {}).pop(self.provider_id, None)
        async_dispatcher_send(self.hass, SIGNAL_PROVIDER_REMOVED, self.provider_id)

    @callback
    def async_notify(self) -> None:
        """Tell the hub the spatial data changed; it will re-fetch."""
        async_dispatcher_send(self.hass, SIGNAL_DATA_UPDATED, self.provider_id)
