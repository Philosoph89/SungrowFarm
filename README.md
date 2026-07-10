# SungrowFarm – Sungrow iSolarCloud für Home Assistant

Home-Assistant-Add-on, das dein **Sungrow iSolarCloud**-Konto über die offizielle
Developer-API anbindet: ein cleanes Dashboard mit animiertem Energiefluss direkt in
der HA-Seitenleiste – und **alle Messwerte als Sensoren** via MQTT-Discovery, bereit
für Automationen und das Energie-Dashboard.

## Installation

1. In Home Assistant: **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**
   und diese Repository-URL hinzufügen:

   ```
   https://github.com/philippmueller/sungrowfarm
   ```

2. **Sungrow iSolarCloud** installieren und starten.
3. API-Zugangsdaten in den Optionen hinterlegen – die Schritt-für-Schritt-Anleitung
   steht im Tab **Dokumentation** des Add-ons (bzw. in
   [`sungrow_isolarcloud/DOCS.md`](sungrow_isolarcloud/DOCS.md)).

Ohne Zugangsdaten startet das Add-on im **Demo-Modus** mit simulierten Daten.

## Features

| | |
|---|---|
| 🔀 Energiefluss | Live-Animation PV → Haus / Batterie / Netz |
| 📊 KPIs | Tagesertrag, Autarkie, Eigenverbrauch, Netzbezug/Einspeisung, Batterie-SoC |
| 📈 Verlauf | Leistungs-Charts (12 h bis 7 Tage) mit Crosshair-Tooltip |
| 🔌 Geräte | Wechselrichter, Speicher, Zähler, Logger inkl. Status |
| 🧩 Parameter | Alle Messpunkte durchsuchbar, Entity-ID per Klick kopieren |
| 📡 MQTT | Jeder Messwert als HA-Sensor (Discovery, Energie-Dashboard-tauglich) |
| 🌓 UI | Light/Dark, responsiv, Deutsch/Englisch |

## Standalone (ohne Home Assistant OS)

Für Docker-Setups ohne Supervisor lässt sich der Dienst auch direkt betreiben:

```bash
cp .env.example .env   # Zugangsdaten eintragen
docker compose up -d   # UI auf http://localhost:8099
```

## Entwicklung

```bash
cd sungrow_isolarcloud/app
python3 -m venv venv && venv/bin/pip install -r requirements.txt
SG_DEMO_MODE=true venv/bin/python -m uvicorn main:app --port 8099 --reload
```

## Hinweise

- Die iSolarCloud aktualisiert Messwerte ca. alle **5 Minuten** – kürzere
  Poll-Intervalle bringen keine neuen Daten und belasten nur das API-Kontingent.
- Dieses Projekt steht in keiner Verbindung zu Sungrow. iSolarCloud ist eine Marke
  der Sungrow Power Supply Co., Ltd.
