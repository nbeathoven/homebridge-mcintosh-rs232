# Releasing

This repo contains multiple deliverables, so releases should stay explicit about which component they refer to.

## Bridge Service

- Use GitHub tags/releases in the form `bridge-service-vX.Y.Z`.
- Keep [`bridge-service/CHANGELOG.md`](bridge-service/CHANGELOG.md) up to date first.
- Create the GitHub Release from the matching changelog entry.

## Homebridge Plugin

- Keep the npm package version in [`homebridge-ma352/package.json`](homebridge-ma352/package.json).
- Publish the plugin to npm separately from the bridge-service GitHub releases.

## Current Convention

- GitHub Releases page: bridge-service history
- npm package history: Homebridge plugin history
