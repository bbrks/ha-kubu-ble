// Tests for the card's pure logic. Run: node --test tests/js/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activityEntities,
  activityItems,
  configChange,
  findEntities,
  firstSensorDevice,
  formatWhen,
  lastRealChange,
  mainEntity,
  overallState,
  realChanges,
  relativeTime,
  resolveConfig,
  timeOptions,
} from "../../custom_components/kubu_ble/frontend/kubu-ble-card.js";

const DEVICE = "dev1";

/** A fake hass with a Kubu sensor device's entities. */
function fakeHass(states, { window = false, extra = {} } = {}) {
  const entities = {};
  const hassStates = {};
  const add = (entityId, deviceClass, translationKey, state, platform = "kubu_ble", deviceId = DEVICE) => {
    entities[entityId] = {
      entity_id: entityId,
      device_id: deviceId,
      platform,
      translation_key: translationKey,
    };
    hassStates[entityId] = {
      entity_id: entityId,
      state,
      last_changed: "2026-11-25T08:00:00Z",
      attributes: deviceClass ? { device_class: deviceClass } : {},
    };
  };
  const contact = window ? "window" : "door";
  add("binary_sensor.s_lock", "lock", null, states.lock ?? "off");
  add(`binary_sensor.s_${contact}`, contact, null, states[contact] ?? "off");
  add("binary_sensor.s_tamper", "tamper", null, states.tamper ?? "off");
  add("binary_sensor.s_battery", "battery", null, states.battery ?? "off");
  add("binary_sensor.s_vibration", "vibration", "vibration", "off");
  add("binary_sensor.s_force", "vibration", "excessive_force", "off");
  add("sensor.s_voltage", "voltage", null, "2.86");
  add("sensor.s_signal", "signal_strength", null, "-62");
  add("sensor.s_firmware", null, "firmware", "04.46");
  if (window) add("binary_sensor.s_tilted", null, "tilted", states.tilted ?? "off");
  // Not this device / not this integration: must be ignored.
  add("binary_sensor.other_door", "door", null, "on", "kubu_ble", "dev2");
  add("binary_sensor.zigbee_door", "door", null, "on", "zha", DEVICE);
  add("sensor.kubu_cloud_status", "enum", "cloud_status", "ok", "kubu_ble", "cloud");
  for (const [id, state] of Object.entries(extra)) hassStates[id] = state;
  return { entities, states: hassStates };
}

test("findEntities maps a door sensor's entities to roles", () => {
  const found = findEntities(fakeHass({}), DEVICE);
  assert.deepEqual(found, {
    lock: "binary_sensor.s_lock",
    door: "binary_sensor.s_door",
    tamper: "binary_sensor.s_tamper",
    battery_low: "binary_sensor.s_battery",
    vibration: "binary_sensor.s_vibration",
    excessive_force: "binary_sensor.s_force",
    voltage: "sensor.s_voltage",
    signal: "sensor.s_signal",
    firmware: "sensor.s_firmware",
  });
});

test("findEntities finds window and tilt roles", () => {
  const found = findEntities(fakeHass({}, { window: true }), DEVICE);
  assert.equal(found.window, "binary_sensor.s_window");
  assert.equal(found.tilted, "binary_sensor.s_tilted");
  assert.equal(found.door, undefined);
});

const status = (states, options) => {
  const hass = fakeHass(states, options);
  return overallState(hass, findEntities(hass, DEVICE));
};

test("locked door", () => {
  assert.deepEqual(status({}), {
    status: "Locked",
    detail: null,
    tone: "ok",
    icon: "mdi:door-closed-lock",
  });
});

test("unlocked but closed, like the app", () => {
  const s = status({ lock: "on" });
  assert.equal(s.status, "Unlocked");
  assert.equal(s.detail, "closed");
  assert.equal(s.tone, "warn");
  assert.equal(s.icon, "mdi:door-closed");
});

test("open door", () => {
  const s = status({ lock: "on", door: "on" });
  assert.equal(s.status, "Open");
  assert.equal(s.tone, "alert");
  assert.equal(s.icon, "mdi:door-open");
});

test("tamper wins over everything but out of range", () => {
  assert.equal(status({ tamper: "on", door: "on" }).status, "Tamper alert");
});

test("tilted window", () => {
  const s = status({ lock: "on", window: "on", tilted: "on" }, { window: true });
  assert.equal(s.status, "Tilted");
  assert.equal(s.icon, "mdi:window-open-variant");
});

test("open window", () => {
  const s = status({ lock: "on", window: "on" }, { window: true });
  assert.equal(s.status, "Open");
  assert.equal(s.icon, "mdi:window-open-variant");
});

test("out of range and waiting", () => {
  assert.equal(status({ lock: "unavailable" }).status, "Out of range");
  assert.equal(status({ lock: "unknown" }).status, "Waiting for the sensor");
});

test("a sensor without a lock (open/closed only)", () => {
  const hass = fakeHass({ door: "off" });
  delete hass.entities["binary_sensor.s_lock"];
  const entities = findEntities(hass, DEVICE);
  assert.equal(mainEntity(entities), "binary_sensor.s_door");
  assert.deepEqual(overallState(hass, entities), {
    status: "Closed",
    detail: null,
    tone: "ok",
    icon: "mdi:door-closed",
  });
});

test("no entities yet means out of range", () => {
  assert.equal(overallState({ states: {} }, {}).status, "Out of range");
});

test("activityItems merges, labels and sorts newest first", () => {
  const entities = findEntities(fakeHass({}), DEVICE);
  const history = {
    "binary_sensor.s_lock": [
      { s: "on", lu: 100 },
      { s: "off", lu: 400 },
      { s: "unavailable", lu: 450 },
    ],
    "binary_sensor.s_door": [
      { s: "on", lu: 200 },
      { s: "off", lc: 300, lu: 310 },
    ],
    "binary_sensor.s_tamper": [{ s: "on", lu: 50 }, { s: "off", lu: 60 }],
  };
  assert.deepEqual(
    activityItems(history, entities).map((i) => [i.label, i.time]),
    [
      ["Locked", 400_000],
      ["Closed", 300_000],
      ["Opened", 200_000],
      ["Unlocked", 100_000],
      ["Tamper alert", 50_000],
    ],
  );
});

test("activityItems gives each change an icon and a colour", () => {
  const icons = (hass, history) =>
    activityItems(history, findEntities(hass, DEVICE)).map((i) => [i.label, i.icon, i.tone]);
  assert.deepEqual(
    icons(fakeHass({}), {
      "binary_sensor.s_lock": [{ s: "on", lu: 1 }, { s: "off", lu: 4 }],
      "binary_sensor.s_door": [{ s: "on", lu: 2 }, { s: "off", lu: 3 }],
      "binary_sensor.s_tamper": [{ s: "on", lu: 5 }],
    }),
    [
      ["Tamper alert", "mdi:shield-alert", "alert"],
      ["Locked", "mdi:lock", "ok"],
      ["Closed", "mdi:door-closed", null],
      ["Opened", "mdi:door-open", "alert"],
      ["Unlocked", "mdi:lock-open-variant", "warn"],
    ],
  );
  assert.deepEqual(
    icons(fakeHass({}, { window: true }), {
      "binary_sensor.s_window": [{ s: "on", lu: 1 }, { s: "off", lu: 3 }],
      "binary_sensor.s_tilted": [{ s: "on", lu: 2 }],
    }),
    [
      ["Closed", "mdi:window-closed-variant", null],
      ["Tilted", "mdi:window-open-variant", "warn"],
      ["Opened", "mdi:window-open-variant", "alert"],
    ],
  );
});

test("activityItems keeps only the newest five", () => {
  const entities = findEntities(fakeHass({}), DEVICE);
  const changes = Array.from({ length: 8 }, (_, i) => ({ s: i % 2 ? "on" : "off", lu: i }));
  const items = activityItems({ "binary_sensor.s_lock": changes }, entities);
  assert.equal(items.length, 5);
  assert.equal(items[0].time, 7000);
});

test("activityItems copes with a missing or empty answer", () => {
  const entities = findEntities(fakeHass({}), DEVICE);
  assert.deepEqual(activityItems(null, entities), []);
  assert.deepEqual(activityItems({}, entities), []);
});

test("activityEntities lists only the state roles", () => {
  const entities = findEntities(fakeHass({}), DEVICE);
  assert.deepEqual(activityEntities(entities).sort(), [
    "binary_sensor.s_door",
    "binary_sensor.s_lock",
    "binary_sensor.s_tamper",
  ]);
});

test("relativeTime", () => {
  const now = 1_000_000_000;
  assert.equal(relativeTime(now - 10_000, now), "just now");
  assert.equal(relativeTime(now - 5 * 60_000, now), "5 minutes ago");
  assert.equal(relativeTime(now - 3 * 3600_000, now), "3 hours ago");
  assert.equal(relativeTime(now - 26 * 3600_000, now), "yesterday");
});

test("firstSensorDevice skips the Kubu cloud device", () => {
  const hass = {
    entities: {
      "sensor.kubu_cloud_status": {
        entity_id: "sensor.kubu_cloud_status",
        platform: "kubu_ble",
        device_id: "cloud",
      },
      "binary_sensor.s_lock": {
        entity_id: "binary_sensor.s_lock",
        platform: "kubu_ble",
        device_id: DEVICE,
      },
    },
  };
  assert.equal(firstSensorDevice(hass), DEVICE);
  assert.equal(firstSensorDevice({ entities: {} }), null);
});

test("realChanges drops unknown/unavailable and repeats (like after a restart)", () => {
  assert.deepEqual(
    realChanges([
      { s: "off", lu: 10 },
      { s: "unavailable", lu: 20 },
      { s: "off", lu: 30 }, // restart: same state again
      { s: "on", lc: 40, lu: 41 },
    ]),
    [
      { state: "off", time: 10_000 },
      { state: "on", time: 40_000 },
    ],
  );
  assert.deepEqual(realChanges(undefined), []);
});

test("activity after a restart does not repeat the state", () => {
  const entities = findEntities(fakeHass({}), DEVICE);
  const history = {
    "binary_sensor.s_lock": [
      { s: "on", lu: 100 },
      { s: "unavailable", lu: 7000 },
      { s: "on", lu: 7100 },
    ],
  };
  assert.deepEqual(
    activityItems(history, entities).map((i) => [i.label, i.time]),
    [["Unlocked", 100_000]],
  );
});

test("lastRealChange: when the shown state really started", () => {
  const entities = findEntities(fakeHass({}), DEVICE);
  const history = {
    "binary_sensor.s_lock": [{ s: "on", lu: 100 }, { s: "on", lu: 7100 }],
    "binary_sensor.s_door": [{ s: "on", lu: 200 }, { s: "off", lu: 300 }],
    "binary_sensor.s_tamper": [{ s: "on", lu: 250 }, { s: "off", lu: 260 }],
  };
  assert.equal(lastRealChange(history, entities), 300_000);
  assert.equal(lastRealChange(null, entities), null);
  assert.equal(lastRealChange({}, entities), null);
});

test("formatWhen shows clock times, with the day when needed", () => {
  const options = { locale: "en-GB", hour12: false, timeZone: "UTC" };
  const now = Date.UTC(2026, 10, 25, 16, 0); // Wed 25 Nov 2026 16:00
  assert.equal(formatWhen(Date.UTC(2026, 10, 25, 13, 42), now, options), "13:42");
  assert.equal(formatWhen(Date.UTC(2026, 10, 24, 9, 5), now, options), "Yesterday 09:05");
  assert.equal(formatWhen(Date.UTC(2026, 10, 22, 18, 30), now, options), "Sun 18:30");
  assert.equal(formatWhen(Date.UTC(2026, 10, 12, 7, 0), now, options), "12 Nov 07:00");
  // 12-hour clock when the user chose it.
  const twelve = formatWhen(Date.UTC(2026, 10, 25, 13, 42), now, { ...options, hour12: true });
  assert.match(twelve, /^0?1:42\s?pm$/i);
});

test("timeOptions follows the user's HA profile", () => {
  assert.deepEqual(
    timeOptions({
      locale: { language: "de", time_format: "24", time_zone: "server" },
      config: { time_zone: "Europe/Berlin" },
    }),
    { locale: "de", hour12: false, timeZone: "Europe/Berlin" },
  );
  assert.deepEqual(
    timeOptions({ locale: { language: "en", time_format: "language", time_zone: "local" } }),
    { locale: "en", hour12: undefined, timeZone: undefined },
  );
});

const PARTS = [
  "show_icon",
  "show_name",
  "show_status",
  "show_since",
  "show_alerts",
  "show_battery",
  "show_signal",
  "show_firmware",
  "show_activity",
  "show_details",
];

test("resolveConfig: every part is on by default", () => {
  const config = resolveConfig({ device_id: DEVICE });
  for (const part of PARTS) assert.equal(config[part], true, part);
  assert.equal(config.compact, false);
  assert.equal(config.activity_hours, 24);
});

test("resolveConfig: compact shows one line unless parts are turned on", () => {
  const off = ["show_since", "show_alerts", "show_signal", "show_firmware", "show_activity", "show_details"];
  const compact = resolveConfig({ device_id: DEVICE, compact: true });
  for (const part of PARTS) assert.equal(compact[part], !off.includes(part), part);
  assert.equal(resolveConfig({ device_id: DEVICE, compact: true, show_activity: true }).show_activity, true);
  assert.equal(resolveConfig({ device_id: DEVICE, show_firmware: false }).show_firmware, false);
});

test("configChange keeps only what differs from the defaults", () => {
  const old = { type: "custom:kubu-ble-card", device_id: DEVICE };
  const shown = resolveConfig(old);
  assert.deepEqual(configChange(old, shown, { ...shown, show_firmware: false }), {
    ...old,
    show_firmware: false,
  });
  // Turning it back on removes it again.
  const hidden = { ...old, show_firmware: false };
  assert.deepEqual(configChange(hidden, resolveConfig(hidden), resolveConfig(old)), old);
  // A cleared name goes away.
  const named = { ...old, name: "Back door" };
  assert.deepEqual(configChange(named, resolveConfig(named), { ...resolveConfig(named), name: "" }), old);
});

test("configChange: switching to compact picks up the compact defaults", () => {
  const old = { type: "custom:kubu-ble-card", device_id: DEVICE };
  const shown = resolveConfig(old);
  const next = configChange(old, shown, { ...shown, compact: true });
  assert.deepEqual(next, { ...old, compact: true });
  assert.equal(resolveConfig(next).show_activity, false);
  // A part the user turned on by hand stays on.
  const withActivity = { ...old, compact: true, show_activity: true };
  const shownCompact = resolveConfig(withActivity);
  assert.deepEqual(
    configChange(withActivity, shownCompact, { ...shownCompact, compact: false }),
    old,
  );
});
