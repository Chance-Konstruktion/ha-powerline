"""Unit tests for the sidebar panel registration toggle."""

from pathlib import Path
from unittest import TestCase

import custom_components.powerline as integration
from custom_components.powerline.const import PANEL_URL_PATH


class _Hass:
    def __init__(self):
        self.data = {}


class TestSidebarPanel(TestCase):
    def test_panel_registered_when_enabled(self):
        hass = _Hass()

        integration._async_update_panel(hass, True)

        self.assertIn(PANEL_URL_PATH, hass.registered_panels)
        panel = hass.registered_panels[PANEL_URL_PATH]
        self.assertEqual(panel["sidebar_title"], "Powerline")
        custom = panel["config"]["_panel_custom"]
        self.assertEqual(custom["name"], "powerline-topology-panel")
        self.assertIn("powerline-topology-panel.js", custom["module_url"])

    def test_panel_removed_when_disabled(self):
        hass = _Hass()
        integration._async_update_panel(hass, True)

        integration._async_update_panel(hass, False)

        self.assertEqual(hass.registered_panels, {})

    def test_enable_is_idempotent(self):
        hass = _Hass()
        integration._async_update_panel(hass, True)
        integration._async_update_panel(hass, True)

        self.assertEqual(len(hass.registered_panels), 1)

    def test_disable_without_registration_is_noop(self):
        hass = _Hass()

        integration._async_update_panel(hass, False)

        self.assertFalse(getattr(hass, "registered_panels", {}))

    def test_frontend_adapter_png_assets_are_embedded(self):
        card = (
            Path(integration.__file__).parent
            / "frontend"
            / "powerline-topology-card.js"
        ).read_text()

        self.assertEqual(card.count("data:image/png;base64,"), 2)
        self.assertIn("const ADAPTER_ASSETS", card)

