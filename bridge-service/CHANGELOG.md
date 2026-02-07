# Changelog

## 2026-02-07
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
