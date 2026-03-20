# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
