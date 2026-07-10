"""Demo client: simulates a realistic 9.8 kWp home PV plant with a 12.8 kWh
battery so the dashboard is fully explorable without iSolarCloud credentials.
Implements the same surface as ISolarCloudClient."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

DEMO_PS_ID = "4711001"
KWP = 9.8          # installed power
BATT_WH = 12800.0  # battery capacity


def _solar_curve(dt: datetime) -> float:
    """PV output in W for a given time (sunrise ~6h, sunset ~21h, peak ~13h)."""
    h = dt.hour + dt.minute / 60
    if h < 5.8 or h > 21.2:
        return 0.0
    x = (h - 5.8) / (21.2 - 5.8)          # 0..1 over the solar day
    base = math.sin(math.pi * x) ** 1.6 * KWP * 1000 * 0.86
    # deterministic "clouds" so history and realtime agree
    seed = int(dt.timestamp() // 300)
    rng = random.Random(seed)
    clouds = 0.75 + 0.25 * math.sin(seed / 7.3) * rng.uniform(0.7, 1.0)
    return max(0.0, base * clouds)


def _load_curve(dt: datetime) -> float:
    """Household load in W: base + morning/evening peaks + noise."""
    h = dt.hour + dt.minute / 60
    base = 320.0
    morning = 900 * math.exp(-((h - 7.4) ** 2) / 1.4)
    midday = 500 * math.exp(-((h - 12.8) ** 2) / 3.0)
    evening = 1500 * math.exp(-((h - 19.4) ** 2) / 2.6)
    seed = int(dt.timestamp() // 300)
    noise = random.Random(seed ^ 0xBEEF).uniform(-80, 140)
    return max(180.0, base + morning + midday + evening + noise)


def _flows(dt: datetime) -> dict:
    """Consistent instantaneous power flows at time dt (all W)."""
    pv = _solar_curve(dt)
    load = _load_curve(dt)
    # battery SoC approximated from time of day: drained overnight, full by
    # late afternoon, discharging through the evening
    h = dt.hour + dt.minute / 60
    if h < 7:
        soc = max(11.0, 38.0 - h * 4.5)          # overnight drain, empty ~6h
    elif h < 16:
        soc = 11.0 + (h - 7) * (95.0 - 11.0) / 9  # charging window
    else:
        soc = max(11.0, 95.0 - (h - 16) * 7.5)    # evening discharge
    surplus = pv - load
    batt = 0.0  # + charging, - discharging
    grid = 0.0  # + import, - export
    if surplus > 0:
        batt = min(surplus, 3400.0) if soc < 94.5 else 0.0
        grid = -(surplus - batt)
    else:
        deficit = -surplus
        batt = -min(deficit, 4600.0) if soc > 12.0 else 0.0
        grid = deficit + batt
    return {"pv": pv, "load": load, "battery": batt, "grid": grid, "soc": soc}


def _daily_energies(dt: datetime) -> dict:
    """Integrate the deterministic curves from midnight until dt (Wh)."""
    acc = {"yield": 0.0, "load": 0.0, "feed_in": 0.0, "purchased": 0.0,
           "charge": 0.0, "discharge": 0.0}
    t = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    step = timedelta(minutes=15)
    while t < dt:
        f = _flows(t)
        hrs = 0.25
        acc["yield"] += f["pv"] * hrs
        acc["load"] += f["load"] * hrs
        if f["grid"] < 0:
            acc["feed_in"] += -f["grid"] * hrs
        else:
            acc["purchased"] += f["grid"] * hrs
        if f["battery"] > 0:
            acc["charge"] += f["battery"] * hrs
        else:
            acc["discharge"] += -f["battery"] * hrs
        t += step
    return acc


class DemoClient:
    base_url = "demo://sungrowfarm"

    async def close(self) -> None:
        return

    async def login(self) -> dict:
        return {"user_name": "Demo", "token": "demo-token"}

    async def get_plants(self) -> list[dict]:
        return [{
            "ps_id": DEMO_PS_ID,
            "ps_name": "Sonnenhof Demo",
            "ps_location": "Musterweg 8, 74523 Schwäbisch Hall",
            "ps_type": 4,
            "ps_current_time_zone": "GMT+2",
            "online_status": 1,
            "grid_connection_status": 1,
            "install_date": "2023-05-12",
            "total_capcity": {"value": str(KWP), "unit": "kWp"},
            "ps_fault_status": 4,
            "build_status": 3,
            "share_type": "0",
        }]

    async def get_plant_detail(self, ps_id) -> dict:
        return {
            "ps_id": DEMO_PS_ID,
            "ps_name": "Sonnenhof Demo",
            "design_capacity": KWP * 1000,
            "battery_capacity": BATT_WH,
            "ps_location": "Musterweg 8, 74523 Schwäbisch Hall",
            "install_date": "2023-05-12",
        }

    async def get_devices(self, ps_id) -> list[dict]:
        return [
            {"ps_key": f"{DEMO_PS_ID}_1_1_1", "ps_id": DEMO_PS_ID, "device_type": 1,
             "device_name": "SH10RT Hybrid-Wechselrichter", "device_model_code": "SH10RT",
             "device_sn": "A2290711223", "dev_status": "1", "dev_fault_status": "4",
             "type_name": "Inverter", "factory_name": "Sungrow"},
            {"ps_key": f"{DEMO_PS_ID}_43_2_1", "ps_id": DEMO_PS_ID, "device_type": 43,
             "device_name": "SBR128 Batteriespeicher", "device_model_code": "SBR128",
             "device_sn": "B1180922817", "dev_status": "1", "dev_fault_status": "4",
             "type_name": "Battery", "factory_name": "Sungrow"},
            {"ps_key": f"{DEMO_PS_ID}_7_3_1", "ps_id": DEMO_PS_ID, "device_type": 7,
             "device_name": "DTSU666 Smart Meter", "device_model_code": "DTSU666",
             "device_sn": "M4410233551", "dev_status": "1", "dev_fault_status": "4",
             "type_name": "Meter", "factory_name": "Chint"},
            {"ps_key": f"{DEMO_PS_ID}_9_4_1", "ps_id": DEMO_PS_ID, "device_type": 9,
             "device_name": "WiNet-S Datenlogger", "device_model_code": "WiNet-S",
             "device_sn": "L0550112204", "dev_status": "1", "dev_fault_status": "4",
             "type_name": "Data Logger", "factory_name": "Sungrow"},
        ]

    async def get_realtime_points(self, ps_id, point_ids) -> dict:
        now = datetime.now()
        f = _flows(now)
        e = _daily_energies(now)
        totals_factor = 412.0  # pretend plant ran ~412 "average days"
        values = {
            "83033": f["pv"], "83067": f["pv"],
            "83106": f["load"], "83052": f["load"],
            "83549": f["grid"],
            "83238": f["battery"], "83046": f["battery"],
            "83129": f["soc"], "83252": f["soc"],
            "83022": e["yield"], "83118": e["load"],
            "83102": e["purchased"], "83072": e["feed_in"],
            "83119": e["feed_in"], "83243": e["charge"], "83244": e["discharge"],
            "83024": 8.43e6 + e["yield"] * totals_factor / 412,
            "83124": 6.02e6, "83105": 1.41e6, "83075": 3.38e6,
            "83241": 1.9e6, "83242": 1.76e6,
            "83097": e["yield"] - e["feed_in"] - e["charge"],
            "83100": 3.1e6,
            "83019": f["pv"] / (KWP * 1000),
            "83005": e["yield"] / (KWP * 1000),
            "83018": e["yield"] * 1.12,
            "83023": 0.87,
            "83012": f["pv"] / (KWP * 1000) * 1000 * 1.1,
            "83013": e["yield"] / (KWP * 1000) * 1.1,
            "83016": 14 + 12 * math.sin(math.pi * (now.hour - 5) / 14),
            "83017": 16 + 22 * f["pv"] / (KWP * 1000),
            "83235": BATT_WH * (1 - f["soc"] / 100),
            "83236": BATT_WH * (f["soc"] / 100 - 0.05),
        }
        point_map = {"ps_key": f"{DEMO_PS_ID}_11_0_0",
                     "device_time": now.strftime("%Y%m%d%H%M%S")}
        for pid in point_ids:
            if str(pid) in values:
                point_map[f"p{pid}"] = round(values[str(pid)], 2)
        return {"device_point_list": [{"device_point": point_map}]}

    async def get_minute_history(self, ps_id, point_ids, start, end, minute_interval=5):
        rows = []
        t = start
        while t <= end:
            f = _flows(t)
            values = {"83033": f["pv"], "83067": f["pv"], "83106": f["load"],
                      "83549": f["grid"], "83238": f["battery"], "83046": f["battery"],
                      "83129": f["soc"], "83252": f["soc"]}
            frame = {"ps_key": f"{DEMO_PS_ID}_11_0_0",
                     "time_stamp": t.strftime("%Y%m%d%H%M%S")}
            for pid in point_ids:
                if str(pid) in values:
                    frame[f"p{pid}"] = round(values[str(pid)], 1)
            rows.append({"device_point": frame})
            t = t + timedelta(minutes=minute_interval)
        return {"device_point_list": rows}

    @staticmethod
    def plant_ps_key(ps_id) -> str:
        return f"{ps_id}_11_0_0"

    parse_point_rows = None  # filled below to reuse the real parser


from isolarcloud import ISolarCloudClient as _Real  # noqa: E402
DemoClient.parse_point_rows = staticmethod(_Real.parse_point_rows)
