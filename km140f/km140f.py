#!/usr/bin/env python3
"""
Junctek KM140F — TCP to MQTT bridge
Connects to the monitor's WiFi module (port 8899), parses :A= and :C= lines,
and publishes sensor data to Home Assistant via MQTT Discovery.

Confirmed :A= field map:
  [0]  voltage,            *0.01  V
  [1]  current (abs),      *0.001 A
  [2]  direction,           0=discharging / 1=charging
  [3]  time remaining,      minutes
  [4]  remaining capacity,  *0.001 Ah
  [5]  set capacity,        *0.1   Ah
  [6]  display brightness   (ignored)
  [7]  protection limits    (ignored)
  [8]  device setting       (ignored)

Confirmed :C= field map:
  [0]  total energy charged,    *0.001 kWh
  [1]  total energy discharged, *0.001 kWh
"""

import os
import socket
import time
import json
import logging
import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
MONITOR_HOST = os.getenv("MONITOR_HOST", "192.168.1.100")  # WiFi module IP
MONITOR_PORT = int(os.getenv("MONITOR_PORT", "8899"))

MQTT_HOST    = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT    = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER    = os.getenv("MQTT_USER", "")
MQTT_PASS    = os.getenv("MQTT_PASS", "")

DEVICE_ID    = os.getenv("DEVICE_ID", "junctek_km140f")
DEVICE_NAME  = os.getenv("DEVICE_NAME", "Junctek KM140F")

POLL_C_INTERVAL = int(os.getenv("POLL_C_INTERVAL", "30"))  # seconds between :C requests
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))

# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("km140f")

# ---------------------------------------------------------------------------
# MQTT helpers
# ---------------------------------------------------------------------------
DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": DEVICE_NAME,
    "manufacturer": "Junctek",
    "model": "KM140F",
}

def discovery_topic(component, unique_id):
    return f"homeassistant/{component}/{DEVICE_ID}/{unique_id}/config"

def state_topic(key):
    return f"{DEVICE_ID}/{key}"

SENSORS = [
    # (unique_id, name, key, unit, device_class, state_class, icon)
    ("voltage",            "Voltage",               "voltage",            "V",   "voltage",     "measurement", None),
    ("current",            "Current",               "current",            "A",   "current",     "measurement", None),
    ("power",              "Power",                 "power",              "W",   "power",       "measurement", None),
    ("remaining_capacity", "Remaining Capacity",    "remaining_capacity", "Ah",  None,          "measurement", "mdi:battery-charging"),
    ("time_remaining",     "Time Remaining",        "time_remaining",     "min", None,          "measurement", "mdi:timer-sand"),
    ("set_capacity",       "Set Capacity",          "set_capacity",       "Ah",  None,          "measurement", "mdi:battery-charging-100"),
    ("soc",                "State of Charge",       "soc",                "%",   "battery",     "measurement", None),
    ("charge_kwh",         "Total Energy Charged",  "charge_kwh",         "kWh", "energy",      "total_increasing", None),
    ("discharge_kwh",      "Total Energy Discharged","discharge_kwh",     "kWh", "energy",      "total_increasing", None),
]

TEXT_SENSORS = [
    # (unique_id, name, key, icon)
    ("status", "Status", "status", "mdi:battery-arrow-up-outline"),
]

def publish_discovery(mq):
    """Publish MQTT Discovery configs for all sensors."""
    for uid, name, key, unit, dev_class, state_class, icon in SENSORS:
        payload = {
            "name": f"{DEVICE_NAME} {name}",
            "unique_id": f"{DEVICE_ID}_{uid}",
            "state_topic": state_topic(key),
            "unit_of_measurement": unit,
            "state_class": state_class,
            "device": DEVICE_INFO,
            "availability_topic": state_topic("availability"),
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        if dev_class:
            payload["device_class"] = dev_class
        if icon:
            payload["icon"] = icon

        mq.publish(discovery_topic("sensor", uid), json.dumps(payload), retain=True)

    for uid, name, key, icon in TEXT_SENSORS:
        payload = {
            "name": f"{DEVICE_NAME} {name}",
            "unique_id": f"{DEVICE_ID}_{uid}",
            "state_topic": state_topic(key),
            "icon": icon,
            "device": DEVICE_INFO,
            "availability_topic": state_topic("availability"),
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        mq.publish(discovery_topic("sensor", uid), json.dumps(payload), retain=True)

    log.info("MQTT Discovery configs published.")

def publish_availability(mq, online: bool):
    mq.publish(state_topic("availability"), "online" if online else "offline", retain=True)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_a(fields, mq):
    if len(fields) < 6:
        return
    try:
        raw0 = int(fields[0])
        raw1 = int(fields[1])
        charging = (int(fields[2]) == 1)
        mins     = int(fields[3])
        ah_rem   = int(fields[4]) / 1000.0
        capacity = int(fields[5]) / 10.0

        voltage  = raw0 / 100.0
        current  = raw1 / 1000.0
        # positive = charging, negative = discharging
        signed_current = current if charging else -current
        # power from raw integers to avoid float drift
        power = (raw0 * raw1) / 100000.0
        signed_power = power if charging else -power

        soc = round((ah_rem / capacity) * 100.0, 1) if capacity > 0 else 0
        soc = max(0.0, min(100.0, soc))

        status = "Charging" if charging else "Discharging"

        data = {
            "voltage":            round(voltage, 2),
            "current":            round(signed_current, 2),
            "power":              round(signed_power, 2),
            "remaining_capacity": round(ah_rem, 3),
            "time_remaining":     mins,
            "set_capacity":       round(capacity, 1),
            "soc":                soc,
            "status":             status,
        }
        for key, val in data.items():
            mq.publish(state_topic(key), str(val))

        log.debug("A: %s", data)
    except (ValueError, ZeroDivisionError) as e:
        log.warning("parse_a error: %s | fields: %s", e, fields)

def parse_c(fields, mq):
    if len(fields) < 2:
        return
    try:
        charge_kwh    = int(fields[0]) / 1000.0
        discharge_kwh = int(fields[1]) / 1000.0
        mq.publish(state_topic("charge_kwh"),    str(round(charge_kwh, 3)))
        mq.publish(state_topic("discharge_kwh"), str(round(discharge_kwh, 3)))
        log.debug("C: charged=%.3f kWh  discharged=%.3f kWh", charge_kwh, discharge_kwh)
    except ValueError as e:
        log.warning("parse_c error: %s | fields: %s", e, fields)

def parse_line(line: str, mq):
    line = line.strip()
    if not line:
        return
    for prefix in ("A=", "C="):
        idx = line.find(prefix)
        if idx != -1:
            csv = line[idx + 2:]
            fields = csv.rstrip(",").split(",")
            if prefix == "A=":
                parse_a(fields, mq)
            elif prefix == "C=":
                parse_c(fields, mq)
            return

# ---------------------------------------------------------------------------
# TCP connection loop
# ---------------------------------------------------------------------------
def tcp_loop(mq):
    buf = ""
    last_c_request = 0.0
    sock = None

    while True:
        try:
            log.info("Connecting to %s:%d ...", MONITOR_HOST, MONITOR_PORT)
            sock = socket.create_connection((MONITOR_HOST, MONITOR_PORT), timeout=10)
            sock.settimeout(15)
            publish_availability(mq, True)
            log.info("Connected.")

            while True:
                # Periodically request :C= (energy totals)
                now = time.time()
                if now - last_c_request >= POLL_C_INTERVAL:
                    sock.sendall(b":C\n")
                    last_c_request = now

                try:
                    chunk = sock.recv(256).decode("ascii", errors="ignore")
                except socket.timeout:
                    # Send a keepalive — monitor should respond with :A=
                    sock.sendall(b":A\n")
                    continue

                if not chunk:
                    raise ConnectionResetError("Connection closed by monitor")

                buf += chunk
                while "\n" in buf or "\r" in buf:
                    for sep in ("\r\n", "\n", "\r"):
                        if sep in buf:
                            line, buf = buf.split(sep, 1)
                            parse_line(line, mq)
                            break

        except Exception as e:
            log.error("TCP error: %s — reconnecting in %ds", e, RECONNECT_DELAY)
            publish_availability(mq, False)
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None
            time.sleep(RECONNECT_DELAY)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("Starting Junctek KM140F TCP→MQTT bridge")
    log.info("Monitor : %s:%d", MONITOR_HOST, MONITOR_PORT)
    log.info("MQTT    : %s:%d", MQTT_HOST, MQTT_PORT)

    mq = mqtt.Client(client_id=DEVICE_ID)
    if MQTT_USER:
        mq.username_pw_set(MQTT_USER, MQTT_PASS)

    mq.will_set(state_topic("availability"), "offline", retain=True)

    while True:
        try:
            mq.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            log.error("MQTT connect failed: %s — retrying in %ds", e, RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)

    mq.loop_start()
    publish_discovery(mq)

    tcp_loop(mq)

if __name__ == "__main__":
    main()
