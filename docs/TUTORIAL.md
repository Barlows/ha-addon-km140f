# Video Tutorial

## Quick Start

[![KM140F Add-on Tutorial](https://img.youtube.com/vi/dQw4w9WgXcQ/0.jpg)](https://www.youtube.com/watch?v=dQw4w9WgXcQ)

*Click the image above to watch the video tutorial.*

## Written Guide

### 1. Install the Add-on

1. Go to **Settings → Add-ons → Add-on Store**
2. Click **⋮ → Repositories**
3. Add `https://github.com/Barlows/ha-addon-km140f`
4. Find **Junctek KM140F** and click **Install**

### 2. Configure

1. Go to the add-on **Configuration** tab
2. Set `monitor_host` to your KM140F WiFi module IP
3. Configure MQTT settings (usually `core-mosquitto`)
4. Click **Save**

### 3. Start

1. Go to the **Info** tab
2. Click **Start**
3. Check the **Log** tab for any errors

### 4. Verify

1. Go to **Settings → Devices & Services**
2. Find **MQTT** integration
3. Check that **Junctek KM140F** device appears
4. Verify sensors are updating

## Troubleshooting

### No data appearing

- Check the add-on logs for connection errors
- Verify the KM140F WiFi module IP address
- Ensure the MQTT broker is running

### Connection errors

- Verify network connectivity to the KM140F
- Check firewall rules
- Try increasing `socket_timeout`

### MQTT errors

- Verify MQTT broker hostname
- Check MQTT credentials
- Ensure MQTT broker is accessible
