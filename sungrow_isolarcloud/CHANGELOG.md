# Changelog

## 1.6.0 – 2026-07-12

- **Solar-Planer 2.0** – deutlich genauere Prognose:
  - Haurwitz-Klarhimmelmodell (Luftmassen-Dämpfung) statt reinem Sonnenstand
  - „Heute“ = real erzeugte kWh (Zählerstand) + Rest-Prognose ab jetzt;
    Anzeige von Sonnenuntergang, verbleibenden Sonnenstunden und
    Rest-Erzeugung
  - **Nowcasting:** die Live-Leistung der Anlage korrigiert die Prognose der
    nächsten ~3 Stunden (Ist/Modell-Verhältnis, abklingend)
  - **Selbstkalibrierung:** laufender Abgleich Modell ↔ reale Erzeugung lernt
    einen Anlagenfaktor (Ausrichtung, Verschattung, Verluste) – persistiert
  - **Überschuss-Logik:** reicht die Rest-Erzeugung für Restverbrauch +
    Batterieladung + Gerät (≥ 2,5 kWh Puffer), lautet die Empfehlung „heute“,
    selbst wenn morgen sonniger wird – warten brächte nichts
  - Kontext-Chips in der Karte (Rest-Sonne, Rest-Erzeugung, Überschuss,
    Kalibrierfaktor)

## 1.5.0 – 2026-07-12

- **Netz- und Batterie-Leistung in allen Charts** („Leistung heute“ und
  Verlauf): Die Serien „Netz“ und „Batterie“ sind jetzt virtuell – das Backend
  wählt automatisch die passende Quelle. Liefert die Anlage die Punkte 83549/
  83238 nicht (typisch bei Heimanlagen), wird die Historie vom Speichergerät
  geladen (Netz = Bezug − Einspeisung, Batterie = Ladung − Entladung,
  Vorzeichen wie im Energiefluss: + Bezug/Laden, − Einspeisen/Entladen).
- Geräte-Historie nutzt denselben Endpoint-Fallback und die
  Einheiten-Erkennung wie die Echtzeitwerte.

## 1.4.1 – 2026-07-12

- **Fix: Energiefluss-Zerlegung** – die Geräte-Punkte (13xxx) kommen je nach
  Konto in kW/kWh **oder bereits in W/Wh**. Die pauschale kW→W-Umrechnung aus
  1.4.0 blähte bei W-Konten die Netz-/Batterie-Leistungen um Faktor 1000 auf,
  wodurch das Diagramm die gesamte PV dem Netz zuordnete und PV→Haus leer
  blieb. Das Einheitensystem wird jetzt pro Anlage automatisch erkannt
  (Abgleich Geräte-Last ↔ Anlagen-Last, Plausibilitätsgrenzen) und gemerkt.

## 1.4.0 – 2026-07-11

- **Batterie-Fluss jetzt messbasiert:** Zusätzlich zu den Anlagen-Punkten
  werden die Geräte-Punkte des Speichersystems abgefragt (13xxx-Reihe aus dem
  GoSungrow-Katalog): Lade-/Entladeleistung (13126/13150), SOC/SOH/Temperatur,
  Einspeise-/Bezugsleistung (13121/13149), Hausverbrauch (13119), PV-DC-Leistung
  und Tages-/Gesamtenergien. Damit zeigt der Energiefluss PV→Batterie und
  Batterie→Haus korrekt an, statt Überschüsse fälschlich dem Netz zuzuordnen.
- Geräte-Leistungen (kW) und -Energien (kWh) werden automatisch auf W/Wh
  normalisiert; alle neuen Messwerte erscheinen auch als MQTT-Sensoren
  (u. a. `battery_charging_power`, `battery_soh`, `battery_temperature`).
- Der Geräte-Endpunkt wird je API-Familie automatisch ermittelt
  (platform/klassisch) und gemerkt; nicht verfügbare Gerätetypen werden
  einmalig geloggt und übersprungen.
- Update-Hinweis: Frontend-Cache wird dank v1.3.2 automatisch erneuert.

## 1.3.2 – 2026-07-11

- **Fix: Solar-Planer-Karte fehlte** trotz API-Key. Zwei Ursachen behoben:
  Frontend-Dateien werden jetzt mit Versions-Parameter geladen und die
  Startseite mit `no-cache` ausgeliefert (Ingress/Browser hielten nach
  Add-on-Updates altes JS im Cache), und die Karte blendet sich bei
  Problemen nicht mehr still aus – ohne Key zeigt sie eine Einrichtungshilfe,
  bei API-Fehlern die konkrete Fehlermeldung.
- Add-on-Log zeigt beim Start, ob ein OpenWeather-Key ankommt
  (maskiert, letzte 4 Zeichen).

## 1.3.1 – 2026-07-11

- Irreführende Startmeldung „Got unexpected response from the API: Service not
  enabled“ unterdrückt – das war bashios interne Prüfung, ob ein MQTT-Dienst
  existiert (kein OpenWeather-Fehler).
- MQTT-Erkennung wartet beim Start jetzt bis zu 60 s auf Mosquitto (behebt
  deaktivierte Sensoren, wenn das Add-on beim HA-Boot vor dem Broker startet).
- Doku: Klarstellung, dass der Solar-Planer den kostenlosen
  5-Tage/3-Stunden-Forecast nutzt (kein One-Call-Abo nötig).

## 1.3.0 – 2026-07-11

- **Neu: Solar-Planer** 🌦️ – Empfehlung, ob stromintensive Geräte
  (Waschmaschine, Trockner, Spülmaschine, E-Auto) jetzt, heute oder besser an
  einem sonnigeren Folgetag laufen sollten. Kombiniert die
  OpenWeather-Vorhersage (3-Stunden-Raster, Bewölkung + Regenwahrscheinlichkeit)
  mit einem Sonnenstandsmodell für den Anlagenstandort zu einer
  PV-Ertragsprognose für heute + 3 Tage, inkl. bestem Tageszeitfenster.
  Berücksichtigt den Live-Zustand: Läuft die Anlage gerade im Überschuss bei
  gut geladener Batterie, lautet die Empfehlung „sofort einschalten“.
  Neue Optionen: `openweather_api_key` (kostenloser Key von openweathermap.org),
  optional `latitude`/`longitude` (sonst automatisch aus den Anlagendaten).

## 1.2.1 – 2026-07-11

- **Batterie-Ladestand korrekt:** SOC-Punkte (83129/83252/83334) liefern je nach
  Konto einen Bruchteil 0–1 – wird jetzt automatisch auf Prozent skaliert
  (Dashboard, Parameter, MQTT-Sensoren und Verlauf).
- **Batterie-Richtung korrekt:** Liefert die Anlage keine Batterie-Leistung,
  wird sie aus der Energiebilanz (PV + Netz − Haus) hergeleitet; zusätzliche
  Fallback-Punkte (EMS-Reihe 83322–83334, Zähler 83032, Wechselrichter 83002).
- **Verlauf repariert:** Die Minutendaten-API begrenzt die Zeitspanne pro
  Abfrage – Abfragen werden jetzt automatisch in zulässige Fenster zerlegt
  (das Limit wird beim ersten Fehler gelernt und gemerkt).
- **Parameter-Browser:** Messpunkte, die die Anlage nicht liefert, werden
  ausgeblendet (über `?include_empty=true` weiterhin abrufbar).

## 1.2.0 – 2026-07-10

- **Auto-Negotiation:** Der Client probiert beim Start automatisch alle
  bekannten API-Varianten durch (klassische `/openapi/*`- vs. neue
  `/openapi/platform/*`-Endpunkte, verschlüsselt/unverschlüsselt,
  Token-Header vs. Bearer) und übernimmt die erste funktionierende.
- **Verbindungs-Diagnose** im Status-Tab: testet alle Varianten und zeigt
  den Sungrow-Fehlercode je Variante an.
- **OAuth-Autorisierung:** Für Anwendungen, die die Konto-Freigabe über die
  iSolarCloud-Weboberfläche verlangen (neue Option `app_id`,
  Autorisierungs-Flow direkt im Status-Tab, Tokens werden persistiert).

## 1.1.0 – 2026-07-10

- **Fix:** Daten-Endpunkte antworteten mit `E900 Unauthorized access` –
  das Session-Token wird jetzt zusätzlich als HTTP-Header gesendet.
- **Neu:** Verschlüsselter API-Modus (AES-128 + RSA, `api_key_param`) für
  neuere Developer-Portal-Anwendungen – aktivierbar über die neue Option
  `rsa_public_key`. Implementiert in reinem Python, keine nativen
  Abhängigkeiten (läuft auch auf armv7).

## 1.0.0 – 2026-07-10

Erste Version 🎉

- Anbindung an die Sungrow iSolarCloud Developer-API (alle 4 Regionen)
- Ingress-Dashboard: animierter Energiefluss, Tages-KPIs (Ertrag, Autarkie,
  Eigenverbrauch, Netzbezug, Batterie), Leistungs-Charts mit Verlauf (bis 7 Tage)
- Geräte-Übersicht und durchsuchbarer Parameter-Browser mit kopierbaren Entity-IDs
- Alle Anlagen-Messpunkte als Home-Assistant-Sensoren via MQTT-Discovery
  (automatische Broker-Erkennung, Energie-Dashboard-kompatibel)
- Light/Dark-Theme, Demo-Modus mit simulierten Daten
