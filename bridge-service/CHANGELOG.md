# Changelog

## 2026-02-07
- Hardened serial I/O loop to recover from unexpected exceptions without dying.
- Capped volume handling at 50 and enforced max per-request change of 10.
- Clamped volume values in cache, outbound commands, and inbound status parsing.
- Added serial watchdog and /health endpoint for runtime diagnostics.
- Added serial error tracking and stale-connection recovery.
- Added section comments throughout app.py for clarity.
- Added docstrings for functions and classes to improve readability.
