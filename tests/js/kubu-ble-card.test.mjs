// Tests for the card's pure logic. Run: node --test tests/js/
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activityEntities,
  activityItems,
  findEntities,
  firstSensorDevice,
  mainEntity,
  overallState,
  relativeTime,
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
