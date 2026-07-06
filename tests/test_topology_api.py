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
