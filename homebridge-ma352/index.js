"use strict";

const PLUGIN_NAME = "homebridge-ma352";
const PLATFORM_NAME = "MA352Platform";
const DEVICE_VOLUME_MAX = 50;
const HOMEKIT_VOLUME_MAX = 100;
const VOLUME_RAMP_STEP = 5;
const VOLUME_RAMP_DELAY_MS = 1000;
const REQUEST_TIMEOUT_MS = 2000;
const READ_TIMEOUT_MS = 3500;
const STATE_REFRESH_INTERVAL_MS = 5000;
const WRITE_REFRESH_DELAY_MS = 250;

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
    this.refreshInFlight = null;
    this.refreshTimer = null;
    this.statePollTimer = null;
    this.volumeRampTimer = null;
    this.volumeRampTarget = null;

    this.inputMap = this.buildInputMap();

    if (!api) {
      return;
    }

    this.api.on("didFinishLaunching", () => {
      this.log.info("MA352 platform initialized; configuring accessories.");
      this.setupAccessories();
      this.refreshStateSoon();
      this.startStatePolling();
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
        this.refreshStateSoon(WRITE_REFRESH_DELAY_MS);
      })
      .onGet(() => {
        return this.getCachedPower();
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
          this.refreshStateSoon(WRITE_REFRESH_DELAY_MS);
        })
        .onGet(() => {
          const current = this.getCachedInput();
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
        this.refreshStateSoon(WRITE_REFRESH_DELAY_MS);
      })
      .onGet(() => {
        return this.getCachedMute();
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
      .setProps({ minValue: 0, maxValue: HOMEKIT_VOLUME_MAX, minStep: 1 })
      .onSet(async (value) => {
        const requested = this.homekitToDeviceVolume(value);
        const current = this.getLastKnownDeviceVolume();
        this.stopVolumeRamp();
        try {
          await this.request(`/volume/set?level=${requested}`, { method: "POST" });
        } catch (err) {
          this.log.warn(`Volume request failed: ${err.message || err}`);
          throw err;
        }

        if (requested > current && (requested - current) > VOLUME_RAMP_STEP) {
          this.startVolumeRamp(service, requested);
          this.refreshStateSoon(WRITE_REFRESH_DELAY_MS);
          return;
        }

        this.lastKnown.volume = requested;
        service.updateCharacteristic(Characteristic.Brightness, this.deviceToHomekitVolume(requested));
        this.refreshStateSoon(WRITE_REFRESH_DELAY_MS);
      })
      .onGet(() => {
        return this.deviceToHomekitVolume(this.getCachedVolume());
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

  startVolumeRamp(service, target) {
    this.stopVolumeRamp();
    this.volumeRampTarget = target;

    const tick = () => {
      const current = this.getLastKnownDeviceVolume();
      if (current >= target) {
        this.volumeRampTimer = null;
        return;
      }
      const next = Math.min(target, current + VOLUME_RAMP_STEP);
      this.lastKnown.volume = next;
      service.updateCharacteristic(
        this.api.hap.Characteristic.Brightness,
        this.deviceToHomekitVolume(next),
      );
      if (next < target) {
        this.volumeRampTimer = setTimeout(tick, VOLUME_RAMP_DELAY_MS);
      } else {
        this.volumeRampTimer = null;
      }
    };

    this.volumeRampTimer = setTimeout(tick, 0);
  }

  stopVolumeRamp() {
    if (!this.volumeRampTimer) {
      this.volumeRampTarget = null;
      return;
    }
    clearTimeout(this.volumeRampTimer);
    this.volumeRampTimer = null;
    this.volumeRampTarget = null;
  }

  startStatePolling() {
    if (this.statePollTimer) {
      clearInterval(this.statePollTimer);
    }
    this.statePollTimer = setInterval(() => {
      this.refreshStateSoon();
    }, STATE_REFRESH_INTERVAL_MS);
  }

  refreshStateSoon(delayMs = 0) {
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.refreshState();
    }, delayMs);
  }

  async refreshState() {
    if (this.refreshInFlight) {
      return this.refreshInFlight;
    }

    this.refreshInFlight = (async () => {
      try {
        const res = await this.request("/state", { timeoutMs: READ_TIMEOUT_MS });
        const data = await res.json();
        if (data && typeof data === "object") {
          this.applyStateSnapshot(data);
        }
      } catch (err) {
        this.logReadFailure("State", err);
      } finally {
        this.refreshInFlight = null;
      }
    })();

    return this.refreshInFlight;
  }

  applyStateSnapshot(snapshot) {
    if (typeof snapshot.power === "boolean") {
      this.lastKnown.power = snapshot.power;
    }
    if (typeof snapshot.mute === "boolean") {
      this.lastKnown.mute = snapshot.mute;
    }
    if (typeof snapshot.volume === "number") {
      this.lastKnown.volume = this.normalizeDeviceVolume(snapshot.volume);
    }
    if (typeof snapshot.input === "number") {
      const value = Math.round(Number(snapshot.input));
      if (this.inputMap.has(value)) {
        this.lastKnown.input = value;
      }
    }

    this.updateAccessoriesFromCache();
  }

  updateAccessoriesFromCache() {
    const Service = this.api?.hap?.Service;
    const Characteristic = this.api?.hap?.Characteristic;
    if (!Service || !Characteristic) {
      return;
    }

    const accessory = this.accessories.get(this.api.hap.uuid.generate(this.mainKey));
    if (!accessory) {
      return;
    }

    const tvService = accessory.getService(Service.Television);
    if (tvService) {
      tvService.updateCharacteristic(Characteristic.Active, this.lastKnown.power);
      if (typeof this.lastKnown.input === "number") {
        tvService.updateCharacteristic(Characteristic.ActiveIdentifier, this.lastKnown.input);
      }
    }

    const muteService = accessory.getService(Service.Switch);
    if (muteService) {
      muteService.updateCharacteristic(Characteristic.On, this.lastKnown.mute);
    }

    const volumeService = accessory.getService(Service.Lightbulb);
    if (volumeService) {
      volumeService.updateCharacteristic(
        Characteristic.Brightness,
        this.deviceToHomekitVolume(this.lastKnown.volume),
      );
    }
  }

  getCachedPower() {
    this.refreshStateSoon();
    return this.lastKnown.power;
  }

  getCachedMute() {
    this.refreshStateSoon();
    return this.lastKnown.mute;
  }

  getCachedVolume() {
    this.refreshStateSoon();
    return this.getLastKnownDeviceVolume();
  }

  getCachedInput() {
    this.refreshStateSoon();
    return this.lastKnown.input;
  }

  async safeGetVolume() {
    try {
      const res = await this.request("/volume", { timeoutMs: READ_TIMEOUT_MS });
      const data = await res.json();
      if (typeof data.level === "number") {
        return this.normalizeDeviceVolume(data.level);
      }
    } catch (err) {
      this.logReadFailure("Volume", err);
    }

    try {
      const res = await this.request("/volume/lvl", { timeoutMs: READ_TIMEOUT_MS });
      const text = await res.text();
      const level = Number(text.trim());
      if (!Number.isNaN(level)) {
        return this.normalizeDeviceVolume(level);
      }
    } catch (err) {
      this.logReadFailure("Volume fallback", err);
    }

    return this.getLastKnownDeviceVolume();
  }

  normalizeDeviceVolume(value) {
    return Math.max(0, Math.min(DEVICE_VOLUME_MAX, Math.round(Number(value))));
  }

  getLastKnownDeviceVolume() {
    return Number.isFinite(this.lastKnown.volume) ? this.lastKnown.volume : 0;
  }

  homekitToDeviceVolume(value) {
    const homekitValue = Math.max(0, Math.min(HOMEKIT_VOLUME_MAX, Math.round(Number(value))));
    return Math.round((homekitValue / HOMEKIT_VOLUME_MAX) * DEVICE_VOLUME_MAX);
  }

  deviceToHomekitVolume(value) {
    const deviceValue = this.normalizeDeviceVolume(value);
    return Math.round((deviceValue / DEVICE_VOLUME_MAX) * HOMEKIT_VOLUME_MAX);
  }

  async safeGetMute() {
    try {
      const res = await this.request("/mute", { timeoutMs: READ_TIMEOUT_MS });
      const data = await res.json();
      if (typeof data.muted === "boolean") {
        return data.muted;
      }
    } catch (err) {
      this.logReadFailure("Mute", err);
    }

    return this.lastKnown.mute;
  }

  async safeGetPower() {
    try {
      const res = await this.request("/power", { timeoutMs: READ_TIMEOUT_MS });
      const data = await res.json();
      if (typeof data.on === "boolean") {
        return data.on;
      }
    } catch (err) {
      this.logReadFailure("Power", err);
    }

    return this.lastKnown.power;
  }

  async safeGetInput() {
    try {
      const res = await this.request("/input", { timeoutMs: READ_TIMEOUT_MS });
      const data = await res.json();
      if (typeof data.value === "number") {
        const value = Math.round(Number(data.value));
        if (this.inputMap.has(value)) {
          return value;
        }
      }
    } catch (err) {
      this.logReadFailure("Input", err);
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

  logReadFailure(label, err) {
    if (this.isAbortError(err)) {
      this.log.debug?.(`${label} read timed out; keeping last known value.`);
      return;
    }
    this.log.warn(`${label} read failed: ${err.message || err}`);
  }

  isAbortError(err) {
    if (!err) {
      return false;
    }
    return err.name === "AbortError" || err.message === "This operation was aborted";
  }

  async request(path, options = {}) {
    const { timeoutMs = REQUEST_TIMEOUT_MS, ...fetchOptions } = options;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...fetchOptions,
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

module.exports.MA352Platform = MA352Platform;
