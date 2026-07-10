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

    async def publish_all(self) -> None:
        """Publish discovery configs (once per entity) and current states."""
        if not self._client or not self.store.mqtt_connected:
            return
        async with self._lock:
            for ps_id, points in self.store.points.items():
                plant = next((p for p in self.store.plants if str(p.get("ps_id")) == ps_id), {})
                plant_name = plant.get("ps_name") or f"Plant {ps_id}"
                device_block = {
                    "identifiers": [f"sungrowfarm_{ps_id}"],
                    "name": f"Sungrow {plant_name}",
                    "manufacturer": "Sungrow",
                    "model": "iSolarCloud",
                    "configuration_url": "https://web3.isolarcloud.eu",
                }
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
