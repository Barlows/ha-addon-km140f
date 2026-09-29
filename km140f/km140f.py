#!/usr/bin/env python3
"""
Junctek KM140F — TCP to MQTT bridge
Connects to the monitor's WiFi module (port 8899), parses :A= and :C= lines,
and publishes sensor data to Home Assistant via a unified JSON MQTT payload.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import time
from typing import Any

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
MONITOR_HOST = os.getenv("MONITOR_HOST", "192.168.0.204")
MONITOR_PORT = int(os.getenv("MONITOR_PORT", "8899"))

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "km140f")
MQTT_PASS = os.getenv("MQTT_PASS", "")
MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() in ("true", "1", "yes")
MQTT_TLS_CA_CERT = os.getenv("MQTT_TLS_CA_CERT", "")

DEVICE_ID = os.getenv("DEVICE_ID", "junctek_km140f")
DEVICE_NAME = os.getenv("DEVICE_NAME", "Junctek KM140F")
SW_VERSION = os.getenv("SW_VERSION", "1.2.0")

POLL_C_INTERVAL = int(os.getenv("POLL_C_INTERVAL", "30"))
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))
MAX_RECONNECT_DELAY = int(os.getenv("MAX_RECONNECT_DELAY", "300"))
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "15"))
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
STALE_TIMEOUT = int(os.getenv("STALE_TIMEOUT", "60"))

# Throttle configuration (Debouncing)
THROTTLE_HEARTBEAT_INTERVAL = 10.0


class BridgeState:
    """Encapsulates mutable bridge state for better testability."""

    def __init__(self) -> None:
        self.tcp_connected: bool = False
        self.last_published_values: dict[str, Any] = {}
        self.last_heartbeat_time: float = 0.0


# Global state instance
STATE = BridgeState()
TCP_CONNECTED = STATE.tcp_connected  # Backwards compatibility

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("km140f")

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": DEVICE_NAME,
    "manufacturer": "Junctek",
    "model": "KM140F",
    "sw_version": SW_VERSION,
}

SENSORS = [
    {
        "uid": "voltage",
        "name": "Voltage",
        "key": "voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "precision": 2,
    },
    {
        "uid": "current",
        "name": "Current",
        "key": "current",
        "unit": "A",
        "device_class": "current",
        "state_class": "measurement",
        "precision": 2,
    },
    {
        "uid": "power",
        "name": "Power",
        "key": "power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "precision": 2,
    },
    {
        "uid": "remaining_capacity",
        "name": "Remaining Capacity",
        "key": "remaining_capacity",
        "unit": "Ah",
        "icon": "mdi:battery-charging",
        "state_class": "measurement",
        "precision": 3,
    },
    {
        "uid": "time_remaining",
        "name": "Time Remaining",
        "key": "time_remaining",
        "unit": "min",
        "icon": "mdi:timer-sand",
        "state_class": "measurement",
        "precision": 0,
    },
    {
        "uid": "set_capacity",
        "name": "Set Capacity",
        "key": "set_capacity",
        "unit": "Ah",
        "icon": "mdi:battery-charging-100",
        "state_class": "measurement",
        "precision": 1,
    },
    {
        "uid": "soc",
        "name": "State of Charge",
        "key": "soc",
        "unit": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "precision": 1,
    },
    {
        "uid": "charge_kwh",
        "name": "Total Energy Charged",
        "key": "charge_kwh",
        "unit": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "precision": 3,
    },
    {
        "uid": "discharge_kwh",
        "name": "Total Energy Discharged",
        "key": "discharge_kwh",
        "unit": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "precision": 3,
    },
]

TEXT_SENSORS = [
    {
        "uid": "status",
        "name": "Status",
        "key": "status",
        "icon": "mdi:battery-arrow-up-outline",
    },
]

def discovery_topic(component: str, unique_id: str) -> str:
    return f"homeassistant/{component}/{DEVICE_ID}/{unique_id}/config"

def state_topic(key: str) -> str:
    return f"{DEVICE_ID}/{key}"

def publish_discovery(mq: mqtt.Client) -> None:
    for sensor in SENSORS:
        payload = {
            "name": f"{DEVICE_NAME} {sensor['name']}",
            "unique_id": f"{DEVICE_ID}_{sensor['uid']}",
            "state_topic": state_topic("state"),
            "value_template": f"{{{{ value_json.{sensor['key']} | default(None) }}}}",
            "unit_of_measurement": sensor.get("unit"),
            "state_class": sensor.get("state_class"),
            "device": DEVICE_INFO,
            "availability_topic": state_topic("availability"),
            "payload_available": "online",
            "payload_not_available": "offline",
            "suggested_display_precision": sensor["precision"],
        }
        if sensor.get("device_class"):
            payload["device_class"] = sensor["device_class"]
        if sensor.get("icon"):
            payload["icon"] = sensor["icon"]

        mq.publish(discovery_topic("sensor", sensor["uid"]), json.dumps(payload), retain=True)

    for sensor in TEXT_SENSORS:
        payload = {
            "name": f"{DEVICE_NAME} {sensor['name']}",
            "unique_id": f"{DEVICE_ID}_{sensor['uid']}",
            "state_topic": state_topic("state"),
            "value_template": f"{{{{ value_json.{sensor['key']} | default(None) }}}}",
            "icon": sensor["icon"],
            "device": DEVICE_INFO,
            "availability_topic": state_topic("availability"),
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        mq.publish(discovery_topic("sensor", sensor["uid"]), json.dumps(payload), retain=True)

    log.info("Published consolidated MQTT discovery configurations")

def publish_availability(mq: mqtt.Client, online: bool) -> None:
    mq.publish(state_topic("availability"), "online" if online else "offline", retain=True)

def publish_state_map_throttled(mq: mqtt.Client, data: dict[str, Any]) -> None:
    now = time.monotonic()
    should_publish = False

    if now - STATE.last_heartbeat_time >= THROTTLE_HEARTBEAT_INTERVAL:
        should_publish = True
        STATE.last_heartbeat_time = now

    for key, value in data.items():
        if STATE.last_published_values.get(key) != value:
            STATE.last_published_values[key] = value
            should_publish = True

    if should_publish:
        mq.publish(state_topic("state"), json.dumps(STATE.last_published_values), retain=True)

def parse_a(fields: list[str]) -> dict[str, Any] | None:
    if len(fields) < 6:
        log.warning("Short A frame: %s", fields)
        return None

    if len(fields) > 6:
        log.debug("A frame has %d extra fields: %s", len(fields) - 6, fields[6:])

    try:
        raw_voltage = int(fields[0])
        raw_current = int(fields[1])
        charging = int(fields[2]) == 1
        mins = int(fields[3])
        ah_rem = int(fields[4]) / 1000.0
        capacity = int(fields[5]) / 10.0

        voltage = raw_voltage / 100.0
        current = raw_current / 1000.0
        signed_current = current if charging else -current

        # Calculated explicitly using integer scale first to limit float drift
        power = (raw_voltage * raw_current) / 100000.0
        signed_power = power if charging else -power

        soc = round((ah_rem / capacity) * 100.0, 1) if capacity > 0 else 0.0
        soc = max(0.0, min(100.0, soc))

        return {
            "voltage": round(voltage, 2),
            "current": round(signed_current, 2),
            "power": round(signed_power, 2),
            "remaining_capacity": round(ah_rem, 3),
            "time_remaining": mins,
            "set_capacity": round(capacity, 1),
            "soc": soc,
            "status": "Charging" if charging else "Discharging",
        }
    except ValueError as exc:
        log.warning("parse_a error: %s | fields=%s", exc, fields)
        return None

def parse_c(fields: list[str]) -> dict[str, Any] | None:
    if len(fields) < 2:
        log.warning("Short C frame: %s", fields)
        return None

    try:
        return {
            "charge_kwh": round(int(fields[0]) / 1000.0, 3),
            "discharge_kwh": round(int(fields[1]) / 1000.0, 3),
        }
    except ValueError as exc:
        log.warning("parse_c error: %s | fields=%s", exc, fields)
        return None

def parse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None

    if line.startswith(":A="):
        fields = line[3:].rstrip(",").split(",")
        return parse_a(fields)

    if line.startswith(":C="):
        fields = line[3:].rstrip(",").split(",")
        return parse_c(fields)

    log.debug("Ignoring line: %r", line)
    return None

def tcp_loop(mq: mqtt.Client) -> None:
    last_c_request = 0.0
    last_data_time = 0.0
    reconnect_delay = RECONNECT_DELAY

    while True:
        sock = None
        buffer_bytes = b""

        try:
            log.info("Connecting to monitor at %s:%d", MONITOR_HOST, MONITOR_PORT)
            sock = socket.create_connection((MONITOR_HOST, MONITOR_PORT), timeout=10)
            sock.settimeout(SOCKET_TIMEOUT)
            
            STATE.tcp_connected = True
            last_data_time = time.monotonic()
            reconnect_delay = RECONNECT_DELAY
            publish_availability(mq, True)
            log.info("Bridge status is now ONLINE")

            while True:
                now = time.monotonic()
                
                # Staleness watchdog: if no data received within STALE_TIMEOUT, reconnect
                if now - last_data_time >= STALE_TIMEOUT:
                    log.warning("No data received for %ds, reconnecting...", STALE_TIMEOUT)
                    raise ConnectionError("Stale connection")
                
                if now - last_c_request >= POLL_C_INTERVAL:
                    try:
                        sock.sendall(b":C\n")
                        last_c_request = now
                    except OSError as exc:
                        log.warning("Failed to send :C poll command: %s", exc)
                        raise

                try:
                    chunk = sock.recv(512)
                except TimeoutError:
                    try:
                        sock.sendall(b":A\n")
                        continue
                    except OSError as exc:
                        log.warning("Keepalive heartbeat failed: %s", exc)
                        raise
                except OSError as exc:
                    log.warning("Socket read error encountered: %s", exc)
                    raise

                if not chunk:
                    raise ConnectionResetError("Connection closed by monitor")

                last_data_time = time.monotonic()
                buffer_bytes += chunk
                while b"\n" in buffer_bytes:
                    line_bytes, buffer_bytes = buffer_bytes.split(b"\n", 1)
                    line_str = line_bytes.decode("ascii", errors="replace").strip()
                    data = parse_line(line_str)
                    if data:
                        publish_state_map_throttled(mq, data)

        except (OSError, ConnectionError, ValueError) as exc:
            STATE.tcp_connected = False
            log.error("TCP error: %s; reconnecting in %ds", exc, reconnect_delay)
            publish_availability(mq, False)
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

def setup_signal_handlers(mq: mqtt.Client) -> None:
    def handle_exit(signum: int, frame: Any) -> None:
        log.info("Received shutdown signal. Clearing states...")
        try:
            publish_availability(mq, False)
            mq.loop_stop()
            mq.disconnect()
        except (OSError, RuntimeError) as exc:
            log.debug("Error during shutdown: %s", exc)
        log.info("Bridge exited cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

# Added properties parameter to support Paho MQTT VERSION2 compliance
def on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: int, properties: Any = None) -> None:
    if rc == 0:
        log.info("MQTT connected")
        publish_discovery(client)
        if STATE.tcp_connected:
            publish_availability(client, True)
    else:
        log.error("MQTT connect failed: rc=%s", rc)

# Added properties parameter to support Paho MQTT VERSION2 compliance
def on_disconnect(client: mqtt.Client, userdata: Any, rc: int, properties: Any = None) -> None:
    if rc != 0:
        log.warning("MQTT disconnected unexpectedly: rc=%s", rc)

def build_mqtt_client() -> mqtt.Client:
    # Swapped cleanly over to CallbackAPIVersion.VERSION2
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    if MQTT_USE_TLS:
        if MQTT_TLS_CA_CERT:
            client.tls_set(ca_certs=MQTT_TLS_CA_CERT)
        else:
            client.tls_set()
        log.info("MQTT TLS enabled")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.will_set(state_topic("availability"), "offline", retain=True)
    return client

def main() -> None:
    log.info("Starting Junctek KM140F TCP to MQTT bridge")
    mq = build_mqtt_client()

    while True:
        try:
            mq.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
            break
        except (OSError, ConnectionError) as exc:
            log.error("MQTT connect failed: %s; retrying in %ds", exc, RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)

    mq.loop_start()
    setup_signal_handlers(mq)
    tcp_loop(mq)

if __name__ == "__main__":
    main()
