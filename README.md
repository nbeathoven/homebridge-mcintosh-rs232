<<<<<<< HEAD
# homebridge-mcintosh-rs232
Build a low-cost solution that runs on a Raspberry Pi and enables controlling a McIntosh MA-352 amplifier from Apple Home (HomeKit) via Homebridge. The Raspberry Pi shall communicate with the amplifier over RS-232 and expose a small local API to Homebridge. A Homebridge plugin shall map amplifier controls into HomeKit accessories.
=======
# MA-352 RS-232 Bridge + Homebridge

This repo contains a Python bridge service for the McIntosh MA-352 RS-232 port and a Homebridge platform plugin that exposes Power, Mute, and Volume accessories in Apple Home.

## Structure

- `bridge-service/` Python HTTP bridge service
- `homebridge-ma352/` Homebridge platform plugin
- `bridge-service/systemd/ma352-bridge.service` sample systemd unit

## Hardware Notes

- USB-to-RS232 adapter required (true RS-232 voltage levels).
- RS-232 to 3.5 mm TRS cable required. Use the pinout from `McIntosh_RS232ControlApplicationNote.pdf`.
- No flow control (RTS/CTS) required.

## Bridge Service (Python)

### Install

```bash
cd bridge-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run (manual)

```bash
cd bridge-service
source .venv/bin/activate
SERIAL_PORT=/dev/ttyUSB0 python app.py
```

### Run (systemd)

1. Copy the service and code to the Pi (example uses `/opt/ma352-bridge`).
2. Edit `bridge-service/systemd/ma352-bridge.service` to match your paths and user.
3. Install and enable:

```bash
sudo cp -R bridge-service /opt/ma352-bridge
sudo cp bridge-service/systemd/ma352-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ma352-bridge
sudo systemctl start ma352-bridge
```

### Environment

- `SERIAL_PORT` (default `/dev/ttyUSB0`)
- `SERIAL_BAUD` (default `115200`)
- `BRIDGE_HOST` (default `0.0.0.0`)
- `BRIDGE_PORT` (default `5000`)
- `HOLD_INTERVAL` (default `0.12` seconds)
- `QUERY_INTERVAL` (default `5.0` seconds; set `0` to disable polling)
- `QUERY_ON_CONNECT` (default `1`; set `0` to wait for the first poll interval)

The bridge periodically sends `(QRY)` to refresh status. Replies update `/volume` and `/mute`. If your unit does not support `QRY`, those values remain last-known.

### Test Commands

```bash
curl http://127.0.0.1:5000/ping
curl -X POST http://127.0.0.1:5000/power/on
curl -X POST http://127.0.0.1:5000/power/off
curl http://127.0.0.1:5000/power
curl -X POST http://127.0.0.1:5000/mute/on
curl -X POST http://127.0.0.1:5000/mute/off
curl http://127.0.0.1:5000/mute
curl -X POST "http://127.0.0.1:5000/volume/set?level=35"
curl http://127.0.0.1:5000/volume
curl http://127.0.0.1:5000/volume/lvl
curl http://127.0.0.1:5000/state
curl -X POST http://127.0.0.1:5000/hold/start -H "Content-Type: application/json" -d '{"dir":"up"}'
curl -X POST http://127.0.0.1:5000/hold/stop
```

## Homebridge Plugin

### Install as local tarball

From the repo root:

```bash
cd homebridge-ma352
npm pack
```

This produces a `.tgz` file. In Homebridge UI, install the plugin from a local tarball and select the generated file.

### Homebridge Config

Add this platform entry:

```json
{
  "platform": "MA352Platform",
  "host": "127.0.0.1",
  "port": 5000
}
```

Expected accessories in Apple Home:

- `MA352 Power` (Switch)
- `MA352 Mute` (Switch)
- `MA352 Volume` (Lightbulb brightness slider)

## Security Note

This service is designed for a closed LAN. Do not expose the HTTP port to the public internet.
>>>>>>> fb2cc1b (Initial MA-352 bridge + Homebridge plugin)
