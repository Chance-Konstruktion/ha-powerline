"""Der Spatial-Adapter gegen ein echtes Home Assistant.

Was diese Datei prueft und was nicht -- das gehoert an den Anfang, sonst
liest sich ein gruener Lauf hier groesser, als er ist.

Anders als die Suite in ``tests/`` steht hier nichts Nachgebautes vom
Home-Assistant-Kern: echtes ``hass``, echte Device Registry, echter
Config Entry, echter Dispatcher. Der Adapter schlaegt fuer jeden Adapter
in der Device Registry nach -- Bereich und Geraeteseite kommen von dort.
Ein nachgebautes Registry haette diesen Weg nie geprueft, und genau ihn
kann jede neue HA-Version brechen.

Der Koordinator ist ein Doppel. Er gehoert zu DIESER Integration, und
ihn echt hochzufahren hiesse, HomePlug-Adapter im Netz zu haben -- die
gibt es in der CI nicht (siehe tests/test_hardware_integration.py, die
sich genau deshalb ueberspringt). Das Doppel liefert nur, was der
Adapter liest: ``data``, ``adapter_name`` und ``history``.

Der teuerste Fehler in diesem Weg ist nicht ein falscher Knoten, sondern
eine Ausnahme in ``async_data()``: Der Hub verwirft dann **die ganze
Ebene** fuer diesen Durchlauf, nicht nur den einen Knoten. Deshalb steht
der leere Fall ganz vorn.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from spatial_hub_conformance import check

from custom_components.powerline.spatial import async_create_provider

EIGENE_DOMAIN = "powerline"

TOPOLOGIE = {
    "nodes": [
        {"mac": "aa:bb:cc:dd:ee:01", "name": "Router", "role": "CCo", "online": True},
        {"mac": "aa:bb:cc:dd:ee:02", "name": "Buero", "role": "STA", "online": True},
        {"mac": "aa:bb:cc:dd:ee:03", "name": "Keller", "role": "STA", "online": False},
    ],
    "edges": [
        {
            "source": "aa:bb:cc:dd:ee:01",
            "destination": "aa:bb:cc:dd:ee:02",
            "average_rate": 480,
            "tx_phy_rate": 500,
            "rx_phy_rate": 460,
            "link_quality": "good",
        },
        {
            "source": "aa:bb:cc:dd:ee:01",
            "destination": "aa:bb:cc:dd:ee:03",
            "average_rate": 0,
            "estimated": True,
        },
    ],
}


class Verlauf:
    def series(self, source, destination, hours):
        return []


class KoordinatorDoppel:
    """Genau das, was der Adapter liest -- nicht mehr.

    Kein ``async_add_listener``: Das SDK nimmt einen Koordinator ohne
    diese Methode ausdruecklich an und schreibt eine Warnung. Dass dieser
    Weg traegt, gehoert mitgeprueft -- er ist der Weg jedes Anbieters,
    der keinen DataUpdateCoordinator hat.
    """

    def __init__(self, data=None):
        self.data = data
        self.history = Verlauf()

    def adapter_name(self, mac: str) -> str:
        # Wie im Betrieb ohne Umbenennung: die Abfrage gibt die MAC zurueck.
        return mac

    # Die beiden ruft nur async_action auf. Der Konformitaetssatz prueft,
    # dass Aktionen deklariert und aufrufbar sind -- ausgefuehrt werden sie
    # nicht. Sie stehen hier trotzdem: Die Liste kommt aus
    #   grep -o "coordinator\.[a-zA-Z_]*" custom_components/powerline/spatial.py
    # und ein Doppel, das nur zufaellig reicht, reicht beim naechsten Umbau
    # nicht mehr. Bei espeasy-p2p hat genau diese Luecke die Pipeline
    # umgeworfen.
    async def async_set_led(self, mac: str, an: bool) -> bool:
        return True

    async def async_restart_adapter(self, mac: str) -> bool:
        return True


@pytest.fixture
def eintrag(hass: HomeAssistant):
    eigener = MockConfigEntry(domain=EIGENE_DOMAIN, title="Powerline")
    eigener.add_to_hass(hass)
    return eigener


def _registrierung(hass, eintrag, koordinator):
    async_create_provider(hass, eintrag, koordinator)
    providers = hass.data.get("spatial_hub_providers") or {}
    assert EIGENE_DOMAIN in providers, (
        f"Der Adapter hat sich nicht angemeldet. Vorhanden: {sorted(providers)}"
    )
    return providers[EIGENE_DOMAIN]


@pytest.fixture
def leer(hass: HomeAssistant, eintrag):
    """Der Koordinator hat noch nie geantwortet -- ``data`` ist None."""
    return _registrierung(hass, eintrag, KoordinatorDoppel())


@pytest.fixture
def besetzt(hass: HomeAssistant, eintrag):
    return _registrierung(hass, eintrag, KoordinatorDoppel({"topology": TOPOLOGIE}))


# ── Anmeldung ─────────────────────────────────────────────────────────


def test_der_adapter_meldet_sich_am_hub_an(leer):
    """Ohne Eintrag in hass.data existiert die Ebene fuer den Hub nicht."""
    assert leer["provider_id"] == EIGENE_DOMAIN
    assert leer["name"]
    assert callable(leer["data"])


@pytest.mark.parametrize("welche", ["leer", "besetzt"])
def test_die_anmeldung_haelt_den_hub_vertrag_ein(welche, request):
    """Der mitgelieferte Konformitaets-Satz, gegen ein echtes hass.

    Beide Zustaende, weil sie verschiedene Wege nehmen: ohne Antwort des
    Koordinators gibt es weder Knoten noch Kanten, mit Antwort beides --
    und Kanten auf Knoten, die es nicht gibt, sind der Klassiker, der
    Grundrisse zerlegt.
    """
    probleme = check(request.getfixturevalue(welche))
    assert not probleme, "Verstoesse gegen den Hub-Vertrag:\n  " + "\n  ".join(probleme)


# ── Das Verhalten ─────────────────────────────────────────────────────


def test_ohne_antwort_bleibt_die_ebene_leer_statt_kaputt(leer):
    """``data is None`` heisst leer -- nicht Ausnahme.

    Das ist der Zustand zwischen Einrichtung und erster Abfrage, und der
    Hub fragt genau dort schon. Ein Stapelabzug an dieser Stelle nimmt
    die ganze Ebene mit.
    """
    nutzlast = leer["data"]()
    assert nutzlast == {"nodes": [], "edges": []}


def test_jede_kante_zeigt_auf_einen_knoten_den_es_gibt(besetzt):
    nutzlast = besetzt["data"]()
    ids = {k["id"] for k in nutzlast["nodes"]}
    assert len(ids) == len(TOPOLOGIE["nodes"]), "Knoten-IDs sind nicht eindeutig"
    for kante in nutzlast["edges"]:
        assert kante["source"] in ids, f"Kante ins Leere: {kante}"
        assert kante["target"] in ids, f"Kante ins Leere: {kante}"


def test_die_knoten_ids_bleiben_ueber_abfragen_gleich(besetzt):
    """Eine ID, die sich zwischen zwei Abfragen aendert, wirft jede vom
    Nutzer gesetzte Position weg -- der Grundriss ordnet sich neu, ohne
    dass jemand etwas angefasst hat."""
    erste = {k["id"] for k in besetzt["data"]()["nodes"]}
    for _ in range(3):
        weitere = {k["id"] for k in besetzt["data"]()["nodes"]}
    assert erste == weitere


def test_die_nutzlast_ueberlebt_den_websocket(besetzt):
    """Alles geht als JSON an den Browser. Ein datetime, ein set oder eine
    eigene Klasse in den Metadaten nimmt das ganze Modell mit -- fuer
    jeden Anbieter, nicht nur diesen."""
    json.dumps(besetzt["data"]())
