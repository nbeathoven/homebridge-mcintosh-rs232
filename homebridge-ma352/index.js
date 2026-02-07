"use strict";

const PLUGIN_NAME = "homebridge-ma352";
const PLATFORM_NAME = "MA352Platform";

class MA352Platform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    this.api = api;

    this.deviceName = this.config.name || "McIntosh Amp";
    this.host = this.config.host || "127.0.0.1";
    this.port = this.config.port || 5000;
    this.baseUrl = `http://${this.host}:${this.port}`;
    this.mainKey = "ma352-main";

    this.accessories = new Map();
    this.lastKnown = {
      power: false,
      mute: false,
      volume: 0,
      input: null,
    };

    this.inputMap = this.buildInputMap();

    if (!api) {
      return;
    }

    this.api.on("didFinishLaunching", () => {
      this.log.info("MA352 platform initialized; configuring accessories.");
      this.setupAccessories();
    });
  }

  buildInputMap() {
    const map = new Map();
    const inputs = Array.isArray(this.config.inputs) ? this.config.inputs : null;
    if (inputs && inputs.length > 0) {
      for (const entry of inputs) {
        const value = Number(entry?.value);
        const name = String(entry?.name || "").trim();
        if (!Number.isInteger(value) || value < 1 || value > 9 || !name) {
          this.log.warn(`Invalid input entry skipped: ${JSON.stringify(entry)}`);
          continue;
        }
        if (map.has(value)) {
          this.log.warn(`Duplicate input value ${value} skipped.`);
          continue;
        }
        map.set(value, name);
      }
      return map;
    }

    map.set(1, "MC");
    map.set(2, "MM");
    map.set(3, "CD1");
    map.set(4, "CD2");
    map.set(5, "DVD");
    map.set(6, "AUX");
    map.set(7, "Server");
    map.set(8, "D2A");
    map.set(9, "Tuner");
    return map;
  }

  configureAccessory(accessory) {
    this.accessories.set(accessory.UUID, accessory);
  }

  setupAccessories() {
    const mainAccessory = this.getOrCreateAccessory(this.deviceName, this.mainKey);
    this.removeStaleAccessories(mainAccessory.UUID);
    this.setupTelevision(mainAccessory);
    this.setupMute(mainAccessory);
    this.setupVolume(mainAccessory);
  }

  getOrCreateAccessory(name, key) {
    const uuid = this.api.hap.uuid.generate(key);
    if (this.accessories.has(uuid)) {
      return this.accessories.get(uuid);
    }

    const accessory = new this.api.platformAccessory(name, uuid);
    this.api.registerPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, [accessory]);
    this.accessories.set(uuid, accessory);
    return accessory;
  }

  removeStaleAccessories(keepUuid) {
    const stale = [];
    for (const [uuid, accessory] of this.accessories.entries()) {
      if (uuid !== keepUuid) {
        stale.push(accessory);
      }
    }
    if (stale.length > 0) {
      this.api.unregisterPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, stale);
      for (const accessory of stale) {
        this.accessories.delete(accessory.UUID);
      }
    }
  }

  setupTelevision(accessory) {
    const Service = this.api.hap.Service;
    const Characteristic = this.api.hap.Characteristic;

    const tvService = accessory.getService(Service.Television) ||
      accessory.addService(Service.Television, this.deviceName);

    accessory.category = this.api.hap.Categories.TELEVISION;
    accessory.primaryService = tvService;

    tvService.setCharacteristic(Characteristic.ConfiguredName, this.deviceName);
    tvService.setCharacteristic(
      Characteristic.SleepDiscoveryMode,
      Characteristic.SleepDiscoveryMode.ALWAYS_DISCOVERABLE,
    );

    tvService.getCharacteristic(Characteristic.Active)
      .onSet(async (value) => {
        const isOn = Boolean(value);
        this.lastKnown.power = isOn;
        const path = isOn ? "/power/on" : "/power/off";
        await this.safePost(path, "power");
      })
      .onGet(async () => {
        const isOn = await this.safeGetPower();
        this.lastKnown.power = isOn;
        return isOn;
      });

    if (this.inputMap.size > 0) {
      this.setupInputs(accessory, tvService);
      tvService.getCharacteristic(Characteristic.ActiveIdentifier)
        .onSet(async (value) => {
          const identifier = Number(value);
          if (!this.inputMap.has(identifier)) {
            return;
          }
          await this.safeSetInput(identifier);
          this.lastKnown.input = identifier;
          tvService.updateCharacteristic(Characteristic.ActiveIdentifier, identifier);
        })
        .onGet(async () => {
          const current = await this.safeGetInput();
          if (typeof current === "number") {
            return current;
          }
          const first = this.inputMap.keys().next().value;
          return Number.isInteger(first) ? first : 1;
        });
    }
  }

  setupMute(accessory) {
    const Service = this.api.hap.Service;
    const Characteristic = this.api.hap.Characteristic;

    const service = accessory.getService(Service.Switch) || accessory.addService(Service.Switch, "MA352 Mute");
    service.getCharacteristic(Characteristic.On)
      .onSet(async (value) => {
        const isOn = Boolean(value);
        this.lastKnown.mute = isOn;
        const path = isOn ? "/mute/on" : "/mute/off";
        await this.safePost(path, "mute");
      })
      .onGet(async () => {
        const muted = await this.safeGetMute();
        this.lastKnown.mute = muted;
        return muted;
      });
  }

  setupVolume(accessory) {
    const Service = this.api.hap.Service;
    const Characteristic = this.api.hap.Characteristic;

    const service = accessory.getService(Service.Lightbulb) || accessory.addService(Service.Lightbulb, "MA352 Volume");
    service.getCharacteristic(Characteristic.On)
      .onSet(async () => {
        // Keep the volume slider always available; ignore On/Off toggles.
      })
      .onGet(async () => {
        return true;
      });
    service.getCharacteristic(Characteristic.Brightness)
      .setProps({ minValue: 0, maxValue: 50, minStep: 1 })
      .onSet(async (value) => {
        const level = Math.max(0, Math.min(50, Math.round(Number(value))));
        this.lastKnown.volume = level;
        try {
          await this.request(`/volume/set?level=${level}`, { method: "POST" });
        } catch (err) {
          this.log.warn(`Volume request failed: ${err.message || err}`);
          throw err;
        }
        service.updateCharacteristic(Characteristic.Brightness, level);
      })
      .onGet(async () => {
        const level = await this.safeGetVolume();
        this.lastKnown.volume = level;
        return level;
      });
  }

  setupInputs(accessory, tvService) {
    const Service = this.api.hap.Service;
    const Characteristic = this.api.hap.Characteristic;

    const validSubtypes = new Set();
    for (const [value, label] of this.inputMap.entries()) {
      const subtype = `input-${value}`;
      validSubtypes.add(subtype);
      const inputService = accessory.getServiceById(Service.InputSource, subtype) ||
        accessory.addService(Service.InputSource, label, subtype);

      inputService.setCharacteristic(Characteristic.Identifier, value);
      inputService.setCharacteristic(Characteristic.ConfiguredName, label);
      inputService.setCharacteristic(Characteristic.IsConfigured, Characteristic.IsConfigured.CONFIGURED);
      inputService.setCharacteristic(Characteristic.InputSourceType, Characteristic.InputSourceType.OTHER);

      tvService.addLinkedService(inputService);
    }

    for (const service of accessory.services) {
      if (service.UUID !== Service.InputSource.UUID) {
        continue;
      }
      if (!validSubtypes.has(service.subtype)) {
        accessory.removeService(service);
      }
    }
  }

  async safeGetVolume() {
    try {
      const res = await this.request("/volume");
      const data = await res.json();
      if (typeof data.level === "number") {
        return Math.max(0, Math.min(50, Math.round(data.level)));
      }
    } catch (err) {
      this.log.warn(`Volume read failed: ${err.message || err}`);
    }

    try {
      const res = await this.request("/volume/lvl");
      const text = await res.text();
      const level = Number(text.trim());
      if (!Number.isNaN(level)) {
        return Math.max(0, Math.min(50, Math.round(level)));
      }
    } catch (err) {
      this.log.warn(`Volume fallback read failed: ${err.message || err}`);
    }

    return this.lastKnown.volume || 0;
  }

  async safeGetMute() {
    try {
      const res = await this.request("/mute");
      const data = await res.json();
      if (typeof data.muted === "boolean") {
        return data.muted;
      }
    } catch (err) {
      this.log.warn(`Mute read failed: ${err.message || err}`);
    }

    return this.lastKnown.mute;
  }

  async safeGetPower() {
    try {
      const res = await this.request("/power");
      const data = await res.json();
      if (typeof data.on === "boolean") {
        return data.on;
      }
    } catch (err) {
      this.log.warn(`Power read failed: ${err.message || err}`);
    }

    return this.lastKnown.power;
  }

  async safeGetInput() {
    try {
      const res = await this.request("/input");
      const data = await res.json();
      if (typeof data.value === "number") {
        const value = Math.round(Number(data.value));
        if (this.inputMap.has(value)) {
          return value;
        }
      }
    } catch (err) {
      this.log.warn(`Input read failed: ${err.message || err}`);
    }

    return this.lastKnown.input;
  }

  async safeSetInput(value) {
    try {
      await this.request(`/input/set?value=${value}`, { method: "POST" });
    } catch (err) {
      this.log.warn(`Input request failed: ${err.message || err}`);
    }
  }

  async safePost(path, label) {
    try {
      await this.request(path, { method: "POST" });
    } catch (err) {
      this.log.warn(`${label} request failed: ${err.message || err}`);
    }
  }

  async request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    let response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    if (!response.ok) {
      let detail = "";
      try {
        const data = await response.json();
        if (data && typeof data.error === "string") {
          detail = data.error;
        } else {
          detail = JSON.stringify(data);
        }
      } catch (err) {
        try {
          detail = await response.text();
        } catch (textErr) {
          detail = "";
        }
      }
      const suffix = detail ? `: ${detail}` : "";
      throw new Error(`HTTP ${response.status} for ${path}${suffix}`);
    }
    return response;
  }
}

module.exports = (api) => {
  api.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, MA352Platform);
};
