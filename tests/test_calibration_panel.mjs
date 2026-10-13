/** Real panel-controller regressions without a DOM, HA server or live lights. */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url),
  "utf8",
);
const registered = new Map();
const originalHTMLElement = globalThis.HTMLElement;
const originalCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {};
globalThis.customElements = {
  get: (name) => registered.get(name),
  define: (name, constructor) => registered.set(name, constructor),
};
const { PreviewSession, CalibrationFlow, CircadianCalibrationPanel, selectedEntityValues } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);
if (originalHTMLElement === undefined) delete globalThis.HTMLElement;
else globalThis.HTMLElement = originalHTMLElement;
if (originalCustomElements === undefined) delete globalThis.customElements;
else globalThis.customElements = originalCustomElements;

const API = "circadian_pro/calibration/";
const FLOW = "config/config_entries/options/flow";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function flushMicrotasks() {
  for (let index = 0; index < 8; index++) await Promise.resolve();
}

function sessionFor(t, callWS, getDraft = () => ({ settings: { red: 101 }, kelvin: 4000 }), onChange) {
  const session = new PreviewSession(callWS, "profile-1", getDraft, onChange);
  t.after(() => session.clearTimers());
  return session;
}

test("checking previews immediately without submitting an options flow", async (t) => {
  const messages = [];
  const session = sessionFor(t, async (message) => {
    messages.push(structuredClone(message));
    return { expires_in: 60 };
  });
  const pending = session.setEnabled(true);
  assert.equal(session.enabled, true);
  await flushMicrotasks();
  assert.deepEqual(messages, [{
    type: API + "preview", entry_id: "profile-1", settings: { red: 101 }, kelvin: 4000,
  }]);
  await pending;
  assert.ok(messages.every((message) => !message.type.startsWith("config/")));
});

test("unchecking requests restoration and immediately clears enabled state", async (t) => {
  const messages = [];
  const changes = [];
  let session;
  session = sessionFor(t, async (message) => {
    messages.push(message.type);
    return { expires_in: 60 };
  }, undefined, () => changes.push(session.enabled));
  await session.setEnabled(true);
  const pending = session.setEnabled(false);
  assert.equal(session.enabled, false);
  await pending;
  assert.deepEqual(messages, [API + "preview", API + "stop"]);
  assert.deepEqual(changes, [true, false]);
  assert.equal(session.timer, null);
});

test("unchecking during an in-flight preview leaves stop as the last command", async (t) => {
  const reply = deferred();
  const messages = [];
  const session = sessionFor(t, async (message) => {
    messages.push(message.type);
    if (message.type === API + "preview") return reply.promise;
  });
  const enable = session.setEnabled(true);
  await flushMicrotasks();
  assert.deepEqual(messages, [API + "preview"]);
  const disable = session.setEnabled(false);
  assert.equal(session.enabled, false);
  assert.deepEqual(messages, [API + "preview"]);
  reply.resolve({ expires_in: 60 });
  await Promise.all([enable, disable]);
  assert.deepEqual(messages, [API + "preview", API + "stop"]);
  assert.equal(session.enabled, false);
  assert.equal(session.timer, null);
});

test("a superseded queued preview is skipped and the latest draft is used", async (t) => {
  let draft = { settings: { red: 10 }, kelvin: 4000 };
  const messages = [];
  const session = sessionFor(t, async (message) => {
    messages.push(structuredClone(message));
    return { expires_in: 60 };
  }, () => draft);
  const first = session.setEnabled(true);
  draft = { settings: { red: 99 }, kelvin: 1000 };
  const second = session.setEnabled(true);
  await Promise.all([first, second]);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].settings.red, 99);
  assert.equal(messages[0].kelvin, 1000);
});

test("rapid edits debounce to one update containing the latest values", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let red = 1;
  const messages = [];
  const session = sessionFor(t, async (message) => {
    messages.push(structuredClone(message));
    return { expires_in: 60 };
  }, () => ({ settings: { red }, kelvin: 4000 }));
  await session.setEnabled(true);
  red = 11;
  session.refresh();
  t.mock.timers.tick(100);
  red = 22;
  session.refresh();
  t.mock.timers.tick(100);
  red = 33;
  session.refresh();
  t.mock.timers.tick(179);
  await flushMicrotasks();
  assert.equal(messages.length, 1);
  t.mock.timers.tick(1);
  await session.queue;
  assert.equal(messages.length, 2);
  assert.equal(messages[1].settings.red, 33);
  assert.equal(messages[1].type, API + "preview");
});

test("unchecking cancels a pending debounce so edits cannot reactivate preview", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const messages = [];
  const session = sessionFor(t, async (message) => {
    messages.push(message.type);
    return { expires_in: 60 };
  });
  await session.setEnabled(true);
  session.refresh();
  await session.setEnabled(false);
  t.mock.timers.tick(1000);
  await flushMicrotasks();
  assert.deepEqual(messages, [API + "preview", API + "stop"]);
  assert.equal(session.enabled, false);
});

test("expiry disables the checkbox and requests restoration", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const messages = [];
  const changes = [];
  let session;
  session = sessionFor(t, async (message) => {
    messages.push(message.type);
    return { expires_in: 2 };
  }, undefined, () => changes.push(session.enabled));
  await session.setEnabled(true);
  t.mock.timers.tick(1999);
  assert.equal(session.enabled, true);
  t.mock.timers.tick(1);
  assert.equal(session.enabled, false);
  await session.queue;
  assert.deepEqual(messages, [API + "preview", API + "stop"]);
  assert.deepEqual(changes, [true, false]);
});

test("an update failure stops the preceding preview and leaves the queue reusable", async (t) => {
  const messages = [];
  let fail = false;
  const error = new Error("invalid white mixture");
  const session = sessionFor(t, async (message) => {
    messages.push(message.type);
    if (message.type === API + "preview" && fail) throw error;
    return { expires_in: 60 };
  });
  await session.setEnabled(true);
  fail = true;
  await assert.rejects(session.setEnabled(true), (received) => received === error);
  assert.equal(session.enabled, false);
  assert.deepEqual(messages, [API + "preview", API + "preview", API + "stop"]);
  fail = false;
  await session.setEnabled(true);
  assert.equal(session.enabled, true);
  assert.equal(messages.at(-1), API + "preview");
});

test("a rejected in-flight preview followed by uncheck still finishes with stop", async (t) => {
  const reply = deferred();
  const messages = [];
  const session = sessionFor(t, async (message) => {
    messages.push(message.type);
    if (message.type === API + "preview") return reply.promise;
  });
  const enable = session.setEnabled(true);
  const rejected = assert.rejects(enable, /controller unavailable/);
  await flushMicrotasks();
  const disable = session.setEnabled(false);
  reply.reject(new Error("controller unavailable"));
  await Promise.all([rejected, disable]);
  assert.equal(session.enabled, false);
  assert.deepEqual(messages, [API + "preview", API + "stop"]);
});

class OptionsBackend {
  constructor(events) {
    this.events = events;
    this.nextId = 1;
    this.flows = new Map();
    this.saved = [];
    this.steps = ["init", "white_channels", "cal_neutral", "cal_warm"];
  }

  async callApi(method, path, data) {
    this.events.push({ source: "api", method, path, data: data === undefined ? undefined : structuredClone(data) });
    if (method === "POST" && path === FLOW) {
      const id = `flow-${this.nextId++}`;
      this.flows.set(id, { index: 0, drafts: {} });
      return this.form(id, 0);
    }
    const id = path.slice(FLOW.length + 1);
    const flow = this.flows.get(id);
    assert.ok(flow, `Options flow must exist: ${id}`);
    if (method === "DELETE") {
      this.flows.delete(id);
      return {};
    }
    assert.equal(method, "POST");
    flow.drafts[this.steps[flow.index]] = structuredClone(data);
    if (flow.index === this.steps.length - 1) {
      this.saved.push(structuredClone(flow.drafts));
      this.flows.delete(id);
      return { type: "create_entry", flow_id: id, result: { entry_id: "profile-1" } };
    }
    flow.index++;
    return this.form(id, flow.index);
  }

  form(id, index) {
    return { type: "form", flow_id: id, step_id: this.steps[index], errors: {}, last_step: index === 3 };
  }
}

function flowHarness(stopGate) {
  const events = [];
  const backend = new OptionsBackend(events);
  const preview = {
    async setEnabled(enabled) {
      assert.equal(enabled, false);
      events.push({ source: "stop-start" });
      if (stopGate) await stopGate.promise;
      events.push({ source: "stop-end" });
    },
  };
  const flow = new CalibrationFlow(backend.callApi.bind(backend), preview, "profile-1");
  return { events, backend, flow };
}

test("Next waits for restoration before posting the draft to the flow", async () => {
  const gate = deferred();
  const { events, backend, flow } = flowHarness(gate);
  await flow.start();
  events.length = 0;
  const next = flow.next({ lights: ["light.one"] });
  await flushMicrotasks();
  assert.deepEqual(events, [{ source: "stop-start" }]);
  gate.resolve();
  const result = await next;
  assert.equal(result.step_id, "white_channels");
  assert.deepEqual(events.map((event) => event.source), ["stop-start", "stop-end", "api"]);
  assert.equal(events[2].method, "POST");
  assert.equal(events[2].path, FLOW + "/flow-1");
  assert.deepEqual(flow.drafts.init, { lights: ["light.one"] });
  assert.equal(backend.saved.length, 0);
});

test("Back stops, restarts and replays only prior steps while retaining drafts", async () => {
  const { events, backend, flow } = flowHarness();
  await flow.start();
  const general = { lights: ["light.one"], hidden_option: 17 };
  const physical = { white_w_kelvin: 3000, white_cw_kelvin: 6500 };
  const editedNeutral = { cal_rgb_neutral_r: 199, cal_white_kelvin_neutral: 4500 };
  await flow.next(general);
  await flow.next(physical);
  assert.equal(flow.index, 2);
  events.length = 0;
  const result = await flow.back(editedNeutral);
  assert.equal(result.step_id, "white_channels");
  assert.equal(flow.index, 1);
  assert.deepEqual(events.map((event) => event.source), ["stop-start", "stop-end", "api", "api", "api"]);
  assert.deepEqual(events.slice(2).map((event) => [event.method, event.path]), [
    ["DELETE", FLOW + "/flow-1"], ["POST", FLOW], ["POST", FLOW + "/flow-2"],
  ]);
  assert.deepEqual(events[4].data, general);
  assert.deepEqual(flow.drafts.cal_neutral, editedNeutral);
  assert.deepEqual(flow.drafts.white_channels, physical);
  assert.equal(backend.saved.length, 0);
  await flow.next({ ...physical, white_cw_kelvin: 7000 });
  assert.equal(flow.current.step_id, "cal_neutral");
  assert.deepEqual(flow.drafts.cal_neutral, editedNeutral);
  assert.equal(backend.saved.length, 0);
});

test("Back from the physical step reaches general without submitting a final step", async () => {
  const { events, backend, flow } = flowHarness();
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  events.length = 0;
  const physical = { white_w_kelvin: 3100, white_cw_kelvin: 6600 };
  const result = await flow.back(physical);
  assert.equal(result.step_id, "init");
  assert.equal(flow.index, 0);
  assert.deepEqual(flow.drafts.white_channels, physical);
  assert.deepEqual(events.filter((event) => event.source === "api").map((event) => event.method), ["DELETE", "POST"]);
  assert.equal(backend.saved.length, 0);
});

test("Close restores before deleting the unsaved flow", async () => {
  const { events, backend, flow } = flowHarness();
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  events.length = 0;
  await flow.close();
  assert.deepEqual(events.map((event) => event.source), ["stop-start", "stop-end", "api"]);
  assert.equal(events[2].method, "DELETE");
  assert.equal(flow.current, null);
  assert.equal(backend.flows.size, 0);
  assert.equal(backend.saved.length, 0);
});

test("only the final Next creates an entry; closing afterward does not delete it", async () => {
  const { events, backend, flow } = flowHarness();
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  assert.equal(backend.saved.length, 0);
  await flow.next({ white_w_kelvin: 3000, white_cw_kelvin: 6500 });
  assert.equal(backend.saved.length, 0);
  await flow.next({ cal_white_kelvin_neutral: 4500, cal_rgb_neutral_r: 100 });
  assert.equal(backend.saved.length, 0);
  assert.equal(flow.current.step_id, "cal_warm");
  events.length = 0;
  const result = await flow.next({ cal_rgb_warm_r: 200 });
  assert.equal(result.type, "create_entry");
  assert.equal(backend.saved.length, 1);
  assert.deepEqual(events.map((event) => event.source), ["stop-start", "stop-end", "api"]);
  assert.equal(events[2].method, "POST");
  assert.equal(backend.saved[0].cal_warm.cal_rgb_warm_r, 200);
  events.length = 0;
  await flow.close();
  assert.deepEqual(events.map((event) => event.source), ["stop-start", "stop-end"]);
  assert.equal(backend.saved.length, 1);
});

test("failed restoration prevents advancing or saving", async () => {
  const { events, backend, flow } = flowHarness();
  await flow.start();
  flow.preview.setEnabled = async () => { throw new Error("restore unavailable"); };
  events.length = 0;
  await assert.rejects(flow.next({ lights: ["light.one"] }), /restore unavailable/);
  assert.deepEqual(events, []);
  assert.equal(flow.current.step_id, "init");
  assert.equal(backend.saved.length, 0);
});

test("compact entity values remain an array for multiple, single and empty selections", () => {
  const choices = [
    { value: "light.one", checked: true },
    { value: "light.two", checked: true },
  ];
  const picker = {
    dataset: { kind: "entity", field: "lights", required: "false" },
    querySelectorAll(selector) {
      assert.equal(selector, "input[data-entity-choice]:checked");
      return choices.filter((choice) => choice.checked);
    },
  };
  const form = { querySelectorAll: () => [picker], reportValidity: () => true };
  const panel = { values: { lights: [] }, copy: { invalid: "Invalid" }, shadowRoot: { querySelector: () => form } };
  assert.deepEqual(selectedEntityValues(picker), ["light.one", "light.two"]);
  assert.deepEqual(CircadianCalibrationPanel.prototype.readValues.call(panel).lights, ["light.one", "light.two"]);
  choices[0].checked = false;
  assert.deepEqual(CircadianCalibrationPanel.prototype.readValues.call(panel).lights, ["light.two"]);
  choices[1].checked = false;
  assert.deepEqual(CircadianCalibrationPanel.prototype.readValues.call(panel).lights, []);
});

test("an empty required entity selection opens and focuses the chooser instead of advancing", () => {
  const attributes = {};
  let focused = false;
  const summary = { setAttribute: (key, value) => { attributes[key] = value; }, focus: () => { focused = true; } };
  const picker = {
    dataset: { kind: "entity", field: "lights", required: "true" },
    querySelectorAll: () => [], querySelector: () => summary, open: false,
  };
  const form = { querySelectorAll: () => [picker], reportValidity: () => true };
  const panel = { values: { lights: [] }, copy: { invalid: "Choose a light" }, shadowRoot: { querySelector: () => form } };
  assert.throws(() => CircadianCalibrationPanel.prototype.readValues.call(panel), /Choose a light/);
  assert.equal(picker.open, true);
  assert.equal(focused, true);
  assert.equal(attributes["aria-invalid"], "true");
});

test("compact chooser shows friendly selections and closes after adding another light", (t) => {
  class Node {
    constructor(tag) {
      this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
      this.listeners = {}; this.textContent = ""; this.open = false;
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    focus() { this.focused = true; }
    descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
    querySelector(selector) { return this.descendants().find((node) => node.tag === selector); }
    querySelectorAll(selector) {
      assert.equal(selector, "input[data-entity-choice]:checked");
      return this.descendants().filter((node) => node.tag === "input" && Object.hasOwn(node.dataset, "entityChoice") && node.checked);
    }
  }
  const originalDocument = globalThis.document;
  globalThis.document = { createElement: (tag) => new Node(tag) };
  t.after(() => {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  });
  let refreshes = 0;
  let picker;
  const panel = {
    flow: { current: { step_id: "init" } },
    values: { lights: ["light.one"] }, settings: {},
    translations: { step: { init: { data: { lights: "Luces" } } } },
    copy: { selectEntities: "Seleccionar luces", missing: "No disponible" },
    _hass: { states: {
      "light.one": { attributes: { friendly_name: "Light A" } },
      "light.two": { attributes: { friendly_name: "Light B" } },
      "light.three": { attributes: { friendly_name: "Light C" } },
    } },
    readValues: () => ({ lights: selectedEntityValues(picker) }),
    session: { refresh: () => { refreshes++; } },
  };
  const wrapper = CircadianCalibrationPanel.prototype.field.call(panel, {
    name: "lights", selector: { entity: { domain: "light", multiple: true } },
  });
  picker = wrapper.querySelector("details");
  const summary = picker.querySelector("summary");
  const selection = summary.children[0];
  assert.equal(wrapper.tag, "div");
  assert.equal(picker.open, false);
  assert.deepEqual(selection.children.map((node) => node.textContent), ["Light A"]);
  assert.match(summary.attributes["aria-labelledby"], /field-lights-label/);
  const checkboxes = picker.descendants().filter((node) => node.tag === "input");
  picker.open = true;
  checkboxes[1].checked = true;
  picker.listeners.input(); // Native checkbox input bubbles to the chooser.
  checkboxes[1].listeners.change();
  assert.equal(picker.open, false);
  assert.equal(summary.focused, true);
  assert.deepEqual(panel.values.lights, ["light.one", "light.two"]);
  assert.deepEqual(selection.children.map((node) => node.textContent), ["Light A", "Light B"]);
  assert.equal(refreshes, 1);
  checkboxes[0].checked = false;
  picker.listeners.input();
  checkboxes[0].listeners.change();
  assert.deepEqual(selection.children.map((node) => node.textContent), ["Light B"]);
  assert.deepEqual(panel.values.lights, ["light.two"]);
});
