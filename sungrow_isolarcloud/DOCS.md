# Sungrow iSolarCloud Add-on

Verbindet dein Sungrow iSolarCloud-Konto über die offizielle **Developer-API (OpenAPI)**
mit Home Assistant:

- 🌞 **Dashboard** mit animiertem Energiefluss (PV → Haus / Batterie / Netz), Tages-KPIs
  (Ertrag, Autarkie, Eigenverbrauch, Netzbezug, Batterie) und Verlaufs-Charts – direkt
  in der Seitenleiste (Ingress).
- 📡 **Alle Messwerte als Sensoren**: Jeder Parameter der Anlage wird per
  MQTT-Discovery automatisch als Home-Assistant-Entität angelegt und lässt sich in
  Automationen, im Energie-Dashboard und in eigenen Karten weiterverwenden.
- 🔎 **Parameter-Browser**: Alle Messpunkte durchsuchbar, mit Entity-ID zum Kopieren.

## Voraussetzungen: API-Zugangsdaten anlegen

Das Add-on nutzt die offizielle Entwickler-API von Sungrow. Du brauchst dafür einmalig
einen **App-Key** und einen **Secret-Key** (x-access-key):

1. Registriere dich im Sungrow Developer Portal:
   <https://developer-api.isolarcloud.com>
   (mit demselben Konto, das du in der iSolarCloud-App verwendest, oder einem neuen).
2. Lege unter **Applications** eine neue Anwendung an („Create Application“).
   Als Typ genügt eine einfache Anwendung; nach der Freigabe durch Sungrow
   (dauert meist 1–2 Werktage) findest du dort **Appkey** und **Secret Key**.
3. Trage in den Add-on-Optionen ein:
   - `appkey` – der Appkey deiner Anwendung
   - `secret_key` – der zugehörige Secret / Access Key
   - `username` / `password` – deine normalen iSolarCloud-Zugangsdaten
   - `region` – der Server, auf dem dein Konto liegt (für Deutschland/Europa: `eu`)

> **Tipp:** Ohne Zugangsdaten startet das Add-on im **Demo-Modus** mit simulierten
> Daten – so kannst du dir die Oberfläche vorab ansehen.

## Optionen

| Option | Beschreibung |
|---|---|
| `region` | iSolarCloud-Server: `eu`, `international`, `china`, `australia` |
| `appkey` | Appkey aus dem Developer Portal |
| `secret_key` | Secret/Access Key aus dem Developer Portal |
| `username` | iSolarCloud-Benutzername (E-Mail) |
| `password` | iSolarCloud-Passwort |
| `poll_interval` | Abfrage-Intervall in Sekunden (Standard 300 – die Cloud aktualisiert Werte nur alle ~5 min) |
| `language` | Sprache der Sensornamen: `de` oder `en` |
| `mqtt_enabled` | Sensoren per MQTT-Discovery anlegen (empfohlen) |
| `demo_mode` | Erzwingt den Demo-Modus mit simulierten Daten |

## MQTT-Sensoren

Für die Sensoren wird der **Mosquitto-Broker** (offizielles Add-on) plus die
**MQTT-Integration** in Home Assistant benötigt. Das Add-on findet den Broker
automatisch – es ist keine weitere Konfiguration nötig.

Alle Entitäten werden unter einem Gerät „Sungrow *Anlagenname*“ gruppiert und heißen
z. B.:

```
sensor.sungrow_<anlagen_id>_power           # Aktuelle Leistung (W)
sensor.sungrow_<anlagen_id>_daily_yield     # Tagesertrag (Wh)
sensor.sungrow_<anlagen_id>_battery_soc     # Batterie-Ladestand (%)
sensor.sungrow_<anlagen_id>_load_power      # Hausverbrauch (W)
sensor.sungrow_<anlagen_id>_feed_in_today   # Einspeisung heute (Wh)
...
```

Energie-Sensoren tragen `device_class: energy` und `state_class: total_increasing`
und können direkt im **Energie-Dashboard** von Home Assistant verwendet werden.

## Fehlerbehebung

- **„Fehler“ im Statuspunkt / Login schlägt fehl:** Prüfe Region, Appkey und
  Secret-Key. Der letzte API-Fehler wird im Tab **Status** im Klartext angezeigt
  (inkl. Sungrow-Fehlercode).
- **Keine Sensoren in HA:** Mosquitto-Add-on installiert und die MQTT-Integration
  eingerichtet? Im Tab **Status** muss „MQTT-Sensoren: Verbunden“ stehen.
- **Werte aktualisieren sich langsam:** Das ist normal – die iSolarCloud liefert
  neue Werte nur ca. alle 5 Minuten.
