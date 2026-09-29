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
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------
# Support for multiple devices: MONITOR_HOSTS="192.168.0.204,192.168.0.205"
MONITOR_HOSTS_STR = os.getenv("MONITOR_HOSTS", os.getenv("MONITOR_HOST", "192.168.0.204"))
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
SW_VERSION = os.getenv("SW_VERSION", "1.2.0")

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
ENABLE_HEALTH_CHECK = os.getenv("ENABLE_HEALTH_CHECK", "true").lower() in ("true", "1", "yes")
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "8081"))


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


# Global state instance
STATE = BridgeState()
TCP_CONNECTED = STATE.tcp_connected  # Backwards compatibility

# Data buffer for MQTT outage resilience
DATA_BUFFER: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAX_SIZE)

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

TEXT_SENSORS: list[dict[str, Any]] = [
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
        payload = json.dumps(STATE.last_published_values)
        result = mq.publish(state_topic("state"), payload, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Failed to publish state: rc=%s — buffering for later", result.rc)
            DATA_BUFFER.append({"topic": state_topic("state"), "payload": payload, "retain": True})
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
        errors.append(f"POLL_C_INTERVAL must be at least 1 second, got {POLL_C_INTERVAL}")
    if RECONNECT_DELAY < 1:
        errors.append(f"RECONNECT_DELAY must be at least 1 second, got {RECONNECT_DELAY}")
    if SOCKET_TIMEOUT < 1:
        errors.append(f"SOCKET_TIMEOUT must be at least 1 second, got {SOCKET_TIMEOUT}")
    if STALE_TIMEOUT < SOCKET_TIMEOUT:
        errors.append(f"STALE_TIMEOUT ({STALE_TIMEOUT}) should be >= SOCKET_TIMEOUT ({SOCKET_TIMEOUT})")
    return errors


def tcp_loop(mq: mqtt.Client) -> None:
    """Main TCP loop supporting multiple devices."""
    reconnect_delay = RECONNECT_DELAY

    while True:
        for host in MONITOR_HOSTS:
            _tcp_loop_single(mq, host, MONITOR_PORT, reconnect_delay)
            reconnect_delay = RECONNECT_DELAY  # Reset after each device attempt


def _tcp_loop_single(mq: mqtt.Client, host: str, port: int, reconnect_delay: int) -> None:
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
            log.error("TCP error for %s: %s; reconnecting in %ds", host, exc, reconnect_delay)
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


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP health check endpoint."""

    def do_GET(self) -> None:
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
                "uptime": time.monotonic() - STATE.metrics["uptime_seconds"] if STATE.metrics["uptime_seconds"] > 0 else 0,
            }
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

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

# Added properties parameter to support Paho MQTT VERSION2 compliance
def on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: int, properties: Any = None) -> None:
    if rc == 0:
        log.info("MQTT connected")
        STATE.mqtt_connected = True
        STATE.metrics["mqtt_reconnects"] += 1
        publish_discovery(client)
        if STATE.tcp_connected:
            publish_availability(client, True)
        _flush_buffer(client)
    else:
        log.error("MQTT connect failed: rc=%s", rc)

# Added properties parameter to support Paho MQTT VERSION2 compliance
def on_disconnect(client: mqtt.Client, userdata: Any, rc: int, properties: Any = None) -> None:
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
    client.will_set(state_topic("availability"), "offline", retain=True)
    return client

def main() -> None:
    log.info("Starting Junctek KM140F TCP to MQTT bridge")
    
    # Validate configuration
    config_errors = validate_config()
    if config_errors:
        for error in config_errors:
            log.error("Configuration error: %s", error)
        sys.exit(1)
    
    # Record start time for metrics
    STATE.metrics["uptime_seconds"] = time.monotonic()
    
    # Start health check server
    if ENABLE_HEALTH_CHECK:
        start_health_check_server()
    
    # Start metrics server
    if ENABLE_METRICS:
        start_metrics_server()
    
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
