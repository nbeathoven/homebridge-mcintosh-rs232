# homebridge-ma352

Homebridge platform plugin for controlling a McIntosh MA-352 through the companion local RS-232 bridge service in this repository.

Author: `nbeathoven`

## Install

```bash
npm install -g homebridge-ma352
```

You also need the MA-352 bridge service running on a host that can talk to the amplifier over RS-232.

Repository: https://github.com/nbeathoven/homebridge-mcintosh-rs232

Revisions: see `CHANGELOG.md` in this package for release notes.

## Homebridge Config

```json
{
  "platform": "MA352Platform",
  "name": "McIntosh Amp",
  "host": "192.168.1.50",
  "port": 5000,
  "inputs": [
    { "value": 1, "name": "MC" },
    { "value": 3, "name": "CD1" },
    { "value": 6, "name": "AUX" }
  ]
}
```

If `inputs` is omitted, the plugin exposes the default 1-9 input map.

## Features

- Power on/off
- Mute on/off
- Volume slider mapped from HomeKit 0-100 to device 0-50
- Input selection through a TV-style accessory

## Requirements

- Node.js 18+
- Homebridge 1.6+
- The MA-352 bridge service from the repository above
