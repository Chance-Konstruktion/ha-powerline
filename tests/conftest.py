"""Shared Home Assistant stubs for unit tests.

Injected into sys.modules before any integration code is imported, so tests
can run without a full Home Assistant installation.
"""

import sys
import types
from typing import Generic, TypeVar

_T = TypeVar("_T")


def _ensure(name: str, **attrs):
    """Return an existing or newly created stub module registered in sys.modules."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
    return sys.modules[name]


# -- homeassistant (root package needs __path__ so sub-imports work) ----------
if "homeassistant" not in sys.modules:
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []  # mark as package
    sys.modules["homeassistant"] = ha


# -- homeassistant.config_entries ---------------------------------------------
_ensure("homeassistant.config_entries", ConfigEntry=object)


# -- homeassistant.core -------------------------------------------------------
if "homeassistant.core" not in sys.modules:
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # pragma: no cover
        pass

    def callback(func):
        return func

    core.HomeAssistant = HomeAssistant
    core.callback = callback
    sys.modules["homeassistant.core"] = core


# -- homeassistant.components.websocket_api -----------------------------------
if "homeassistant.components" not in sys.modules:
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components

if "homeassistant.components.websocket_api" not in sys.modules:
    ws = types.ModuleType("homeassistant.components.websocket_api")

    def websocket_command(schema):
        def decorator(func):
            func._ws_schema = schema
            return func
        return decorator

    def async_register_command(hass, command):
        hass.registered_websocket_command = command

    ws.websocket_command = websocket_command
    ws.async_register_command = async_register_command
    sys.modules["homeassistant.components.websocket_api"] = ws
    sys.modules["homeassistant.components"].websocket_api = ws


# -- homeassistant.helpers (parent package) -----------------------------------
if "homeassistant.helpers" not in sys.modules:
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers


# -- homeassistant.helpers.update_coordinator ---------------------------------
if "homeassistant.helpers.update_coordinator" not in sys.modules:
    uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator(Generic[_T]):  # pragma: no cover
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass

    class UpdateFailed(Exception):
        pass

    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.UpdateFailed = UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = uc
    sys.modules["homeassistant.helpers"].update_coordinator = uc


# -- homeassistant.helpers.device_registry ------------------------------------
if "homeassistant.helpers.device_registry" not in sys.modules:
    dr = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceEntry:  # pragma: no cover
        pass

    dr.DeviceEntry = DeviceEntry
    dr.async_get = lambda hass: None
    dr.async_entries_for_config_entry = lambda reg, entry_id: []
    sys.modules["homeassistant.helpers.device_registry"] = dr
    sys.modules["homeassistant.helpers"].device_registry = dr


# -- homeassistant.helpers.entity_registry ------------------------------------
if "homeassistant.helpers.entity_registry" not in sys.modules:
    er = types.ModuleType("homeassistant.helpers.entity_registry")

    class EntityEntry:  # pragma: no cover
        pass

    er.EntityEntry = EntityEntry
    er.async_get = lambda hass: None
    er.async_entries_for_config_entry = lambda reg, entry_id: []
    sys.modules["homeassistant.helpers.entity_registry"] = er
    sys.modules["homeassistant.helpers"].entity_registry = er


# Attach sub-modules to the helpers package so
# `from homeassistant.helpers import device_registry as dr` works.
_ha_helpers = sys.modules["homeassistant.helpers"]
if not hasattr(_ha_helpers, "device_registry"):
    _ha_helpers.device_registry = sys.modules["homeassistant.helpers.device_registry"]
if not hasattr(_ha_helpers, "entity_registry"):
    _ha_helpers.entity_registry = sys.modules["homeassistant.helpers.entity_registry"]
if not hasattr(_ha_helpers, "update_coordinator"):
    _ha_helpers.update_coordinator = sys.modules[
        "homeassistant.helpers.update_coordinator"
    ]
