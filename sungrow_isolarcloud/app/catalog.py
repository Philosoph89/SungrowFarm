"""Catalog of iSolarCloud plant-level measure points (device_type 11).

Point IDs follow Sungrow's official "Common plant measuring points" enumeration.
Each entry carries the metadata needed for Home Assistant MQTT discovery and
for a clean presentation in the UI. Names/units returned live by the API
(point_dict) take precedence for display; this catalog is the semantic layer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PointMeta:
    point_id: str
    code: str                    # stable machine-readable slug
    name_de: str
    name_en: str
    unit: str | None             # native unit as delivered by the API
    device_class: str | None     # HA device class
    state_class: str | None      # HA state class
    icon: str                    # mdi icon
    group: str                   # UI grouping: production/consumption/grid/battery/plant
    transform: str | None = None  # "fraction_pct": API delivers 0–1, display 0–100 %


P = PointMeta


def apply_transform(meta: "PointMeta | None", value):
    """Normalise API values. SOC-style points arrive as a 0–1 fraction on some
    accounts and as 0–100 on others – scale only when the value is ≤ 1."""
    if meta and meta.transform == "fraction_pct" and isinstance(value, (int, float)):
        return round(value * 100, 1) if value <= 1.0 else value
    return value

PLANT_POINTS: dict[str, PointMeta] = {p.point_id: p for p in [
    # --- Production ------------------------------------------------------
    P("83033", "power", "Aktuelle Leistung", "Current Power", "W", "power", "measurement", "mdi:solar-power", "production"),
    P("83002", "inverter_ac_power", "Wechselrichter-Leistung", "Inverter AC Power", "W", "power", "measurement", "mdi:current-ac", "production"),
    P("83329", "pv_power_ems", "PV-Leistung (EMS)", "PV Power (EMS)", "W", "power", "measurement", "mdi:solar-panel", "production"),
    P("83331", "daily_pv_yield_ems", "PV-Tagesertrag (EMS)", "Daily PV Yield (EMS)", "Wh", "energy", "total_increasing", "mdi:solar-power-variant", "production"),
    P("83332", "total_pv_yield_ems", "PV-Gesamtertrag (EMS)", "Total PV Yield (EMS)", "Wh", "energy", "total_increasing", "mdi:sigma", "production"),
    P("83022", "daily_yield", "Tagesertrag", "Daily Yield", "Wh", "energy", "total_increasing", "mdi:solar-power-variant", "production"),
    P("83024", "total_yield", "Gesamtertrag", "Total Yield", "Wh", "energy", "total_increasing", "mdi:sigma", "production"),
    P("83067", "pv_power", "PV-Leistung", "PV Power", "W", "power", "measurement", "mdi:solar-panel", "production"),
    P("83019", "power_fraction", "Leistung / installierte Leistung", "Power Fraction", None, None, "measurement", "mdi:percent", "production"),
    P("83005", "daily_equivalent_hours", "Volllaststunden (Tag)", "Equivalent Hours (day)", "h", None, "measurement", "mdi:clock-outline", "production"),
    P("83018", "daily_yield_theoretical", "Theoretischer Tagesertrag", "Theoretical Daily Yield", "Wh", "energy", "total_increasing", "mdi:chart-bell-curve", "production"),
    P("83023", "plant_pr", "Performance Ratio", "Performance Ratio", None, None, "measurement", "mdi:speedometer", "production"),
    # --- Consumption ------------------------------------------------------
    P("83106", "load_power", "Hausverbrauch (Leistung)", "Load Power", "W", "power", "measurement", "mdi:home-lightning-bolt", "consumption"),
    P("83330", "load_power_ems", "Hausverbrauch (EMS)", "Load Power (EMS)", "W", "power", "measurement", "mdi:home-lightning-bolt", "consumption"),
    P("83052", "total_load_active_power", "Last Wirkleistung gesamt", "Total Load Active Power", "W", "power", "measurement", "mdi:home-lightning-bolt-outline", "consumption"),
    P("83118", "daily_load_consumption", "Tagesverbrauch", "Daily Load Consumption", "Wh", "energy", "total_increasing", "mdi:home-battery", "consumption"),
    P("83124", "total_load_consumption", "Gesamtverbrauch", "Total Load Consumption", "Wh", "energy", "total_increasing", "mdi:home-battery-outline", "consumption"),
    P("83097", "daily_direct_consumption", "Direktverbrauch (Tag)", "Daily Direct Consumption", "Wh", "energy", "total_increasing", "mdi:transmission-tower-off", "consumption"),
    P("83100", "total_direct_consumption", "Direktverbrauch (gesamt)", "Total Direct Consumption", "Wh", "energy", "total_increasing", "mdi:transmission-tower-off", "consumption"),
    # --- Grid -------------------------------------------------------------
    P("83549", "grid_power", "Netz-Wirkleistung", "Grid Active Power", "W", "power", "measurement", "mdi:transmission-tower", "grid"),
    P("83032", "meter_ac_power", "Netz-Leistung (Zähler)", "Meter AC Power", "W", "power", "measurement", "mdi:meter-electric", "grid"),
    P("83328", "grid_power_ems", "Netz-Leistung (EMS)", "Grid Power (EMS)", "W", "power", "measurement", "mdi:transmission-tower", "grid"),
    P("83102", "purchased_today", "Netzbezug heute", "Purchased Energy Today", "Wh", "energy", "total_increasing", "mdi:transmission-tower-import", "grid"),
    P("83105", "purchased_total", "Netzbezug gesamt", "Total Purchased Energy", "Wh", "energy", "total_increasing", "mdi:transmission-tower-import", "grid"),
    P("83072", "feed_in_today", "Einspeisung heute", "Feed-in Energy Today", "Wh", "energy", "total_increasing", "mdi:transmission-tower-export", "grid"),
    P("83075", "feed_in_total", "Einspeisung gesamt", "Total Feed-in Energy", "Wh", "energy", "total_increasing", "mdi:transmission-tower-export", "grid"),
    P("83119", "daily_feed_in_pv", "PV-Einspeisung heute", "Daily PV Feed-in", "Wh", "energy", "total_increasing", "mdi:transmission-tower-export", "grid"),
    # --- Battery ----------------------------------------------------------
    P("83129", "battery_soc", "Batterie-Ladestand", "Battery SoC", "%", "battery", "measurement", "mdi:battery-high", "battery", transform="fraction_pct"),
    P("83252", "battery_level_soc", "Batterie-Ladestand (ESS)", "Battery Level SoC", "%", "battery", "measurement", "mdi:battery-high", "battery", transform="fraction_pct"),
    P("83334", "battery_soc_ems", "Batterie-Ladestand (EMS)", "Battery SoC (EMS)", "%", "battery", "measurement", "mdi:battery-high", "battery", transform="fraction_pct"),
    P("83238", "battery_power", "Batterie-Wirkleistung", "Battery Active Power", "W", "power", "measurement", "mdi:battery-charging", "battery"),
    P("83326", "battery_power_ems", "Speicher-Leistung (EMS)", "Storage Power (EMS)", "W", "power", "measurement", "mdi:battery-charging", "battery"),
    P("83322", "ess_daily_charge", "Ladung heute (EMS)", "Daily Charge (EMS)", "Wh", "energy", "total_increasing", "mdi:battery-plus", "battery"),
    P("83323", "ess_daily_discharge", "Entladung heute (EMS)", "Daily Discharge (EMS)", "Wh", "energy", "total_increasing", "mdi:battery-minus", "battery"),
    P("83324", "ess_total_charge", "Ladung gesamt (EMS)", "Total Charge (EMS)", "Wh", "energy", "total_increasing", "mdi:battery-plus-outline", "battery"),
    P("83325", "ess_total_discharge", "Entladung gesamt (EMS)", "Total Discharge (EMS)", "Wh", "energy", "total_increasing", "mdi:battery-minus-outline", "battery"),
    P("83327", "ess_remaining_charge", "Restladung (EMS)", "Remaining Charge (EMS)", "Wh", "energy_storage", "measurement", "mdi:battery-arrow-down", "battery"),
    P("83046", "pcs_total_active_power", "Speicher-Wirkleistung (PCS)", "PCS Total Active Power", "W", "power", "measurement", "mdi:battery-charging-outline", "battery"),
    P("83243", "daily_charge", "Ladung heute", "Daily Charge", "Wh", "energy", "total_increasing", "mdi:battery-plus", "battery"),
    P("83244", "daily_discharge", "Entladung heute", "Daily Discharge", "Wh", "energy", "total_increasing", "mdi:battery-minus", "battery"),
    P("83241", "total_charge", "Ladung gesamt", "Total Charge", "Wh", "energy", "total_increasing", "mdi:battery-plus-outline", "battery"),
    P("83242", "total_discharge", "Entladung gesamt", "Total Discharge", "Wh", "energy", "total_increasing", "mdi:battery-minus-outline", "battery"),
    P("83235", "chargeable_energy", "Ladbare Energie", "Chargeable Energy", "Wh", "energy_storage", "measurement", "mdi:battery-arrow-up", "battery"),
    P("83236", "dischargeable_energy", "Entladbare Energie", "Dischargeable Energy", "Wh", "energy_storage", "measurement", "mdi:battery-arrow-down", "battery"),
    # --- Plant / environment ---------------------------------------------
    P("83012", "irradiance", "Einstrahlung", "Irradiance", "W/m²", "irradiance", "measurement", "mdi:weather-sunny", "plant"),
    P("83013", "daily_irradiation", "Tageseinstrahlung", "Daily Irradiation", "Wh/m²", None, "total_increasing", "mdi:weather-sunny", "plant"),
    P("83016", "ambient_temperature", "Umgebungstemperatur", "Ambient Temperature", "°C", "temperature", "measurement", "mdi:thermometer", "plant"),
    P("83017", "module_temperature", "Modultemperatur", "Module Temperature", "°C", "temperature", "measurement", "mdi:thermometer-lines", "plant"),
]}

# Points the dashboard needs for the energy-flow view (subset of the above)
FLOW_POINT_IDS = [
    "83033", "83067", "83106", "83549", "83238", "83046", "83129", "83252",
    "83022", "83102", "83072", "83118", "83243", "83244",
]

# Sensible default set for history charts
HISTORY_DEFAULT_POINTS = ["83033", "83106", "83549", "83238"]


def meta_for(point_id: str) -> PointMeta | None:
    return PLANT_POINTS.get(str(point_id))


def display_name(point_id: str, lang: str, api_name: str | None = None) -> str:
    m = meta_for(point_id)
    if m:
        return m.name_de if lang == "de" else m.name_en
    return api_name or f"Point {point_id}"
