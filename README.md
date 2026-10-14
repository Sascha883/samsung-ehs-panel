# Samsung EHS Panel

Bedieneinheit für Samsung-EHS-Wärmepumpen in Home Assistant – Menüführung nach dem Bosch HMI 800,
Optik nach dem Samsung-Bedienteil. Erscheint als eigener Eintrag in der Seitenleiste.

**Voraussetzung:** Integration [EHS Sentinel](https://github.com/echoDaveD/ehs_sentinel_hacs_integration)
(für Einstellungen mit aktivem Schreibmodus).

## Installation über HACS

1. HACS → ⋮ → **Benutzerdefinierte Repositories**
2. URL dieses Repositories eintragen, Kategorie **Integration** → Hinzufügen
3. „Samsung EHS Panel“ suchen → **Herunterladen**
4. Home Assistant **neu starten**
5. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „Samsung EHS Panel“**
   – Titel, Symbol und „Nur für Administratoren“ wählen

Das Panel erscheint danach in der Seitenleiste und nutzt automatisch die Anmeldung von Home Assistant
(kein Token nötig). Titel und Symbol lassen sich später unter **Konfigurieren** ändern.

## Bedienung

- **Startseite:** Wärmepumpe, Zone, Warmwasser, Anwesend/Urlaub, Energie
- **≡** Menü · **≡ 2 Sekunden halten** → Servicemenü mit allen Samsung-FSV-Werten, Monitorwerten und Störungen
- **Menü → Panel-Einstellungen → Entitäten-Zuordnung:** fehlende Werte prüfen oder eigene Entitäten zuordnen
  (z. B. Zirkulationspumpe)

Die Entitäten werden unabhängig vom Präfix gefunden (`samsung_ehssentinel_…`, `heizung_wp_ehssentinel_…` usw.).

## Verbindung & Protokoll (wie EMS-ESP Manager)

**≡ 2 s halten → Diagnose → Verbindung & Protokoll** (nur für Administratoren):

- **Status je EHS-Sentinel-Instanz:** Verbindung zum RS485-Adapter, IP:Port, Schreibmodus, Polling,
  Paketzähler (gelesen / verarbeitet / angefordert), Warteschlange
- **Verbindung neu aufbauen:** lädt die EHS-Sentinel-Integration neu
- **Protokoll:** alle Meldungen von EHS Sentinel seit HA-Start (max. 2000), Filter Info/Warnungen/Fehler,
  Pause, Kopieren, Leeren – aktualisiert sich alle 4 Sekunden
- **Protokollstufe:** Standard (HA) / Info / Debug – nur für die Fehlersuche, wird beim Neustart zurückgesetzt
- **Diagnose anfordern:** ruft `ehs_sentinel.request_diagnostic_logs` auf (Tasks, Warteschlange, Zähler → Protokoll)
- **Paket-Log:** die letzten 300 Zeilen aus `www/ehs_sentinel/logs/packet_*.log`
  (nur bei aktivem „Erweitertes Logging“ in EHS Sentinel)

## Update

Neue Version in HACS herunterladen, Home Assistant neu starten. Der Browser lädt die neue Panel-Datei
automatisch (Versionsnummer in der Adresse).

## Direktaufruf (z. B. Wandtablet)

`http://<home-assistant>:8123/samsung_ehs_panel/samsung-ehs-hmi800.html`
– außerhalb von Home Assistant im Menü → Internet einen Long-Lived-Token hinterlegen.
