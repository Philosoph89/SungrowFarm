"""Publishes every collected measure point to Home Assistant via MQTT
discovery, so all parameters become first-class HA sensor entities."""
from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from config import settings
from store import Store

_LOGGER = logging.getLogger(__name__)

AVAILABILITY_TOPIC = "sungrowfarm/status"
DISCOVERY_PREFIX = "homeassistant"


class MqttPublisher:
    def __init__(self, store: Store):
        self.store = store
        self._client: aiomqtt.Client | None = None
        self._discovered: set[str] = set()
        self._lock = asyncio.Lock()

    async def run(self) -> None:
        """Keep a broker connection alive; reconnect with backoff."""
        if not settings.mqtt_enabled:
            return
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=settings.mqtt_host,
                    port=settings.mqtt_port,
                    username=settings.mqtt_user or None,
                    password=settings.mqtt_password or None,
                    identifier="sungrowfarm-addon",
                    will=aiomqtt.Will(AVAILABILITY_TOPIC, "offline", retain=True),
                ) as client:
                    self._client = client
                    self.store.mqtt_connected = True
                    self._discovered.clear()
                    await client.publish(AVAILABILITY_TOPIC, "online", retain=True)
                    _LOGGER.info("Connected to MQTT broker %s:%s", settings.mqtt_host, settings.mqtt_port)
                    await self.publish_all()
                    # hold the connection open until it drops
                    while True:
                        await asyncio.sleep(30)
                        await client.publish(AVAILABILITY_TOPIC, "online", retain=True)
            except aiomqtt.MqttError as err:
                self._client = None
                self.store.mqtt_connected = False
                _LOGGER.warning("MQTT connection lost (%s), retrying in 15s", err)
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                if self._client:
                    try:
                        await self._client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
                    except Exception:
                        pass
                raise

    def _device_block(self, ps_id: str) -> dict:
        plant = next((p for p in self.store.plants if str(p.get("ps_id")) == ps_id), {})
        plant_name = plant.get("ps_name") or f"Plant {ps_id}"
        return {
            "identifiers": [f"sungrowfarm_{ps_id}"],
            "name": f"Sungrow {plant_name}",
            "manufacturer": "Sungrow",
            "model": "iSolarCloud",
            "configuration_url": "https://web3.isolarcloud.eu",
        }

    async def publish_all(self) -> None:
        """Publish discovery configs (once per entity) and current states."""
        if not self._client or not self.store.mqtt_connected:
            return
        async with self._lock:
            for ps_id, points in self.store.points.items():
                device_block = self._device_block(ps_id)
                for pid, row in points.items():
                    if row["value"] is None:
                        continue
                    uid = f"sungrowfarm_{ps_id}_{row['code']}"
                    if uid not in self._discovered:
                        config = {
                            "name": row["name"],
                            "unique_id": uid,
                            "object_id": f"sungrow_{ps_id}_{row['code']}",
                            "state_topic": row["mqtt_topic"],
                            "availability_topic": AVAILABILITY_TOPIC,
                            "icon": row["icon"],
                            "device": device_block,
                        }
                        if row["unit"]:
                            config["unit_of_measurement"] = row["unit"]
                        if row["device_class"] and row["device_class"] not in ("energy_storage",):
                            config["device_class"] = row["device_class"]
                        if row["state_class"]:
                            config["state_class"] = row["state_class"]
                        topic = f"{DISCOVERY_PREFIX}/sensor/sungrowfarm_{ps_id}/{row['code']}/config"
                        try:
                            await self._client.publish(topic, json.dumps(config), retain=True)
                            self._discovered.add(uid)
                        except aiomqtt.MqttError as err:
                            _LOGGER.warning("Discovery publish failed: %s", err)
                            return
                    try:
                        value = row["value"]
                        if isinstance(value, float):
                            value = round(value, 3)
                        await self._client.publish(row["mqtt_topic"], str(value), retain=True)
                    except aiomqtt.MqttError as err:
                        _LOGGER.warning("State publish failed: %s", err)
                        return

    # ---------------------------------------------------- solar planner ----

    PLANNER_ENTITIES = [
        # (component, key, name, unit, icon)
        ("sensor", "planner_verdict", "Solar-Planer Empfehlung", None, "mdi:washing-machine"),
        ("binary_sensor", "planner_run", "Solar-Planer Einschalten lohnt sich", None, "mdi:power-plug"),
        ("sensor", "planner_surplus_today", "Solar-Planer Überschuss heute", "kWh", "mdi:solar-power"),
        ("sensor", "planner_remaining_today", "Solar-Planer Rest-Erzeugung heute", "kWh", "mdi:weather-sunset-down"),
        ("sensor", "planner_sun_hours_remaining", "Solar-Planer Rest-Sonnenstunden", "h", "mdi:weather-sunny"),
        ("sensor", "planner_best_day", "Solar-Planer bester Tag", None, "mdi:calendar-star"),
    ]

    async def publish_advisor(self, ps_id: str, advice: dict) -> None:
        """Expose the solar planner verdict + forecast figures as HA entities,
        so automations can e.g. enable the washing machine plug."""
        if not self._client or not self.store.mqtt_connected:
            return
        verdict = advice.get("verdict") or {}
        ctx = advice.get("context") or {}
        days = advice.get("days") or []
        base = f"sungrowfarm/{ps_id}/planner"
        async with self._lock:
            device_block = self._device_block(ps_id)
            for component, key, name, unit, icon in self.PLANNER_ENTITIES:
                uid = f"sungrowfarm_{ps_id}_{key}"
                if uid not in self._discovered:
                    config = {
                        "name": name,
                        "unique_id": uid,
                        "object_id": f"sungrow_{ps_id}_{key}",
                        "state_topic": f"{base}/{key}/state",
                        "availability_topic": AVAILABILITY_TOPIC,
                        "icon": icon,
                        "device": device_block,
                    }
                    if unit:
                        config["unit_of_measurement"] = unit
                    if key == "planner_verdict":
                        config["json_attributes_topic"] = f"{base}/attributes"
                    topic = f"{DISCOVERY_PREFIX}/{component}/sungrowfarm_{ps_id}/{key}/config"
                    try:
                        await self._client.publish(topic, json.dumps(config), retain=True)
                        self._discovered.add(uid)
                    except aiomqtt.MqttError as err:
                        _LOGGER.warning("Planner discovery publish failed: %s", err)
                        return

            recommended = next((d for d in days if d["index"] == verdict.get("day_index")), None)
            states = {
                "planner_verdict": verdict.get("type", "unknown"),
                "planner_run": "ON" if verdict.get("type") in ("now", "today") else "OFF",
                "planner_surplus_today": ctx.get("surplus_kwh"),
                "planner_remaining_today": ctx.get("remaining_today_kwh"),
                "planner_sun_hours_remaining": ctx.get("remaining_sun_h"),
                "planner_best_day": recommended.get("label_full") if recommended else None,
            }
            attributes = {
                "headline": verdict.get("headline"),
                "message": verdict.get("message"),
                "recommended_date": recommended.get("date") if recommended else None,
                "recommended_window": recommended.get("window") if recommended else None,
                "produced_today_kwh": ctx.get("produced_today_kwh"),
                "remaining_load_kwh": ctx.get("remaining_load_kwh"),
                "battery_headroom_kwh": ctx.get("battery_headroom_kwh"),
                "sunset": ctx.get("sunset"),
                "calibration": ctx.get("calibration"),
                "days": [{k: d.get(k) for k in
                          ("date", "label", "est_kwh", "rel", "icon", "pop", "window")}
                         for d in days],
            }
            try:
                for key, value in states.items():
                    if value is None:
                        continue
                    await self._client.publish(f"{base}/{key}/state", str(value), retain=True)
                await self._client.publish(f"{base}/attributes",
                                           json.dumps(attributes, ensure_ascii=False), retain=True)
            except aiomqtt.MqttError as err:
                _LOGGER.warning("Planner state publish failed: %s", err)
