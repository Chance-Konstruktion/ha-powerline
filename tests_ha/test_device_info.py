"""Device registry entries name the real vendor, not a hard-coded one."""

from unittest import TestCase

from custom_components.powerline.const import MANUFACTURER
from custom_components.powerline.sensor import (
    device_info_for_adapter,
    network_device_info,
)


class DeviceInfoManufacturerTest(TestCase):
    def test_unknown_vendor_falls_back_to_neutral_name(self):
        # A Netgear XAV5004 reported "TP-Link" from the forum thread.
        info = device_info_for_adapter("A0:21:B7:00:00:01", {"mac": "A0:21:B7:00:00:01"})
        self.assertEqual(MANUFACTURER, info["manufacturer"])

    def test_known_prefix_names_its_vendor(self):
        info = device_info_for_adapter("EC:08:6B:00:00:01", {})
        self.assertEqual("TP-Link", info["manufacturer"])

    def test_reported_manufacturer_wins(self):
        info = device_info_for_adapter("EC:08:6B:00:00:01", {"manufacturer": "devolo"})
        self.assertEqual("devolo", info["manufacturer"])

    def test_network_device_is_not_branded(self):
        self.assertEqual(MANUFACTURER, network_device_info()["manufacturer"])
