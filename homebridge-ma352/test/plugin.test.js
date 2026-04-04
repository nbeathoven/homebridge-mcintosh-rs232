const test = require("node:test");
const assert = require("node:assert/strict");

const pluginModule = require("../index.js");
const { MA352Platform } = pluginModule;

function createPlatform() {
  const log = {
    info() {},
    warn() {},
    debug() {},
  };
  const platform = new MA352Platform(log, {}, null);
  platform.updateAccessoriesFromCache = () => {};
  return platform;
}

test("applyStateSnapshot updates cached values from /state", () => {
  const platform = createPlatform();

  platform.applyStateSnapshot({
    power: true,
    mute: true,
    volume: 23,
    input: 6,
  });

  assert.equal(platform.lastKnown.power, true);
  assert.equal(platform.lastKnown.mute, true);
  assert.equal(platform.lastKnown.volume, 23);
  assert.equal(platform.lastKnown.input, 6);
});

test("refreshState deduplicates in-flight /state requests", async () => {
  const platform = createPlatform();
  let requestCount = 0;
  let releaseRequest;
  const waitForRequest = new Promise((resolve) => {
    releaseRequest = resolve;
  });

  platform.request = async () => {
    requestCount += 1;
    await waitForRequest;
    return {
      async json() {
        return { power: true, mute: false, volume: 11, input: 3 };
      },
    };
  };

  const first = platform.refreshState();
  const second = platform.refreshState();

  releaseRequest();
  await Promise.all([first, second]);
  assert.equal(requestCount, 1);
  assert.equal(platform.lastKnown.power, true);
  assert.equal(platform.lastKnown.volume, 11);
});

test("cached getters return immediately and trigger a refresh", () => {
  const platform = createPlatform();
  let refreshCalls = 0;
  platform.lastKnown = { power: true, mute: false, volume: 17, input: 4 };
  platform.refreshStateSoon = () => {
    refreshCalls += 1;
  };

  assert.equal(platform.getCachedPower(), true);
  assert.equal(platform.getCachedMute(), false);
  assert.equal(platform.getCachedVolume(), 17);
  assert.equal(platform.getCachedInput(), 4);
  assert.equal(refreshCalls, 4);
});
