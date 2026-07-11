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
                           settings.latitude, settings.longitude)


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


async def _fetch_history_chunked(pid: str, point_ids: list[str],
                                 start: datetime, end: datetime,
                                 interval: int) -> list[dict]:
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
            result = await client.get_minute_history(pid, point_ids, s, e,
                                                     minute_interval=interval)
            rows.extend(client.parse_point_rows(result))
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


@app.get("/api/history")
async def api_history(
    ps_id: str | None = None,
    points: str = Query(default=",".join(HISTORY_DEFAULT_POINTS)),
    hours: int = Query(default=24, ge=1, le=168),
    interval: int = Query(default=5, ge=1, le=60),
):
    pid = _resolve_ps_id(ps_id)
    point_ids = [p.strip() for p in points.split(",") if p.strip()]
    cache_key = f"{pid}:{points}:{hours}:{interval}"
    cached = _history_cache.get(cache_key)
    if cached and time.time() - cached[0] < 240:
        return cached[1]

    end = datetime.now()
    start = end - timedelta(hours=hours)
    try:
        rows = await _fetch_history_chunked(pid, point_ids, start, end, interval)
    except ISolarCloudError as err:
        raise HTTPException(502, f"iSolarCloud: {err}") from err
    series: dict[str, dict] = {}
    for row in rows:
        pid_point = str(row["point_id"])
        s = series.setdefault(pid_point, {
            "point_id": pid_point,
            "name": display_name(pid_point, settings.language),
            "unit": (meta_for(pid_point).unit if meta_for(pid_point) else None),
            "data": [],
        })
        ts = row.get("timestamp")
        if ts and row["value"] is not None:
            try:
                t = datetime.strptime(str(ts), "%Y%m%d%H%M%S").timestamp()
            except ValueError:
                continue
            s["data"].append([t, apply_transform(meta_for(pid_point), row["value"])])
    for s in series.values():
        s["data"].sort(key=lambda x: x[0])
        # chunked fetching can produce duplicate boundary samples
        s["data"] = [d for i, d in enumerate(s["data"])
                     if i == 0 or d[0] != s["data"][i - 1][0]]

    payload = {"ps_id": pid, "series": list(series.values()),
               "start": start.timestamp(), "end": end.timestamp()}
    _history_cache[cache_key] = (time.time(), payload)
    if len(_history_cache) > 50:
        oldest = min(_history_cache, key=lambda k: _history_cache[k][0])
        _history_cache.pop(oldest, None)
    return payload


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
