"""FastAPI server: REST API for the dashboard + static frontend.

All frontend URLs are relative so the app works both standalone and behind
Home Assistant ingress (/api/hassio_ingress/<token>/...).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from catalog import HISTORY_DEFAULT_POINTS, apply_transform, display_name, meta_for
from config import settings
from isolarcloud import ISolarCloudClient, ISolarCloudError
from mqtt_publisher import MqttPublisher
from poller import Poller
from store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
_LOGGER = logging.getLogger("sungrowfarm")

STATIC_DIR = Path(__file__).parent / "static"

store = Store(lang=settings.language)
mqtt = MqttPublisher(store)

_TOKEN_FILE = Path(settings.data_dir) / "oauth_tokens.json"


def _token_store(tokens: dict | None) -> dict | None:
    """load (None) / save (dict) persisted OAuth tokens."""
    if tokens is None:
        try:
            return json.loads(_TOKEN_FILE.read_text())
        except (OSError, ValueError):
            return None
    try:
        _TOKEN_FILE.write_text(json.dumps(tokens))
    except OSError as err:
        _LOGGER.warning("Could not persist OAuth tokens: %s", err)
    return tokens


if settings.demo_mode or not settings.configured:
    from demo import DemoClient
    client = DemoClient()
    if not settings.demo_mode and not settings.configured:
        _LOGGER.warning("No credentials configured – running in demo mode. "
                        "Set appkey/secret_key/username/password in the add-on options.")
else:
    client = ISolarCloudClient(
        region=settings.region,
        appkey=settings.appkey,
        secret_key=settings.secret_key,
        username=settings.username,
        password=settings.password,
        lang=settings.api_lang,
        rsa_public_key=settings.rsa_public_key,
        app_id=settings.app_id,
        token_store=_token_store,
    )

poller = Poller(client, store, mqtt)
_history_cache: dict[str, tuple[float, dict]] = {}

if settings.demo_mode or not settings.configured:
    from advisor import DemoAdvisor
    advisor = DemoAdvisor(store)
else:
    from advisor import SolarAdvisor
    advisor = SolarAdvisor(settings.openweather_api_key, store,
                           settings.latitude, settings.longitude,
                           state_path=Path(settings.data_dir) / "advisor_state.json")
    if settings.openweather_api_key.strip():
        _LOGGER.info("Solar planner enabled (OpenWeather key …%s)",
                     settings.openweather_api_key.strip()[-4:])
    else:
        _LOGGER.info("Solar planner disabled – no OpenWeather API key configured")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(poller.run()), asyncio.create_task(mqtt.run())]
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await client.close()
    await advisor.close()


app = FastAPI(title="Sungrow iSolarCloud", lifespan=lifespan)


def _demo_active() -> bool:
    return settings.demo_mode or not settings.configured


@app.get("/api/status")
async def api_status():
    profile = getattr(client, "profile", None)
    return {
        "configured": settings.configured,
        "demo_mode": _demo_active(),
        "region": settings.region,
        "language": settings.language,
        "poll_interval": settings.poll_interval,
        "login_ok": store.login_ok,
        "mqtt": {"enabled": settings.mqtt_enabled, "connected": store.mqtt_connected},
        "last_success": store.last_success,
        "last_error": store.last_error,
        "last_error_ts": store.last_error_ts,
        "poll_count": store.poll_count,
        "server_time": time.time(),
        "api_profile": profile.as_dict() if profile else None,
        "negotiation": getattr(client, "last_negotiation", []),
        "rsa_configured": bool(settings.rsa_public_key.strip()),
        "app_id_configured": bool(settings.app_id.strip()),
        "has_oauth": getattr(client, "has_oauth", False),
    }


@app.get("/api/advisor")
async def api_advisor():
    try:
        return await advisor.advise()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Advisor failed: %s", err)
        return {"configured": True, "error": str(err)}


@app.post("/api/diagnose")
async def api_diagnose():
    if _demo_active():
        return {"results": [], "demo": True}
    try:
        results = await client.diagnose()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(500, f"Diagnose fehlgeschlagen: {err}") from err
    return {"results": results, "demo": False}


@app.get("/api/oauth/url")
async def api_oauth_url(redirect_uri: str):
    if _demo_active():
        raise HTTPException(400, "Demo-Modus aktiv")
    if not settings.app_id.strip():
        raise HTTPException(400, "app_id ist nicht konfiguriert (Add-on-Optionen)")
    return {"url": client.oauth_authorize_url(redirect_uri)}


@app.post("/api/oauth/code")
async def api_oauth_code(payload: dict = Body(...)):
    if _demo_active():
        raise HTTPException(400, "Demo-Modus aktiv")
    code = (payload.get("code") or "").strip()
    redirect_uri = (payload.get("redirect_uri") or "").strip()
    # allow pasting the full redirect URL instead of the bare code
    if "code=" in code:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(code).query)
        code = (q.get("code") or [""])[0]
    if not code:
        raise HTTPException(400, "Kein Autorisierungs-Code angegeben")
    try:
        await client.oauth_exchange_code(code, redirect_uri)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, str(err)) from err
    return {"ok": True}


@app.get("/api/plants")
async def api_plants():
    return {"plants": store.plants}


def _resolve_ps_id(ps_id: str | None) -> str:
    if ps_id:
        return str(ps_id)
    if store.plants:
        return str(store.plants[0].get("ps_id"))
    raise HTTPException(503, "No plant data yet – waiting for first poll")


@app.get("/api/overview")
async def api_overview(ps_id: str | None = None):
    pid = _resolve_ps_id(ps_id)
    plant = next((p for p in store.plants if str(p.get("ps_id")) == pid), None)
    return {
        "ps_id": pid,
        "plant": plant,
        "kpis": store.overview(pid),
        "updated": store.last_success,
    }


@app.get("/api/devices")
async def api_devices(ps_id: str | None = None):
    pid = _resolve_ps_id(ps_id)
    return {"ps_id": pid, "devices": store.devices.get(pid, [])}


@app.get("/api/points")
async def api_points(ps_id: str | None = None, include_empty: bool = False):
    pid = _resolve_ps_id(ps_id)
    points = sorted(store.points.get(pid, {}).values(),
                    key=lambda r: (r["group"], r["name"]))
    if not include_empty:
        # points the plant never delivers (utility-ESS-only, meteo station …)
        # are noise in the browser and get no MQTT sensor either
        points = [p for p in points if p["value"] is not None]
    return {"ps_id": pid, "points": points}


# The minute-data endpoint rejects long time spans ("[010] query time interval
# exceeds the maximum limit"). The allowed maximum is undocumented, so we learn
# it: on a span error the window is halved (and remembered for future calls).
_hist_max_span_s: float | None = None
_HIST_MAX_REQUESTS = 24


def _is_span_error(err: ISolarCloudError) -> bool:
    msg = (err.message or "").lower()
    return err.code == "010" and ("interval" in msg or "maximum" in msg or "limit" in msg)


async def _fetch_history_chunked(fetch_rows, start: datetime, end: datetime) -> list[dict]:
    """Run `fetch_rows(s, e) -> list[rows]` over the range, splitting windows
    that exceed the (learned) span limit."""
    global _hist_max_span_s
    rows: list[dict] = []
    requests_made = 0

    async def fetch(s: datetime, e: datetime, depth: int = 0) -> None:
        nonlocal requests_made
        global _hist_max_span_s
        if requests_made >= _HIST_MAX_REQUESTS:
            _LOGGER.warning("History request budget exhausted – returning partial range")
            return
        # a sibling window may already have taught us the limit – split first
        if (_hist_max_span_s and depth < 6
                and (e - s).total_seconds() > _hist_max_span_s + 1):
            mid = s + (e - s) / 2
            await fetch(s, mid, depth + 1)
            await fetch(mid, e, depth + 1)
            return
        requests_made += 1
        try:
            rows.extend(await fetch_rows(s, e))
        except ISolarCloudError as err:
            if _is_span_error(err) and depth < 6 and (e - s) > timedelta(hours=1):
                _hist_max_span_s = (e - s).total_seconds() / 2
                mid = s + (e - s) / 2
                await fetch(s, mid, depth + 1)
                await fetch(mid, e, depth + 1)
            else:
                raise

    # pre-chunk with the learned limit, newest windows first
    span = timedelta(seconds=_hist_max_span_s) if _hist_max_span_s else (end - start)
    windows: list[tuple[datetime, datetime]] = []
    w_end = end
    while w_end > start:
        w_start = max(start, w_end - span)
        windows.append((w_start, w_end))
        w_end = w_start
    for w_start, w_end in windows:
        await fetch(w_start, w_end)
    return rows


# Virtual chart series: signed grid/battery power. Which source feeds them
# depends on what the plant delivers – plant-level point if it has live data,
# otherwise composed from the ESS device points (positive − negative).
VIRTUAL_SERIES = {
    "grid": {"plant": "83549", "pos": "13149", "neg": "13121",
             "name_de": "Netz", "name_en": "Grid"},
    "battery": {"plant": "83238", "pos": "13126", "neg": "13150",
                "name_de": "Batterie", "name_en": "Battery"},
}


def _combine_signed(pos: list, neg: list) -> list:
    m: dict[float, float] = {}
    for t, v in pos:
        m[t] = m.get(t, 0.0) + v
    for t, v in neg:
        m[t] = m.get(t, 0.0) - v
    return sorted([t, round(v, 1)] for t, v in m.items())


@app.get("/api/history")
async def api_history(
    ps_id: str | None = None,
    points: str = Query(default=",".join(HISTORY_DEFAULT_POINTS)),
    hours: int = Query(default=24, ge=1, le=168),
    interval: int = Query(default=5, ge=1, le=60),
):
    pid = _resolve_ps_id(ps_id)
    requested = [p.strip() for p in points.split(",") if p.strip()]
    cache_key = f"{pid}:{points}:{hours}:{interval}"
    cached = _history_cache.get(cache_key)
    if cached and time.time() - cached[0] < 240:
        return cached[1]

    # plan the fetches: which real points feed which requested series
    plant_ids: list[str] = []
    device_ids: list[str] = []
    virtual_source: dict[str, tuple[str, dict]] = {}
    for p in requested:
        if p in VIRTUAL_SERIES:
            v = VIRTUAL_SERIES[p]
            if store.value(pid, v["plant"]) is not None:
                plant_ids.append(v["plant"])
                virtual_source[p] = ("plant", v)
            else:
                device_ids.extend([v["pos"], v["neg"]])
                virtual_source[p] = ("device", v)
        elif p.startswith("13"):
            device_ids.append(p)
        else:
            plant_ids.append(p)

    end = datetime.now()
    start = end - timedelta(hours=hours)
    rows: list[dict] = []
    try:
        if plant_ids:
            async def fetch_plant(s, e):
                result = await client.get_minute_history(
                    pid, plant_ids, s, e, minute_interval=interval)
                return client.parse_point_rows(result)
            rows += await _fetch_history_chunked(fetch_plant, start, end)
    except ISolarCloudError as err:
        raise HTTPException(502, f"iSolarCloud: {err}") from err

    bdev = store.battery_device.get(pid)
    if device_ids and bdev and hasattr(client, "get_device_minute_history"):
        try:
            async def fetch_device(s, e):
                result = await client.get_device_minute_history(
                    bdev["ps_key"], device_ids, s, e, minute_interval=interval)
                return client.parse_point_rows(result)
            drows = await _fetch_history_chunked(fetch_device, start, end)
            if (store.device_unit_mode or "kw") == "kw":
                from poller import _DEVICE_ENERGY_IDS, _DEVICE_POWER_IDS
                for r in drows:
                    if (r["point_id"] in _DEVICE_POWER_IDS
                            or r["point_id"] in _DEVICE_ENERGY_IDS) \
                            and isinstance(r.get("value"), (int, float)):
                        r["value"] = round(r["value"] * 1000.0, 1)
            rows += drows
        except ISolarCloudError as err:
            _LOGGER.warning("Device history unavailable: %s", err)

    # bucket rows by point id
    data_by_point: dict[str, list] = {}
    for row in rows:
        pid_point = str(row["point_id"])
        ts = row.get("timestamp")
        if not ts or row["value"] is None:
            continue
        try:
            t = datetime.strptime(str(ts), "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            continue
        data_by_point.setdefault(pid_point, []).append(
            [t, apply_transform(meta_for(pid_point), row["value"])])
    for data in data_by_point.values():
        data.sort(key=lambda x: x[0])
        # chunked fetching can produce duplicate boundary samples
        data[:] = [d for i, d in enumerate(data) if i == 0 or d[0] != data[i - 1][0]]

    # assemble exactly the requested series (virtuals synthesised)
    lang = settings.language
    out_series = []
    for p in requested:
        if p in VIRTUAL_SERIES:
            src_kind, v = virtual_source[p]
            data = (data_by_point.get(v["plant"], []) if src_kind == "plant"
                    else _combine_signed(data_by_point.get(v["pos"], []),
                                         data_by_point.get(v["neg"], [])))
            out_series.append({
                "point_id": p,
                "name": v["name_de"] if lang == "de" else v["name_en"],
                "unit": "W",
                "data": data,
            })
        else:
            out_series.append({
                "point_id": p,
                "name": display_name(p, lang),
                "unit": (meta_for(p).unit if meta_for(p) else None),
                "data": data_by_point.get(p, []),
            })

    payload = {"ps_id": pid, "series": out_series,
               "start": start.timestamp(), "end": end.timestamp()}
    _history_cache[cache_key] = (time.time(), payload)
    if len(_history_cache) > 50:
        oldest = min(_history_cache, key=lambda k: _history_cache[k][0])
        _history_cache.pop(oldest, None)
    return payload


@app.get("/")
async def index():
    # the HTML must never be cached – asset URLs carry the version and would
    # otherwise point at stale JS/CSS after an add-on update (ingress caches)
    return FileResponse(STATIC_DIR / "index.html",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
