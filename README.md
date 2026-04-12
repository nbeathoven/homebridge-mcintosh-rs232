# About
I always wanted a simple way to control my McIntosh amp from HomeKit. I already had Homebridge running for other devices, and I really appreciated other people’s attempts at solving this, but none of them worked exactly the way I wanted. So here we are.

I grabbed a Raspberry Pi Zero 2W and kept it as lean as possible (minimal packages, no extra fluff). On top of that, I built a lightweight bridge that runs on the Pi, talks to the amp over RS-232, and exposes the controls through Homebridge so it shows up nicely in Apple Home.
The Pi connects to the McIntosh using a USB-A to RS-232 adapter and the proper cable into the McIntosh control port. My little nook is up and running now! hopefully you’ll find this useful too.

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

Quick install (RPi)
```bash
cd bridge-service
sudo ./rpi-install.sh
```
This installs to `/opt/ma352-bridge`, creates a venv, installs requirements, writes a systemd unit, enables it, and starts it.
You can override device-specific settings in `/etc/default/ma352-bridge`.
By default, the installer keeps the service running as the invoking `sudo` user and `dialout` group; override with `SERVICE_USER`/`SERVICE_GROUP` if needed.
Set `SAFETY_ENABLED=0` there to disable the safety logic.

**Environment**
- `SERIAL_PORT` (default `/dev/ttyUSB0`)
- `SERIAL_BAUD` (default `115200`)
- `BRIDGE_HOST` (default unset; secure bind resolves to `127.0.0.1` unless `BRIDGE_INTERFACE` is set)
- `BRIDGE_INTERFACE` (optional; bind to the IPv4 address of a specific LAN interface such as `eth0`)
- `BRIDGE_PORT` (default `5000`)
- `BRIDGE_VERSION` (default `1.0.10`; reported by `/health` and `/`)
- `HOLD_INTERVAL` (default `0.12` seconds)
- `QUERY_INTERVAL` (default `5.0` seconds; set `0` to disable polling)
- `QUERY_ON_CONNECT` (default `1`; set `0` to wait for the first poll interval)
- `SERIAL_STALE_TIMEOUT` (default `30.0` seconds; force reconnect if no RX)
- `SERIAL_WATCHDOG_INTERVAL` (default `2.0` seconds; watchdog check interval)
- `VOLUME_RAMP_STEP` (default `5`; max step for queued volume increases)
- `VOLUME_RAMP_DELAY` (default `1.0` seconds; delay between queued steps)
- `OUTBOUND_LOG_MAX` (default `200`; max outbound commands kept for correlation)
- `INVALID_CMD_LOOKBACK` (default `2.0` seconds; lookback window for invalid command correlation)
- `STARTUP_VOLUME_ENABLED` (default `1`; set `0` to disable startup volume)
- `STARTUP_VOLUME` (default `15`; applied once on first serial connect)
- `SAFETY_ENABLED` (default `1`; set `0` to disable safety logic)
- `SAFE_UNMUTE_MAX` (default `30`; if last volume exceeds this, unmute clamps)
- `SAFE_UNMUTE_FALLBACK` (default `20`; volume forced before unmute)
- `COMMAND_STYLE` (`auto`, `short`, or `zone`; default `auto`)
- `DEFAULT_COMMAND_STYLE` (`short` or `zone`; default `short`)
- `COMMAND_ZONE` (default `Z1`)

The bridge supports both short‑form commands (e.g., `PWR`, `VOL`) and zone‑form commands (e.g., `PON Z1`, `VST Z1`). In `auto` mode it will detect and fall back if the device reports `Invalid Command`.

**Volume Behavior**
- Volume is capped at `0..50`.
- With safety enabled (default), large increases are queued and ramped in steps of `VOLUME_RAMP_STEP` with `VOLUME_RAMP_DELAY` between steps.
- With safety enabled, volume changes requested while muted are deferred until unmute.
- With safety enabled, unmute will clamp volume to `SAFE_UNMUTE_FALLBACK` if the last requested volume is above `SAFE_UNMUTE_MAX`.
- With safety enabled, startup volume will only be applied if it does not increase the current device volume.
- With safety disabled, volume changes are sent immediately and unmute/startup volume clamps are skipped.

**HTTP API**
- `GET /ping`
- `GET /health` (procmon-facing readiness, serial status, version, watchdog info)
- `POST /power/on`, `POST /power/off`, `GET /power`
- `POST /mute/on`, `POST /mute/off`, `GET /mute`
- `POST /volume/set?level=NN`, `GET /volume`, `GET /volume/lvl`
- `POST /input/set?value=N`, `GET /input` (N = 1..9)
- `POST /hold/start` (JSON `{ "dir": "up"|"down" }`), `POST /hold/stop`
- `GET /state`
- `GET /help` (tries `HLP`, falls back to `QRY`)
- `GET /firmware` (derived from `QRY`/`HLP`)

**Health & Diagnostics**
- `GET /ping` is liveness only. It returns a minimal `{"alive": true}` when the process is answering HTTP.
- `GET /health` is the readiness signal for procmon. It returns stable machine-readable JSON with at least `ok`, `alive`, `ready`, `service`, `version`, `serial_connected`, `serial_port`, `serial_baud`, `last_error`, and watchdog/query timing fields.
- `GET /health` returns HTTP `200` only when the bridge is operational enough for control traffic: the process is alive and the serial transport is connected.
- `GET /health` returns HTTP `503` with `alive: true`, `ready: false`, `ok: false`, and `serial_connected: false` when the process is up but serial transport is unavailable or disconnected.
- Procmon should trust `/health` as readiness and should not infer bridge readiness from process liveness alone.
- Invalid command warnings include recent outbound commands for correlation.

**Remote Monitoring With Procmon**
- Service name: `ma352-bridge`
- Default port: `5000`
- Procmon health URL: `http://<ma352-host>:5000/health`
- Standard procmon checks:
  - `systemd_service` for `ma352-bridge`
  - `http_json` with `require_ok: true`
  - `http_json` with `require_serial_connected: true`
- Standard procmon recovery:
  - `restart_systemd_service`
  - `sleep`
  - `recheck`
- Keep `GET /ping` as the lightweight HTTP liveness probe when you only need to know whether the bridge process is answering.
- Use `GET /health` as the readiness signal when procmon must know whether the amp is actually reachable over serial.
- New installs default to local-only bind. To allow procmon on another host, set `BRIDGE_HOST=0.0.0.0` or `BRIDGE_INTERFACE=<lan-iface>` in `/etc/default/ma352-bridge`.
- For local-only operation, leave `BRIDGE_HOST` unset so the app binds to `127.0.0.1`.
- Restart the service externally through SSH and systemd on the MA352 host. Do not add or use an HTTP restart endpoint.
- A canonical procmon monitor snippet is included at `bridge-service/procmon/ma352-monitor.example.json`.

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
curl http://127.0.0.1:5000/health
curl "http://127.0.0.1:5000/help?timeout=1.5"
curl "http://127.0.0.1:5000/firmware?timeout=1.5"
```

**Homebridge Plugin**

Install from npm
```bash
npm install -g homebridge-ma352
```

In Homebridge UI, search for `homebridge-ma352` from the Plugins screen and install it there.

Local tarball install is still useful for development, but it is no longer the normal install path:
```bash
cd homebridge-ma352
npm pack
```

Compatibility
- Node.js `18+`
- Homebridge `>=1.6.0`
- Homebridge `2.0.0-beta` supported

Homebridge config
```json
{
  "platform": "MA352Platform",
  "name": "McIntosh Amp",
  "host": "Epcilon",
  "port": 5000,
  "fallbackHosts": ["192.168.5.163"],
  "inputs": [
    { "value": 1, "name": "MC" },
    { "value": 3, "name": "CD1" },
    { "value": 6, "name": "AUX" }
  ]
}
```
If `inputs` is omitted, the plugin exposes the default 1–9 map.
If `fallbackHosts` is set, the plugin will retry those endpoints when the primary bridge host is unreachable.
The plugin exposes a single accessory with a TV-style input selector, plus mute and a volume slider under the same device.
HomeKit sees the volume slider as a normal 0–100 percentage, and the plugin maps that to the bridge's 0–50 device range.
Upward changes still ramp in +5 device-volume steps to match the bridge queue behavior.

**Security Note**
This service is designed for a closed LAN. Do not expose the HTTP port to the public internet.

**Release History**
- GitHub Releases for this repo track bridge-service versions with tags like `bridge-service-v1.0.9`.
- The Homebridge plugin is published separately to npm as `homebridge-ma352`.
- Homebridge plugin release tags use `homebridge-ma352-vX.Y.Z` and publish through `.github/workflows/publish.yml`.
- `bridge-service/CHANGELOG.md` is the source of truth for bridge release notes, and GitHub Releases should mirror those entries.
- `homebridge-ma352/CHANGELOG.md` is the source of truth for plugin release notes.
