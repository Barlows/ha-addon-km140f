# Junctek KM140F — Home Assistant Add-on

https://s.click.aliexpress.com/e/_c4MP4mrn

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

TCP → MQTT bridge for the **Junctek KM140F** battery monitor connected via a WiFi module (port 8899).

The add-on connects to the monitor over TCP, parses the push data stream (`:A=` lines) and energy totals (`:C=` lines), and publishes everything to Home Assistant via **MQTT Discovery** — no manual entity configuration needed.

---

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**
2. Click **⋮ (three dots) → Repositories**
3. Add `https://github.com/Barlows/ha-addon-km140f`
4. Find **Junctek KM140F** in the store and click **Install**

---

## Prerequisites

- Junctek KM140F with a WiFi module (known IP address, port 8899)
- **Mosquitto** MQTT broker add-on installed and running

---

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `monitor_host` | `192.168.0.204` | IP address of the KM140F WiFi module |
| `monitor_port` | `8899` | TCP port (fixed by Junctek) |
| `mqtt_host` | `core-mosquitto` | MQTT broker hostname |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | _(empty)_ | MQTT username (if auth enabled) |
| `mqtt_pass` | _(empty)_ | MQTT password |
| `mqtt_use_tls` | `false` | Enable TLS encryption for MQTT connection |
| `mqtt_tls_ca_cert` | _(empty)_ | Path to custom CA certificate (optional) |
| `device_id` | `junctek_km140f` | Unique device ID (used as MQTT topic prefix) |
| `device_name` | `Junctek KM140F` | Friendly name shown in Home Assistant |
| ~~`sw_version`~~ | — | *Removed in v2.3.1 — now derived from the add-on version automatically* |
| `poll_c_interval` | `30` | How often to request `:C=` energy totals (seconds) |
| `reconnect_delay` | `5` | Seconds between TCP reconnection attempts |
| `socket_timeout` | `15` | TCP socket timeout (seconds) |
| `mqtt_keepalive` | `60` | MQTT keepalive interval (seconds) |

---

## Entities created in Home Assistant

| Entity | Unit | Notes |
|--------|------|-------|
| Voltage | V | |
| Current | A | positive = charging, negative = discharging |
| Power | W | positive = charging, negative = discharging |
| Remaining Capacity | Ah | |
| Time Remaining | min | to full discharge or charge (duration sensor) |
| Nominal Voltage | V | auto-detected pack size |
| Alert State | — | `ok` or `alert` |
| Set Capacity | Ah | configured in the monitor |
| State of Charge | % | calculated from Remaining / Set Capacity |
| Total Energy Charged | kWh | cumulative, from `:C=` |
| Total Energy Discharged | kWh | cumulative, from `:C=` |
| Status | text | `Charging` or `Discharging` |

---

## Features

- **Automatic discovery**: Sensors are created automatically via a single MQTT device-discovery message
- **Recovers after HA restart**: Re-publishes discovery on the Home Assistant birth message
- **Staleness watchdog**: Automatically reconnects if no data is received for 60 seconds
- **Exponential backoff**: Reconnection delay increases gradually (up to 5 minutes max)
- **MQTT TLS support**: Optional TLS encryption for secure MQTT connections
- **Robust parsing**: Handles `\r\n` line endings, extra fields, and malformed data gracefully
- **Clean shutdown**: Properly publishes offline status on SIGTERM/SIGINT
- **Data buffering**: Messages buffered when MQTT is down, flushed on reconnect
- **Health check endpoint**: HTTP endpoint at `/health` (port 8081)
- **Prometheus metrics**: Metrics at `/metrics` (port 8080)
- **Multiple device support**: Configure multiple KM140F devices via `MONITOR_HOSTS`
- **Graceful degradation**: Data written to fallback file when MQTT unavailable
- **Structured logging**: JSON log format support via `LOG_FORMAT=json`
- **Configuration validation**: Validates at startup with helpful errors
- **Docker healthcheck**: `HEALTHCHECK` instruction in Dockerfile
- **Automatic voltage class detection**: Alert thresholds adapt to 12 V / 24 V / 36 V / 48 V packs
- **Actionable alerts**: Alerts published to MQTT so automations can react
- **Configuration hot-reload**: Reload config via SIGHUP without restarting
- **Data persistence**: SQLite storage for historical data (30-day retention)
- **Alerting**: Threshold-based alerts for voltage and SOC
- **Web UI**: Simple web interface at port 8082 for live data
- **Docker Compose**: Easy local development with `docker-compose up`

---

## Protocol notes

The KM140F WiFi module streams data automatically without polling.
Push format (confirmed by field-testing against display readings):

```
:A=<voltage*100>,<current*1000>,<direction>,<minutes>,<ah*1000>,<capacity*10>,...
:C=<charged_kwh*1000>,<discharged_kwh*1000>,...
```

Direction: `0` = discharging, `1` = charging.

---

## Releases

| Version | Description |
|---------|-------------|
| [v2.4.0](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.4.0) | Auto voltage class detection, actionable alerts, duration sensor |
| [v2.3.1](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.3.1) | Firmware version now tracks the actual add-on version |
| [v2.3.0](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.3.0) | HA restart recovery, DB cleanup, device discovery, doubled-name fix |
| [v2.2.3](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.2.3) | BrokenPipeError fix, 48V alert thresholds |
| [v2.2.2](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.2.2) | Type annotations for mypy compliance |
| [v2.2.1](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.2.1) | Fix pre-commit and Snyk CI workflows |
| [v2.2.0](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.2.0) | Config hot-reload, SQLite persistence, alerts, web UI, Docker Compose |
| [v2.1.1](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.1.1) | Ruff cleanup in tests |
| [v2.1.0](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.1.0) | Data buffering, health checks, metrics, multi-device support |
| [v2.0.3](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.0.3) | Docker build fix |
| [v2.0.2](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.0.2) | Reliability & developer experience |
| [v2.0.1](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.0.1) | Mypy type annotation fix |
| [v2.0.0](https://github.com/Barlows/ha-addon-km140f/releases/tag/v2.0.0) | Major improvements: staleness watchdog, exponential backoff, MQTT TLS, full config exposure, unit tests, CI/CD |
| [v1.0.0](https://github.com/Barlows/ha-addon-km140f/releases/tag/v1.0.0) | Initial release |

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
