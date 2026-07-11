"""Solar planner: combines the OpenWeather 3-hour forecast with a solar
position model to estimate PV yield for today + the next 3 days, and derives
a recommendation for running energy-intensive appliances (washing machine,
dryer, dishwasher, EV charging …) now, today, or on a better day.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone

import aiohttp

_LOGGER = logging.getLogger(__name__)

FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
PERFORMANCE_RATIO = 0.78     # system losses (inverter, temperature, wiring)
HORIZON_DAYS = 4             # today + 3
CACHE_SECONDS = 1800         # OpenWeather free tier friendly

WEEKDAYS_DE = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]
WEEKDAYS_FULL_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                    "Freitag", "Samstag", "Sonntag"]


# ------------------------------------------------------------ solar model

def solar_elevation(lat: float, lon: float, when_utc: datetime) -> float:
    """Solar elevation angle in radians (simple NOAA-style approximation)."""
    n = when_utc.timetuple().tm_yday
    decl = math.radians(23.45) * math.sin(math.radians(360.0 * (284 + n) / 365.0))
    # local solar time via longitude (equation of time omitted – ±15 min is
    # irrelevant when integrating 3-hour forecast slots)
    solar_hour = (when_utc.hour + when_utc.minute / 60.0 + lon / 15.0) % 24
    hour_angle = math.radians(15.0 * (solar_hour - 12.0))
    lat_r = math.radians(lat)
    sin_el = (math.sin(lat_r) * math.sin(decl)
              + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
    return math.asin(max(-1.0, min(1.0, sin_el)))


def slot_yield_factor(lat: float, lon: float, slot_start_utc: datetime,
                      clouds_pct: float, pop: float, hours: float = 3.0) -> float:
    """Equivalent full-sun hours contributed by one forecast slot."""
    total = 0.0
    # integrate in 30-min steps – 3 h slots are too coarse around sunrise/set
    steps = int(hours * 2)
    for i in range(steps):
        t = slot_start_utc + timedelta(hours=(i + 0.5) * hours / steps)
        sin_el = math.sin(solar_elevation(lat, lon, t))
        if sin_el <= 0:
            continue
        cloud_f = 1.0 - 0.75 * (clouds_pct / 100.0) ** 3.4   # Kasten–Czeplak
        rain_f = 1.0 - 0.15 * (pop or 0.0)
        total += sin_el * cloud_f * rain_f * (hours / steps)
    return total


# ------------------------------------------------------------ plant meta

def _scan_number(obj, keys: tuple[str, ...]) -> float | None:
    """Tolerantly pull a numeric value out of API dicts (fields vary by
    account: latitude/ps_latitude/…, values as str/number/{value,unit})."""
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
            if v > 1000:          # delivered in W
                return v / 1000.0
            return v
    return None


# ------------------------------------------------------------ the advisor

class SolarAdvisor:
    def __init__(self, api_key: str, store, lat: float | None, lon: float | None):
        self.api_key = api_key.strip()
        self.store = store
        self.cfg_lat = lat
        self.cfg_lon = lon
        self._cache: tuple[float, dict] | None = None
        self._session: aiohttp.ClientSession | None = None

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

    # ----------------------------------------------------------- scoring

    def _evaluate(self, forecast: dict, lat: float, lon: float) -> dict:
        tz_offset = int(forecast.get("city", {}).get("timezone", 0))
        tz = timezone(timedelta(seconds=tz_offset))
        now_local = datetime.now(tz)
        today = now_local.date()

        days: dict = {}
        for slot in forecast.get("list", []):
            start_utc = datetime.fromtimestamp(slot["dt"], tz=timezone.utc)
            local = start_utc.astimezone(tz)
            day_idx = (local.date() - today).days
            if not 0 <= day_idx < HORIZON_DAYS:
                continue
            clouds = float(slot.get("clouds", {}).get("all", 50))
            pop = float(slot.get("pop", 0.0))
            weather = (slot.get("weather") or [{}])[0]
            esh = slot_yield_factor(lat, lon, start_utc.replace(tzinfo=None), clouds, pop)
            d = days.setdefault(day_idx, {
                "date": local.date(), "esh": 0.0, "slots": [],
                "pop_max": 0.0, "temp_max": None, "conditions": {},
            })
            d["esh"] += esh
            d["pop_max"] = max(d["pop_max"], pop)
            temp = slot.get("main", {}).get("temp")
            if temp is not None:
                d["temp_max"] = temp if d["temp_max"] is None else max(d["temp_max"], temp)
            if esh > 0.01:  # daylight slots decide the day's weather badge
                wid = int(weather.get("id", 800))
                d["conditions"][wid] = d["conditions"].get(wid, 0.0) + esh
            d["slots"].append({"hour": local.hour, "esh": esh})

        if not days:
            return {"configured": True, "error": "Vorhersage enthält keine nutzbaren Daten."}

        kwp = plant_kwp(self.store.plants, self.store.plant_details)
        best_esh = max(d["esh"] for d in days.values()) or 1.0

        day_rows = []
        for idx in sorted(days):
            d = days[idx]
            # today's already-passed slots are missing from the forecast → for
            # a fair comparison judge today by what is *left* of the day
            wid = max(d["conditions"], key=d["conditions"].get) if d["conditions"] else 800
            day_rows.append({
                "index": idx,
                "date": d["date"].isoformat(),
                "label": "Heute" if idx == 0 else "Morgen" if idx == 1
                         else WEEKDAYS_DE[d["date"].weekday()],
                "label_full": "heute" if idx == 0 else "morgen" if idx == 1
                              else WEEKDAYS_FULL_DE[d["date"].weekday()],
                "est_kwh": round(d["esh"] * kwp * PERFORMANCE_RATIO, 1) if kwp else None,
                "esh": round(d["esh"], 2),
                "rel": round(100.0 * d["esh"] / best_esh),
                "icon": _icon_for(wid),
                "pop": round(d["pop_max"] * 100),
                "temp_max": round(d["temp_max"]) if d["temp_max"] is not None else None,
                "window": _best_window(d["slots"]),
            })

        verdict = self._verdict(day_rows)
        return {
            "configured": True,
            "location": {"lat": round(lat, 3), "lon": round(lon, 3),
                         "city": forecast.get("city", {}).get("name")},
            "kwp": kwp,
            "days": day_rows,
            "verdict": verdict,
            "updated": time.time(),
        }

    def _verdict(self, day_rows: list[dict]) -> dict:
        today = day_rows[0]
        best = max(day_rows, key=lambda d: d["esh"])
        ps_id = str(self.store.plants[0].get("ps_id")) if self.store.plants else ""
        kpis = self.store.overview(ps_id) if ps_id else {}

        soc = kpis.get("battery_soc")
        exporting = (kpis.get("grid_power_w") or 0) < -300
        surplus_now = exporting or ((kpis.get("pv_power_w") or 0) >
                                    (kpis.get("load_power_w") or 0) + 500)

        def kwh_txt(d):
            return f"ca. {str(d['est_kwh']).replace('.', ',')} kWh" if d["est_kwh"] is not None else None

        # 1. right now the plant runs a surplus and the battery is comfortable
        if surplus_now and (soc is None or soc >= 60):
            extra = f" und die Batterie ist zu {round(soc)} % geladen" if soc is not None else ""
            return {"type": "now", "day_index": 0,
                    "headline": "Jetzt ist ein perfekter Zeitpunkt",
                    "message": f"Deine Anlage produziert gerade Überschuss{extra} – "
                               "stromintensive Geräte am besten sofort einschalten."}

        # 2. today is (nearly) the best day of the horizon
        if today["esh"] >= 0.85 * best["esh"] or today is best:
            kwh = kwh_txt(today)
            msg = "Heute ist der beste Solartag der nächsten Tage"
            if kwh:
                msg += f" ({kwh} erwartet)"
            if today["window"]:
                msg += f". Beste Zeit: {today['window']}."
            else:
                msg += "."
            return {"type": "today", "day_index": 0,
                    "headline": "Heute einschalten", "message": msg}

        # 3. a clearly better day is coming
        gain = round(100.0 * (best["esh"] / max(today["esh"], 0.01) - 1.0))
        gain_txt = f"voraussichtlich {gain} % mehr Solarstrom" if gain <= 400 else \
                   "ein Vielfaches an Solarstrom"
        kwh_best, kwh_today = kwh_txt(best), kwh_txt(today)
        detail = f" ({kwh_best} statt {kwh_today})" if kwh_best and kwh_today else ""
        msg = f"{best['label_full'].capitalize()} bringt {gain_txt} als heute{detail}."
        if best["window"]:
            msg += f" Beste Zeit: {best['window']}."
        return {"type": "wait", "day_index": best["index"],
                "headline": f"Besser bis {best['label_full']} warten", "message": msg}


def _best_window(slots: list[dict]) -> str | None:
    """Best contiguous ~4 h production window, e.g. '11–15 Uhr'."""
    daylight = [s for s in slots if s["esh"] > 0.02]
    if len(daylight) < 1:
        return None
    best_sum, best_i = -1.0, 0
    window = 2 if len(daylight) >= 2 else 1  # 2 × 3h-Slots ≈ 6 h → gerundet 4-6 h
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
