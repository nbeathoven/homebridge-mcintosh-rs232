# Changelog

## 2026-03-18 (1.0.9)
- Added stable `service: "ma352-bridge"` to `/health` responses for remote monitoring.
- Made `/health` failure semantics explicit: return HTTP 503 with `ok: false` when the serial runtime is unavailable or the serial device has never opened.
- Kept `/health` machine-readable on both success and failure with consistent serial, version, last-error, and watchdog/query fields.
- Added focused health endpoint tests covering success, startup open failure, reconnect, and runtime-missing cases.
- Updated `rpi-install.sh` to keep the bridge running as the invoking sudo user by default instead of silently switching the service to root.
- Updated docs for remote procmon monitoring and clarified the preferred `/opt/ma352-bridge` + `/etc/default/ma352-bridge` deployment layout.

## 2026-02-07 (1.0.8)
- Added secure bind resolution via `BRIDGE_HOST`/`BRIDGE_INTERFACE`, defaulting to localhost.
- Added zone-form `HELP`/`QUERY` commands with auto-detect probe and fallback for short vs zone style.
- Added serial write timeout to prevent hangs and tightened writer thread shutdown via sentinel.
- Added status-on-connect query with startup state snapshot logging.
- Added `/hold/start` serial connectivity check to fail fast when disconnected.
- Documented single-process deployment guidance in `bridge-service/README.md`.
- Bumped default app version to 1.0.8.

## 2026-02-07 (1.0.7)
- Added startup volume safety: only apply when not increasing device volume.
- Added unmute safety fallback volume for high targets.
- Added safety toggle and configurable limits via `SAFETY_ENABLED`, `SAFE_UNMUTE_MAX`, `SAFE_UNMUTE_FALLBACK`.
- Added Raspberry Pi install script (`bridge-service/rpi-install.sh`).
- Documented Raspberry Pi installer and safety behavior in `README.md`.

## 2026-02-07 (1.0.6 and earlier)
- Hardened serial I/O loop to recover from unexpected exceptions without dying.
- Capped volume handling at 50 and enforced max per-request change of 10.
- Clamped volume values in cache, outbound commands, and inbound status parsing.
- Added serial watchdog and /health endpoint for runtime diagnostics.
- Added serial error tracking and stale-connection recovery.
- Added section comments throughout app.py for clarity.
- Added docstrings for functions and classes to improve readability.
- Added app version to startup log and health/root responses.
- Set default app version baseline to 1.0.0.
- Limited volume increases to +5 per request; decreases may be larger.
- Matched Homebridge volume slider to 0..50 with +5 max increase.
- Added queued volume ramping for increases above +5 with configurable delay.
- Updated Homebridge slider behavior to send requested volume (0..50).
- Bumped default app version to 1.0.1.
- Included max/requested details in volume validation errors.
- Homebridge volume set now surfaces bridge errors instead of swallowing them.
- Fixed indentation error in serial loop exception handling.
- Added outbound command logging to correlate invalid-command errors.
- Logged recent outbound commands when device reports invalid commands.
- Bumped default app version to 1.0.2.
- Paused volume ramps and hold changes while muted; resume queued ramps on unmute.
- Deferred volume changes while muted to prevent auto-unmute.
- Bumped default app version to 1.0.3.
- Applied a startup volume (default 20) on first serial connect after service start.
- Added STARTUP_VOLUME and STARTUP_VOLUME_ENABLED settings.
- Bumped default app version to 1.0.4.
- Changed default startup volume to 15 and bumped app version to 1.0.5.
- Fixed race in volume ramp thread when stop_event is cleared.
- Bumped default app version to 1.0.6.
