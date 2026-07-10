"""In-memory state shared between poller, MQTT publisher and the HTTP API."""
from __future__ import annotations

import time
from typing import Any

from catalog import meta_for, display_name


class Store:
    def __init__(self, lang: str = "de"):
        self.lang = lang
        self.plants: list[dict] = []
        self.plant_details: dict[str, dict] = {}
        self.devices: dict[str, list[dict]] = {}          # ps_id -> devices
        self.points: dict[str, dict[str, dict]] = {}      # ps_id -> point_id -> point row
        self.last_success: float | None = None
        self.last_error: str | None = None
        self.last_error_ts: float | None = None
        self.poll_count: int = 0
        self.mqtt_connected: bool = False
        self.login_ok: bool = False

    # ---------------------------------------------------------------- write

    def set_plants(self, plants: list[dict]) -> None:
        self.plants = plants

    def set_devices(self, ps_id: str, devices: list[dict]) -> None:
        self.devices[str(ps_id)] = devices

    def update_points(self, ps_id: str, rows: list[dict]) -> None:
        ps_id = str(ps_id)
        bucket = self.points.setdefault(ps_id, {})
        now = time.time()
        for row in rows:
            pid = str(row["point_id"])
            meta = meta_for(pid)
            bucket[pid] = {
                "point_id": pid,
                "code": meta.code if meta else f"point_{pid}",
                "name": display_name(pid, self.lang),
                "value": row.get("value"),
                "unit": meta.unit if meta else None,
                "device_class": meta.device_class if meta else None,
                "state_class": meta.state_class if meta else None,
                "icon": meta.icon if meta else "mdi:chart-line",
                "group": meta.group if meta else "other",
                "updated": now,
                "entity_id": f"sensor.sungrow_{ps_id}_{meta.code if meta else pid}",
                "mqtt_topic": f"sungrowfarm/{ps_id}/{pid}/state",
            }

    def mark_success(self) -> None:
        self.last_success = time.time()
        self.last_error = None
        self.poll_count += 1

    def mark_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_ts = time.time()

    # ----------------------------------------------------------------- read

    def value(self, ps_id: str, point_id: str) -> Any:
        row = self.points.get(str(ps_id), {}).get(str(point_id))
        return row["value"] if row else None

    def first_value(self, ps_id: str, point_ids: list[str]) -> Any:
        """First non-None value among candidate points (fallback chains)."""
        for pid in point_ids:
            v = self.value(ps_id, pid)
            if v is not None:
                return v
        return None

    def overview(self, ps_id: str) -> dict:
        """Computed KPI block for the dashboard energy-flow view."""
        pv = self.first_value(ps_id, ["83033", "83067"]) or 0.0
        load = self.first_value(ps_id, ["83106", "83052"]) or 0.0
        grid = self.first_value(ps_id, ["83549"])
        battery = self.first_value(ps_id, ["83238", "83046"])
        soc = self.first_value(ps_id, ["83129", "83252"])
        daily_yield = self.value(ps_id, "83022") or 0.0
        daily_load = self.value(ps_id, "83118")
        purchased_today = self.value(ps_id, "83102") or 0.0
        feed_in_today = self.first_value(ps_id, ["83072", "83119"]) or 0.0

        if battery is None:
            battery = 0.0
        if grid is None:
            # derive grid from the balance if the meter point is missing
            grid = load - pv + battery

        self_sufficiency = None
        if daily_load and daily_load > 0:
            self_sufficiency = max(0.0, min(1.0, 1.0 - purchased_today / daily_load))
        self_consumption = None
        if daily_yield and daily_yield > 0:
            self_consumption = max(0.0, min(1.0, 1.0 - feed_in_today / daily_yield))

        return {
            "pv_power_w": round(float(pv), 1),
            "load_power_w": round(float(load), 1),
            "grid_power_w": round(float(grid), 1),          # + import / − export
            "battery_power_w": round(float(battery), 1),    # + charge / − discharge
            "battery_soc": round(float(soc), 1) if soc is not None else None,
            "daily_yield_wh": daily_yield,
            "daily_load_wh": daily_load,
            "purchased_today_wh": purchased_today,
            "feed_in_today_wh": feed_in_today,
            "daily_charge_wh": self.value(ps_id, "83243"),
            "daily_discharge_wh": self.value(ps_id, "83244"),
            "total_yield_wh": self.value(ps_id, "83024"),
            "self_sufficiency": self_sufficiency,
            "self_consumption": self_consumption,
        }
