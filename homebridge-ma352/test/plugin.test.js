const test = require("node:test");
const assert = require("node:assert/strict");

const pluginModule = require("../index.js");
const { MA352Platform } = pluginModule;

function createPlatform() {
  const infoCalls = [];
  const warnCalls = [];
  const log = {
    info(message) {
      infoCalls.push(message);
    },
    warn(message) {
      warnCalls.push(message);
    },
    debug() {},
  };
  const platform = new MA352Platform(log, {}, null);
  platform.updateAccessoriesFromCache = () => {};
  platform.infoCalls = infoCalls;
  platform.warnCalls = warnCalls;
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

test("applyStateSnapshot logs change-only state transitions after first snapshot", () => {
  const infoCalls = [];
  const platform = new MA352Platform(
    {
      info(message) {
        infoCalls.push(message);
      },
      warn() {},
      debug() {},
    },
    {},
    null,
  );
  platform.updateAccessoriesFromCache = () => {};

  platform.applyStateSnapshot({
    power: false,
    mute: false,
    volume: 10,
    input: 1,
  });

  assert.deepEqual(infoCalls, []);

  platform.applyStateSnapshot({
    power: true,
    mute: true,
    volume: 15,
    input: 6,
  });

  assert.deepEqual(infoCalls, [
    "Power changed: off -> on",
    "Mute changed: false -> true",
    "Volume changed: 10 -> 15",
    "Input changed: 1 (MC) -> 6 (AUX)",
  ]);
});

test("request falls back to the next configured bridge host", async () => {
  const platform = new MA352Platform(
    {
      info() {},
      warn() {},
      debug() {},
    },
    {
      host: "bad-host",
      fallbackHosts: ["good-host"],
      port: 5000,
    },
    null,
  );
  platform.updateAccessoriesFromCache = () => {};

  const originalFetch = global.fetch;
  global.fetch = async (url) => {
    if (url.startsWith("http://bad-host:5000")) {
      const error = new Error("fetch failed");
      error.cause = { code: "EHOSTUNREACH", message: "connect EHOSTUNREACH" };
      throw error;
    }
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  try {
    const response = await platform.request("/state");
    assert.equal(response.ok, true);
    assert.equal(platform.activeHost, "good-host");
    assert.equal(platform.bridgeAvailable, true);
  } finally {
    global.fetch = originalFetch;
  }
});

test("bridge failures are logged once until connectivity returns", async () => {
  const platform = createPlatform();
  platform.hosts = ["bad-host"];
  platform.activeHost = "bad-host";

  const originalFetch = global.fetch;
  global.fetch = async () => {
    const error = new Error("fetch failed");
    error.cause = { code: "EHOSTUNREACH", message: "connect EHOSTUNREACH" };
    throw error;
  };

  try {
    await assert.rejects(platform.request("/state"));
    await assert.rejects(platform.request("/state"));
    assert.equal(platform.bridgeAvailable, false);
    assert.equal(platform.warnCalls.length, 1);
    assert.match(platform.warnCalls[0], /Bridge request failed for \/state/);
    assert.match(platform.warnCalls[0], /bad-host:5000/);
  } finally {
    global.fetch = originalFetch;
  }
});

test("bridge recovery is logged after an outage", async () => {
  const platform = createPlatform();
  platform.hosts = ["good-host"];
  platform.activeHost = "good-host";

  let shouldFail = true;
  const originalFetch = global.fetch;
  global.fetch = async () => {
    if (shouldFail) {
      const error = new Error("fetch failed");
      error.cause = { code: "ETIMEDOUT", message: "connect ETIMEDOUT" };
      throw error;
    }
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  try {
    await assert.rejects(platform.request("/state"));
    shouldFail = false;
    await platform.request("/state");
    assert.equal(platform.bridgeAvailable, true);
    assert.deepEqual(platform.infoCalls, [
      "Bridge connectivity restored via good-host:5000.",
    ]);
  } finally {
    global.fetch = originalFetch;
  }
});
