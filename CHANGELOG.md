# Changelog

All notable changes to **Powerline Network** (ha-powerline) are documented here.

## [Unreleased]

### Improved
- **Verbraucher-Korrelation ist belastbarer.** Leistungssensoren werden mit
  vier Tagen Recorder-Historie verglichen. Ein Zusammenhang wird erst gemeldet,
  wenn an mindestens drei verschiedenen UTC-Tagen jeweils wiederholt sowohl
  Last- als auch Leerlauf-Messungen vorliegen. Ein einzelner kurzer Einbruch
  löst dadurch keine Verbraucher-Warnung mehr aus (#7).

## [261123] - 2026-11-23

### Fixed
- **Einzelner fehlgeschlagener Suchlauf wird abgefedert.** Wirft `discover()`
  eine unerwartete Ausnahme (etwa ein kurzlebiger `OSError` beim Roh-Socket-
  Senden) oder meldet sich die Remote-Sonde einmal nicht, bleibt der letzte
  Stand fuer einen Abfrage-Zyklus stehen, statt alle Entities auf
  "Nicht verfuegbar" zu werfen; erst zwei Fehlversuche in Folge melden einen
  Fehler. Ohne vorherigen Stand (erste Abfrage) bleibt es beim ehrlichen
  Fehler ([#109](https://github.com/Chance-Konstruktion/ha-powerline/issues/109)).
- **Erster Alarm liest keine Datei mehr im Event-Loop.** Die Benachrichtigungs-
  kataloge werden beim Einrichten einmalig im Executor vorgeladen; bisher
  oeffnete der erste Alarm je Sprache die Katalogdatei direkt im
  Ereignis-Loop ("Detected blocking call to open"). Die Diagnose liest die
  Integrations-Version aus dem geladenen Integrations-Objekt statt per
  `read_text` aus der manifest.json.
- **FRITZ!Powerline: LED-Zustand nach Neustart richtig.** Der Zustand wird an
  den AVM-eigenen PIB-Stellen gelesen; bisher zeigte der LED-Schalter nach
  einem HA-Neustart den Standardwert. AVM-Firmware kennt keinen
  Energiesparmodus, dafuer wird kein geratener Wert mehr angezeigt.
- **Zustand spaeter gefundener Adapter.** LED, QoS und Energiesparmodus werden
  auch fuer Adapter abgefragt, die erst nach dem Start auftauchen; eine
  fehlgeschlagene Zustandsabfrage wird beim naechsten Zyklus wiederholt.
- **Suchlauf und Diagnose mit Zeitbudget.** Die Discovery-Kaskade und jede
  Diagnose-Pruefung halten ein festes Zeitbudget ein, statt sich bei stummen
  Adaptern aufzusummieren.
- **Robuster gegen beschaedigte Speicherdateien.** Raten-Verlauf und
  Topologie-Layout mit unerwarteter Struktur werden ignoriert statt das
  Einrichten oder den Layout-Websocket zu stoeren.
- **Verbraucher-Korrelation findet umbenannte Raten-Entities** ueber die
  Entity-Registry statt ueber den Namen.
- Kein KeyError mehr im Log, wenn sich Optionen eines nicht geladenen
  Eintrags aendern (etwa beim Loeschen eines Adapter-Geraets).

## [261031] - 2026-10-31

### Fixed
- **Falsche PHY-Raten bei MEDIAXTREAM-Adaptern.** Bit 11 des Ratenfelds ist
  ein Merker, die Rate steht in den unteren 11 Bits; bisher wurden solche
  Werte verworfen oder als 0 gezeigt
  ([#121](https://github.com/Chance-Konstruktion/ha-powerline/issues/121)).
- **LED-Schalter fuer INT6x00/INT7400-Adapter** wie den Netgear XAV500x ueber
  VS_SET_LED_BEHAVIOR. Die Einstellung gilt bis zum naechsten Neustart des
  Adapters ([#122](https://github.com/Chance-Konstruktion/ha-powerline/issues/122)).
- **Hersteller je Adapter** statt fest TP-Link: gemeldeter Hersteller, sonst
  die OUI-Tabelle, sonst ein neutraler Name.

## [261028] - 2026-10-28

### Added
- **Alarme nennen die wahrscheinliche Ursache.** Wird eine Strecke langsam,
  steht im Alarm, was die Historie dazu sagt: wiederkehrende Tageszeit,
  einzelner Adapter oder ganze Strecke
  ([#115](https://github.com/Chance-Konstruktion/ha-powerline/pull/115)).
- **Einbrueche mit Verbrauchern verknuepfen.** In den Optionen lassen sich
  Leistungssensoren waehlen; faellt die Rate, sobald z. B. Waschmaschine oder
  Heizluefter laufen, meldet die Integration den Zusammenhang. Die Werte
  kommen nur aus dem Recorder und werden nicht gespeichert
  ([#119](https://github.com/Chance-Konstruktion/ha-powerline/pull/119)).
- **Diagnose-Download mit den letzten Abfragen** je Adapter (gesehen, nicht
  gesehen, Fehlerart), ohne Fehlertexte und ohne URLs
  ([#120](https://github.com/Chance-Konstruktion/ha-powerline/pull/120)).

### Fixed
- **Ein einzelner verpasster Poll macht keinen Adapter mehr offline**;
  die Diagnose zeigt den Grund, das Debug-Log den Traceback
  ([#116](https://github.com/Chance-Konstruktion/ha-powerline/pull/116)).

### Performance
- **Die Alarm-Pruefung ist rund dreimal schneller** (12 Adapter, 30 Tage
  Historie: 148 ms auf 54 ms je Poll; einmal je Stunde ~105 ms, wenn die
  Stundenanalyse neu gerechnet wird).

### Documentation
- README trennt die behobene AV2000-Bit-Auslegung (#112) von der weiter
  offenen Einschraenkung zwischen zwei AV500-Adaptern
  ([#118](https://github.com/Chance-Konstruktion/ha-powerline/pull/118)).

## [261016] - 2026-10-16

### Added
- **Remote-Probe fuer Home Assistant in einem eigenen VLAN.** HomePlug wird
  nicht geroutet; steht HA im IoT-VLAN und haengen die Adapter am Trunk,
  fand die Einrichtung bisher 0 Adapter. Jetzt laeuft `probe/powerline_probe.py`
  auf einem Linux-Geraet im Netz der Adapter und liefert die Werte per HTTP
  (optional mit Token); in der Einrichtung heisst die Wahl *remote_probe*.
  Nur lesend: LED, Energiesparen, QoS und Neustart gehen darueber nicht.
  Angeregt von tim.plas.
- **Diagnose-Download mit der rohen Ratentabelle.** Unter *Geraet → Diagnose
  herunterladen* steht jetzt jedes gelesene Ratenfeld als 16-Bit-Rohwert mit
  Flag-Nibble, Bit 11 und dekodiertem Wert, auch die verworfenen
  Nennleistungs-Eintraege. Hinweis: Der Dump enthaelt die MAC-Adressen der
  Adapter. Von @fboundy
  ([#114](https://github.com/Chance-Konstruktion/ha-powerline/pull/114)).

### Fixed
- **Auch zwischen zwei AV2000 keine Phantomrate mehr.** Dort meldet jeder
  Adapter den anderen mit seiner Nennleistung (z. B. `0x68F4` = 2292), in
  einem Fremdeintrag, den der MAC-Vergleich aus 261008 nicht erwischt.
  Bit 11 (`0x0800`) des Ratenfelds kennzeichnet die Nennleistung; solche
  Werte gelten jetzt als „Rate unbekannt“ (0), die Gegenstelle kann die
  Luecke per Spiegelung fuellen. Belegt an 24 echten Werten ohne Fehltreffer.
  Gefunden und behoben von @fboundy
  ([#112](https://github.com/Chance-Konstruktion/ha-powerline/pull/112)).

### Documentation
- **Tipp fuer G.hn-Besitzer:** README verweist auf
  [dieneuser/zyxel_pla](https://github.com/dieneuser/zyxel_pla) fuer den
  Zyxel PLA6457 (G.hn wird hier nicht unterstuetzt).
- Die README behauptete, die Karte werde als Lovelace-Ressource
  registriert. Sie wird als Frontend-Modul geladen; eine Ressource braucht
  es nicht. Hinweis: nach Installation oder Update einmal Strg+F5. Gemeldet
  von @fboundy ([#113](https://github.com/Chance-Konstruktion/ha-powerline/issues/113)).

## [261008] - 2026-10-08

### Fixed
- **Keine Phantom-Verbindung mehr mit der Nennleistung des Adapters.**
  Der erste Eintrag jeder MEDIAXTREAM-`NW_STATS`-Antwort ist der antwortende
  Adapter selbst, und sein Ratenfeld traegt die Nennleistung (AV2000 ~2100,
  AV1000 ~1000) statt einer Verbindungsrate. Sie wurde als Rate des Adapters
  uebernommen und gespiegelt; zwischen zwei AV2000 stand so eine Kante mit
  2098 Mbit/s, wo tpPLC 43-55 misst. Der Eigeneintrag wird jetzt
  uebersprungen, `decode_phy_rate()` bleibt unveraendert. Gefunden, mit
  Mitschnitten belegt und behoben von @fboundy
  ([#110](https://github.com/Chance-Konstruktion/ha-powerline/issues/110),
  [#111](https://github.com/Chance-Konstruktion/ha-powerline/pull/111));
  ein Test haelt seinen Mitschnitt fest.

## [261007] - 2026-10-07

### Fixed
- **Ein verpasster Suchlauf macht keinen Adapter mehr "nicht verfuegbar".**
  Bisher galt ein Adapter als offline, sobald er in einem einzigen
  Discovery-Fenster (5 s) nicht antwortete. Auf verrauschten Leitungen oder
  ueber Phasengrenzen kommt so ein Fenster mal kurz oder ganz leer zurueck --
  dann sprangen alle Geraete fuer einen Zyklus auf "unavailable" und fingen
  sich danach wieder
  ([#109](https://github.com/Chance-Konstruktion/ha-powerline/issues/109)).
  Jetzt braucht es zwei Fehlrunden in Folge (`OFFLINE_AFTER_MISSED_POLLS`);
  die erste Antwort danach setzt den Zaehler zurueck.

### Added
- **Zyxel PLA5456** in der Hardwaretabelle: Discovery vom Melder bestaetigt
  (#109), LED / Energiesparen / QoS noch offen.

