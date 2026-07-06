"""Unit tests for the topology websocket API."""

from unittest import TestCase

import custom_components.powerline as integration
from custom_components.powerline.const import DOMAIN


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


class TestTopologyApi(TestCase):
    def test_returns_topology_for_entry_id(self):
        topology = {"nodes": [{"mac": "AA"}], "edges": []}
        hass = type(
            "Hass", (), {"data": {DOMAIN: {"entry-1": _Coordinator(topology)}}}
        )()
        connection = _Connection()

        integration._websocket_get_topology(
            hass, connection, {"id": 7, "entry_id": "entry-1"}
        )

        self.assertEqual(connection.results, [(7, topology)])
        self.assertEqual(connection.errors, [])

    def test_reports_missing_entry(self):
        hass = type("Hass", (), {"data": {DOMAIN: {}}})()
        connection = _Connection()

        integration._websocket_get_topology(
            hass, connection, {"id": 8, "entry_id": "missing"}
        )

        self.assertEqual(connection.results, [])
        self.assertEqual(
            connection.errors, [(8, "not_found", "Powerline config entry not found")]
        )
