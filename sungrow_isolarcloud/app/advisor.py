"""Solar planner: combines the OpenWeather 3-hour forecast with a clear-sky
solar model to estimate PV yield for today + the next 3 days, and derives a
recommendation for running energy-intensive appliances now, today, or on a
better day.

Accuracy measures beyond the raw forecast:
- Haurwitz clear-sky irradiance (air-mass attenuation) instead of plain sin(h)
- today is split into *produced so far* (real meter value) + *remaining*
  (forecast from now until sunset), incl. remaining sun hours
- nowcasting: the plant's live output corrects the next ~3 h of forecast
- self-calibration: an EWMA of live output vs. model output learns a plant
  factor (orientation, shading, soiling), persisted across restarts
- surplus logic: if the remaining day already covers expected household load
  plus battery charging, waiting for a sunnier day is pointless
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

_LOGGER = logging.getLogger(__name__)

FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
PERFORMANCE_RATIO = 0.82     # inverter/temperature/wiring losses (pre-calibration)
HORIZON_DAYS = 4             # today + 3
CACHE_SECONDS = 900
APPLIANCE_KWH = 2.5          # surplus needed to comfortably run a big appliance
DEFAULT_BATTERY_KWH = 10.0   # assumed capacity when the plant doesn't report one

WEEKDAYS_DE = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]
WEEKDAYS_FULL_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                    "Freitag", "Samstag", "Sonntag"]


# ------------------------------------------------------------ solar model

def _declination(when_utc: datetime) -> float:
    n = when_utc.timetuple().tm_yday
    return math.radians(23.45) * math.sin(math.radians(360.0 * (284 + n) / 365.0))


def solar_elevation(lat: float, lon: float, when_utc: datetime) -> float:
    """Solar elevation angle in radians (NOAA-style approximation)."""
    decl = _declination(when_utc)
    solar_hour = (when_utc.hour + when_utc.minute / 60.0 + lon / 15.0) % 24
    hour_angle = math.radians(15.0 * (solar_hour - 12.0))
    lat_r = math.radians(lat)
    sin_el = (math.sin(lat_r) * math.sin(decl)
              + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
    return math.asin(max(-1.0, min(1.0, sin_el)))


def clear_sky_ghi(elevation_rad: float) -> float:
    """Haurwitz clear-sky global horizontal irradiance in W/m²."""
    s = math.sin(elevation_rad)
    if s <= 0.0:
        return 0.0
    return 1098.0 * s * math.exp(-0.057 / s)


def cloud_factor(clouds_pct: float, pop: float) -> float:
    f = 1.0 - 0.75 * (clouds_pct / 100.0) ** 3.4   # Kasten–Czeplak
    return f * (1.0 - 0.15 * (pop or 0.0))


def slot_yield_factor(lat: float, lon: float, slot_start_utc: datetime,
                      clouds_pct: float, pop: float, hours: float = 3.0,
                      not_before_utc: datetime | None = None,
                      boost=None) -> float:
    """Equivalent full-sun hours contributed by one forecast slot.

    not_before_utc – skip the part of the slot that already lies in the past
    boost(t_utc)   – optional multiplicative nowcast correction per step
    """
    total = 0.0
    steps = int(hours * 4)  # 15-min integration steps
    cf = cloud_factor(clouds_pct, pop)
    for i in range(steps):
        t = slot_start_utc + timedelta(hours=(i + 0.5) * hours / steps)
        if not_before_utc and t < not_before_utc:
            continue
        ghi = clear_sky_ghi(solar_elevation(lat, lon, t))
        if ghi <= 0:
            continue
        f = cf
        if boost is not None:
            f *= boost(t)
        total += (ghi / 1000.0) * f * (hours / steps)
    return total


def sun_times(lat: float, lon: float, day_utc: datetime,
              tz: timezone) -> tuple[datetime | None, datetime | None]:
    """(sunrise, sunset) as local datetimes for the given day (±15 min)."""
    decl = _declination(day_utc)
    lat_r = math.radians(lat)
    cos_ha = -math.tan(lat_r) * math.tan(decl)
    if cos_ha >= 1.0 or cos_ha <= -1.0:
        return None, None  # polar night / midnight sun
    ha_deg = math.degrees(math.acos(cos_ha))
    base = day_utc.replace(hour=0, minute=0, second=0, microsecond=0,
                           tzinfo=timezone.utc)

    def to_local(solar_hour: float) -> datetime:
        utc_h = solar_hour - lon / 15.0
        return (base + timedelta(hours=utc_h)).astimezone(tz)

    return to_local(12.0 - ha_deg / 15.0), to_local(12.0 + ha_deg / 15.0)


# ------------------------------------------------------------ plant meta

def _scan_number(obj, keys: tuple[str, ...]) -> float | None:
    if not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if k.lower() in keys:
            if isinstance(v, dict):
                v = v.get("value")
            try:
                f = float(v)
                if f != 0.0:
                    return f
            except (TypeError, ValueError):
                continue
    return None


def plant_location(plants: list[dict], details: dict) -> tuple[float, float] | None:
    for src in [*plants, *details.values()]:
        lat = _scan_number(src, ("latitude", "ps_latitude", "lat"))
        lon = _scan_number(src, ("longitude", "ps_longitude", "lon", "lng"))
        if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            return lat, lon
    return None


def plant_kwp(plants: list[dict], details: dict) -> float | None:
    for src in [*plants, *details.values()]:
        v = _scan_number(src, ("total_capcity", "total_capacity", "design_capacity",
                               "installed_power", "ps_capacity_kw"))
        if v:
            return v / 1000.0 if v > 1000 else v
    return None


# ------------------------------------------------------------ the advisor

class SolarAdvisor:
    def __init__(self, api_key: str, store, lat: float | None, lon: float | None,
                 state_path: Path | None = None):
        self.api_key = api_key.strip()
        self.store = store
        self.cfg_lat = lat
        self.cfg_lon = lon
        self.state_path = state_path
        self.calibration = 1.0     # learned plant factor (EWMA)
        self.cal_samples = 0
        self._load_state()
        self._cache: tuple[float, dict] | None = None
        self._session: aiohttp.ClientSession | None = None

    # ---------------------------------------------------- state handling

    def _load_state(self) -> None:
        if not self.state_path:
            return
        try:
            state = json.loads(self.state_path.read_text())
            self.calibration = float(state.get("factor", 1.0))
            self.cal_samples = int(state.get("samples", 0))
        except (OSError, ValueError):
            pass

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.write_text(json.dumps(
                {"factor": round(self.calibration, 4), "samples": self.cal_samples}))
        except OSError as err:
            _LOGGER.debug("Could not persist advisor state: %s", err)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _location(self) -> tuple[float, float] | None:
        if self.cfg_lat is not None and self.cfg_lon is not None:
            return self.cfg_lat, self.cfg_lon
        return plant_location(self.store.plants, self.store.plant_details)

    async def _fetch_forecast(self, lat: float, lon: float) -> dict:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        params = {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "appid": self.api_key,
                  "units": "metric", "lang": "de"}
        async with self._session.get(FORECAST_URL, params=params) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(
                    f"OpenWeather: {data.get('message', f'HTTP {resp.status}')}")
            return data

    async def advise(self) -> dict:
        if not self.api_key:
            return {"configured": False}
        if self._cache and time.time() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        loc = self._location()
        if not loc:
            return {"configured": True, "error":
                    "Kein Anlagen-Standort gefunden – bitte latitude/longitude "
                    "in den Add-on-Optionen setzen."}
        lat, lon = loc
        forecast = await self._fetch_forecast(lat, lon)
        result = self._evaluate(forecast, lat, lon)
        self._cache = (time.time(), result)
        return result

    # ------------------------------------------------------ live context

    def _live(self) -> dict:
        ps_id = str(self.store.plants[0].get("ps_id")) if self.store.plants else ""
        return self.store.overview(ps_id) if ps_id else {}

    def _battery_capacity_kwh(self) -> float:
        for ps_points in self.store.points.values():
            row = ps_points.get("13140")
            if row and isinstance(row.get("value"), (int, float)) and row["value"] > 500:
                return row["value"] / 1000.0
        for src in self.store.plant_details.values():
            v = _scan_number(src, ("battery_capacity",))
            if v:
                return v / 1000.0 if v > 500 else v
        return DEFAULT_BATTERY_KWH

    # ----------------------------------------------------------- scoring

    def _evaluate(self, forecast: dict, lat: float, lon: float) -> dict:
        tz_offset = int(forecast.get("city", {}).get("timezone", 0))
        tz = timezone(timedelta(seconds=tz_offset))
        now_local = datetime.now(tz)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        today = now_local.date()
        kwp = plant_kwp(self.store.plants, self.store.plant_details)
        live = self._live()

        # --- nowcast + calibration from the plant's live output ------------
        pv_now_w = live.get("pv_power_w") or 0.0
        slots_sorted = sorted(forecast.get("list", []), key=lambda s: s["dt"])
        current_slot = next((s for s in reversed(slots_sorted)
                             if s["dt"] <= now_utc.replace(tzinfo=timezone.utc).timestamp()),
                            slots_sorted[0] if slots_sorted else None)
        r_now = None
        if kwp and current_slot:
            ghi = clear_sky_ghi(solar_elevation(lat, lon, now_utc))
            cf = cloud_factor(float(current_slot.get("clouds", {}).get("all", 50)),
                              float(current_slot.get("pop", 0.0)))
            p_model_w = kwp * 1000.0 * PERFORMANCE_RATIO * (ghi / 1000.0) * cf * self.calibration
            if p_model_w > 0.08 * kwp * 1000.0:      # only in solid daylight
                r_now = max(0.3, min(2.0, pv_now_w / p_model_w))
                # EWMA calibration: learns systematic bias, ignores single clouds
                self.calibration = max(0.5, min(1.5,
                    0.93 * self.calibration + 0.07 * self.calibration * r_now))
                self.cal_samples += 1
                self._save_state()

        def nowcast_boost(t_utc: datetime) -> float:
            """Blend the live ratio into the next ~3 h, decaying to 1."""
            if r_now is None:
                return 1.0
            dt_h = max(0.0, (t_utc - now_utc).total_seconds() / 3600.0)
            w = math.exp(-dt_h / 1.5)
            return 1.0 + w * (r_now - 1.0)

        # --- integrate the forecast per day ---------------------------------
        days: dict = {}
        for slot in slots_sorted:
            start_utc = datetime.fromtimestamp(slot["dt"], tz=timezone.utc)
            local = start_utc.astimezone(tz)
            day_idx = (local.date() - today).days
            if not 0 <= day_idx < HORIZON_DAYS:
                continue
            clouds = float(slot.get("clouds", {}).get("all", 50))
            pop = float(slot.get("pop", 0.0))
            weather = (slot.get("weather") or [{}])[0]
            esh = slot_yield_factor(
                lat, lon, start_utc.replace(tzinfo=None), clouds, pop,
                not_before_utc=now_utc if day_idx == 0 else None,
                boost=nowcast_boost if day_idx == 0 else None)
            d = days.setdefault(day_idx, {
                "date": local.date(), "esh": 0.0, "slots": [],
                "pop_max": 0.0, "temp_max": None, "conditions": {},
            })
            d["esh"] += esh
            d["pop_max"] = max(d["pop_max"], pop)
            temp = slot.get("main", {}).get("temp")
            if temp is not None:
                d["temp_max"] = temp if d["temp_max"] is None else max(d["temp_max"], temp)
            if esh > 0.01:
                wid = int(weather.get("id", 800))
                d["conditions"][wid] = d["conditions"].get(wid, 0.0) + esh
            d["slots"].append({"hour": local.hour, "esh": esh})

        if not days:
            return {"configured": True, "error": "Vorhersage enthält keine nutzbaren Daten."}

        eff_kwp = (kwp or 0.0) * PERFORMANCE_RATIO * self.calibration

        def to_kwh(esh: float) -> float | None:
            return round(esh * eff_kwp, 1) if kwp else None

        # --- today's split: produced (real) + remaining (forecast) ----------
        produced_kwh = round((live.get("daily_yield_wh") or 0.0) / 1000.0, 1)
        remaining_kwh = to_kwh(days[0]["esh"]) if 0 in days else None
        _, sunset = sun_times(lat, lon, now_utc, tz)
        remaining_sun_h = None
        if sunset:
            remaining_sun_h = round(max(0.0, (sunset - now_local).total_seconds() / 3600.0), 1)

        # --- expected remaining consumption + battery headroom --------------
        elapsed_h = max(1.0, now_local.hour + now_local.minute / 60.0)
        daily_load_kwh = (live.get("daily_load_wh") or 0.0) / 1000.0
        avg_load_kw = daily_load_kwh / elapsed_h if daily_load_kwh else 0.35
        remaining_load_kwh = round(avg_load_kw * (24.0 - elapsed_h) * 1.05, 1)
        soc = live.get("battery_soc")
        battery_headroom_kwh = round(
            self._battery_capacity_kwh() * max(0.0, 100.0 - (soc if soc is not None else 50.0)) / 100.0, 1)
        surplus_kwh = None
        if remaining_kwh is not None:
            surplus_kwh = round(remaining_kwh - remaining_load_kwh - battery_headroom_kwh, 1)

        # --- day rows --------------------------------------------------------
        best_esh = max(d["esh"] for d in days.values()) or 1.0
        day_rows = []
        for idx in sorted(days):
            d = days[idx]
            wid = max(d["conditions"], key=d["conditions"].get) if d["conditions"] else 800
            est = to_kwh(d["esh"])
            row = {
                "index": idx,
                "date": d["date"].isoformat(),
                "label": "Heute" if idx == 0 else "Morgen" if idx == 1
                         else WEEKDAYS_DE[d["date"].weekday()],
                "label_full": "heute" if idx == 0 else "morgen" if idx == 1
                              else WEEKDAYS_FULL_DE[d["date"].weekday()],
                "est_kwh": est,
                "esh": round(d["esh"], 2),
                "rel": round(100.0 * d["esh"] / best_esh),
                "icon": _icon_for(wid),
                "pop": round(d["pop_max"] * 100),
                "temp_max": round(d["temp_max"]) if d["temp_max"] is not None else None,
                "window": _best_window(d["slots"]),
            }
            if idx == 0:
                row["produced_kwh"] = produced_kwh
                row["remaining_kwh"] = remaining_kwh
                if est is not None:
                    row["est_kwh"] = round(produced_kwh + est, 1)  # full-day total
            day_rows.append(row)

        context = {
            "produced_today_kwh": produced_kwh,
            "remaining_today_kwh": remaining_kwh,
            "remaining_sun_h": remaining_sun_h,
            "sunset": sunset.strftime("%H:%M") if sunset else None,
            "remaining_load_kwh": remaining_load_kwh,
            "battery_headroom_kwh": battery_headroom_kwh,
            "surplus_kwh": surplus_kwh,
            "calibration": round(self.calibration, 2),
            "cal_samples": self.cal_samples,
            "nowcast_ratio": round(r_now, 2) if r_now is not None else None,
        }
        verdict = self._verdict(day_rows, context, live)
        return {
            "configured": True,
            "location": {"lat": round(lat, 3), "lon": round(lon, 3),
                         "city": forecast.get("city", {}).get("name")},
            "kwp": kwp,
            "days": day_rows,
            "context": context,
            "verdict": verdict,
            "updated": time.time(),
        }

    def _verdict(self, day_rows: list[dict], ctx: dict, live: dict) -> dict:
        today = day_rows[0]
        best = max(day_rows, key=lambda d: d["esh"])
        soc = live.get("battery_soc")
        exporting = (live.get("grid_power_w") or 0) < -300
        surplus_now = exporting or ((live.get("pv_power_w") or 0) >
                                    (live.get("load_power_w") or 0) + 500)

        def kwh(v):
            return str(v).replace(".", ",") if v is not None else None

        sun_info = ""
        if ctx["remaining_sun_h"] and ctx["sunset"]:
            sun_info = (f" Bis Sonnenuntergang ({ctx['sunset']} Uhr, noch "
                        f"{kwh(ctx['remaining_sun_h'])} h Sonne) werden noch ca. "
                        f"{kwh(ctx['remaining_today_kwh'])} kWh erwartet.")

        # 1. the plant is running a surplus right now with a healthy battery
        if surplus_now and (soc is None or soc >= 60):
            extra = f" und die Batterie ist zu {round(soc)} % geladen" if soc is not None else ""
            return {"type": "now", "day_index": 0,
                    "headline": "Jetzt ist ein perfekter Zeitpunkt",
                    "message": f"Deine Anlage produziert gerade Überschuss{extra} – "
                               f"stromintensive Geräte am besten sofort einschalten.{sun_info}"}

        # 2. the remaining day already covers load + battery → no reason to wait
        if ctx["surplus_kwh"] is not None and ctx["surplus_kwh"] >= APPLIANCE_KWH:
            msg = (f"Der restliche Solartag deckt den Bedarf: noch ca. "
                   f"{kwh(ctx['remaining_today_kwh'])} kWh PV bis Sonnenuntergang "
                   f"({ctx['sunset']} Uhr) gegenüber ca. {kwh(ctx['remaining_load_kwh'])} kWh "
                   f"Restverbrauch und {kwh(ctx['battery_headroom_kwh'])} kWh Batterie-Ladebedarf – "
                   f"macht ca. {kwh(ctx['surplus_kwh'])} kWh Überschuss.")
            if today["window"]:
                msg += f" Beste Zeit: {today['window']}."
            return {"type": "today", "day_index": 0,
                    "headline": "Heute einschalten – der Überschuss reicht", "message": msg}

        # 3. today is (nearly) the best day of the horizon anyway
        if today["esh"] >= 0.85 * best["esh"] or today is best:
            msg = "Heute ist der beste Solartag der nächsten Tage." + sun_info
            if today["window"]:
                msg += f" Beste Zeit: {today['window']}."
            return {"type": "today", "day_index": 0,
                    "headline": "Heute einschalten", "message": msg}

        # 4. a clearly better day is coming
        gain = round(100.0 * (best["esh"] / max(today["esh"], 0.01) - 1.0))
        gain_txt = (f"voraussichtlich {gain} % mehr Solarstrom" if gain <= 400
                    else "ein Vielfaches an Solarstrom")
        rest = f" (heute Rest nur noch ca. {kwh(ctx['remaining_today_kwh'])} kWh)" \
            if ctx["remaining_today_kwh"] is not None else ""
        msg = (f"{best['label_full'].capitalize()} bringt {gain_txt} als der restliche "
               f"heutige Tag{rest}.")
        if best["est_kwh"] is not None:
            msg += f" Erwartet: ca. {kwh(best['est_kwh'])} kWh."
        if best["window"]:
            msg += f" Beste Zeit: {best['window']}."
        return {"type": "wait", "day_index": best["index"],
                "headline": f"Besser bis {best['label_full']} warten", "message": msg}


def _best_window(slots: list[dict]) -> str | None:
    daylight = [s for s in slots if s["esh"] > 0.02]
    if len(daylight) < 1:
        return None
    best_sum, best_i = -1.0, 0
    window = 2 if len(daylight) >= 2 else 1
    for i in range(len(daylight) - window + 1):
        s = sum(x["esh"] for x in daylight[i:i + window])
        if s > best_sum:
            best_sum, best_i = s, i
    start = daylight[best_i]["hour"]
    end = daylight[min(best_i + window - 1, len(daylight) - 1)]["hour"] + 3
    return f"{start}–{min(end, 23)} Uhr"


def _icon_for(weather_id: int) -> str:
    if weather_id >= 800:
        return {800: "sun", 801: "partly", 802: "partly"}.get(weather_id, "cloud")
    if weather_id >= 700:
        return "fog"
    if weather_id >= 600:
        return "snow"
    if weather_id >= 300:
        return "rain"
    return "storm"


# ------------------------------------------------------------ demo data

class DemoAdvisor:
    """Deterministic demo: rainy today, brilliant tomorrow → 'wait'."""

    def __init__(self, store):
        self.store = store
        self._real = SolarAdvisor("demo", store, 49.11, 9.74)

    async def close(self) -> None:
        await self._real.close()

    async def advise(self) -> dict:
        now = datetime.now()
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        profile = [(90, 0.8), (35, 0.15), (55, 0.3), (80, 0.6)]  # clouds %, pop
        slots = []
        for day in range(4):
            for hour in range(0, 24, 3):
                t = base + timedelta(days=day, hours=hour)
                if t < now - timedelta(hours=3):
                    continue
                clouds, pop = profile[day]
                slots.append({
                    "dt": int(t.timestamp()),
                    "clouds": {"all": clouds},
                    "pop": pop,
                    "main": {"temp": 16 + day * 2 + (6 if 12 <= hour <= 15 else 0)},
                    "weather": [{"id": 500 if pop > 0.5 else (800 if clouds < 40 else 803)}],
                })
        forecast = {"city": {"name": "Schwäbisch Hall (Demo)",
                             "timezone": -time.timezone + (3600 if time.daylight else 0)},
                    "list": slots}
        result = self._real._evaluate(forecast, 49.11, 9.74)
        result["demo"] = True
        return result
