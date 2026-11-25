/**
 * Kubu sensor card for Home Assistant dashboards.
 *
 * Served and loaded by the Kubu BLE integration itself, so it needs no
 * manual resource. Plain ES module, no build step and no packages: it uses
 * HA's own <ha-card>, <ha-icon> and <ha-form> elements and theme colours.
 *
 * Layout ideas (big state picture, "Unlocked, closed" status line, alert
 * banner, history list) follow the Kubu app; the look is HA's own.
 *
 *   type: custom:kubu-ble-card
 *   device_id: <a Kubu sensor device>
 *   compact: false
 *
 * The pure functions are exported so `node --test tests/js/*.test.mjs` can test them.
 */

export const DOMAIN = "kubu_ble";
export const CARD_TYPE = "kubu-ble-card";
const DOCS_URL = "https://github.com/bbrks/ha-kubu-ble#dashboard-card";
const MAX_ACTIVITY = 5;

export const DEFAULTS = {
  compact: false,
  show_battery: true,
  show_alerts: true,
  show_activity: true,
  show_details: true,
  activity_hours: 24,
};

const TONES = {
  ok: "var(--success-color, #43a047)",
  warn: "var(--warning-color, #ffa600)",
  alert: "var(--error-color, #db4437)",
  off: "var(--disabled-color, #bdbdbd)",
};

// --- Pure logic (tested with node --test) ------------------------------------

const BINARY_BY_KEY = ["tilted", "vibration", "excessive_force"];
const BINARY_BY_CLASS = ["lock", "door", "window", "tamper"];
const SENSOR_BY_KEY = ["firmware", "vibration_sensitivity", "battery_type"];

/** Map one Kubu device's entities to roles: {lock: "binary_sensor.x", ...}. */
export function findEntities(hass, deviceId) {
  const found = {};
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.device_id !== deviceId || entry.platform !== DOMAIN) continue;
    const domain = entry.entity_id.split(".")[0];
    const deviceClass = hass.states[entry.entity_id]?.attributes?.device_class;
    const key = entry.translation_key;
    let role = null;
    if (domain === "binary_sensor") {
      if (BINARY_BY_KEY.includes(key)) role = key;
      else if (BINARY_BY_CLASS.includes(deviceClass)) role = deviceClass;
      else if (deviceClass === "battery") role = "battery_low";
    } else if (domain === "sensor") {
      if (SENSOR_BY_KEY.includes(key)) role = key;
      else if (deviceClass === "voltage") role = "voltage";
      else if (deviceClass === "signal_strength") role = "signal";
    }
    if (role && !found[role]) found[role] = entry.entity_id;
  }
  return found;
}

/** The entity to open on tap: the lock, else the door or window. */
export function mainEntity(entities) {
  return entities.lock || entities.door || entities.window || null;
}

/**
 * The sensor's overall state, the way the Kubu app shows it.
 * Returns {status, detail, tone, icon}; detail is shown muted after a comma.
 */
export function overallState(hass, entities) {
  const state = (role) => (entities[role] ? hass.states[entities[role]] : undefined);
  const on = (role) => state(role)?.state === "on";
  const isWindow = Boolean(entities.window) && !entities.door;
  const contact = isWindow ? "window" : "door";
  const closedIcon = isWindow ? "mdi:window-closed-variant" : "mdi:door-closed";
  const main = state("lock") || state(contact);

  if (!main || main.state === "unavailable") {
    return { status: "Out of range", detail: null, tone: "off", icon: "mdi:bluetooth-off" };
  }
  if (main.state === "unknown") {
    return {
      status: "Waiting for the sensor",
      detail: null,
      tone: "off",
      icon: "mdi:bluetooth-settings",
    };
  }
  if (on("tamper")) {
    return { status: "Tamper alert", detail: null, tone: "alert", icon: "mdi:shield-alert" };
  }
  if (on(contact)) {
    if (on("tilted")) {
      return { status: "Tilted", detail: null, tone: "warn", icon: "mdi:window-open-variant" };
    }
    return {
      status: "Open",
      detail: null,
      tone: "alert",
      icon: isWindow ? "mdi:window-open-variant" : "mdi:door-open",
    };
  }
  if (state("lock")) {
    if (on("lock")) {
      return { status: "Unlocked", detail: "closed", tone: "warn", icon: closedIcon };
    }
    return {
      status: "Locked",
      detail: null,
      tone: "ok",
      icon: isWindow ? "mdi:window-closed-variant" : "mdi:door-closed-lock",
    };
  }
  return { status: "Closed", detail: null, tone: "ok", icon: closedIcon };
}

const ACTIVITY_LABELS = {
  lock: { on: "Unlocked", off: "Locked" },
  door: { on: "Opened", off: "Closed" },
  window: { on: "Opened", off: "Closed" },
  tilted: { on: "Tilted" },
  tamper: { on: "Tamper alert" },
};

/** The roles whose changes make up the activity list. */
export function activityEntities(entities) {
  return Object.keys(ACTIVITY_LABELS)
    .filter((role) => entities[role])
    .map((role) => entities[role]);
}

const NOT_A_STATE = new Set(["unknown", "unavailable"]);

/**
 * The real changes of one entity in a minimal-response history list
 * ([{s, lu, lc}]): no unknown/unavailable, and no repeats of the same
 * state (HA writes the state again after a restart, for example).
 * Returns [{state, time (ms)}], oldest first.
 */
export function realChanges(changes) {
  const out = [];
  let last = null;
  for (const change of changes || []) {
    const seconds = change.lc ?? change.lu;
    if (NOT_A_STATE.has(change.s) || typeof seconds !== "number") continue;
    if (change.s === last) continue;
    last = change.s;
    out.push({ state: change.s, time: seconds * 1000 });
  }
  return out;
}

/**
 * Turn a minimal-response history answer ({entity_id: [{s, lu, lc}]}) into
 * the newest few labelled changes: [{label, time (ms), entityId}].
 */
export function activityItems(history, entities, limit = MAX_ACTIVITY) {
  const items = [];
  for (const [role, labels] of Object.entries(ACTIVITY_LABELS)) {
    const entityId = entities[role];
    for (const change of realChanges(entityId && history?.[entityId])) {
      const label = labels[change.state];
      if (label) items.push({ label, time: change.time, entityId });
    }
  }
  items.sort((a, b) => b.time - a.time);
  return items.slice(0, limit);
}

/**
 * When the shown state started: the newest real change of the state
 * entities in the history, or null if there is none. The entities'
 * last_changed is not used for this: HA resets it on every restart.
 */
export function lastRealChange(history, entities) {
  let newest = null;
  for (const entityId of activityEntities(entities)) {
    for (const change of realChanges(history?.[entityId])) {
      if (newest === null || change.time > newest) newest = change.time;
    }
  }
  return newest;
}

/**
 * A clock time for the activity list: "13:42" today, "Yesterday 13:42",
 * "Mon 13:42" this week, else "12 Nov 13:42".
 * options: {locale, hour12, timeZone}, from the user's HA settings.
 */
export function formatWhen(time, now = Date.now(), options = {}) {
  const locale = options.locale || "en";
  const base = { timeZone: options.timeZone };
  const clock = new Intl.DateTimeFormat(locale, {
    ...base,
    hour: "2-digit",
    minute: "2-digit",
    hour12: options.hour12,
  }).format(time);
  const day = (t) =>
    new Intl.DateTimeFormat("en-CA", { ...base, year: "numeric", month: "2-digit", day: "2-digit" })
      .format(t);
  if (day(time) === day(now)) return clock;
  if (day(time) === day(now - 86_400_000)) {
    const yesterday = new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-1, "day");
    return `${yesterday.charAt(0).toUpperCase()}${yesterday.slice(1)} ${clock}`;
  }
  const date =
    now - time < 6 * 86_400_000
      ? new Intl.DateTimeFormat(locale, { ...base, weekday: "short" }).format(time)
      : new Intl.DateTimeFormat(locale, { ...base, day: "numeric", month: "short" }).format(time);
  return `${date} ${clock}`;
}

/** Date format options from the user's HA profile (hass.locale, hass.config). */
export function timeOptions(hass) {
  const locale = hass?.locale?.language || hass?.language || "en";
  const format = hass?.locale?.time_format;
  const hour12 = format === "12" ? true : format === "24" ? false : undefined;
  const timeZone =
    hass?.locale?.time_zone === "server" ? hass?.config?.time_zone || undefined : undefined;
  return { locale, hour12, timeZone };
}

/** "just now", "5 minutes ago", "yesterday", ... */
export function relativeTime(time, now = Date.now(), locale = "en") {
  const seconds = Math.round((time - now) / 1000);
  if (Math.abs(seconds) < 45) return "just now";
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return format.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return format.format(hours, "hour");
  return format.format(Math.round(hours / 24), "day");
}

/** The first Kubu sensor device (not the "Kubu cloud" device), for new cards. */
export function firstSensorDevice(hass) {
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.platform === DOMAIN && entry.entity_id.startsWith("binary_sensor.")) {
      if (entry.device_id) return entry.device_id;
    }
  }
  return null;
}

// --- The card -----------------------------------------------------------------

const Base = typeof HTMLElement === "undefined" ? class {} : HTMLElement;

const escape = (text) =>
  String(text ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const STYLE = `
  ha-card { padding: 16px; overflow: hidden; }
  ha-card.compact { padding: 8px 12px; }
  .clickable { cursor: pointer; }
  .head { display: flex; align-items: center; gap: 16px; }
  .compact .head { gap: 12px; }
  .badge {
    flex: none; display: flex; align-items: center; justify-content: center;
    width: 64px; height: 64px; border-radius: 50%;
    color: var(--tone); background: color-mix(in srgb, var(--tone) 18%, transparent);
  }
  .badge ha-icon { --mdc-icon-size: 36px; }
  .compact .badge { width: 40px; height: 40px; }
  .compact .badge ha-icon { --mdc-icon-size: 24px; }
  .text { min-width: 0; flex: 1; }
  .name {
    font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .compact .name { font-size: 1em; }
  .status { font-size: 1.25em; font-weight: 500; color: var(--tone); }
  .compact .status { font-size: 0.95em; }
  .status .detail { color: var(--secondary-text-color); font-weight: 400; }
  .since { font-size: 0.85em; color: var(--secondary-text-color); }
  .compact .row { display: flex; align-items: baseline; gap: 8px; }
  .banner {
    display: flex; align-items: center; gap: 8px; margin-top: 12px;
    padding: 8px 12px; border-radius: 8px; color: var(--tone); font-weight: 500;
    background: color-mix(in srgb, var(--tone) 15%, transparent);
  }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .compact .chips { margin-top: 0; flex: none; }
  .chip {
    display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px;
    border-radius: 16px; font-size: 0.85em; color: var(--primary-text-color);
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
  }
  .chip ha-icon { --mdc-icon-size: 16px; }
  .chip.alert { color: ${TONES.alert}; }
  .section { margin-top: 12px; }
  .section-title { font-size: 0.85em; color: var(--secondary-text-color); margin-bottom: 4px; }
  .activity { list-style: none; margin: 0; padding: 0; }
  .activity li {
    display: flex; justify-content: space-between; gap: 8px;
    padding: 3px 0; border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
  }
  .activity li:last-child { border-bottom: none; }
  .activity .when { color: var(--secondary-text-color); font-size: 0.9em; white-space: nowrap; }
  .details { margin-top: 12px; font-size: 0.85em; color: var(--secondary-text-color); }
  .warning { color: var(--error-color, #db4437); }
`;

class KubuBleCard extends Base {
  static getConfigElement() {
    return document.createElement(`${CARD_TYPE}-editor`);
  }

  static getStubConfig(hass) {
    return { device_id: firstSensorDevice(hass) || "" };
  }

  setConfig(config) {
    if (!config || !config.device_id) {
      throw new Error("Choose a Kubu sensor (device_id).");
    }
    this._config = { ...DEFAULTS, ...config };
    this._renderKey = null;
    this._historyKey = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    this._maybeFetchHistory();
  }

  getCardSize() {
    return this._config?.compact ? 1 : 4;
  }

  getGridOptions() {
    return this._config?.compact
      ? { columns: 6, min_columns: 4, rows: 1, min_rows: 1 }
      : { columns: 12, min_columns: 6 };
  }

  connectedCallback() {
    // Keep "5 minutes ago" texts fresh.
    this._timer = setInterval(() => this._render(), 60_000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  _entities() {
    return findEntities(this._hass, this._config.device_id);
  }

  async _maybeFetchHistory() {
    const config = this._config;
    if (!this._hass || !config || config.compact || !config.show_activity) return;
    const ids = activityEntities(this._entities());
    if (!ids.length) return;
    const key = ids.map((id) => this._hass.states[id]?.last_changed).join("|") +
      `|${config.activity_hours}`;
    if (key === this._historyKey) return;
    this._historyKey = key;
    try {
      const start = new Date(Date.now() - config.activity_hours * 3600_000);
      this._history = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        entity_ids: ids,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
        include_start_time_state: false,
      });
      this._historyFailed = false;
    } catch (err) {
      this._history = null;
      this._historyFailed = true;
    }
    this._renderKey = null;
    this._render();
  }

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _format(entityId) {
    const stateObj = this._state(entityId);
    if (!stateObj) return null;
    if (typeof this._hass.formatEntityState === "function") {
      return this._hass.formatEntityState(stateObj);
    }
    const unit = stateObj.attributes?.unit_of_measurement;
    return unit ? `${stateObj.state} ${unit}` : stateObj.state;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const hass = this._hass;
    const config = this._config;
    const entities = this._entities();
    const device = hass.devices?.[config.device_id];
    const name = config.name || device?.name_by_user || device?.name || "Kubu sensor";
    const overall = overallState(hass, entities);
    const main = mainEntity(entities);
    const locale = hass.locale?.language || hass.language || "en";

    // Skip work when nothing shown has changed.
    const shown = Object.values(entities).map((id) => {
      const s = hass.states[id];
      return s ? `${id}=${s.state}@${s.last_changed}` : id;
    });
    const minute = Math.floor(Date.now() / 60_000);
    const key = JSON.stringify([config, name, shown, minute, Boolean(this._history)]);
    if (key === this._renderKey) return;
    this._renderKey = key;

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    }

    // last_changed is reset by every HA restart; the history is not.
    const changed =
      lastRealChange(this._history, entities) ??
      (this._state(main)?.last_changed ? Date.parse(this._state(main).last_changed) : null);
    const since = changed ? `since ${relativeTime(changed, Date.now(), locale)}` : "";
    const status = overall.detail
      ? `${escape(overall.status)}<span class="detail">, ${escape(overall.detail)}</span>`
      : escape(overall.status);
    const badge = `<div class="badge"><ha-icon icon="${overall.icon}"></ha-icon></div>`;

    let body;
    if (config.compact) {
      body = `
        <div class="head clickable" data-entity="${escape(main)}">
          ${badge}
          <div class="text">
            <div class="name">${escape(name)}</div>
            <div class="status">${status}</div>
          </div>
          <div class="chips">${this._batteryChip(entities, true)}</div>
        </div>`;
    } else {
      body = `
        <div class="head clickable" data-entity="${escape(main)}">
          ${badge}
          <div class="text">
            <div class="name">${escape(name)}</div>
            <div class="status">${status}</div>
            <div class="since">${escape(since)}</div>
          </div>
        </div>
        ${config.show_alerts ? this._banner(entities) : ""}
        ${config.show_battery ? this._chips(entities) : ""}
        ${config.show_activity ? this._activity(entities, locale) : ""}
        ${config.show_details ? this._details(entities, device) : ""}`;
    }

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card class="${config.compact ? "compact" : ""}" style="--tone: ${TONES[overall.tone]}">
        ${body}
      </ha-card>`;
  }

  _banner(entities) {
    const on = (role) => this._state(entities[role])?.state === "on";
    let text = null;
    let tone = "alert";
    let icon = "mdi:alert";
    if (on("tamper")) {
      text = "Tamper alert";
      icon = "mdi:shield-alert";
    } else if (on("excessive_force")) {
      text = "Excessive force";
      icon = "mdi:alert-octagon";
    } else if (on("vibration")) {
      text = "Vibration";
      tone = "warn";
      icon = "mdi:vibrate";
    }
    if (!text) return "";
    return `<div class="banner" style="--tone: ${TONES[tone]}">
      <ha-icon icon="${icon}"></ha-icon>${escape(text)}</div>`;
  }

  _batteryChip(entities, onlyIfLow = false) {
    const low = this._state(entities.battery_low)?.state === "on";
    if (onlyIfLow && !low) return "";
    if (!entities.battery_low && !entities.voltage) return "";
    const voltage = this._format(entities.voltage);
    const text = low ? "Battery low" : "Battery OK";
    const entity = entities.battery_low || entities.voltage;
    return `<span class="chip clickable ${low ? "alert" : ""}" data-entity="${escape(entity)}">
      <ha-icon icon="${low ? "mdi:battery-alert-variant-outline" : "mdi:battery"}"></ha-icon>
      ${escape(text)}${voltage && !onlyIfLow ? ` · ${escape(voltage)}` : ""}</span>`;
  }

  _chips(entities) {
    const chips = [this._batteryChip(entities)];
    const signal = this._format(entities.signal);
    if (signal && this._state(entities.signal)?.state !== "unavailable") {
      chips.push(`<span class="chip clickable" data-entity="${escape(entities.signal)}">
        <ha-icon icon="mdi:bluetooth"></ha-icon>${escape(signal)}</span>`);
    }
    const firmware = this._state(entities.firmware)?.state;
    if (firmware && !["unknown", "unavailable"].includes(firmware)) {
      chips.push(`<span class="chip clickable" data-entity="${escape(entities.firmware)}">
        <ha-icon icon="mdi:chip"></ha-icon>Firmware ${escape(firmware)}</span>`);
    }
    const shown = chips.filter(Boolean);
    return shown.length ? `<div class="chips">${shown.join("")}</div>` : "";
  }

  _activity(entities, locale) {
    if (!activityEntities(entities).length) return "";
    const title = `<div class="section-title">Recent activity</div>`;
    if (this._historyFailed) {
      return "";
    }
    if (!this._history) {
      return `<div class="section">${title}<div class="since">Loading…</div></div>`;
    }
    const items = activityItems(this._history, entities);
    if (!items.length) {
      const hours = this._config.activity_hours;
      return `<div class="section">${title}
        <div class="since">Nothing in the last ${escape(hours)} hours</div></div>`;
    }
    const options = timeOptions(this._hass);
    const rows = items
      .map(
        (item) => `<li class="clickable" data-entity="${escape(item.entityId)}">
          <span>${escape(item.label)}</span>
          <span class="when" title="${escape(relativeTime(item.time, Date.now(), locale))}">
            ${escape(formatWhen(item.time, Date.now(), options))}</span></li>`,
      )
      .join("");
    return `<div class="section">${title}<ul class="activity">${rows}</ul></div>`;
  }

  _details(entities, device) {
    const parts = [];
    if (device?.model) parts.push(escape(device.model));
    const sensitivity = this._format(entities.vibration_sensitivity);
    if (sensitivity && this._state(entities.vibration_sensitivity)?.state !== "unknown") {
      parts.push(`Vibration sensitivity: ${escape(sensitivity)}`);
    }
    const battery = this._state(entities.battery_type)?.state;
    if (battery && !["unknown", "unavailable"].includes(battery)) {
      parts.push(`Battery: ${escape(battery)}`);
    }
    return parts.length ? `<div class="details">${parts.join(" · ")}</div>` : "";
  }

  _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset?.entity);
    const entityId = target?.dataset.entity;
    if (!entityId || entityId === "null") return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

// --- The visual editor --------------------------------------------------------

const SCHEMA = [
  {
    name: "device_id",
    required: true,
    selector: {
      device: {
        filter: [{ integration: DOMAIN }],
        entity: [{ domain: "binary_sensor" }],
      },
    },
  },
  { name: "name", selector: { text: {} } },
  { name: "compact", selector: { boolean: {} } },
  { name: "show_battery", selector: { boolean: {} } },
  { name: "show_alerts", selector: { boolean: {} } },
  { name: "show_activity", selector: { boolean: {} } },
  { name: "show_details", selector: { boolean: {} } },
  {
    name: "activity_hours",
    selector: { number: { min: 1, max: 168, mode: "box", unit_of_measurement: "h" } },
  },
];

const LABELS = {
  device_id: "Kubu sensor",
  name: "Name (optional)",
  compact: "Compact (one line)",
  show_battery: "Show battery, signal and firmware",
  show_alerts: "Show tamper and vibration alerts",
  show_activity: "Show recent activity",
  show_details: "Show model and settings",
  activity_hours: "Recent activity: hours to look back",
};

class KubuBleCardEditor extends Base {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => LABELS[schema.name] || schema.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: this._config },
            bubbles: true,
            composed: true,
          }),
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = SCHEMA;
    this._form.data = { ...DEFAULTS, ...this._config };
  }
}

// --- Registration -------------------------------------------------------------

if (typeof customElements !== "undefined") {
  if (!customElements.get(CARD_TYPE)) customElements.define(CARD_TYPE, KubuBleCard);
  if (!customElements.get(`${CARD_TYPE}-editor`)) {
    customElements.define(`${CARD_TYPE}-editor`, KubuBleCardEditor);
  }
  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
    window.customCards.push({
      type: CARD_TYPE,
      name: "Kubu sensor",
      description: "A Kubu door or window sensor: lock and door state, alerts, battery and recent activity.",
      preview: true,
      documentationURL: DOCS_URL,
    });
  }
}
