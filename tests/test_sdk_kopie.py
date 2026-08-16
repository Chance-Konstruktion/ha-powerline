"""Die mitgelieferte SDK-Kopie gegen die Quelle, aus der sie stammt.

Der Anbieter-Shim und der Konformitaetssatz sind absichtlich kopiert und
nicht als Paket eingebunden -- die Begruendung steht in `sdk/install.py`
des Hubs und bleibt richtig. Die Kosten davon sind Kopien, die
auseinanderlaufen, und genau das ist einmal passiert: Die Anker kamen in
die sechs ausgelieferten Anbieter, aber nie zurueck ins SDK. Danach trug
die Quelle -- das, was ein neuer Anbieter-Autor bekommt -- eine
*aeltere* Fassung als jeder ausgelieferte Anbieter.

Gesehen hat das keiner. Der Hub prueft seine drei Dateien gegeneinander,
jeder Anbieter prueft seine eigenen; beide Seiten waren in sich stimmig.
Kein Repository konnte beide Seiten sehen. Dieser Test kann es, weil das
GitLab ohne Anmeldung lesbar ist.

Die drei Faelle, absichtlich verschieden gewichtet:

* Kopie **neuer** als die Quelle -> Fehler. Dann wurde hier bearbeitet,
  was im SDK haette bearbeitet werden muessen. Das ist der Fall von oben.
* gleiche Nummer, anderer Inhalt -> Fehler. Stiller Drift, die
  schlimmste Sorte: die Versionsnummer behauptet Gleichheit.
* Kopie **aelter** als die Quelle -> uebersprungen, mit Grund im Log.
  Das ist kein Fehler, sondern die Zusage des Entwurfs: eine alte Kopie
  laeuft weiter, und der Hub sagt in seiner Diagnose Bescheid.

Ohne Netz wird uebersprungen, ebenfalls mit Grund. Ein Test, der bei
Netzausfall rot wird, wird nach dem zweiten Mal ignoriert.
"""

from __future__ import annotations

import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

WURZEL = Path(__file__).resolve().parents[1]
QUELLE = (
    "https://gitlab.schanz.ipv64.net/chance-konstruktion/ha-spatial-hub"
    "/-/raw/main/sdk/"
)
# Wo die beiden Dateien in diesem Repository liegen, wird gesucht statt
# eingetragen: Der Satz liegt je nach Anbieter in tests/ oder tests_ha/,
# und der Shim unter einem Ordner, dessen Namen diese Datei nicht kennt.
DATEIEN = ("spatial_hub_provider.py", "spatial_hub_conformance.py")


def _hier(name: str) -> Path:
    treffer = [p for p in WURZEL.rglob(name) if ".git" not in p.parts]
    assert treffer, f"{name} liegt nirgends in diesem Repository"
    assert len(treffer) == 1, (
        f"{name} liegt mehrfach hier: {sorted(str(p) for p in treffer)} -- "
        "welche davon die gueltige ist, kann dieser Test nicht raten"
    )
    return treffer[0]


def _stempel(text: str) -> int:
    treffer = re.search(r"^SDK_VERSION = (\d+)", text, re.M)
    assert treffer, "die Datei hat kein SDK_VERSION -- ist sie noch die richtige?"
    return int(treffer.group(1))


@pytest.fixture
def netz():
    """Sockets fuer die Dauer dieses Tests, falls sie gesperrt sind.

    In tests_ha/ laeuft pytest-socket mit (es kommt mit
    pytest-homeassistant-custom-component) und sperrt jeden echten
    Netzzugriff -- zu Recht: Ein Integrationstest, der ans Internet geht,
    ist keiner mehr. Dieser Test *muss* aber hinaus, das ist sein ganzer
    Zweck. Also wird die Sperre genau um ihn herum geoeffnet und danach
    wieder geschlossen -- und nur dann, wenn sie vorher zu war.

    Die Sperre hat zwei Schloesser, und das war beim ersten Versuch nicht
    klar: ``enable_socket`` gibt das Erzeugen eines Sockets frei, aber
    ``connect`` prueft zusaetzlich gegen eine Host-Liste, auf der nur
    127.0.0.1 steht. Nur das erste zu oeffnen sah aus wie ein Fortschritt
    -- drei der vier Tests liefen -- und war trotzdem nutzlos.
    """
    try:
        import pytest_socket
    except ImportError:  # in tests/ ohne Home Assistant gar nicht dabei
        yield
        return

    try:
        socket.socket().close()
    except pytest_socket.SocketBlockedError:
        war_zu = True
    else:
        war_zu = False

    hosts = getattr(pytest_socket, "socket_allow_hosts", None)
    pytest_socket.enable_socket()
    if hosts:
        hosts([urlsplit(QUELLE).hostname, "127.0.0.1"], allow_unix_socket=True)
    try:
        yield
    finally:
        if war_zu:
            if hosts:
                hosts(["127.0.0.1"], allow_unix_socket=True)
            pytest_socket.disable_socket()


def _quelle(name: str) -> str:
    try:
        with urllib.request.urlopen(QUELLE + name, timeout=15) as antwort:
            return antwort.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as fehler:
        pytest.skip(f"{QUELLE + name} nicht erreichbar ({fehler}) -- ungeprueft")


@pytest.mark.parametrize("name", DATEIEN)
def test_die_kopie_ist_nicht_neuer_als_die_quelle(name: str, netz) -> None:
    """Wer hier bearbeitet, bearbeitet die Kopie und nicht das Original."""
    hier = _hier(name).read_text(encoding="utf-8")
    dort = _quelle(name)

    assert _stempel(hier) <= _stempel(dort), (
        f"{name} ist hier SDK_VERSION {_stempel(hier)}, im SDK des Hubs aber "
        f"{_stempel(dort)}. Diese Datei wird kopiert, nicht gepflegt: Die "
        "Aenderung gehoert nach ha-spatial-hub/sdk/, von dort in die "
        "examples/ und erst dann per `python3 sdk/install.py` hierher."
    )


@pytest.mark.parametrize("name", DATEIEN)
def test_gleiche_nummer_heisst_gleicher_inhalt(name: str, netz) -> None:
    """Die Nummer ist eine Zusage. Ein Test macht sie zu einer.

    Zwei Dateien mit demselben Stempel und verschiedenem Inhalt sind der
    unangenehmste Fall: Beide Seiten halten sich fuer aktuell, und die
    Diagnose des Hubs meldet nichts, weil sie nur die Nummer sieht.
    """
    pfad = _hier(name)
    hier = pfad.read_text(encoding="utf-8")
    dort = _quelle(name)

    if _stempel(hier) < _stempel(dort):
        ordner = pfad.parent.relative_to(WURZEL).as_posix()
        pytest.skip(
            f"{name} ist SDK_VERSION {_stempel(hier)}, aktuell waere "
            f"{_stempel(dort)} -- erlaubt, aber es gibt eine neuere: "
            f"`python3 sdk/install.py --into {ordner}`"
        )

    assert hier == dort, (
        f"{name} traegt hier dieselbe SDK_VERSION wie im Hub, hat aber "
        "anderen Inhalt. Eine der beiden Seiten wurde bearbeitet, ohne den "
        "Stempel zu erhoehen -- ab hier ist die Nummer wertlos."
    )
