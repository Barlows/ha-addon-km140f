# Wiki

## Table of Contents

- [Configuration](#configuration)
- [Advanced Features](#advanced-features)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [FAQ](#faq)

## Configuration

### Basic Options

| Option | Default | Description |
|--------|---------|-------------|
| `monitor_host` | `192.168.0.204` | IP address of the KM140F WiFi module |
| `monitor_port` | `8899` | TCP port (fixed by Junctek) |
| `mqtt_host` | `core-mosquitto` | MQTT broker hostname |
| `mqtt_port` | `1883` | MQTT broker port |

### Advanced Options

| Option | Default | Description |
|--------|---------|-------------|
| `enable_persistence` | `true` | Store historical data in SQLite |
| `enable_alerts` | `true` | Send alerts when thresholds exceeded |
| `enable_web_ui` | `true` | Enable web interface |
| `enable_metrics` | `false` | Enable Prometheus metrics |
| `log_format` | `text` | Log format (`text` or `json`) |

### Multiple Devices

Configure multiple KM140F devices:

```
MONITOR_HOSTS="192.168.0.204,192.168.0.205"
```

### Alert Thresholds

| Option | Default | Description |
|--------|---------|-------------|
| `alert_voltage_min` | `10.0` | Minimum voltage threshold (V) |
| `alert_voltage_max` | `15.0` | Maximum voltage threshold (V) |
| `alert_soc_min` | `20.0` | Minimum state of charge threshold (%) |

## Advanced Features

### Data Persistence

When enabled, sensor data is stored in SQLite at `/data/km140f.db`. Data is retained for 30 days by default.

### Web UI

Access the web interface at `http://<host>:8082/` for live data and status.

### Prometheus Metrics

Access metrics at `http://<host>:8080/metrics` for monitoring.

### Health Check

Access health status at `http://<host>:8081/health`.

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| No data | Check monitor_host and network connectivity |
| MQTT errors | Verify MQTT broker settings |
| High CPU usage | Increase poll_c_interval |
| Memory leaks | Check buffer_max_size setting |

### Debug Logging

Enable debug logging by setting `LOG_LEVEL=DEBUG`.

## Development

### Setup

```bash
git clone https://github.com/Barlows/ha-addon-km140f.git
cd ha-addon-km140f
make install
```

### Testing

```bash
make test
make lint
make typecheck
```

### Docker Compose

```bash
docker-compose up -d
```

## FAQ

**Q: Can I use multiple KM140F devices?**

A: Yes! Set `MONITOR_HOSTS` to a comma-separated list of IP addresses.

**Q: How do I enable MQTT TLS?**

A: Set `mqtt_use_tls` to `true` and optionally provide `mqtt_tls_ca_cert`.

**Q: What happens if MQTT goes down?**

A: Messages are buffered (up to 1000) and flushed when MQTT reconnects.

**Q: How do I access historical data?**

A: Data is stored in SQLite at `/data/km140f.db` when persistence is enabled.
