#!/usr/bin/with-contenv bashio
# ==============================================================================
# Sungrow iSolarCloud add-on: read options, resolve MQTT service, start server
# ==============================================================================
set -e

export SG_REGION="$(bashio::config 'region')"
export SG_APPKEY="$(bashio::config 'appkey')"
export SG_SECRET_KEY="$(bashio::config 'secret_key')"
export SG_USERNAME="$(bashio::config 'username')"
export SG_PASSWORD="$(bashio::config 'password')"
export SG_POLL_INTERVAL="$(bashio::config 'poll_interval')"
export SG_LANGUAGE="$(bashio::config 'language')"
export SG_DEMO_MODE="$(bashio::config 'demo_mode')"
export SG_MQTT_ENABLED="$(bashio::config 'mqtt_enabled')"

# Auto-discover the Home Assistant MQTT broker (Mosquitto add-on)
if bashio::config.true 'mqtt_enabled'; then
    if bashio::services.available 'mqtt'; then
        export SG_MQTT_HOST="$(bashio::services 'mqtt' 'host')"
        export SG_MQTT_PORT="$(bashio::services 'mqtt' 'port')"
        export SG_MQTT_USER="$(bashio::services 'mqtt' 'username')"
        export SG_MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
        bashio::log.info "MQTT broker discovered at ${SG_MQTT_HOST}:${SG_MQTT_PORT}"
    else
        bashio::log.warning "MQTT service not available – sensors will not be published. Install the Mosquitto broker add-on."
        export SG_MQTT_ENABLED="false"
    fi
fi

bashio::log.info "Starting Sungrow iSolarCloud (region: ${SG_REGION}, demo: ${SG_DEMO_MODE})"
cd /app
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8099 --log-level warning
