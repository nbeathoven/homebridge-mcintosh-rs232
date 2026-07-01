# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-07-01
### Changed
- Volume is now exposed through a `TelevisionSpeaker` service linked to the TV accessory instead of a `Fan`. Volume is controlled with the iPhone/iPad hardware volume buttons and the Control Center Remote, and mute is integrated into the speaker (the standalone mute switch remains for a quick toggle). There is no on-tile volume slider — that is a limitation of how Apple's Home app renders `TelevisionSpeaker`. Any previous `Fan` or `Lightbulb` volume tile is removed automatically on startup.
- Removed the plugin's client-side volume ramp animation; the bridge already ramps volume server-side, so the client-side stepping was redundant.

### Fixed
- Removed unused per-characteristic read helpers (`safeGetPower`/`safeGetMute`/`safeGetVolume`/`safeGetInput`) now that all reads go through the cached `/state` snapshot.
- Volume writes now swallow bridge errors and reconcile on the next poll, consistent with power/mute/input, instead of surfacing a HomeKit error.
- Poll and refresh timers are now cleared on Homebridge shutdown.

## [1.1.0] - 2026-06-30
### Changed
- Renamed the npm package to the scoped name `@nbeathoven/homebridge-ma352`. The previous unscoped `homebridge-ma352` package is deprecated in favor of this one. Update the plugin through the Homebridge UI or reinstall under the scoped name; Homebridge config is unchanged because the platform is still `MA352Platform`.
- Exposed volume through a `Fan` service (rotation speed) instead of a `Lightbulb` service. The control keeps the same 0-100 slider but no longer appears as a light, so it is not affected by "turn off all the lights" scenes, automations, or Siri commands.
- Any previously created `Lightbulb` volume tile is removed automatically on startup during migration.

## [1.0.10] - 2026-04-12
### Changed
- Changed bridge failover to prefer the configured primary hostname, retry it once after a short delay, and then fail over to fallback hosts.
- Made fallback selection sticky so the plugin no longer flips back and forth between hostname and IP on every successful poll.
- Primary host recovery is now promoted only on an intentional later probe instead of the first opportunistic success after failover.
- Added focused tests covering primary retry order, sticky failover, and controlled promotion back to the primary host.

## [1.0.9] - 2026-04-11
### Changed
- Suppressed duplicate `State read failed` warnings during a single bridge outage window so logs now emphasize the first outage summary and the later recovery event.
- Added a regression test covering repeated `/state` refresh failures during one backend outage.

## [1.0.8] - 2026-04-11
### Changed
- Added optional `fallbackHosts` support so the plugin can retry bridge requests against alternate hostnames or IPs when the primary endpoint is unreachable.
- Improved bridge failure diagnostics to include attempted endpoints and underlying network error details instead of only logging `fetch failed`.
- Added explicit backend outage and recovery logs while preserving the last known HomeKit state during bridge outages.
- Added focused tests covering endpoint failover and outage/recovery logging.

## [1.0.7] - 2026-04-06
### Changed
- Added change-only Homebridge info logs for bridge state transitions after the first successful `/state` snapshot.
- Power, mute, volume, and input changes now produce explicit log lines when the amp state changes externally or through the plugin.
- Added tests covering the new transition logging behavior.

## [1.0.6] - 2026-04-04
### Changed
- Switched characteristic reads from multiple live endpoint calls to a shared cached `/state` refresh path.
- `onGet` handlers now return cached values immediately and refresh bridge state in the background, which reduces Homebridge slow-read warnings for `On`, `Active`, `Active Identifier`, and `Brightness`.
- Added focused Node tests for state snapshot application, refresh deduplication, and cached getter behavior.

## [1.0.5] - 2026-03-19
### Changed
- Expanded the Homebridge version range to explicitly allow the `2.0.0-beta` series as well as stable `1.x` releases.
- This fixes npm peer dependency resolution failures when installing on Homebridge `2.0.0-beta.78`.

## [1.0.4] - 2026-03-19
### Changed
- Added standard npm metadata for homepage, repository subdirectory, publish config, and display name.
- Added a package-scoped README so npm and Homebridge users see install and configuration guidance directly on the package page.
- Declared Homebridge as a peer dependency and prepared the package for public npm publishing.

## [1.0.3] - 2026-03-19
### Changed
- Increased read request timeout tolerance for power, mute, input, and volume polling.
- Suppressed noisy warning logs for aborted read polls by keeping the last known value when a read times out.

## [1.0.2] - 2026-03-19
### Changed
- HomeKit now sees volume as a normal 0..100 percentage while the bridge still uses the amp's 0..50 device range internally.
- Volume reads, writes, and slider updates now translate cleanly between HomeKit percentage values and MA352 device levels.
- This fixes incorrect-looking Home app percentages and out-of-range display artifacts caused by exposing a 0..50 brightness range directly.

## [1.0.1] - 2026-02-07
### Changed
- Volume slider now ramps upward in +5 steps to mirror the bridge queue behavior.
- Volume range is capped at 0..50 in the UI and reads.
- Volume set errors from the bridge are surfaced to Homebridge logs.

## [1.0.0] - 2026-02-06
### Added
- Initial release.
