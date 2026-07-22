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


# -- voluptuous (only stubbed when the real package is missing) ---------------
try:
    import voluptuous  # noqa: F401
except ImportError:
    _vol = types.ModuleType("voluptuous")

    class _VolMarker(str):
        """Hashable stand-in for vol.Required/vol.Optional schema keys."""

        def __new__(cls, schema, **kwargs):
            return super().__new__(cls, schema)

    _vol.Required = _VolMarker
    _vol.Optional = _VolMarker
    _vol.All = lambda *validators: validators
    _vol.Any = lambda *validators: validators
    _vol.Coerce = lambda target: target
    _vol.Range = lambda **kwargs: kwargs
    _vol.Schema = lambda schema, **kwargs: schema
    sys.modules["voluptuous"] = _vol


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
        if not hasattr(hass, "registered_websocket_commands"):
            hass.registered_websocket_commands = []
        hass.registered_websocket_commands.append(command)

    ws.websocket_command = websocket_command
    ws.async_register_command = async_register_command
    sys.modules["homeassistant.components.websocket_api"] = ws
    sys.modules["homeassistant.components"].websocket_api = ws


# -- homeassistant.components.frontend -----------------------------------------
if "homeassistant.components.frontend" not in sys.modules:
    fe = types.ModuleType("homeassistant.components.frontend")

    def async_register_built_in_panel(hass, component_name, **kwargs):
        if not hasattr(hass, "registered_panels"):
            hass.registered_panels = {}
        hass.registered_panels[kwargs.get("frontend_url_path")] = kwargs

    def async_remove_panel(hass, frontend_url_path):
        if hasattr(hass, "registered_panels"):
            hass.registered_panels.pop(frontend_url_path, None)

    def add_extra_js_url(hass, url, es5=False):
        if not hasattr(hass, "extra_js_urls"):
            hass.extra_js_urls = []
        hass.extra_js_urls.append(url)

    fe.async_register_built_in_panel = async_register_built_in_panel
    fe.async_remove_panel = async_remove_panel
    fe.add_extra_js_url = add_extra_js_url
    sys.modules["homeassistant.components.frontend"] = fe
    sys.modules["homeassistant.components"].frontend = fe


# -- homeassistant.components.persistent_notification ---------------------------
if "homeassistant.components.persistent_notification" not in sys.modules:
    pn = types.ModuleType("homeassistant.components.persistent_notification")

    def pn_async_create(hass, message, title=None, notification_id=None):
        if not hasattr(hass, "notifications"):
            hass.notifications = []
        hass.notifications.append(
            {"message": message, "title": title, "notification_id": notification_id}
        )

    pn.async_create = pn_async_create
    sys.modules["homeassistant.components.persistent_notification"] = pn
    sys.modules["homeassistant.components"].persistent_notification = pn


# -- homeassistant.helpers.storage ---------------------------------------------
if "homeassistant.helpers.storage" not in sys.modules:
    storage = types.ModuleType("homeassistant.helpers.storage")

    class Store:  # pragma: no cover - simple in-memory stand-in
        def __init__(self, hass, version, key):
            self.key = key
            self.saved = None

        async def async_load(self):
            return None

        async def async_save(self, data):
            self.saved = data

        def async_delay_save(self, data_func, delay=0):
            self.saved = data_func()

    storage.Store = Store
    sys.modules["homeassistant.helpers.storage"] = storage


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
