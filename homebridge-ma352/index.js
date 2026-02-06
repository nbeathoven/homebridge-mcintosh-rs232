"use strict";

const PLUGIN_NAME = "homebridge-ma352";
const PLATFORM_NAME = "MA352Platform";

class MA352Platform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    this.api = api;

    this.host = this.config.host || "127.0.0.1";
    this.port = this.config.port || 5000;
    this.baseUrl = `http://${this.host}:${this.port}`;

    this.accessories = new Map();
    this.lastKnown = {
      power: false,
      mute: false,
      volume: 0,
    };

    if (!api) {
      return;
    }

    this.api.on("didFinishLaunching", () => {
      this.log.info("MA352 platform initialized; configuring accessories.");
      this.setupAccessories();
    });
  }

  configureAccessory(accessory) {
    this.accessories.set(accessory.UUID, accessory);
  }

  setupAccessories() {
    const powerAccessory = this.getOrCreateAccessory("MA352 Power", "ma352-power");
    this.setupPower(powerAccessory);

    const muteAccessory = this.getOrCreateAccessory("MA352 Mute", "ma352-mute");
    this.setupMute(muteAccessory);

    const volumeAccessory = this.getOrCreateAccessory("MA352 Volume", "ma352-volume");
    this.setupVolume(volumeAccessory);
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

  setupPower(accessory) {
    const Service = this.api.hap.Service;
    const Characteristic = this.api.hap.Characteristic;

    const service = accessory.getService(Service.Switch) || accessory.addService(Service.Switch, "MA352 Power");
    service.getCharacteristic(Characteristic.On)
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
      .setProps({ minValue: 0, maxValue: 100, minStep: 1 })
      .onSet(async (value) => {
        const level = Math.max(0, Math.min(100, Math.round(Number(value))));
        this.lastKnown.volume = level;
        await this.safePost(`/volume/set?level=${level}`, "volume");
      })
      .onGet(async () => {
        const level = await this.safeGetVolume();
        this.lastKnown.volume = level;
        return level;
      });
  }

  async safeGetVolume() {
    try {
      const res = await this.request("/volume");
      const data = await res.json();
      if (typeof data.level === "number") {
        return Math.max(0, Math.min(100, Math.round(data.level)));
      }
    } catch (err) {
      this.log.warn(`Volume read failed: ${err.message || err}`);
    }

    try {
      const res = await this.request("/volume/lvl");
      const text = await res.text();
      const level = Number(text.trim());
      if (!Number.isNaN(level)) {
        return Math.max(0, Math.min(100, Math.round(level)));
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
      throw new Error(`HTTP ${response.status} for ${path}`);
    }
    return response;
  }
}

module.exports = (api) => {
  api.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, MA352Platform);
};
