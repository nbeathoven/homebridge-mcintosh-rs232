# @nbeathoven/homebridge-ma352

Homebridge platform plugin for controlling a McIntosh MA-352 through the companion local RS-232 bridge service in this repository.

Author: `nbeathoven`

## Install

```bash
npm install -g @nbeathoven/homebridge-ma352
```

You also need the MA-352 bridge service running on a host that can talk to the amplifier over RS-232.

Repository: https://github.com/nbeathoven/homebridge-mcintosh-rs232

Revisions: see `CHANGELOG.md` in this package for release notes.

## Homebridge Config

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

If `inputs` is omitted, the plugin exposes the default 1-9 input map.
If `fallbackHosts` is set, the plugin will retry those endpoints when the primary host is unreachable and log connectivity loss and recovery explicitly.

## Features

- Power on/off
- Mute on/off
- Volume exposed as a native `TelevisionSpeaker` linked to the TV accessory: control it with the iPhone/iPad hardware volume buttons and the Control Center Remote (HomeKit 0-100 mapped to device 0-50). Note there is no on-tile slider — Apple's Home app does not render one for `TelevisionSpeaker`.
- Mute available both on the speaker and as a standalone switch for a quick toggle
- Input selection through a TV-style accessory
- Cached bridge state refresh via `/state` so HomeKit reads return quickly instead of blocking on multiple live HTTP calls
- Change-only Homebridge logs when the amp power, mute, input, or volume state changes
- Endpoint failover with explicit backend outage and recovery logging

## Requirements

- Node.js 18+
- Homebridge 1.6+
- The MA-352 bridge service from the repository above
