# MA352 RS-232 Bridge

HTTP bridge for the McIntosh MA-352 over RS-232, designed to run as a single-purpose service on a Raspberry Pi.

**Run In Production**
Use the provided systemd unit (see `rpi-install.sh`) or run the app directly with a single process.

**WSGI Deployment Note**
This service owns a physical serial device and is designed for a single process.
- If you use gunicorn/uwsgi, run with `--workers 1` to avoid multiple processes fighting for `/dev/ttyUSB0`.
- Prefer systemd running `python app.py` on the device for production.

**Environment**
Key environment variables are managed via `/etc/default/ma352-bridge` when you use `rpi-install.sh`.
Common settings include `BRIDGE_HOST`, `BRIDGE_INTERFACE`, `BRIDGE_PORT`, and serial parameters.

**Remote Monitoring**
- Use `GET /ping` for lightweight HTTP liveness only. It returns a minimal `{"alive": true}` response when the process is answering HTTP.
- Use `GET /health` for procmon-facing readiness. It returns stable JSON with `ok`, `alive`, `ready`, `service`, `version`, `serial_connected`, `serial_port`, `serial_baud`, `last_error`, and watchdog/query timing fields.
- `GET /health` returns HTTP `200` only when the bridge is ready to control the amp: the process is alive and the serial transport is connected.
- `GET /health` returns HTTP `503` with `alive: true`, `ready: false`, `ok: false`, and a machine-readable `last_error` when the process is up but the serial transport is unusable.
- For monitoring from another host, bind the bridge to the LAN with `BRIDGE_HOST=0.0.0.0` or `BRIDGE_INTERFACE=<lan-iface>`. New installs default to local-only bind unless you opt into LAN exposure.
- Keep service restarts out of the bridge API. Restart `ma352-bridge` out-of-band with systemd on the MA352 host.

**Procmon Template**
- Service name: `ma352-bridge`
- Default port: `5000`
- Health URL: `http://127.0.0.1:5000/health` for local procmon, or `http://<ma352-host>:5000/health` from another host
- Checks:
  - `systemd_service` for `ma352-bridge`
  - `http_json` with `require_ok: true` and `require_serial_connected: true`
- Recovery:
  - `restart_systemd_service`
  - `sleep`
  - `recheck`

See [`bridge-service/procmon/ma352-monitor.example.json`](procmon/ma352-monitor.example.json) for a canonical monitor definition.
