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
- Use `GET /ping` for lightweight HTTP liveness.
- Use `GET /health` for machine-readable service and serial state.
- For monitoring from another host, bind the bridge to the LAN with `BRIDGE_HOST=0.0.0.0` or `BRIDGE_INTERFACE=<lan-iface>`.
- Keep service restarts out of the bridge API. Restart `ma352-bridge` out-of-band with systemd on the MA352 host.
