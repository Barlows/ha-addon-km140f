#!/usr/bin/env python3
"""
Junctek KM140F — TCP to MQTT bridge
Connects to the monitor's WiFi module (port 8899), parses :A= and :C= lines,
and publishes sensor data to Home Assistant via MQTT Discovery.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from typing import Iterable

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
MONITOR_HOST = os.getenv("MONITOR_HOST", "192.168.0.204")
MONITOR_PORT = int(os.getenv("MONITOR_PORT", "8899"))

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "km140f")
MQTT_PASS = os.getenv("MQTT_PASS", "")  # Removed hardcoded fallback password for security

DEVICE_ID = os.getenv("DEVICE_ID", "junctek_km140f")
DEVICE_NAME = os.getenv("DEVICE_NAME", "Junctek KM140F")
SW_VERSION = os.getenv("SW_VERSION", "1.0.0")

POLL_C_INTERVAL = int(os.getenv("POLL_C_INTERVAL", "30"))
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "15"))
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))

# Global tracker to resolve MQTT reconnect race conditions safely
TCP_CONNECTED = False

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
            "state_topic": state_topic(sensor["key"]),
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

        mq.publish(
            discovery_topic("sensor", sensor["uid"]),
            json.dumps(payload),
            retain=True,
        )

    for sensor in TEXT_SENSORS:
        payload = {
            "name": f"{DEVICE_NAME} {sensor['name']}",
            "unique_id": f"{DEVICE_ID}_{sensor['uid']}",
            "state_topic": state_topic(sensor["key"]),
            "icon": sensor["icon"],
            "device": DEVICE_INFO,
            "availability_topic": state_topic("availability"),
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        mq.publish(
            discovery_topic("sensor", sensor["uid"]),
            json.dumps(payload),
            retain=True,
        )

    log.info("Published MQTT discovery config")

def publish_availability(mq: mqtt.Client, online: bool) -> None:
    mq.publish(
        state_topic("availability"),
        "online" if online else "offline",
        retain=True,
    )

def publish_state_map(mq: mqtt.Client, data: dict[str, object]) -> None:
    for key, value in data.items():
        mq.publish(state_topic(key), str(value), retain=True)

def parse_a(fields: list[str]) -> dict[str, object] | None:
    if len(fields) < 6:
        log.warning("Short A frame: %s", fields)
        return None

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

def parse_c(fields: list[str]) -> dict[str, object] | None:
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

def parse_line(line: str) -> dict[str, object] | None:
    line = line.strip()
    if not line:
        return None

    if "A=" in line:
        fields = line.split("A=", 1)[1].rstrip(",").split(",")
        return parse_a(fields)

    if "C=" in line:
        fields = line.split("C=", 1)[1].rstrip(",").split(",")
        return parse_c(fields)

    log.debug("Ignoring line: %r", line)
    return None

def extract_lines(buffer: str) -> tuple[list[str], str]:
    normalized = buffer.replace("\r\n", "\n").replace("\r", "\n")
    parts = normalized.split("\n")
    return parts[:-1], parts[-1]

def tcp_loop(mq: mqtt.Client) -> None:
    global TCP_CONNECTED
    last_c_request = 0.0

    while True:
        sock = None
        buffer = ""

        try:
            log.info("Connecting to monitor at %s:%d", MONITOR_HOST, MONITOR_PORT)
            sock = socket.create_connection((MONITOR_HOST, MONITOR_PORT), timeout=10)
            sock.settimeout(SOCKET_TIMEOUT)
            
            TCP_CONNECTED = True
            publish_availability(mq, True)
            log.info("Monitor connected")

            while True:
                now = time.monotonic()
                if now - last_c_request >= POLL_C_INTERVAL:
                    try:
                        sock.sendall(b":C\n")
                        last_c_request = now
                    except OSError as exc:
                        log.warning("Failed to send :C poll command: %s", exc)
                        raise

                try:
                    chunk = sock.recv(512)
                except socket.timeout:
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

                buffer += chunk.decode("ascii", errors="ignore")
                lines, buffer = extract_lines(buffer)

                for line in lines:
                    data = parse_line(line)
                    if data:
                        publish_state_map(mq, data)

        except Exception as exc:
            TCP_CONNECTED = False
            log.error("TCP error: %s; reconnecting in %ds", exc, RECONNECT_DELAY)
            publish_availability(mq, False)
            time.sleep(RECONNECT_DELAY)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("MQTT connected")
        publish_discovery(client)
        # Evaluates the actual state of the TCP client to prevent race conditions
        publish_availability(client, TCP_CONNECTED)
    else:
        log.error("MQTT connect failed: rc=%s", rc)

def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning("MQTT disconnected unexpectedly: rc=%s", rc)

def build_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=DEVICE_ID)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.will_set(state_topic("availability"), "offline", retain=True)
    return client

def main() -> None:
    log.info("Starting Junctek KM140F TCP to MQTT bridge")
    log.info("Monitor: %s:%d", MONITOR_HOST, MONITOR_PORT)
    log.info("MQTT: %s:%d", MQTT_HOST, MQTT_PORT)

    mq = build_mqtt_client()

    while True:
        try:
            mq.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
            break
        except Exception as exc:
            log.error("MQTT connect failed: %s; retrying in %ds", exc, RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)

    mq.loop_start()
    tcp_loop(mq)

if __name__ == "__main__":
    main()
