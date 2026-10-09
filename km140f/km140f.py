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
import sqlite3
import sys
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
# Support for multiple devices: MONITOR_HOSTS="192.168.0.204,192.168.0.205"
MONITOR_HOSTS_STR = os.getenv(
    "MONITOR_HOSTS", os.getenv("MONITOR_HOST", "192.168.0.204")
)
MONITOR_HOSTS = [h.strip() for h in MONITOR_HOSTS_STR.split(",") if h.strip()]
MONITOR_PORT = int(os.getenv("MONITOR_PORT", "8899"))

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "km140f")
MQTT_PASS = os.getenv("MQTT_PASS", "")
MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() in ("true", "1", "yes")
MQTT_TLS_CA_CERT = os.getenv("MQTT_TLS_CA_CERT", "")

DEVICE_ID = os.getenv("DEVICE_ID", "junctek_km140f")
DEVICE_NAME = os.getenv("DEVICE_NAME", "Junctek KM140F")

# Software version reported to Home Assistant.
#
# This is deliberately NOT a user-configurable option. Home Assistant persists
# a user's saved options to /data/options.json, and changing a default in
# config.yaml does not update existing installs. Exposing the version as an
# option therefore left users stuck on a stale value after upgrades.
# Instead we bake it in at image build time from config.yaml so it always
# matches the installed add-on version.
SW_VERSION = os.getenv("BUILD_VERSION") or os.getenv("SW_VERSION") or "2.4.0"

POLL_C_INTERVAL = int(os.getenv("POLL_C_INTERVAL", "30"))
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))
MAX_RECONNECT_DELAY = int(os.getenv("MAX_RECONNECT_DELAY", "300"))
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "15"))
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
STALE_TIMEOUT = int(os.getenv("STALE_TIMEOUT", "60"))

# Throttle configuration (Debouncing)
THROTTLE_HEARTBEAT_INTERVAL = 10.0

# Data buffering
BUFFER_MAX_SIZE = int(os.getenv("BUFFER_MAX_SIZE", "1000"))
ENABLE_METRICS = os.getenv("ENABLE_METRICS", "false").lower() in ("true", "1", "yes")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8080"))
ENABLE_HEALTH_CHECK = os.getenv("ENABLE_HEALTH_CHECK", "true").lower() in (
    "true",
    "1",
    "yes",
)
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "8081"))

# Data persistence
ENABLE_PERSISTENCE = os.getenv("ENABLE_PERSISTENCE", "true").lower() in (
    "true",
    "1",
    "yes",
)
DB_PATH = os.getenv("DB_PATH", "/data/km140f.db")
DB_RETENTION_DAYS = int(os.getenv("DB_RETENTION_DAYS", "30"))

# Alerting
ENABLE_ALERTS = os.getenv("ENABLE_ALERTS", "true").lower() in ("true", "1", "yes")
# Voltage thresholds. Empty by default so they can be derived from the
# observed battery voltage class (see detect_voltage_class). Setting these
# explicitly disables auto-detection.
ALERT_VOLTAGE_MIN_RAW = os.getenv("ALERT_VOLTAGE_MIN", "").strip()
ALERT_VOLTAGE_MAX_RAW = os.getenv("ALERT_VOLTAGE_MAX", "").strip()
ALERT_SOC_MIN = float(os.getenv("ALERT_SOC_MIN", "20.0"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "300"))

# Nominal voltage bands for common lead-acid/lithium pack sizes. Thresholds are
# derived from these so a 12 V install does not get 48 V limits, and vice versa.
# (nominal, low, high, charge ceiling)
VOLTAGE_BANDS: list[tuple[float, float, float, float]] = [
    (12.0, 11.0, 14.4, 14.6),
    (24.0, 22.0, 28.8, 29.2),
    (36.0, 33.0, 43.2, 43.8),
    (48.0, 44.0, 57.6, 58.4),
]


def detect_voltage_class(voltage: float) -> tuple[float, float, float]:
    """Return (nominal, min, max) for the pack matching an observed voltage.

    Picks the band whose nominal is closest in log space, so a 52 V reading on
    a 48 V pack resolves to 48 V rather than drifting toward 24 V.
    """
    best = VOLTAGE_BANDS[-1]
    best_err = float("inf")
    for band in VOLTAGE_BANDS:
        err = abs(voltage - band[0]) / band[0]
        if err < best_err:
            best_err = err
            best = band
    return best[0], best[1], best[2]


def resolve_voltage_thresholds() -> tuple[float | None, float | None]:
    """Return explicit thresholds if configured, else None to signal auto."""
    if ALERT_VOLTAGE_MIN_RAW and ALERT_VOLTAGE_MAX_RAW:
        return float(ALERT_VOLTAGE_MIN_RAW), float(ALERT_VOLTAGE_MAX_RAW)
    return None, None


# Web UI
ENABLE_WEB_UI = os.getenv("ENABLE_WEB_UI", "true").lower() in ("true", "1", "yes")
WEB_UI_PORT = int(os.getenv("WEB_UI_PORT", "8082"))


class BridgeState:
    """Encapsulates mutable bridge state for better testability."""

    def __init__(self) -> None:
        self.tcp_connected: bool = False
        self.mqtt_connected: bool = False
        self.last_published_values: dict[str, Any] = {}
        self.last_heartbeat_time: float = 0.0
        self.last_data_time: float = 0.0
        self.metrics: dict[str, Any] = {
            "tcp_reconnects": 0,
            "mqtt_reconnects": 0,
            "messages_published": 0,
            "messages_dropped": 0,
            "buffer_size": 0,
            "uptime_seconds": 0.0,
        }
        self.alerts: dict[str, float] = {}  # alert_name -> last_triggered_time
        self.last_persist_time: float = 0.0
        self.last_cleanup_time: float = 0.0
        self.nominal_voltage: float = 0.0
        self.volts_min: float = 0.0
        self.volts_max: float = 0.0
        self.volts_auto: bool = True
        self.active_alerts: list[str] = []


# Global state instance
STATE = BridgeState()

# Data buffer for MQTT outage resilience
DATA_BUFFER: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAX_SIZE)


class DataPersistence:
    """SQLite-based data persistence for historical analysis."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sensor_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    voltage REAL,
                    current REAL,
                    power REAL,
                    remaining_capacity REAL,
                    time_remaining INTEGER,
                    set_capacity REAL,
                    soc REAL,
                    status TEXT,
                    charge_kwh REAL,
                    discharge_kwh REAL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON sensor_data(timestamp)
            """)
            conn.commit()

    def insert(self, data: dict[str, Any]) -> None:
        """Insert sensor data into the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO sensor_data 
                    (timestamp, voltage, current, power, remaining_capacity, 
                     time_remaining, set_capacity, soc, status, charge_kwh, discharge_kwh)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        time.time(),
                        data.get("voltage"),
                        data.get("current"),
                        data.get("power"),
                        data.get("remaining_capacity"),
                        data.get("time_remaining"),
                        data.get("set_capacity"),
                        data.get("soc"),
                        data.get("status"),
                        data.get("charge_kwh"),
                        data.get("discharge_kwh"),
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.warning("Failed to persist data: %s", exc)

    def cleanup_old_data(self, retention_days: int) -> None:
        """Remove data older than retention_days."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM sensor_data WHERE timestamp < ?",
                    (time.time() - retention_days * 86400,),
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.warning("Failed to cleanup old data: %s", exc)

    def get_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent sensor data."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM sensor_data ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            log.warning("Failed to get recent data: %s", exc)
            return []


# Global persistence instance
DB: DataPersistence | None = None

# How often to write a row to SQLite, and how often to purge old rows.
DB_WRITE_INTERVAL = int(os.getenv("DB_WRITE_INTERVAL", "60"))
DB_CLEANUP_INTERVAL = int(os.getenv("DB_CLEANUP_INTERVAL", "21600"))


def _persist_and_maybe_cleanup(data: dict[str, Any]) -> None:
    """Persist a sample on a fixed interval and purge old rows periodically.

    Writing every incoming frame would produce tens of thousands of rows per
    day for no benefit, so samples are downsampled to DB_WRITE_INTERVAL.
    """
    if DB is None:
        return

    now = time.monotonic()

    if now - STATE.last_persist_time >= DB_WRITE_INTERVAL:
        STATE.last_persist_time = now
        # Merge with the last known values so cumulative :C= fields persist
        # even on frames that don't carry them.
        merged = {**STATE.last_published_values, **data}
        DB.insert(merged)

    if now - STATE.last_cleanup_time >= DB_CLEANUP_INTERVAL:
        STATE.last_cleanup_time = now
        DB.cleanup_old_data(DB_RETENTION_DAYS)
        log.debug("Purged sensor data older than %d days", DB_RETENTION_DAYS)


LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()

if LOG_FORMAT == "json":

    class JSONFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            log_data = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "message": record.getMessage(),
                "logger": record.name,
            }
            if record.exc_info:
                log_data["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_data)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(message)s",
    )
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.getLogger().handlers = [handler]
else:
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

# Origin block — required for device discovery, recommended for single-component.
# Tells Home Assistant which add-on produced these entities.
ORIGIN_INFO = {
    "name": "Junctek KM140F Add-on",
    "sw_version": SW_VERSION,
    "support_url": "https://github.com/Barlows/ha-addon-km140f/issues",
}

SENSORS: list[dict[str, Any]] = [
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
        "device_class": "duration",
        "state_class": "measurement",
        "precision": 0,
    },
    {
        "uid": "alert_state",
        "name": "Alert State",
        "key": "alert_state",
        "icon": "mdi:alert-circle-outline",
        "device_class": "enum",
        "options": ["ok", "alert"],
    },
    {
        "uid": "nominal_voltage",
        "name": "Nominal Voltage",
        "key": "nominal_voltage",
        "unit": "V",
        "icon": "mdi:sine-wave",
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

TEXT_SENSORS: list[dict[str, Any]] = [
    {
        "uid": "status",
        "name": "Status",
        "key": "status",
        "icon": "mdi:battery-arrow-up-outline",
    },
]


def state_topic(key: str) -> str:
    return f"{DEVICE_ID}/{key}"


def build_component(sensor: dict[str, Any], platform: str = "sensor") -> dict[str, Any]:
    """Build a single component config for device discovery."""
    component: dict[str, Any] = {
        "p": platform,
        "name": sensor["name"],
        "unique_id": f"{DEVICE_ID}_{sensor['uid']}",
        "state_topic": state_topic("state"),
        "value_template": f"{{{{ value_json.{sensor['key']} | default(None) }}}}",
    }

    if sensor.get("device_class"):
        component["device_class"] = sensor["device_class"]
    if sensor.get("unit"):
        component["unit_of_measurement"] = sensor["unit"]
    if sensor.get("state_class"):
        component["state_class"] = sensor["state_class"]
    if sensor.get("icon"):
        component["icon"] = sensor["icon"]
    if sensor.get("precision") is not None:
        component["suggested_display_precision"] = sensor["precision"]

    return component


def publish_discovery(mq: mqtt.Client) -> None:
    """Publish a single device discovery payload covering all entities.

    Uses MQTT device discovery (one message) rather than per-entity discovery,
    as recommended for devices with multiple components.
    """
    components: dict[str, Any] = {}
    for sensor in SENSORS:
        components[sensor["uid"]] = build_component(sensor)
    for sensor in TEXT_SENSORS:
        components[sensor["uid"]] = build_component(sensor)

    payload = {
        "dev": DEVICE_INFO,
        "o": ORIGIN_INFO,
        "cmps": components,
        "state_topic": state_topic("state"),
        "qos": 1,
        "availability": [
            {
                "topic": state_topic("availability"),
                "payload_available": "online",
                "payload_not_available": "offline",
            }
        ],
    }

    mq.publish(
        f"homeassistant/device/{DEVICE_ID}/config", json.dumps(payload), retain=True
    )

    # Alert sensors live on their own topics rather than the unified state
    # payload, so they update independently of the throttled sensor stream.
    alert_payload = {
        "dev": DEVICE_INFO,
        "o": ORIGIN_INFO,
        "cmps": {
            "alert_state": {
                "p": "sensor",
                "name": "Alert State",
                "unique_id": f"{DEVICE_ID}_alert_state",
                "state_topic": state_topic("alert_state"),
                "icon": "mdi:alert-circle-outline",
                "device_class": "enum",
                "options": ["ok", "alert"],
            }
        },
        "qos": 1,
        "availability": [
            {
                "topic": state_topic("availability"),
                "payload_available": "online",
                "payload_not_available": "offline",
            }
        ],
    }
    mq.publish(
        f"homeassistant/device/{DEVICE_ID}_alerts/config",
        json.dumps(alert_payload),
        retain=True,
    )
    log.info("Published device discovery payload with %d components", len(components))


def publish_availability(mq: mqtt.Client, online: bool) -> None:
    mq.publish(
        state_topic("availability"), "online" if online else "offline", retain=True
    )


def publish_alerts(mq: mqtt.Client, alerts: list[str]) -> None:
    """Publish alert summary so Home Assistant automations can react.

    Emits a JSON payload on <device>/alerts and a plain 'ok'/'alerts' state on
    <device>/alert_state, letting users build automations without parsing logs.
    """
    payload = {
        "active": len(alerts) > 0,
        "count": len(alerts),
        "alerts": alerts,
        "nominal_voltage": STATE.nominal_voltage,
    }
    mq.publish(state_topic("alerts"), json.dumps(payload), retain=True)
    mq.publish(
        state_topic("alert_state"),
        "ok" if not alerts else "alert",
        retain=True,
    )


def publish_state_map_throttled(mq: mqtt.Client, data: dict[str, Any]) -> None:
    # Reflect the detected pack size so the Nominal Voltage sensor updates
    # even on frames that carry no voltage field.
    if STATE.nominal_voltage and "nominal_voltage" not in data:
        data = {**data, "nominal_voltage": STATE.nominal_voltage}

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
        payload = json.dumps(STATE.last_published_values)
        result = mq.publish(state_topic("state"), payload, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning(
                "Failed to publish state: rc=%s — buffering for later", result.rc
            )
            DATA_BUFFER.append(
                {"topic": state_topic("state"), "payload": payload, "retain": True}
            )
            STATE.metrics["messages_dropped"] += 1
        else:
            STATE.metrics["messages_published"] += 1
            # Flush buffer if we have data queued
            _flush_buffer(mq)


def _flush_buffer(mq: mqtt.Client) -> None:
    """Flush buffered messages when MQTT connection is restored."""
    while DATA_BUFFER:
        msg = DATA_BUFFER[0]
        result = mq.publish(msg["topic"], msg["payload"], retain=msg["retain"])
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Failed to flush buffered message: rc=%s", result.rc)
            break
        DATA_BUFFER.popleft()
        log.debug("Flushed buffered message: %s", msg["topic"])


def _write_to_fallback_file(data: dict[str, Any]) -> None:
    """Write data to fallback file when MQTT is unavailable."""
    fallback_path = os.getenv("FALLBACK_FILE_PATH", "/data/km140f_fallback.jsonl")
    try:
        os.makedirs(os.path.dirname(fallback_path), exist_ok=True)
        with open(fallback_path, "a") as f:
            f.write(json.dumps(data) + "\n")
        log.debug("Wrote data to fallback file: %s", fallback_path)
    except OSError as exc:
        log.warning("Failed to write to fallback file: %s", exc)


def update_voltage_class(voltage: float) -> None:
    """Derive alert thresholds from the observed pack voltage, once."""
    if not STATE.volts_auto:
        return

    nominal, low, high = detect_voltage_class(voltage)

    # Only latch once we've seen a plausible reading; avoid latching on a
    # transient startup spike by requiring two consistent observations.
    if STATE.nominal_voltage == 0.0:
        STATE.nominal_voltage = nominal
        return

    if abs(voltage - STATE.nominal_voltage) / STATE.nominal_voltage > 0.5:
        # Reading is far outside the band we latched — re-evaluate.
        STATE.nominal_voltage = nominal

    STATE.volts_min = low
    STATE.volts_max = high


def check_alerts(mq: mqtt.Client, data: dict[str, Any]) -> None:
    """Check sensor data against alert thresholds.

    Voltage bounds are derived from the detected pack size unless the user
    configured them explicitly, so a 12 V install is not judged by 48 V limits.
    """
    if not ENABLE_ALERTS:
        return

    now = time.monotonic()
    alerts: list[str] = []

    voltage = data.get("voltage")
    if voltage is not None:
        update_voltage_class(voltage)

        vmin, vmax = resolve_voltage_thresholds()
        if vmin is None:
            vmin, vmax = STATE.volts_min, STATE.volts_max

        if vmin and vmax:
            if voltage < vmin:
                alerts.append(f"voltage_low: {voltage}V < {vmin}V")
            elif voltage > vmax:
                alerts.append(f"voltage_high: {voltage}V > {vmax}V")

    soc = data.get("soc")
    if soc is not None and soc < ALERT_SOC_MIN:
        alerts.append(f"soc_low: {soc}% < {ALERT_SOC_MIN}%")

    # Log newly active alerts, respecting the cooldown for repeats.
    for alert in alerts:
        last_triggered = STATE.alerts.get(alert, 0)
        if now - last_triggered >= ALERT_COOLDOWN:
            STATE.alerts[alert] = now
            log.warning("ALERT: %s", alert)

    STATE.active_alerts = alerts
    publish_alerts(mq, alerts)


class WebUIHandler(BaseHTTPRequestHandler):
    """Simple web interface for live data and configuration."""

    def do_GET(self) -> None:
        try:
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{DEVICE_NAME}</title>
    <meta http-equiv="refresh" content="5">
    <style>
        body {{ font-family: sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }}
        .card {{ background: #16213e; padding: 20px; border-radius: 10px; margin: 10px 0; }}
        .metric {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .value {{ font-size: 2em; font-weight: bold; color: #00ff88; }}
        .label {{ font-size: 0.9em; color: #888; }}
        .status {{ padding: 5px 10px; border-radius: 5px; display: inline-block; }}
        .online {{ background: #00ff88; color: #000; }}
        .offline {{ background: #ff4444; color: #fff; }}
    </style>
</head>
<body>
    <h1>{DEVICE_NAME}</h1>
    <div class="card">
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("voltage", "—")}</div>
            <div class="label">Voltage (V)</div>
        </div>
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("current", "—")}</div>
            <div class="label">Current (A)</div>
        </div>
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("power", "—")}</div>
            <div class="label">Power (W)</div>
        </div>
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("soc", "—")}</div>
            <div class="label">State of Charge (%)</div>
        </div>
    </div>
    <div class="card">
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("remaining_capacity", "—")}</div>
            <div class="label">Remaining Capacity (Ah)</div>
        </div>
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("time_remaining", "—")}</div>
            <div class="label">Time Remaining (min)</div>
        </div>
        <div class="metric">
            <div class="value">{STATE.last_published_values.get("status", "—")}</div>
            <div class="label">Status</div>
        </div>
    </div>
    <div class="card">
        <p>TCP: <span class="status {"online" if STATE.tcp_connected else "offline"}">{"Online" if STATE.tcp_connected else "Offline"}</span></p>
        <p>MQTT: <span class="status {"online" if STATE.mqtt_connected else "offline"}">{"Online" if STATE.mqtt_connected else "Offline"}</span></p>
        <p>Buffer: {len(DATA_BUFFER)} messages</p>
        <p>Uptime: {time.monotonic() - STATE.metrics["uptime_seconds"]:.0f}s</p>
    </div>
</body>
</html>"""
                self.wfile.write(html.encode())
            elif self.path == "/api/data":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(STATE.last_published_values).encode())
            elif self.path.startswith("/api/history"):
                limit = 100
                if "?" in self.path:
                    query = self.path.split("?", 1)[1]
                    for part in query.split("&"):
                        if part.startswith("limit="):
                            try:
                                limit = max(1, min(1000, int(part[6:])))
                            except ValueError:
                                pass
                rows = DB.get_recent(limit) if DB else []
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(rows).encode())
            else:
                self.send_response(404)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected before response was sent

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default logging


def start_web_ui_server() -> None:
    """Start the web UI HTTP server in a background thread."""
    try:
        server = HTTPServer(("0.0.0.0", WEB_UI_PORT), WebUIHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log.info("Web UI server started on port %d", WEB_UI_PORT)
    except OSError as exc:
        log.warning("Failed to start web UI server: %s", exc)


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


def validate_config() -> list[str]:
    """Validate configuration and return list of errors."""
    errors = []
    if not MONITOR_HOSTS:
        errors.append("MONITOR_HOSTS cannot be empty")
    if not (1 <= MONITOR_PORT <= 65535):
        errors.append(f"MONITOR_PORT must be between 1 and 65535, got {MONITOR_PORT}")
    if not (1 <= MQTT_PORT <= 65535):
        errors.append(f"MQTT_PORT must be between 1 and 65535, got {MQTT_PORT}")
    if POLL_C_INTERVAL < 1:
        errors.append(
            f"POLL_C_INTERVAL must be at least 1 second, got {POLL_C_INTERVAL}"
        )
    if RECONNECT_DELAY < 1:
        errors.append(
            f"RECONNECT_DELAY must be at least 1 second, got {RECONNECT_DELAY}"
        )
    if SOCKET_TIMEOUT < 1:
        errors.append(f"SOCKET_TIMEOUT must be at least 1 second, got {SOCKET_TIMEOUT}")
    if STALE_TIMEOUT < SOCKET_TIMEOUT:
        errors.append(
            f"STALE_TIMEOUT ({STALE_TIMEOUT}) should be >= SOCKET_TIMEOUT ({SOCKET_TIMEOUT})"
        )
    return errors


def tcp_loop(mq: mqtt.Client) -> None:
    """Main TCP loop supporting multiple devices."""
    reconnect_delay = RECONNECT_DELAY

    while True:
        for host in MONITOR_HOSTS:
            _tcp_loop_single(mq, host, MONITOR_PORT, reconnect_delay)
            reconnect_delay = RECONNECT_DELAY  # Reset after each device attempt


def _tcp_loop_single(
    mq: mqtt.Client, host: str, port: int, reconnect_delay: int
) -> None:
    """Handle TCP connection for a single device."""
    last_c_request = 0.0
    last_data_time = 0.0

    while True:
        sock = None
        buffer_bytes = b""

        try:
            log.info("Connecting to monitor at %s:%d", host, port)
            sock = socket.create_connection((host, port), timeout=10)
            sock.settimeout(SOCKET_TIMEOUT)

            STATE.tcp_connected = True
            STATE.metrics["tcp_reconnects"] += 1
            last_data_time = time.monotonic()
            publish_availability(mq, True)
            log.info("Bridge status is now ONLINE")

            while True:
                now = time.monotonic()

                # Staleness watchdog: if no data received within STALE_TIMEOUT, reconnect
                if now - last_data_time >= STALE_TIMEOUT:
                    log.warning(
                        "No data received for %ds, reconnecting...", STALE_TIMEOUT
                    )
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
                        check_alerts(mq, data)
                        _persist_and_maybe_cleanup(data)

        except (OSError, ConnectionError, ValueError) as exc:
            STATE.tcp_connected = False
            log.error(
                "TCP error for %s: %s; reconnecting in %ds", host, exc, reconnect_delay
            )
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

    def handle_reload(signum: int, frame: Any) -> None:
        log.info("Received SIGHUP — reloading configuration...")
        reload_config()

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGHUP, handle_reload)


def reload_config() -> None:
    """Reload configuration from environment variables."""
    global MONITOR_HOSTS, MONITOR_PORT, MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS
    global DEVICE_ID, DEVICE_NAME, POLL_C_INTERVAL, RECONNECT_DELAY
    global SOCKET_TIMEOUT, STALE_TIMEOUT, BUFFER_MAX_SIZE

    MONITOR_HOSTS_STR = os.getenv(
        "MONITOR_HOSTS", os.getenv("MONITOR_HOST", "192.168.0.204")
    )
    MONITOR_HOSTS = [h.strip() for h in MONITOR_HOSTS_STR.split(",") if h.strip()]
    MONITOR_PORT = int(os.getenv("MONITOR_PORT", "8899"))
    MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
    MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
    MQTT_USER = os.getenv("MQTT_USER", "km140f")
    MQTT_PASS = os.getenv("MQTT_PASS", "")
    DEVICE_ID = os.getenv("DEVICE_ID", "junctek_km140f")
    DEVICE_NAME = os.getenv("DEVICE_NAME", "Junctek KM140F")
    POLL_C_INTERVAL = int(os.getenv("POLL_C_INTERVAL", "30"))
    RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))
    SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "15"))
    STALE_TIMEOUT = int(os.getenv("STALE_TIMEOUT", "60"))
    BUFFER_MAX_SIZE = int(os.getenv("BUFFER_MAX_SIZE", "1000"))

    log.info(
        "Configuration reloaded: %d monitor(s), MQTT %s:%d",
        len(MONITOR_HOSTS),
        MQTT_HOST,
        MQTT_PORT,
    )


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP health check endpoint."""

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                status = 200 if STATE.tcp_connected and STATE.mqtt_connected else 503
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response = {
                    "status": "healthy" if status == 200 else "unhealthy",
                    "tcp_connected": STATE.tcp_connected,
                    "mqtt_connected": STATE.mqtt_connected,
                    "buffer_size": len(DATA_BUFFER),
                    "uptime": time.monotonic() - STATE.metrics["uptime_seconds"]
                    if STATE.metrics["uptime_seconds"] > 0
                    else 0,
                }
                self.wfile.write(json.dumps(response).encode())
            else:
                self.send_response(404)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected before response was sent

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default logging


def start_health_check_server() -> None:
    """Start the health check HTTP server in a background thread."""
    try:
        server = HTTPServer(("0.0.0.0", HEALTH_CHECK_PORT), HealthCheckHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log.info("Health check server started on port %d", HEALTH_CHECK_PORT)
    except OSError as exc:
        log.warning("Failed to start health check server: %s", exc)


def start_metrics_server() -> None:
    """Start the Prometheus metrics HTTP server in a background thread."""
    try:
        server = HTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log.info("Metrics server started on port %d", METRICS_PORT)
    except OSError as exc:
        log.warning("Failed to start metrics server: %s", exc)


class MetricsHandler(BaseHTTPRequestHandler):
    """Prometheus metrics endpoint."""

    def do_GET(self) -> None:
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            metrics_text = f"""# HELP km140f_tcp_connected TCP connection status
# TYPE km140f_tcp_connected gauge
km140f_tcp_connected {1 if STATE.tcp_connected else 0}
# HELP km140f_mqtt_connected MQTT connection status
# TYPE km140f_mqtt_connected gauge
km140f_mqtt_connected {1 if STATE.mqtt_connected else 0}
# HELP km140f_tcp_reconnects Total TCP reconnections
# TYPE km140f_tcp_reconnects counter
km140f_tcp_reconnects {STATE.metrics["tcp_reconnects"]}
# HELP km140f_mqtt_reconnects Total MQTT reconnections
# TYPE km140f_mqtt_reconnects counter
km140f_mqtt_reconnects {STATE.metrics["mqtt_reconnects"]}
# HELP km140f_messages_published Total messages published
# TYPE km140f_messages_published counter
km140f_messages_published {STATE.metrics["messages_published"]}
# HELP km140f_messages_dropped Total messages dropped
# TYPE km140f_messages_dropped counter
km140f_messages_dropped {STATE.metrics["messages_dropped"]}
# HELP km140f_buffer_size Current buffer size
# TYPE km140f_buffer_size gauge
km140f_buffer_size {len(DATA_BUFFER)}
# HELP km140f_uptime_seconds Uptime in seconds
# TYPE km140f_uptime_seconds gauge
km140f_uptime_seconds {time.monotonic() - STATE.metrics["uptime_seconds"] if STATE.metrics["uptime_seconds"] > 0 else 0}
"""
            self.wfile.write(metrics_text.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default logging


# HA birth message topic — HA publishes "online" here when it starts up.
# Entities are unavailable until a discovery message is received, so we must
# re-publish discovery on this event or entities stay unavailable after an
# HA restart.
HA_STATUS_TOPIC = "homeassistant/status"


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    """Handle HA birth messages so entities are restored after an HA restart."""
    payload = msg.payload.decode("utf-8", errors="replace").strip()
    if payload == "online":
        log.info("HA birth message received — republishing discovery")
        publish_discovery(client)
        if STATE.tcp_connected:
            publish_availability(client, True)


# Added properties parameter to support Paho MQTT VERSION2 compliance
def on_connect(
    client: mqtt.Client, userdata: Any, flags: Any, rc: int, properties: Any = None
) -> None:
    if rc == 0:
        log.info("MQTT connected")
        STATE.mqtt_connected = True
        STATE.metrics["mqtt_reconnects"] += 1
        publish_discovery(client)
        # Subscribe to HA birth messages so entities recover after HA restart
        client.subscribe(HA_STATUS_TOPIC, qos=1)
        log.debug("Subscribed to %s for birth messages", HA_STATUS_TOPIC)
        if STATE.tcp_connected:
            publish_availability(client, True)
        _flush_buffer(client)
    else:
        log.error("MQTT connect failed: rc=%s", rc)


# Added properties parameter to support Paho MQTT VERSION2 compliance
def on_disconnect(
    client: mqtt.Client, userdata: Any, rc: int, properties: Any = None
) -> None:
    STATE.mqtt_connected = False
    if rc != 0:
        log.warning("MQTT disconnected unexpectedly: rc=%s — will auto-reconnect", rc)
    else:
        log.info("MQTT disconnected cleanly")


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
    client.on_message = on_message
    client.will_set(state_topic("availability"), "offline", retain=True)
    return client


def main() -> None:
    global DB

    log.info("Starting Junctek KM140F TCP to MQTT bridge")

    # Validate configuration
    config_errors = validate_config()
    if config_errors:
        for error in config_errors:
            log.error("Configuration error: %s", error)
        sys.exit(1)

    # Record start time for metrics
    STATE.metrics["uptime_seconds"] = time.monotonic()

    # Initialize data persistence
    if ENABLE_PERSISTENCE:
        DB = DataPersistence(DB_PATH)
        log.info("Data persistence enabled: %s", DB_PATH)

    # Start health check server
    if ENABLE_HEALTH_CHECK:
        start_health_check_server()

    # Start metrics server
    if ENABLE_METRICS:
        start_metrics_server()

    # Start web UI server
    if ENABLE_WEB_UI:
        start_web_ui_server()

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
