# Junctek KM140F — Home Assistant Add-on

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

TCP → MQTT bridge for the **Junctek KM140F** battery monitor connected via a WiFi module (port 8899).

The add-on connects to the monitor over TCP, parses the push data stream (`:A=` lines) and energy totals (`:C=` lines), and publishes everything to Home Assistant via **MQTT Discovery** — no manual entity configuration needed.

---

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**
2. Click **⋮ (three dots) → Repositories**
3. Add `https://github.com/bootuz-dinamon/ha-addon-km140f`
4. Find **Junctek KM140F** in the store and click **Install**

---

## Prerequisites

- Junctek KM140F with a WiFi module (known IP address, port 8899)
- **Mosquitto** MQTT broker add-on installed and running

---

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `monitor_host` | `192.168.1.100` | IP address of the KM140F WiFi module |
| `monitor_port` | `8899` | TCP port (fixed by Junctek) |
| `mqtt_host` | `core-mosquitto` | MQTT broker hostname |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | _(empty)_ | MQTT username (if auth enabled) |
| `mqtt_pass` | _(empty)_ | MQTT password |
| `device_id` | `junctek_km140f` | Unique device ID (used as MQTT topic prefix) |
| `device_name` | `Junctek KM140F` | Friendly name shown in Home Assistant |
| `poll_c_interval` | `30` | How often to request `:C=` energy totals (seconds) |
| `reconnect_delay` | `5` | Seconds between TCP reconnection attempts |

---

## Entities created in Home Assistant

| Entity | Unit | Notes |
|--------|------|-------|
| Voltage | V | |
| Current | A | positive = charging, negative = discharging |
| Power | W | positive = charging, negative = discharging |
| Remaining Capacity | Ah | |
| Time Remaining | min | to full discharge or charge |
| Set Capacity | Ah | configured in the monitor |
| State of Charge | % | calculated from Remaining / Set Capacity |
| Total Energy Charged | kWh | cumulative, from `:C=` |
| Total Energy Discharged | kWh | cumulative, from `:C=` |
| Status | text | `Charging` or `Discharging` |

---

## Protocol notes

The KM140F WiFi module streams data automatically without polling.
Push format (confirmed by field-testing against display readings):

```
:A=<voltage*100>,<current*1000>,<direction>,<minutes>,<ah*1000>,<capacity*10>,...
:C=<charged_kwh*1000>,<discharged_kwh*1000>,...
```

Direction: `0` = discharging, `1` = charging.
