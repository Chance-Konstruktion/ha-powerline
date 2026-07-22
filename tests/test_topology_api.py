"""Unit tests for the topology websocket API."""

from unittest import TestCase

import custom_components.powerline as integration
from custom_components.powerline.const import DOMAIN

TOPOLOGY = {
    "nodes": [{"mac": "AA:BB:CC:DD:EE:01", "name": "AA:BB:CC:DD:EE:01"}],
    "edges": [],
    "analysis": {},
}


class _Connection:
    def __init__(self):
        self.results = []
        self.errors = []

    def send_result(self, msg_id, payload):
        self.results.append((msg_id, payload))

    def send_error(self, msg_id, code, message):
        self.errors.append((msg_id, code, message))


class _Coordinator:
    def __init__(self, topology):
        self.data = {"topology": topology}


def _hass(coordinators):
    return type("Hass", (), {"data": {DOMAIN: coordinators}})()


class TestTopologyApi(TestCase):
    def test_returns_topology_for_entry_id(self):
        hass = _hass({"entry-1": _Coordinator(TOPOLOGY)})
        connection = _Connection()

        integration._websocket_get_topology(
            hass, connection, {"id": 7, "entry_id": "entry-1"}
        )

        self.assertEqual(len(connection.results), 1)
        msg_id, payload = connection.results[0]
        self.assertEqual(msg_id, 7)
        self.assertEqual(payload["nodes"], TOPOLOGY["nodes"])
        self.assertEqual(connection.errors, [])

    def test_defaults_to_first_entry_without_entry_id(self):
        hass = _hass({"entry-1": _Coordinator(TOPOLOGY)})
        connection = _Connection()

        integration._websocket_get_topology(hass, connection, {"id": 9})

        self.assertEqual(len(connection.results), 1)

    def test_reports_missing_entry(self):
        hass = _hass({})
        connection = _Connection()

        integration._websocket_get_topology(
            hass, connection, {"id": 8, "entry_id": "missing"}
        )

        self.assertEqual(connection.results, [])
        self.assertEqual(
            connection.errors, [(8, "not_found", "Powerline config entry not found")]
        )

    def test_empty_payload_before_first_refresh(self):
        coordinator = _Coordinator(None)
        coordinator.data = None
        hass = _hass({"entry-1": coordinator})
        connection = _Connection()

        integration._websocket_get_topology(hass, connection, {"id": 10})

        msg_id, payload = connection.results[0]
        self.assertEqual(payload["nodes"], [])
        self.assertEqual(payload["edges"], [])

    def test_command_schema_declares_entry_id(self):
        # A real HA websocket schema rejects undeclared keys, so entry_id
        # must be part of the command schema.
        schema = integration._websocket_get_topology._ws_schema
        self.assertIn("type", schema)
        self.assertIn("entry_id", schema)


class TestHistoryApi(TestCase):
    def test_returns_series_for_edge(self):
        from datetime import datetime, timedelta, timezone

        from custom_components.powerline.history import TopologyHistory

        mac_a = "AA:BB:CC:DD:EE:01"
        mac_b = "AA:BB:CC:DD:EE:02"
        # The websocket handler queries relative to the real current time,
        # so the recorded samples must be anchored to it as well.
        now = datetime.now(timezone.utc)

        coordinator = _Coordinator(TOPOLOGY)
        coordinator.history = TopologyHistory()
        snapshot = {
            "nodes": [{"mac": mac_a, "online": True}, {"mac": mac_b, "online": True}],
            "edges": [{"source": mac_a, "destination": mac_b, "average_rate": 500}],
        }
        coordinator.history.record(snapshot, now=now - timedelta(minutes=20))
        coordinator.history.record(snapshot, now=now - timedelta(minutes=10))

        hass = _hass({"entry-1": coordinator})
        connection = _Connection()

        integration._websocket_get_history(
            hass,
            connection,
            {"id": 11, "source": mac_a, "destination": mac_b, "hours": 24},
        )

        self.assertEqual(len(connection.results), 1)
        _, payload = connection.results[0]
        self.assertEqual(payload["source"], mac_a)
        self.assertTrue(payload["series"])

    def test_history_schema_declares_fields(self):
        schema = integration._websocket_get_history._ws_schema
        for field in ("type", "source", "destination", "hours", "entry_id"):
            self.assertIn(field, schema)


class TestLayoutApi(TestCase):
    def _hass_with_layout(self, layouts=None, coordinators=None):
        hass = _hass(coordinators if coordinators is not None else {"entry-1": _Coordinator(TOPOLOGY)})
        hass.data[integration._DATA_LAYOUT] = layouts or {}
        return hass

    def test_get_returns_empty_defaults(self):
        hass = self._hass_with_layout()
        connection = _Connection()

        integration._websocket_get_layout(hass, connection, {"id": 1})

        _, payload = connection.results[0]
        self.assertEqual(payload["positions"], {})
        self.assertIsNone(payload["background"])
        self.assertEqual(payload["icon_scale"], 1)

    def test_set_persists_icon_scale(self):
        hass = self._hass_with_layout()
        connection = _Connection()

        integration._websocket_set_layout(hass, connection, {"id": 8, "icon_scale": 1.6})

        self.assertEqual(connection.errors, [])
        self.assertEqual(
            hass.data[integration._DATA_LAYOUT]["entry-1"]["icon_scale"], 1.6
        )

    def test_set_leaves_icon_scale_untouched_on_position_only_save(self):
        stored = {"entry-1": {"positions": {}, "icon_scale": 2.0}}
        hass = self._hass_with_layout(stored)
        connection = _Connection()

        integration._websocket_set_layout(
            hass, connection, {"id": 9, "positions": {"AA:BB": {"x": 1.0, "y": 1.0}}}
        )

        self.assertEqual(
            hass.data[integration._DATA_LAYOUT]["entry-1"]["icon_scale"], 2.0
        )

    def test_get_returns_stored_layout(self):
        stored = {"entry-1": {"positions": {"AA:BB": {"x": 1.0, "y": 2.0}}, "background": "data:img"}}
        hass = self._hass_with_layout(stored)
        connection = _Connection()

        integration._websocket_get_layout(hass, connection, {"id": 2, "entry_id": "entry-1"})

        _, payload = connection.results[0]
        self.assertEqual(payload["positions"], {"AA:BB": {"x": 1.0, "y": 2.0}})
        self.assertEqual(payload["background"], "data:img")

    def test_set_persists_positions(self):
        hass = self._hass_with_layout()
        connection = _Connection()

        integration._websocket_set_layout(
            hass,
            connection,
            {"id": 3, "positions": {"AA:BB": {"x": 5.0, "y": 6.0}}},
        )

        self.assertEqual(connection.errors, [])
        self.assertEqual(
            hass.data[integration._DATA_LAYOUT]["entry-1"]["positions"],
            {"AA:BB": {"x": 5.0, "y": 6.0}},
        )

    def test_set_leaves_background_untouched_on_position_only_save(self):
        stored = {"entry-1": {"positions": {}, "background": "data:keepme"}}
        hass = self._hass_with_layout(stored)
        connection = _Connection()

        integration._websocket_set_layout(
            hass, connection, {"id": 4, "positions": {"CC:DD": {"x": 0.0, "y": 0.0}}}
        )

        self.assertEqual(
            hass.data[integration._DATA_LAYOUT]["entry-1"]["background"], "data:keepme"
        )

    def test_set_clears_background(self):
        stored = {"entry-1": {"positions": {}, "background": "data:old"}}
        hass = self._hass_with_layout(stored)
        connection = _Connection()

        integration._websocket_set_layout(hass, connection, {"id": 5, "background": None})

        self.assertIsNone(hass.data[integration._DATA_LAYOUT]["entry-1"]["background"])

    def test_set_rejects_oversized_background(self):
        hass = self._hass_with_layout()
        connection = _Connection()
        big = "x" * (integration.MAX_BACKGROUND_BYTES + 1)

        integration._websocket_set_layout(hass, connection, {"id": 6, "background": big})

        self.assertEqual(connection.results, [])
        self.assertEqual(connection.errors[0][1], "invalid_format")

    def test_set_reports_missing_entry(self):
        hass = self._hass_with_layout(coordinators={})
        connection = _Connection()

        integration._websocket_set_layout(hass, connection, {"id": 7, "positions": {}})

        self.assertEqual(connection.errors[0][1], "not_found")

    def test_layout_schemas_declare_fields(self):
        get_schema = integration._websocket_get_layout._ws_schema
        self.assertIn("type", get_schema)
        self.assertIn("entry_id", get_schema)
        set_schema = integration._websocket_set_layout._ws_schema
        for field in ("type", "entry_id", "positions", "background", "icon_scale"):
            self.assertIn(field, set_schema)
