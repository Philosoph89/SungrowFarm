"""Background poller: fetches plants, devices and real-time points from
iSolarCloud on a fixed interval and pushes updates to the store + MQTT."""
from __future__ import annotations

import asyncio
import logging

from catalog import BATTERY_DEVICE_TYPES, DEVICE_BATTERY_POINTS, PLANT_POINTS
from config import settings
from isolarcloud import ISolarCloudError
from mqtt_publisher import MqttPublisher
from store import Store

_LOGGER = logging.getLogger(__name__)

# structural data (plants, devices) refreshes less often than live points
STRUCTURE_REFRESH_EVERY = 12  # poll cycles


class Poller:
    def __init__(self, client, store: Store, mqtt: MqttPublisher):
        self.client = client
        self.store = store
        self.mqtt = mqtt
        self._cycle = 0
        self._device_poll_failed_types: set[tuple[str, int]] = set()

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
                self.store.mark_success()
            except ISolarCloudError as err:
                _LOGGER.error("iSolarCloud error: %s", err)
                self.store.mark_error(str(err))
            except Exception as err:  # noqa: BLE001 — keep the loop alive
                _LOGGER.exception("Unexpected poll error")
                self.store.mark_error(f"{type(err).__name__}: {err}")
            await asyncio.sleep(settings.poll_interval)

    async def poll_once(self) -> None:
        if self._cycle % STRUCTURE_REFRESH_EVERY == 0 or not self.store.plants:
            plants = await self.client.get_plants()
            self.store.set_plants(plants)
            self.store.login_ok = True
            for plant in plants:
                ps_id = str(plant.get("ps_id"))
                try:
                    devices = await self.client.get_devices(ps_id)
                    self.store.set_devices(ps_id, devices)
                except ISolarCloudError as err:
                    _LOGGER.warning("Device list for %s failed: %s", ps_id, err)
                try:
                    detail = await self.client.get_plant_detail(ps_id)
                    if isinstance(detail, dict) and detail:
                        self.store.plant_details[ps_id] = detail
                except ISolarCloudError as err:
                    _LOGGER.debug("Plant detail for %s failed: %s", ps_id, err)
        self._cycle += 1

        all_point_ids = list(PLANT_POINTS.keys())
        for plant in self.store.plants:
            ps_id = str(plant.get("ps_id"))
            # the API caps point_id_list length per call → chunk requests
            rows: list[dict] = []
            for chunk in _chunks(all_point_ids, 10):
                try:
                    result = await self.client.get_realtime_points(ps_id, chunk)
                    rows.extend(self.client.parse_point_rows(result))
                except ISolarCloudError as err:
                    _LOGGER.warning("Realtime chunk failed for %s: %s", ps_id, err)
            if rows:
                self.store.update_points(ps_id, rows)
            await self._poll_battery_devices(ps_id)
        await self.mqtt.publish_all()

    async def _poll_battery_devices(self, ps_id: str) -> None:
        """Device-level battery/grid/load points (13xxx) – residential plants
        don't expose battery power at plant level, only on the ESS device."""
        if not hasattr(self.client, "get_device_realtime"):
            return
        devices = self.store.devices.get(ps_id, [])
        point_ids = list(DEVICE_BATTERY_POINTS.keys())
        for dev_type in BATTERY_DEVICE_TYPES:
            candidates = [d for d in devices
                          if _to_int(d.get("device_type")) == dev_type and d.get("ps_key")]
            got_data = False
            for dev in candidates[:2]:
                key = (ps_id, dev_type)
                rows: list[dict] = []
                try:
                    for chunk in _chunks(point_ids, 10):
                        result = await self.client.get_device_realtime(
                            dev_type, str(dev["ps_key"]), chunk)
                        rows.extend(self.client.parse_point_rows(result))
                except ISolarCloudError as err:
                    if key not in self._device_poll_failed_types:
                        _LOGGER.info("Device points (type %s, %s) not available: %s",
                                     dev_type, dev.get("device_name", "?"), err)
                        self._device_poll_failed_types.add(key)
                    continue
                rows = [r for r in rows if r.get("value") is not None]
                if rows:
                    self.store.update_points(ps_id, rows)
                    got_data = True
            if got_data:
                return  # first device type that delivers data wins


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]
