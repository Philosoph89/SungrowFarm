"""Runtime configuration, read from environment variables set by run.sh
(or a local .env-style export when running standalone/docker-compose)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    region: str = field(default_factory=lambda: os.getenv("SG_REGION", "eu"))
    appkey: str = field(default_factory=lambda: os.getenv("SG_APPKEY", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("SG_SECRET_KEY", ""))
    username: str = field(default_factory=lambda: os.getenv("SG_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("SG_PASSWORD", ""))
    rsa_public_key: str = field(default_factory=lambda: os.getenv("SG_RSA_PUBLIC_KEY", ""))
    poll_interval: int = field(default_factory=lambda: int(os.getenv("SG_POLL_INTERVAL", "300")))
    language: str = field(default_factory=lambda: os.getenv("SG_LANGUAGE", "de"))
    demo_mode: bool = field(default_factory=lambda: _bool(os.getenv("SG_DEMO_MODE"), False))

    mqtt_enabled: bool = field(default_factory=lambda: _bool(os.getenv("SG_MQTT_ENABLED"), False))
    mqtt_host: str = field(default_factory=lambda: os.getenv("SG_MQTT_HOST", "core-mosquitto"))
    mqtt_port: int = field(default_factory=lambda: int(os.getenv("SG_MQTT_PORT", "1883")))
    mqtt_user: str = field(default_factory=lambda: os.getenv("SG_MQTT_USER", ""))
    mqtt_password: str = field(default_factory=lambda: os.getenv("SG_MQTT_PASSWORD", ""))

    @property
    def configured(self) -> bool:
        return bool(self.appkey and self.secret_key and self.username and self.password)

    @property
    def api_lang(self) -> str:
        return {"de": "_de_DE", "en": "_en_US"}.get(self.language, "_en_US")


settings = Settings()
