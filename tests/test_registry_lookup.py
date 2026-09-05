"""Die Geraetesuche, die drei Aufrufer teilen.

Core hat ``async_get_device(identifiers=...)`` abgekuendigt: eine Kennung
ist ueber Konfigurationseintraege hinweg nicht mehr eindeutig, deshalb
will Core den Eintrag genannt bekommen. Der alte Aufruf hoert in Home
Assistant 2027.8 auf zu arbeiten und schreibt bis dahin bei jedem Griff
eine Warnung -- auf der Anlage kam sie aus zwei Dateien gleichzeitig.

Die Suche liegt jetzt einmal in ``registry.py`` statt dreimal verstreut.
Diese Datei haelt beide Welten fest: einen heutigen Core mit der Suche je
Eintrag und einen aelteren, der sie noch nicht hat -- die Untergrenze der
Integration ist 2024.1.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.powerline import registry as modul
from custom_components.powerline.const import DOMAIN

MAC = "AA:BB:CC:DD:EE:FF"
KENNUNG = (DOMAIN, MAC)


class _Hass:
    def __init__(self, *entry_ids):
        self.config_entries = SimpleNamespace(
            async_entries=lambda domain: [
                SimpleNamespace(entry_id=kennung) for kennung in entry_ids
            ]
            if domain == DOMAIN
            else []
        )


class _NeuesRegister:
    """Kann die Suche je Eintrag -- wie Core seit 2025.9."""

    def __init__(self, treffer: dict):
        self._treffer = treffer
        self.gefragt: list[tuple] = []

    def async_get_device_by_identifier(self, kennung, entry_id):
        self.gefragt.append((kennung, entry_id))
        return self._treffer.get(entry_id)

    def async_get_device(self, identifiers=None):  # pragma: no cover
        raise AssertionError("der abgekuendigte Weg darf hier nicht laufen")


class _AltesRegister:
    """Kennt nur den alten Aufruf -- wie Core vor 2025.9."""

    def __init__(self, geraet):
        self._geraet = geraet
        self.gefragt = None

    def async_get_device(self, identifiers=None):
        self.gefragt = identifiers
        return self._geraet


@pytest.fixture
def register(monkeypatch):
    """Setzt das Register, das ``device_for_mac`` zu sehen bekommt."""

    def _setzen(objekt):
        monkeypatch.setattr(modul.dr, "async_get", lambda hass: objekt,
                            raising=False)
        return objekt

    return _setzen


def test_sucht_im_eigenen_eintrag(register):
    geraet = object()
    reg = register(_NeuesRegister({"powerline-1": geraet}))
    assert modul.device_for_mac(_Hass("powerline-1"), MAC) is geraet
    assert reg.gefragt == [(KENNUNG, "powerline-1")]


def test_zweiter_eintrag_wird_auch_gefragt(register):
    """Zwei Adapter-Eintraege sind erlaubt -- beide zaehlen."""
    geraet = object()
    reg = register(_NeuesRegister({"powerline-2": geraet}))
    assert modul.device_for_mac(_Hass("powerline-1", "powerline-2"), MAC) is geraet
    assert reg.gefragt == [(KENNUNG, "powerline-1"), (KENNUNG, "powerline-2")]


def test_unbekannte_mac_gibt_nichts(register):
    register(_NeuesRegister({}))
    assert modul.device_for_mac(_Hass("powerline-1"), MAC) is None


def test_rueckfall_auf_alten_core(register):
    geraet = object()
    reg = register(_AltesRegister(geraet))
    assert modul.device_for_mac(_Hass("powerline-1"), MAC) is geraet
    assert reg.gefragt == {KENNUNG}


def test_ohne_register_kein_absturz(register):
    """Vor dem Start gibt es kein Register -- das ist kein Fehlerfall."""
    register(None)
    assert modul.device_for_mac(_Hass("powerline-1"), MAC) is None
