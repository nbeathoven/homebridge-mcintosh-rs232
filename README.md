# MA-352 RS-232 Bridge + Homebridge

This repo contains a Python bridge service for the McIntosh MA‑352 RS‑232 port and a Homebridge platform plugin that exposes controls in Apple Home.

**Structure**
- `bridge-service/` Python HTTP bridge service
- `homebridge-ma352/` Homebridge platform plugin
- `bridge-service/systemd/ma352-bridge.service` sample systemd unit

**Hardware Notes**
- USB‑to‑RS232 adapter required (true RS‑232 voltage levels).
- RS‑232 to 3.5 mm TRS cable required. Use the pinout from `McIntosh_RS232ControlApplicationNote.pdf`.
- No flow control (RTS/CTS) required.

**Bridge Service (Python)**

Install
```bash
cd bridge-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run (manual)
```bash
cd bridge-service
source .venv/bin/activate
SERIAL_PORT=/dev/ttyUSB0 python app.py
```

Run (systemd)
1. Copy the service and code to the Pi.
2. Edit `bridge-service/systemd/ma352-bridge.service` to match your paths and user.
3. Install and enable:

```bash
sudo cp -R bridge-service /opt/ma352-bridge
sudo cp bridge-service/systemd/ma352-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ma352-bridge
sudo systemctl start ma352-bridge
```

Tip: use a stable serial path like `/dev/serial/by-id/...` instead of `/dev/ttyUSB0` so it does not change on reboot.

**Environment**
- `SERIAL_PORT` (default `/dev/ttyUSB0`)
- `SERIAL_BAUD` (default `115200`)
- `BRIDGE_HOST` (default `0.0.0.0`)
- `BRIDGE_PORT` (default `5000`)
- `HOLD_INTERVAL` (default `0.12` seconds)
- `QUERY_INTERVAL` (default `5.0` seconds; set `0` to disable polling)
- `QUERY_ON_CONNECT` (default `1`; set `0` to wait for the first poll interval)
- `COMMAND_STYLE` (`auto`, `short`, or `zone`; default `auto`)
- `DEFAULT_COMMAND_STYLE` (`short` or `zone`; default `short`)
- `COMMAND_ZONE` (default `Z1`)

The bridge supports both short‑form commands (e.g., `PWR`, `VOL`) and zone‑form commands (e.g., `PON Z1`, `VST Z1`). In `auto` mode it will detect and fall back if the device reports `Invalid Command`.

**HTTP API**
- `GET /ping`
- `POST /power/on`, `POST /power/off`, `GET /power`
- `POST /mute/on`, `POST /mute/off`, `GET /mute`
- `POST /volume/set?level=NN`, `GET /volume`, `GET /volume/lvl`
- `POST /input/set?value=N`, `GET /input` (N = 1..9)
- `POST /hold/start` (JSON `{ "dir": "up"|"down" }`), `POST /hold/stop`
- `GET /state`
- `GET /help` (tries `HLP`, falls back to `QRY`)
- `GET /firmware` (derived from `QRY`/`HLP`)

**Test Commands**
```bash
curl http://127.0.0.1:5000/ping
curl -X POST http://127.0.0.1:5000/power/on
curl -X POST http://127.0.0.1:5000/power/off
curl -X POST http://127.0.0.1:5000/mute/on
curl -X POST http://127.0.0.1:5000/mute/off
curl -X POST "http://127.0.0.1:5000/volume/set?level=30"
curl -X POST "http://127.0.0.1:5000/input/set?value=3"
curl http://127.0.0.1:5000/state
curl "http://127.0.0.1:5000/help?timeout=1.5"
curl "http://127.0.0.1:5000/firmware?timeout=1.5"
```

**Homebridge Plugin**

Install as local tarball
```bash
cd homebridge-ma352
npm pack
```
This produces a `.tgz` file. In Homebridge UI, install the plugin from a local tarball and select the generated file.

Homebridge config
```json
{
  "platform": "MA352Platform",
  "name": "McIntosh Amp",
  "host": "192.168.1.50",
  "port": 5000,
  "inputs": [
    { "value": 1, "name": "MC" },
    { "value": 3, "name": "CD1" },
    { "value": 6, "name": "AUX" }
  ]
}
```
If `inputs` is omitted, the plugin exposes the default 1–9 map.
The plugin exposes a single accessory with a TV-style input selector, plus mute and a volume slider under the same device.

**Security Note**
This service is designed for a closed LAN. Do not expose the HTTP port to the public internet.
