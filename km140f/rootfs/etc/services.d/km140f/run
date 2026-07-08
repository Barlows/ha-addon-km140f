#!/usr/bin/with-contenv bashio

export MONITOR_HOST=$(bashio::config 'monitor_host')
export MONITOR_PORT=$(bashio::config 'monitor_port')
export MQTT_HOST=$(bashio::config 'mqtt_host')
export MQTT_PORT=$(bashio::config 'mqtt_port')
export MQTT_USER=$(bashio::config 'mqtt_user')
export MQTT_PASS=$(bashio::config 'mqtt_pass')
export DEVICE_ID=$(bashio::config 'device_id')
export DEVICE_NAME=$(bashio::config 'device_name')
export POLL_C_INTERVAL=$(bashio::config 'poll_c_interval')
export RECONNECT_DELAY=$(bashio::config 'reconnect_delay')

bashio::log.info "Starting Junctek KM140F bridge → ${MONITOR_HOST}:${MONITOR_PORT}"

exec python3 /km140f.py
