# Changelog

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
