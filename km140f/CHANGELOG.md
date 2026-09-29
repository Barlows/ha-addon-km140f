# Changelog

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
