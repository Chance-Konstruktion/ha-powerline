"""Powerline against the Spatial Hub conformance kit.

`tests/spatial_hub_conformance.py` is copied verbatim from the hub's
docs -- it is the same file every provider author is asked to drop in. If
this file ever fails, our provider drifted from the contract, not the
other way round.
"""

from spatial_hub_conformance import FakeHass, SpatialHubConformance

from custom_components.powerline.spatial import async_create_provider

from test_spatial_provider import FakeCoordinator, FakeEntry


class TestSpatialHubConformance(SpatialHubConformance):
    """The whole integration of the kit: one class, one method."""

    def build_registration(self):
        hass = FakeHass()
        async_create_provider(hass, FakeEntry(), FakeCoordinator())
        return hass.registrations["powerline"]
