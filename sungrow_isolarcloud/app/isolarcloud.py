"""Async client for the Sungrow iSolarCloud OpenAPI (developer API).

Sungrow ships (at least) two API flavours, and which one an application may
use depends on how/when it was created in the developer portal:

* **openapi** family – ``/openapi/getPowerStationList`` etc. Session token
  from ``/openapi/login`` (account credentials), sent as ``token`` header.
  Newer applications additionally require the *secured* transport: body
  AES-128-ECB encrypted with a random per-request key, that key RSA-encrypted
  in the ``x-random-secret-key`` header, plus an ``api_key_param``
  (nonce/timestamp) in the body; responses come back hex-encoded.

* **platform** family – ``/openapi/platform/queryPowerStationList`` etc.,
  authenticated with ``Authorization: Bearer <token>``. The token is either
  the account-login token or an OAuth2 access token obtained by authorising
  the application on the iSolarCloud web UI (authorized-app page) and
  exchanging the code at ``/openapi/apiManage/token``.

Because there is no reliable way to know up front which combination an
application is entitled to (the gateway answers ``E900 unauthorized access``
for the wrong one), the client *negotiates*: it tries all sensible profiles
once and locks onto the first one that can list plants. ``diagnose()`` runs
the full matrix and reports per-profile results for the UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable

import aiohttp

from crypto_util import (
    aes_decrypt_ecb_hex,
    aes_encrypt_ecb_hex,
    parse_rsa_public_key,
    random_key,
    random_nonce,
    rsa_encrypt_pkcs1_b64,
)

_LOGGER = logging.getLogger(__name__)

GATEWAYS = {
    "china": "https://gateway.isolarcloud.com",
    "international": "https://gateway.isolarcloud.com.hk",
    "eu": "https://gateway.isolarcloud.eu",
    "australia": "https://augateway.isolarcloud.com",
}
AUTH_WEB = {
    "china": ("web3.isolarcloud.com", 1),
    "international": ("web3.isolarcloud.com.hk", 2),
    "eu": ("web3.isolarcloud.eu", 3),
    "australia": ("auweb3.isolarcloud.com", 7),
}

_TOKEN_ERROR_CODES = {"E00003", "010", "E900", "er_invalid_token", "er_token_login_invalid", "401"}
_TS_FORMAT = "%Y%m%d%H%M%S"


class ISolarCloudError(Exception):
    def __init__(self, code: str, message: str, endpoint: str = ""):
        super().__init__(f"{endpoint}: [{code}] {message}")
        self.code = code
        self.message = message
        self.endpoint = endpoint


class Profile:
    """One way of talking to the gateway."""

    def __init__(self, pid: str, family: str, auth: str, encrypted: bool, label: str):
        self.id = pid
        self.family = family        # "openapi" | "platform"
        self.auth = auth            # "account" | "account-bearer" | "oauth"
        self.encrypted = encrypted
        self.label = label

    def as_dict(self) -> dict:
        return {"id": self.id, "family": self.family, "auth": self.auth,
                "encrypted": self.encrypted, "label": self.label}


class ISolarCloudClient:
    def __init__(self, region: str, appkey: str, secret_key: str,
                 username: str, password: str, lang: str = "_en_US",
                 rsa_public_key: str = "", app_id: str = "",
                 token_store: Callable[[dict | None], dict | None] | None = None):
        self.region = region
        self.base_url = GATEWAYS.get(region, GATEWAYS["eu"])
        self.appkey = appkey
        self.secret_key = secret_key
        self.username = username
        self.password = password
        self.lang = lang
        self.app_id = app_id.strip()
        self._rsa = parse_rsa_public_key(rsa_public_key) if rsa_public_key.strip() else None
        if self._rsa:
            _LOGGER.info("RSA public key configured – secured transport available")
        # token_store(None) loads persisted OAuth tokens, token_store(dict) saves them
        self._token_store = token_store
        self._oauth: dict | None = token_store(None) if token_store else None
        self._account_token: str | None = None
        self.profile: Profile | None = None
        self.last_negotiation: list[dict] = []
        self._session: aiohttp.ClientSession | None = None
        self._auth_lock = asyncio.Lock()

    # ------------------------------------------------------------- session

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "SungrowFarm-HA-Addon/1.2"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------ profiles

    def _candidate_profiles(self) -> list[Profile]:
        c: list[Profile] = []
        if self._oauth:
            c.append(Profile("platform-oauth", "platform", "oauth", False,
                             "Platform-API mit OAuth-Token"))
        if self._rsa:
            c.append(Profile("openapi-secured", "openapi", "account", True,
                             "Klassische API, verschlüsselt (RSA/AES)"))
        c.append(Profile("openapi-plain", "openapi", "account", False,
                         "Klassische API, unverschlüsselt"))
        c.append(Profile("platform-account", "platform", "account-bearer", False,
                         "Platform-API mit Konto-Token (Bearer)"))
        if self._rsa:
            c.append(Profile("platform-secured", "platform", "account-bearer", True,
                             "Platform-API mit Konto-Token, verschlüsselt"))
        return c

    async def ensure_profile(self) -> Profile:
        if self.profile:
            return self.profile
        async with self._auth_lock:
            if self.profile:
                return self.profile
            attempts = []
            for prof in self._candidate_profiles():
                try:
                    plants = await self._get_plants_with(prof)
                    attempts.append({"profile": prof.as_dict(), "ok": True,
                                     "detail": f"{len(plants)} Anlage(n) gefunden"})
                    self.profile = prof
                    self.last_negotiation = attempts
                    _LOGGER.info("Negotiated API profile: %s", prof.id)
                    return prof
                except ISolarCloudError as err:
                    attempts.append({"profile": prof.as_dict(), "ok": False,
                                     "code": err.code, "detail": err.message,
                                     "endpoint": err.endpoint})
                except Exception as err:  # noqa: BLE001
                    attempts.append({"profile": prof.as_dict(), "ok": False,
                                     "code": type(err).__name__, "detail": str(err)[:200]})
            self.last_negotiation = attempts
            summary = "; ".join(
                f"{a['profile']['id']}→{a.get('code', '?')}" for a in attempts)
            raise ISolarCloudError("no_working_profile",
                                   f"Keine API-Variante akzeptiert ({summary})", "negotiation")

    async def diagnose(self) -> list[dict]:
        """Try every candidate profile and report the outcome of each."""
        results = []
        for prof in self._candidate_profiles():
            row = {"profile": prof.as_dict()}
            try:
                self._account_token = None  # force fresh login per profile
                plants = await self._get_plants_with(prof)
                row.update(ok=True, detail=f"{len(plants)} Anlage(n) gefunden")
                if not self.profile:
                    self.profile = prof
            except ISolarCloudError as err:
                row.update(ok=False, code=err.code, detail=err.message, endpoint=err.endpoint)
            except Exception as err:  # noqa: BLE001
                row.update(ok=False, code=type(err).__name__, detail=str(err)[:200])
            results.append(row)
        self.last_negotiation = results
        return results

    def reset_profile(self) -> None:
        self.profile = None
        self._account_token = None

    # ------------------------------------------------------------- request

    async def _post(self, path: str, payload: dict, prof: Profile,
                    with_auth: bool = True) -> dict:
        session = await self._get_session()
        headers = {
            "Content-Type": "application/json",
            "x-access-key": self.secret_key,
            "sys_code": "901",
        }
        body: dict[str, Any] = {**payload, "appkey": self.appkey, "lang": self.lang}

        if with_auth:
            if prof.auth == "account":
                headers["token"] = self._account_token or ""
                body["token"] = self._account_token or ""
            elif prof.auth == "account-bearer":
                headers["Authorization"] = f"Bearer {self._account_token or ''}"
            elif prof.auth == "oauth":
                headers["Authorization"] = f"Bearer {await self._oauth_access_token()}"

        aes_key: str | None = None
        if prof.encrypted and self._rsa:
            aes_key = random_key(16)
            headers["x-random-secret-key"] = rsa_encrypt_pkcs1_b64(aes_key, *self._rsa)
            body["api_key_param"] = {
                "nonce": random_nonce(32),
                "timestamp": str(int(time.time() * 1000)),
            }
            kwargs = {"data": aes_encrypt_ecb_hex(json.dumps(body), aes_key)}
        else:
            body["sys_code"] = "901"
            kwargs = {"json": body}

        async with session.post(f"{self.base_url}{path}", headers=headers, **kwargs) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise ISolarCloudError(str(resp.status), f"HTTP: {text[:300]}", path)

        data = self._parse_response(text, aes_key, path)
        code = str(data.get("result_code", ""))
        if code != "1":
            raise ISolarCloudError(code or "unknown",
                                   str(data.get("result_msg", data))[:300], path)
        return data.get("result_data") or {}

    @staticmethod
    def _parse_response(text: str, aes_key: str | None, path: str) -> dict:
        stripped = text.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
        if aes_key:
            try:
                return json.loads(aes_decrypt_ecb_hex(stripped, aes_key))
            except (ValueError, json.JSONDecodeError) as err:
                raise ISolarCloudError("decrypt_failed",
                                       f"Antwort nicht entschlüsselbar: {err}", path) from err
        raise ISolarCloudError("bad_response", f"Unerwartete Antwort: {stripped[:200]}", path)

    async def _request(self, path: str, payload: dict) -> dict:
        """Request via the negotiated profile, with one re-auth retry."""
        prof = await self.ensure_profile()
        try:
            return await self._post(path, payload, prof)
        except ISolarCloudError as err:
            if err.code in _TOKEN_ERROR_CODES:
                _LOGGER.info("Auth rejected (%s) – refreshing credentials", err.code)
                self._account_token = None
                if prof.auth in ("account", "account-bearer"):
                    await self._login(prof)
                elif prof.auth == "oauth":
                    await self._oauth_refresh()
                return await self._post(path, payload, prof)
            raise

    # ---------------------------------------------------------------- auth

    async def _login(self, prof: Profile) -> None:
        result = await self._post("/openapi/login", {
            "user_account": self.username,
            "user_password": self.password,
            "login_type": "1",
        }, prof, with_auth=False)
        state = str(result.get("login_state", "1"))
        if state != "1":
            raise ISolarCloudError(f"login_state_{state}",
                                   str(result.get("msg", "Login abgelehnt")), "/openapi/login")
        self._account_token = result.get("token")
        if not self._account_token:
            raise ISolarCloudError("no_token", "Login ok, aber kein Token erhalten", "/openapi/login")
        _LOGGER.info("Logged in to iSolarCloud as %s", result.get("user_name", self.username))

    async def _ensure_account_token(self, prof: Profile) -> None:
        if not self._account_token:
            await self._login(prof)

    # ---- OAuth2 ----------------------------------------------------------

    def oauth_authorize_url(self, redirect_uri: str) -> str:
        host, cloud_id = AUTH_WEB.get(self.region, AUTH_WEB["eu"])
        from urllib.parse import quote_plus
        return (f"https://{host}/#/authorized-app?cloudId={cloud_id}"
                f"&applicationId={self.app_id}&redirectUrl={quote_plus(redirect_uri)}")

    async def oauth_exchange_code(self, code: str, redirect_uri: str) -> dict:
        session = await self._get_session()
        headers = {"x-access-key": self.secret_key, "Content-Type": "application/json"}
        body = {"appkey": self.appkey, "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect_uri}
        async with session.post(f"{self.base_url}/openapi/apiManage/token",
                                json=body, headers=headers) as resp:
            data = await resp.json(content_type=None)
        rd = data.get("result_data") or data
        if "access_token" not in rd:
            raise ISolarCloudError(str(data.get("result_code", "oauth_failed")),
                                   f"Token-Tausch fehlgeschlagen: {str(data)[:300]}",
                                   "/openapi/apiManage/token")
        self._store_oauth(rd)
        self.reset_profile()
        return rd

    async def _oauth_refresh(self) -> None:
        if not self._oauth or not self._oauth.get("refresh_token"):
            raise ISolarCloudError("oauth_missing", "Keine OAuth-Tokens vorhanden", "oauth")
        session = await self._get_session()
        headers = {"x-access-key": self.secret_key, "Content-Type": "application/json"}
        body = {"appkey": self.appkey, "refresh_token": self._oauth["refresh_token"]}
        async with session.post(f"{self.base_url}/openapi/apiManage/refreshToken",
                                json=body, headers=headers) as resp:
            data = await resp.json(content_type=None)
        rd = data.get("result_data") or data
        if "access_token" not in rd:
            raise ISolarCloudError("oauth_refresh_failed", str(data)[:300],
                                   "/openapi/apiManage/refreshToken")
        self._store_oauth(rd)

    def _store_oauth(self, rd: dict) -> None:
        self._oauth = {
            "access_token": rd["access_token"],
            "refresh_token": rd.get("refresh_token", ""),
            "expires_at": time.time() + float(rd.get("expires_in", 3600)) - 30,
        }
        if self._token_store:
            self._token_store(self._oauth)
        _LOGGER.info("OAuth tokens stored (valid ~%d min)",
                     int((self._oauth["expires_at"] - time.time()) / 60))

    async def _oauth_access_token(self) -> str:
        if not self._oauth:
            raise ISolarCloudError("oauth_missing", "Keine OAuth-Tokens vorhanden", "oauth")
        if self._oauth.get("expires_at", 0) < time.time():
            await self._oauth_refresh()
        return self._oauth["access_token"]

    @property
    def has_oauth(self) -> bool:
        return bool(self._oauth)

    # ------------------------------------------------------------- API ----

    async def _get_plants_with(self, prof: Profile) -> list[dict]:
        if prof.auth in ("account", "account-bearer"):
            await self._ensure_account_token(prof)
        if prof.family == "openapi":
            result = await self._post("/openapi/getPowerStationList",
                                      {"curPage": 1, "size": 100}, prof)
        else:
            result = await self._post("/openapi/platform/queryPowerStationList",
                                      {"page": 1, "size": 100}, prof)
        return result.get("pageList") or []

    async def get_plants(self) -> list[dict]:
        prof = await self.ensure_profile()
        return await self._get_plants_with(prof)

    async def get_devices(self, ps_id: str | int) -> list[dict]:
        prof = await self.ensure_profile()
        if prof.family == "openapi":
            result = await self._request("/openapi/getDeviceList",
                                         {"ps_id": str(ps_id), "curPage": 1, "size": 200})
        else:
            result = await self._request("/openapi/platform/getDeviceListByPsId",
                                         {"ps_id": str(ps_id), "page": 1, "size": 200})
        return result.get("pageList") or []

    async def get_realtime_points(self, ps_id: str | int, point_ids: list[str]) -> dict:
        """Plant-level (device_type 11) real-time values."""
        prof = await self.ensure_profile()
        if prof.family == "openapi":
            return await self._request("/openapi/getDeviceRealTimeData", {
                "device_type": 11,
                "point_id_list": [str(p) for p in point_ids],
                "ps_key_list": [self.plant_ps_key(ps_id)],
            })
        return await self._request("/openapi/platform/getPowerStationRealTimeData", {
            "ps_id_list": [str(ps_id)],
            "point_id_list": [str(p) for p in point_ids],
            "is_get_point_dict": "1",
        })

    async def get_minute_history(self, ps_id: str | int, point_ids: list[str],
                                 start: datetime, end: datetime,
                                 minute_interval: int = 5) -> dict:
        prof = await self.ensure_profile()
        points = ",".join(f"p{p}" for p in point_ids)
        common = {
            "start_time_stamp": start.strftime(_TS_FORMAT),
            "end_time_stamp": end.strftime(_TS_FORMAT),
            "minute_interval": str(minute_interval),
        }
        if prof.family == "openapi":
            return await self._request("/openapi/getDevicePointMinuteDataList", {
                "ps_key_list": [self.plant_ps_key(ps_id)], "points": points, **common,
            })
        return await self._request("/openapi/platform/getPowerStationPointMinuteDataList", {
            "ps_id_list": [str(ps_id)], "points": points, "is_get_point_dict": "1", **common,
        })

    @staticmethod
    def plant_ps_key(ps_id: str | int) -> str:
        return f"{ps_id}_11_0_0"

    @staticmethod
    def parse_point_rows(result: dict) -> list[dict]:
        """Flatten realtime/history results into rows of
        {ps_key, point_id, value, timestamp}. Handles both API families:
        entries under device_point_list (nested in "device_point" or flat) and
        the platform history shape (result_data[ps_id] = [frames])."""
        rows: list[dict] = []

        def eat(point_map: dict) -> None:
            ps_key = point_map.get("ps_key") or point_map.get("ps_id", "")
            ts = point_map.get("device_time") or point_map.get("time_stamp")
            for k, v in point_map.items():
                if isinstance(k, str) and len(k) > 1 and k[0] == "p" and k[1:].isdigit():
                    rows.append({"ps_key": str(ps_key), "point_id": k[1:],
                                 "value": _to_number(v), "timestamp": ts})

        if "device_point_list" in result:
            for entry in result.get("device_point_list") or []:
                if isinstance(entry, dict):
                    eat(entry.get("device_point", entry))
        else:
            for key, val in result.items():
                if key == "point_dict" or not isinstance(val, list):
                    continue
                for frame in val:
                    if isinstance(frame, dict):
                        frame.setdefault("ps_id", key)
                        eat(frame)
        return rows


def _to_number(v: Any) -> float | str | None:
    if v is None or v == "" or v == "--":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return v
