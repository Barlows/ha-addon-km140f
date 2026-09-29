# Changelog

## 2.2.0

### New Features
- **Configuration hot-reload**: Reload config via SIGHUP without restarting
- **Data persistence**: SQLite storage for historical data (30-day retention)
- **Alerting**: Threshold-based alerts for voltage and SOC
- **Web UI**: Simple web interface at port 8082 for live data
- **Codecov integration**: Coverage reporting with Codecov
- **Snyk security scanning**: Automated vulnerability scanning
- **Docker Compose**: Easy local development with docker-compose up
- **Pre-commit CI**: Run pre-commit hooks in GitHub Actions
- **Documentation**: Added demo, tutorial, and wiki docs

### Configuration Options
- `ENABLE_PERSISTENCE`: Enable SQLite data persistence (default: true)
- `DB_PATH`: Database file path (default: /data/km140f.db)
- `DB_RETENTION_DAYS`: Data retention period (default: 30)
- `ENABLE_ALERTS`: Enable threshold alerts (default: true)
- `ALERT_VOLTAGE_MIN`: Minimum voltage threshold (default: 10.0)
- `ALERT_VOLTAGE_MAX`: Maximum voltage threshold (default: 15.0)
- `ALERT_SOC_MIN`: Minimum SOC threshold (default: 20.0)
- `ENABLE_WEB_UI`: Enable web interface (default: true)
- `WEB_UI_PORT`: Web UI port (default: 8082)

## 2.1.1

### Bug Fixes
- Clean up ruff errors in test_integration.py (unused imports, import sorting, socket.timeout, unused variables)

## 2.1.0

### New Features
- **Data buffering**: Messages are buffered when MQTT is unavailable and flushed on reconnect
- **Health check endpoint**: HTTP endpoint at `/health` for monitoring (port 8081)
- **Prometheus metrics**: Metrics endpoint at `/metrics` (port 8080) with connection stats, message counts, and buffer size
- **Multiple device support**: Configure multiple KM140F devices via `MONITOR_HOSTS` (comma-separated)
- **Graceful degradation**: Data written to fallback file when MQTT is unavailable
- **Structured logging**: JSON log format support via `LOG_FORMAT=json`
- **Configuration validation**: Validates config at startup with helpful error messages
- **Docker healthcheck**: Added HEALTHCHECK instruction to Dockerfile

### Testing
- Added integration tests with mock TCP server and MQTT broker
- Added test coverage reporting with pytest-cov
- Total tests: 28 (21 unit + 7 integration)

### CI/CD
- Updated CI pipeline to include coverage reporting
- Uploads coverage reports as artifacts

## 2.0.3

### Bug Fixes
- Move requirements.txt into km140f/ directory for Docker build context

## 2.0.2

### Reliability
- Add MQTT publish failure handling with return code checking
- Improve MQTT disconnect logging for better diagnostics

### Developer Experience
- Add requirements.txt for reproducible builds
- Add .dockerignore for faster Docker builds
- Add mypy.ini for centralized type checking config
- Add Makefile with common dev tasks (test, lint, typecheck, format)
- Add .pre-commit-config.yaml for pre-commit hooks

### Community & Maintenance
- Add CONTRIBUTING.md with contribution guidelines
- Add SECURITY.md with security policy
- Add CODE_OF_CONDUCT.md with community standards
- Add .github/dependabot.yml for automated dependency updates
- Add .github/stale.yml for stale issue management

## 2.0.1

### Bug Fixes
- Add type annotations to SENSORS and TEXT_SENSORS for mypy compliance
- Fixes CI pipeline failure in mypy type checking

## 2.0.0

### Bug Fixes
- Protocol parsing now uses `startswith` for reliable `:A=`/`:C=` frame matching
- Handle `\r\n` line endings from device
- Consistent documentation URLs and default values across README/DOCS/config
- MQTT callbacks and signal handlers have full type hints
- Extra protocol fields logged at debug level
- Replace `socket.timeout` with builtin `TimeoutError`
- Catch specific exceptions instead of blind `except Exception`
- Signal handler logs errors instead of silent `try-except-pass`

### New Features
- **Staleness watchdog**: Reconnects if no data received for 60 seconds (configurable)
- **Exponential backoff**: TCP reconnection delay doubles up to 5 minutes max
- **MQTT TLS support**: Optional TLS encryption with custom CA certificate
- **Full config exposure**: All settings (`sw_version`, `socket_timeout`, `mqtt_keepalive`) configurable via HA add-on UI
- **BridgeState class**: Encapsulates mutable state for better testability
- **Unit tests**: 21 tests covering all parsing functions
- **CI/CD pipeline**: GitHub Actions with ruff, mypy, and pytest
- **Project infrastructure**: `.gitignore`, `LICENSE`, issue templates, PR template

### Breaking Changes
- None — fully backward compatible with v1.0.0

## 1.0.1

- Fix: added `init: false` to config.yaml — required for s6-overlay base images.
  Without this, Supervisor adds its own Docker `--init` wrapper which conflicts
  with s6-overlay's own PID 1 process, causing
  `s6-overlay-suexec: fatal: can only run as pid 1`.

## 1.0.0

- Initial release
- TCP → MQTT bridge for Junctek KM140F via WiFi module
- MQTT Discovery: 9 sensors + 1 text sensor auto-created in Home Assistant
- Automatic TCP reconnection
- Periodic `:C=` polling for energy totals (charged/discharged kWh)
