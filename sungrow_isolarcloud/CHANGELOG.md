# Changelog

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
