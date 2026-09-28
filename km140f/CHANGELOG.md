# Changelog

## 2.0.1

- Fix: protocol parsing now uses startswith for reliable frame matching
- Fix: handle \r\n line endings from device
- Fix: consistent documentation URLs and default values
- Fix: MQTT callbacks and signal handlers have full type hints
- Fix: extra protocol fields logged at debug level

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
