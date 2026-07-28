"""Spatial Hub conformance kit -- copy this file into your test suite.

Answers one question: *does my provider actually satisfy the contract?*
Not "does it import", but the things that really break floor plans in the
field -- ids that change between polls (and silently throw away everything
the user arranged), metadata that cannot be sent over the websocket, edges
pointing at nodes that are not there.

Requires **pytest and nothing else**. No Home Assistant install, no hub
install, no async plugin. It runs against the registration dict your
integration publishes, which is the same thing the hub sees.

Usage -- one class in your test suite::

    from .spatial_hub_conformance import SpatialHubConformance

    class TestSpatialHub(SpatialHubConformance):
        def build_registration(self):
            hass = FakeHass()                      # yours, or ours below
            async_create_provider(hass, FakeEntry(), FakeCoordinator())
            return hass.data["spatial_hub_providers"]["my_integration"]

That is it. You get a dozen named tests, each of which tells you what is
wrong and why it matters.

Outside pytest -- in a script, a CI step, a scratch file::

    from spatial_hub_conformance import check

    for problem in check(registration):
        print(problem)
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

API_VERSION = 1
DATA_PROVIDERS = "spatial_hub_providers"

# Which revision of the kit you copied. Kept in step with the shim, so a
# mismatch between the two files in your repository is visible.
SDK_VERSION = 4

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_QUALITY = {"good", "fair", "poor", "unknown", ""}
_KNOWN_KEYS = {
    "provider_id", "api_version", "sdk_version", "name", "icon", "version",
    "capabilities", "layers", "icon_set", "panel_url", "data", "history",
    "action",
}
_NODE_KEYS = {
    "id", "label", "area_id", "floor_id", "position", "state", "icon",
    "color", "entity_id", "actions", "metadata", "layer_id",
}
_EDGE_KEYS = {
    "id", "source", "target", "label", "value", "quality", "color", "width",
    "directed", "dashed", "animated", "actions", "metadata", "layer_id",
}


class FakeHass:
    """The only piece of Home Assistant a registration needs: hass.data."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    @property
    def registrations(self) -> dict[str, Any]:
        return self.data.get(DATA_PROVIDERS, {})


def fetch(registration: dict[str, Any]) -> dict[str, list]:
    """Call the provider's data callable and normalise the payload shape."""
    payload = registration["data"]()
    if asyncio.iscoroutine(payload):
        payload = asyncio.run(payload)
    if isinstance(payload, list):
        payload = {"nodes": payload}
    if not isinstance(payload, dict):
        raise AssertionError(
            f"data() returned {type(payload).__name__}; the hub expects a dict "
            '{"nodes": [...], "edges": [...]} or a bare list of nodes'
        )
    nodes = [
        {"id": item, "entity_id": item} if isinstance(item, str) else item
        for item in payload.get("nodes") or []
    ]
    return {"nodes": nodes, "edges": list(payload.get("edges") or [])}


def check(registration: dict[str, Any]) -> list[str]:
    """Every problem found, as plain sentences. Empty list means conformant.

    Use this outside pytest. Inside pytest, subclass
    :class:`SpatialHubConformance` instead -- the failures are easier to
    read when each rule is its own test.
    """
    problems: list[str] = []
    suite = SpatialHubConformance()
    suite._registration = registration
    for name in sorted(dir(suite)):
        if not name.startswith("test_"):
            continue
        try:
            getattr(suite, name)()
        except Exception as err:  # noqa: BLE001 - a crash is a problem too
            problems.append(f"{name[5:].replace('_', ' ')}: {err}")
    return problems


class SpatialHubConformance:
    """Subclass this in your test suite and implement build_registration()."""

    _registration: dict[str, Any] | None = None

    # ── The one thing you implement ───────────────────────

    def build_registration(self) -> dict[str, Any]:
        """Return the registration dict your integration publishes.

        Usually: set your integration up against a fake hass, then read
        ``hass.data["spatial_hub_providers"]["<your id>"]``.
        """
        raise NotImplementedError(
            "implement build_registration() -- return the dict your "
            "integration writes into hass.data['spatial_hub_providers']"
        )

    @property
    def registration(self) -> dict[str, Any]:
        if self._registration is None:
            self._registration = self.build_registration()
        return self._registration

    # ── The registration itself ───────────────────────────

    def test_registration_is_a_dict_with_an_id(self) -> None:
        registration = self.registration
        assert isinstance(registration, dict), "the registration must be a dict"
        provider_id = registration.get("provider_id")
        assert provider_id, "registration has no provider_id"
        assert _PROVIDER_ID.match(str(provider_id)), (
            f"provider_id {provider_id!r} should be snake_case ASCII -- it "
            "prefixes every one of your node ids and ends up in stored user "
            "layouts, so it has to stay stable and readable"
        )

    def test_api_version_is_supported(self) -> None:
        version = int(self.registration.get("api_version") or 1)
        assert version <= API_VERSION, (
            f"registration declares provider API v{version}; this kit knows "
            f"up to v{API_VERSION}"
        )

    def test_no_unknown_registration_keys(self) -> None:
        unknown = sorted(set(self.registration) - _KNOWN_KEYS)
        assert not unknown, (
            f"unknown registration keys {unknown} -- a typo here is silent, "
            "the hub simply never sees the feature"
        )

    def test_data_is_callable(self) -> None:
        assert callable(self.registration.get("data")), (
            "'data' must be a callable returning your nodes and edges"
        )

    def test_declared_capabilities_are_backed_by_callables(self) -> None:
        capabilities = self.registration.get("capabilities") or {}
        if capabilities.get("history"):
            assert callable(self.registration.get("history")), (
                "capabilities say history is supported, but no history "
                "callable was registered -- the UI would offer an empty graph"
            )
        if capabilities.get("actions"):
            assert callable(self.registration.get("action")), (
                "capabilities say actions are supported, but no action "
                "callable was registered -- buttons would do nothing"
            )
        if capabilities.get("custom_icons"):
            assert self.registration.get("icon_set"), (
                "capabilities say custom icons are shipped, but icon_set is empty"
            )

    def test_layers_have_unique_ids(self) -> None:
        ids = [layer.get("id") for layer in self.registration.get("layers") or []]
        assert all(ids), "every layer needs an id"
        assert len(ids) == len(set(ids)), f"duplicate layer ids: {ids}"

    # ── The payload ───────────────────────────────────────

    def test_data_does_not_raise(self) -> None:
        try:
            fetch(self.registration)
        except AssertionError:
            raise
        except Exception as err:  # noqa: BLE001 - that is the point
            raise AssertionError(
                f"data() raised {type(err).__name__}: {err} -- the hub would "
                "drop your whole layer for that refresh. Return partial data "
                "instead of raising"
            ) from err

    def test_nodes_have_unique_ids(self) -> None:
        ids = [node.get("id") for node in fetch(self.registration)["nodes"]]
        assert all(ids), "every node needs an id"
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, (
            f"duplicate node ids {sorted(duplicates)} -- the later one wins "
            "and the user loses whatever they arranged for the other"
        )

    def test_node_ids_are_stable_across_calls(self) -> None:
        """The single most damaging mistake a provider can make.

        User positions are stored against node ids. Ids derived from a
        timestamp, a counter or dict ordering mean every poll silently
        throws away the floor plan the user built.
        """
        first = [node["id"] for node in fetch(self.registration)["nodes"]]
        second = [node["id"] for node in fetch(self.registration)["nodes"]]
        assert first == second, (
            "node ids changed between two calls to data(). Every stored user "
            f"position is keyed by these ids.\nfirst:  {first}\nsecond: {second}"
        )

    def test_edges_reference_nodes_that_exist(self) -> None:
        payload = fetch(self.registration)
        known = {node.get("id") for node in payload["nodes"]}
        for edge in payload["edges"]:
            source, target = edge.get("source"), edge.get("target")
            assert source and target, f"edge {edge.get('id')} has no source/target"
            assert source in known, (
                f"edge {edge.get('id')} points at unknown node {source!r} -- "
                "the hub drops it rather than drawing a line into nowhere. "
                "Use your own plain ids, the hub adds the namespace"
            )
            assert target in known, (
                f"edge {edge.get('id')} points at unknown node {target!r}"
            )

    def test_edge_quality_uses_the_shared_vocabulary(self) -> None:
        for edge in fetch(self.registration)["edges"]:
            quality = edge.get("quality", "")
            assert quality in _QUALITY, (
                f"edge quality {quality!r} is not one of {sorted(_QUALITY - {''})} "
                "-- renderers colour edges by this without knowing what your "
                "integration does, so it has to be the shared vocabulary"
            )

    def test_positions_are_normalised(self) -> None:
        for node in fetch(self.registration)["nodes"]:
            position = node.get("position")
            if not position:
                continue
            for axis in ("x", "y"):
                value = position.get(axis)
                assert value is not None, f"position on {node['id']} lacks {axis}"
                assert 0.0 <= float(value) <= 1.0, (
                    f"position {axis}={value} on node {node['id']} is outside "
                    "0..1. Positions are normalised floor coordinates, not "
                    "pixels. Unless you truly know where the device is, omit "
                    "position entirely and let the hub place it in its area"
                )

    def test_unknown_keys_are_not_used_on_nodes_or_edges(self) -> None:
        payload = fetch(self.registration)
        for node in payload["nodes"]:
            unknown = sorted(set(node) - _NODE_KEYS)
            assert not unknown, (
                f"node {node.get('id')} has unknown keys {unknown} -- put "
                "extra information in metadata, where the popup shows it"
            )
        for edge in payload["edges"]:
            unknown = sorted(set(edge) - _EDGE_KEYS)
            assert not unknown, (
                f"edge {edge.get('id')} has unknown keys {unknown} -- put "
                "extra information in metadata"
            )

    def test_payload_survives_the_websocket(self) -> None:
        """Everything you deliver is JSON on its way to the browser.

        A datetime, a set or a custom class in metadata takes down the
        whole model for every provider, not just yours.
        """
        payload = fetch(self.registration)
        try:
            json.dumps(payload)
        except TypeError as err:
            raise AssertionError(
                f"payload is not JSON-serialisable: {err}. Convert datetimes "
                "with .isoformat(), sets with sorted(), enums with .value"
            ) from err

    def test_actions_are_declared_and_runnable(self) -> None:
        payload = fetch(self.registration)
        has_actions = any(node.get("actions") for node in payload["nodes"])
        if not has_actions:
            return
        capabilities = self.registration.get("capabilities") or {}
        assert capabilities.get("actions") or callable(
            self.registration.get("action")
        ), (
            "nodes offer actions but the provider registered no action "
            "callable -- the user would press a button that does nothing"
        )
        for node in payload["nodes"]:
            for action in node.get("actions") or []:
                assert action.get("id"), f"action on {node['id']} has no id"
