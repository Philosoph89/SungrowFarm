"""Async client for the Sungrow iSolarCloud OpenAPI (developer API).

Auth model: every request carries the app's `x-access-key` header (secret key)
plus `appkey` in the JSON body. A session token is obtained via /openapi/login
with the iSolarCloud account credentials and sent as `token` HTTP header (and
body field) on subsequent calls. On token expiry the client re-logs-in once.

Encrypted mode: applications created recently in the developer portal require
the "secured" protocol — the JSON body is AES-128-ECB encrypted with a random
per-request key, that key travels RSA-encrypted in the `x-random-secret-key`
header, the body carries an `api_key_param` (nonce + timestamp), and responses
come back hex-encoded/AES-encrypted. Enabled by configuring the application's
RSA public key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

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

# result codes that mean "token invalid/expired" → re-login and retry once
_TOKEN_ERROR_CODES = {"E00003", "010", "E900", "er_invalid_token", "er_token_login_invalid"}
_TS_FORMAT = "%Y%m%d%H%M%S"


class ISolarCloudError(Exception):
    def __init__(self, code: str, message: str, endpoint: str = ""):
        super().__init__(f"{endpoint}: [{code}] {message}")
        self.code = code
        self.message = message
        self.endpoint = endpoint


class ISolarCloudClient:
    def __init__(self, region: str, appkey: str, secret_key: str,
                 username: str, password: str, lang: str = "_en_US",
                 rsa_public_key: str = ""):
        self.base_url = GATEWAYS.get(region, GATEWAYS["eu"])
        self.appkey = appkey
        self.secret_key = secret_key
        self.username = username
        self.password = password
        self.lang = lang
        self._rsa = parse_rsa_public_key(rsa_public_key) if rsa_public_key.strip() else None
        if self._rsa:
            _LOGGER.info("Encrypted OpenAPI mode enabled (RSA public key configured)")
        self._token: str | None = None
        self._token_ts: float = 0.0
        self._session: aiohttp.ClientSession | None = None
        self._login_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "SungrowFarm-HA-Addon/1.0"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _raw_request(self, path: str, payload: dict, with_token: bool) -> dict:
        session = await self._get_session()
        headers = {
            "Content-Type": "application/json",
            "x-access-key": self.secret_key,
            "sys_code": "901",
        }
        body: dict[str, Any] = {
            **payload,
            "appkey": self.appkey,
            "lang": self.lang,
        }
        if with_token and self._token:
            # the OpenAPI expects the session token as an HTTP header;
            # keep it in the body too for older gateway revisions
            headers["token"] = self._token
            body["token"] = self._token

        aes_key: str | None = None
        if self._rsa:
            aes_key = random_key(16)
            headers["x-random-secret-key"] = rsa_encrypt_pkcs1_b64(aes_key, *self._rsa)
            body["api_key_param"] = {
                "nonce": random_nonce(32),
                "timestamp": str(int(time.time() * 1000)),
            }
            request_kwargs = {"data": aes_encrypt_ecb_hex(json.dumps(body), aes_key)}
        else:
            body["sys_code"] = "901"
            request_kwargs = {"json": body}

        async with session.post(f"{self.base_url}{path}", headers=headers, **request_kwargs) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise ISolarCloudError(str(resp.status), f"HTTP error: {text[:300]}", path)
        data = self._parse_response(text, aes_key, path)
        code = str(data.get("result_code", ""))
        if code != "1":
            raise ISolarCloudError(code or "unknown", str(data.get("result_msg", data))[:300], path)
        return data.get("result_data") or {}

    @staticmethod
    def _parse_response(text: str, aes_key: str | None, path: str) -> dict:
        """Responses are plain JSON, or hex-encoded AES ciphertext in
        encrypted mode (error responses may still be plain JSON)."""
        stripped = text.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
        if aes_key:
            try:
                return json.loads(aes_decrypt_ecb_hex(stripped, aes_key))
            except (ValueError, json.JSONDecodeError) as err:
                raise ISolarCloudError("decrypt_failed",
                                       f"Could not decrypt response: {err}", path) from err
        raise ISolarCloudError("bad_response", f"Unexpected response: {stripped[:200]}", path)

    async def login(self) -> dict:
        async with self._login_lock:
            result = await self._raw_request("/openapi/login", {
                "user_account": self.username,
                "user_password": self.password,
                "login_type": "1",
            }, with_token=False)
            state = str(result.get("login_state", "1"))
            if state != "1":
                msg = result.get("msg", "login rejected")
                raise ISolarCloudError(f"login_state_{state}", str(msg), "/openapi/login")
            self._token = result.get("token")
            self._token_ts = time.time()
            if not self._token:
                raise ISolarCloudError("no_token", "Login succeeded but no token returned", "/openapi/login")
            _LOGGER.info("Logged in to iSolarCloud as %s", result.get("user_name", self.username))
            return result

    async def request(self, path: str, payload: dict) -> dict:
        """Authenticated request with one transparent re-login on token expiry."""
        if not self._token:
            await self.login()
        try:
            return await self._raw_request(path, payload, with_token=True)
        except ISolarCloudError as err:
            if err.code in _TOKEN_ERROR_CODES:
                _LOGGER.info("Token rejected (%s), re-logging in", err.code)
                self._token = None
                await self.login()
                return await self._raw_request(path, payload, with_token=True)
            raise

    # ------------------------------------------------------------------ API

    async def get_plants(self) -> list[dict]:
        result = await self.request("/openapi/getPowerStationList", {
            "curPage": 1, "size": 100,
        })
        return result.get("pageList") or []

    async def get_plant_detail(self, ps_id: str | int) -> dict:
        return await self.request("/openapi/getPowerStationDetail", {
            "ps_id": str(ps_id), "is_get_ps_remarks": "1",
        })

    async def get_station_real_kpi(self, ps_id: str | int) -> dict:
        return await self.request("/openapi/getStationRealKpi", {
            "ps_id": str(ps_id),
        })

    async def get_devices(self, ps_id: str | int) -> list[dict]:
        result = await self.request("/openapi/getDeviceList", {
            "ps_id": str(ps_id), "curPage": 1, "size": 200,
        })
        return result.get("pageList") or []

    async def get_realtime_points(self, device_type: int, ps_keys: list[str],
                                  point_ids: list[str]) -> dict:
        """Real-time values for the given points on the given device keys.

        Returns the raw result: device_point_list entries keyed p<point_id>.
        """
        return await self.request("/openapi/getDeviceRealTimeData", {
            "device_type": device_type,
            "point_id_list": [str(p) for p in point_ids],
            "ps_key_list": ps_keys,
        })

    async def get_minute_history(self, ps_keys: list[str], point_ids: list[str],
                                 start: datetime, end: datetime,
                                 minute_interval: int = 5) -> dict:
        return await self.request("/openapi/getDevicePointMinuteDataList", {
            "ps_key_list": ps_keys,
            "points": ",".join(f"p{p}" for p in point_ids),
            "start_time_stamp": start.strftime(_TS_FORMAT),
            "end_time_stamp": end.strftime(_TS_FORMAT),
            "minute_interval": str(minute_interval),
        })

    @staticmethod
    def plant_ps_key(ps_id: str | int) -> str:
        """ps_key addressing the plant itself (device_type 11)."""
        return f"{ps_id}_11_0_0"

    @staticmethod
    def parse_point_rows(result: dict) -> list[dict]:
        """Flatten a getDeviceRealTimeData/…MinuteDataList result into rows of
        {ps_key, point_id, value, timestamp}."""
        rows: list[dict] = []
        device_list = result.get("device_point_list") or []
        for entry in device_list:
            # entries are either the point map directly or nested under "device_point"
            point_map = entry.get("device_point", entry) if isinstance(entry, dict) else {}
            ps_key = point_map.get("ps_key") or point_map.get("ps_id", "")
            ts = point_map.get("device_time") or point_map.get("time_stamp")
            for k, v in point_map.items():
                if isinstance(k, str) and len(k) > 1 and k[0] == "p" and k[1:].isdigit():
                    rows.append({
                        "ps_key": str(ps_key),
                        "point_id": k[1:],
                        "value": _to_number(v),
                        "timestamp": ts,
                    })
        return rows


def _to_number(v: Any) -> float | str | None:
    if v is None or v == "" or v == "--":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return v
