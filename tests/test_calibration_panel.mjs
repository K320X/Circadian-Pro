/** Real panel-controller regressions without a DOM, HA server or live lights. */

import assert from "node:assert/strict";
import { mkdtemp, readFile, rmdir, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

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
const { PreviewSession, CalibrationFlow, NewProfileFlow, EntryCatalog, waitForLoadedEntry, CircadianCalibrationPanel, selectedEntityValues } = await import(
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
    type: API + "preview", entry_id: "profile-1", session_id: session.sessionId, settings: { red: 101 }, kelvin: 4000,
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

const CONFIG_FLOW = "config/config_entries/flow";

class ProfileBackend {
  constructor({ menu = true, loseReply = false, bootstrap = false } = {}) {
    this.menu = menu;
    this.loseReply = loseReply;
    this.bootstrap = bootstrap;
    this.calls = [];
    this.profiles = [];
    this.configActive = false;
    this.optionsSaved = 0;
  }
  async callApi(method, path, data) {
    this.calls.push({ method, path, data: data === undefined ? undefined : structuredClone(data) });
    if (path === CONFIG_FLOW) {
      assert.deepEqual(data, { handler: "circadian_pro", show_advanced_options: false, source: "user" });
      if (this.bootstrap) {
        this.bootstrap = false;
        return { type: "create_entry", result: { entry_id: "panel-anchor", title: "Circadian Pro" } };
      }
      this.configActive = true;
      return { type: "form", flow_id: "server-config-flow", step_id: this.menu ? "menu" : "user" };
    }
    if (path === CONFIG_FLOW + "/server-config-flow") {
      if (method === "DELETE") { this.configActive = false; return {}; }
      if (method === "GET") {
        if (!this.configActive) throw new Error("Flow does not exist");
        return { type: "form", flow_id: "server-config-flow", step_id: "user" };
      }
      assert.equal(method, "POST");
      if (data.action) {
        assert.deepEqual(data, { action: "new" });
        return { type: "form", flow_id: "server-config-flow", step_id: "user" };
      }
      assert.deepEqual(Object.keys(data), ["name"]);
      if (data.name === "Taken") return { type: "form", flow_id: "server-config-flow", step_id: "user", errors: { name: "unique_name" } };
      const profile = { entry_id: "server-returned-entry-id", title: data.name, settings: { lights: [], white_w_kelvin: 3000 } };
      this.profiles.push(profile);
      this.configActive = false;
      if (this.loseReply) throw new Error("Connection lost after creation");
      return { type: "create_entry", result: { entry_id: profile.entry_id, title: profile.title } };
    }
    if (path === FLOW) {
      assert.ok(this.profiles.some((profile) => profile.entry_id === data.handler));
      return { type: "form", flow_id: "server-options-flow", step_id: "init", data_schema: [{ name: "lights", default: [], selector: { entity: { multiple: true } } }] };
    }
    if (path === FLOW + "/server-options-flow" && method === "DELETE") return {};
    throw new Error(`Unexpected request: ${method} ${path}`);
  }
  async callWS(message) {
    this.calls.push({ type: message.type, entry_id: message.entry_id, include_status: message.include_status });
    if (message.type === API + "entries") {
      const entries = structuredClone(this.profiles);
      return message.include_status ? { entries, configured_profiles: this.profiles.length } : entries;
    }
    if (message.type === API + "stop") return { active: false };
    throw new Error(`Unexpected WebSocket request: ${message.type}`);
  }
}

test("opening and cancelling a local new-profile form creates no HA flow or profile", async () => {
  const backend = new ProfileBackend();
  const panel = { _hass: { callApi: backend.callApi.bind(backend) }, render: () => {} };
  CircadianCalibrationPanel.prototype.openNewProfile.call(panel);
  assert.equal(panel.newProfileOpen, true);
  assert.equal(panel.newProfileName, "");
  assert.deepEqual(backend.calls, []);
  await panel.creator.close();
  assert.deepEqual(backend.calls, []);
  assert.equal(backend.profiles.length, 0);
});

test("Create uses the standard config flow, resolves the new action and trusts the returned ID", async () => {
  const backend = new ProfileBackend();
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  const result = await creation.create("  Office  ");
  assert.equal(result.type, "create_entry");
  assert.equal(creation.createdEntryId, "server-returned-entry-id");
  assert.equal(creation.name, "Office");
  assert.deepEqual(backend.calls.map((call) => [call.method, call.path]), [
    ["POST", CONFIG_FLOW], ["POST", CONFIG_FLOW + "/server-config-flow"], ["POST", CONFIG_FLOW + "/server-config-flow"],
  ]);
  assert.deepEqual(backend.calls[1].data, { action: "new" });
  assert.deepEqual(backend.calls[2].data, { name: "Office" });
  assert.deepEqual(backend.profiles[0].settings.lights, []);
  assert.equal(backend.optionsSaved, 0);
  assert.ok(backend.calls.every((call) => !call.path.startsWith(FLOW)));
});

test("a first profile skips the menu; blank names make no requests", async () => {
  const backend = new ProfileBackend({ menu: false });
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  await assert.rejects(creation.create("   "), (error) => error.reason === "name_required");
  assert.deepEqual(backend.calls, []);
  await creation.create("Office");
  assert.equal(backend.calls.length, 2);
  assert.deepEqual(backend.calls[1].data, { name: "Office" });
});

test("panel activation opens a fresh name form and never counts as the requested first profile", async () => {
  const backend = new ProfileBackend({ menu: false, bootstrap: true });
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  await creation.create("Office");
  assert.equal(creation.createdEntryId, "server-returned-entry-id");
  assert.deepEqual(backend.calls.map((call) => [call.method, call.path]), [
    ["POST", CONFIG_FLOW], ["POST", CONFIG_FLOW], ["POST", CONFIG_FLOW + "/server-config-flow"],
  ]);
  assert.deepEqual(backend.calls.at(-1).data, { name: "Office" });
  assert.equal(backend.profiles.length, 1);
});

test("closing while panel activation completes starts no name flow and adopts no anchor as a profile", async () => {
  const response = deferred();
  const calls = [];
  const creation = new NewProfileFlow(async (...args) => { calls.push(args); return response.promise; });
  const pending = creation.create("Office");
  await creation.close();
  response.resolve({ type: "create_entry", result: { entry_id: "panel-anchor" } });
  await assert.rejects(pending, (error) => error.reason === "flow_closed");
  assert.equal(creation.createdEntryId, null);
  assert.equal(creation.current, null);
  assert.equal(calls.length, 1);
});

test("unexpected repeated activation is bounded and never reported as profile creation", async () => {
  let calls = 0;
  const creation = new NewProfileFlow(async () => {
    calls++;
    return { type: "create_entry", result: { entry_id: "panel-anchor" } };
  });
  await assert.rejects(creation.create("Office"), (error) => error.reason === "profile_form_unavailable");
  assert.equal(calls, 2);
  assert.equal(creation.createdEntryId, null);
  assert.equal(creation.current, null);
});

test("losing a real name submission after activation never automatically repeats the profile write", async () => {
  const backend = new ProfileBackend({ menu: false, bootstrap: true, loseReply: true });
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  await assert.rejects(creation.create("Office"), (error) => error.reason === "creation_uncertain");
  await assert.rejects(creation.create("Office"), (error) => error.reason === "creation_uncertain");
  assert.equal(backend.calls.filter((call) => call.data?.name === "Office").length, 1);
  assert.equal(backend.profiles.length, 1);
  assert.equal(creation.createdEntryId, null);
});

test("duplicate-name errors preserve the name and reuse the unsaved name form", async () => {
  const backend = new ProfileBackend();
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  const result = await creation.create("Taken");
  assert.deepEqual(result.errors, { name: "unique_name" });
  assert.equal(creation.name, "Taken");
  assert.equal(creation.createdEntryId, null);
  assert.equal(backend.profiles.length, 0);
  await creation.create("Another office");
  assert.equal(backend.calls.filter((call) => call.path === CONFIG_FLOW).length, 1);
  assert.deepEqual(backend.calls.at(-1).data, { name: "Another office" });
});

test("cancelling a rejected name aborts only its config flow; closing a created profile never deletes it", async () => {
  const backend = new ProfileBackend();
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  await creation.create("Taken");
  await creation.close();
  assert.equal(backend.calls.at(-1).method, "DELETE");
  assert.equal(backend.calls.at(-1).path, CONFIG_FLOW + "/server-config-flow");
  assert.equal(backend.profiles.length, 0);
  const created = new NewProfileFlow(backend.callApi.bind(backend));
  await created.create("Office");
  const callCount = backend.calls.length;
  await created.close();
  assert.equal(backend.calls.length, callCount);
  assert.equal(backend.profiles.length, 1);
  await assert.rejects(created.create("Office"), /already been created/);
  assert.equal(backend.calls.length, callCount);
});

test("a lost create reply prevents another POST and reconciles via GET without adopting a profile", async () => {
  const backend = new ProfileBackend({ loseReply: true });
  const creation = new NewProfileFlow(backend.callApi.bind(backend));
  await assert.rejects(creation.create("Office"), (error) => error.reason === "creation_uncertain");
  assert.equal(creation.uncertain, true);
  assert.equal(creation.name, "Office");
  assert.equal(backend.profiles.length, 1);
  const callCount = backend.calls.length;
  await assert.rejects(creation.create("Office"), (error) => error.reason === "creation_uncertain");
  assert.equal(backend.calls.length, callCount);
  const recovered = await creation.recover();
  assert.equal(recovered, null);
  assert.equal(backend.calls.at(-1).method, "GET");
  assert.equal(creation.createdEntryId, null);
  assert.equal(backend.profiles.length, 1);
});

test("read recovery of a still-active name form permits only a later explicit create", async () => {
  const backend = new ProfileBackend();
  let disconnect = true;
  const creation = new NewProfileFlow(async (...args) => {
    if (args[1].endsWith("/server-config-flow") && args[2]?.name && disconnect) {
      disconnect = false;
      throw new Error("Disconnected before saving");
    }
    return backend.callApi(...args);
  });
  await assert.rejects(creation.create("Office"));
  assert.equal(backend.profiles.length, 0);
  const recovered = await creation.recover();
  assert.equal(recovered.step_id, "user");
  assert.equal(creation.uncertain, false);
  assert.equal(backend.calls.at(-1).method, "GET");
  assert.equal(backend.profiles.length, 0);
  await creation.create("Office");
  assert.equal(backend.profiles.length, 1);
});

test("recovering HA's menu reuses its existing flow; abandoning an unsupported result cleans up first", async () => {
  const backend = new ProfileBackend();
  let loseNameRequest = true;
  const creation = new NewProfileFlow(async (...args) => {
    if (args[0] === "GET") {
      backend.calls.push({ method: "GET", path: args[1] });
      return { type: "form", flow_id: "server-config-flow", step_id: "menu" };
    }
    if (args[2]?.name && loseNameRequest) {
      loseNameRequest = false;
      throw new Error("Lost name request");
    }
    return backend.callApi(...args);
  });
  await assert.rejects(creation.create("Office"));
  const recovered = await creation.recover();
  assert.equal(recovered.step_id, "menu");
  assert.equal(creation.uncertain, false);
  const beforeRetry = backend.calls.length;
  await creation.create("Office");
  assert.deepEqual(backend.calls.slice(beforeRetry).map((call) => [call.method, call.path, call.data]), [
    ["POST", CONFIG_FLOW + "/server-config-flow", { action: "new" }],
    ["POST", CONFIG_FLOW + "/server-config-flow", { name: "Office" }],
  ]);
  assert.equal(backend.calls.filter((call) => call.path === CONFIG_FLOW).length, 1);
  assert.equal(backend.profiles.length, 1);

  const events = [];
  const panel = {
    _hass: { callWS: async () => { events.push("entries"); return []; } },
    creator: {
      recover: async () => { events.push("recover"); return null; },
      close: async () => { assert.equal(panel.newProfileOpen, true); events.push("close-flow"); },
    },
    action: async (job) => job(), newProfileOpen: true, copy: { uncertainList: "Check existing profiles" },
    readEntries: async () => { events.push("entries"); return []; },
    applyEntries: () => {},
  };
  await CircadianCalibrationPanel.prototype.reconcileCreation.call(panel);
  assert.deepEqual(events, ["entries", "recover", "close-flow"]);
  assert.equal(panel.newProfileOpen, false);
  assert.equal(panel.message, "Check existing profiles");
});

test("loaded-entry polling is serial, bounded and does not claim readiness from creation alone", async () => {
  const delays = [];
  let reads = 0;
  let active = false;
  const result = await waitForLoadedEntry(async (message) => {
    assert.equal(message.type, API + "entries");
    assert.equal(active, false);
    active = true;
    await Promise.resolve();
    active = false;
    reads++;
    return [];
  }, "server-returned-entry-id", { wait: async (delay) => { delays.push(delay); } });
  assert.equal(result, null);
  assert.equal(reads, 8);
  assert.deepEqual(delays, [250, 500, 1000, 2000, 3000, 4000, 4000]);
  assert.equal(delays.reduce((total, delay) => total + delay, 0), 14750);
});

test("retrying load reads the same known ID without recreating; cancellation stops further polling", async () => {
  const entry = { entry_id: "server-returned-entry-id", title: "Office", settings: { lights: [] } };
  let ready = false;
  let reads = 0;
  const callWS = async () => { reads++; return ready ? [entry] : []; };
  assert.equal(await waitForLoadedEntry(callWS, entry.entry_id, { delays: [0] }), null);
  ready = true;
  assert.equal(await waitForLoadedEntry(callWS, entry.entry_id, { delays: [0] }), entry);
  assert.equal(reads, 2);
  let cancelled = false;
  ready = false;
  assert.equal(await waitForLoadedEntry(callWS, entry.entry_id, {
    delays: [0, 250], wait: async () => { cancelled = true; }, cancelled: () => cancelled,
  }), null);
  assert.equal(reads, 3);
});

test("creating in the panel opens the options wizard only after loaded confirmation; cancelling keeps the profile", async () => {
  const backend = new ProfileBackend({ menu: false, bootstrap: true });
  const panel = {
    _hass: { callApi: backend.callApi.bind(backend), callWS: backend.callWS.bind(backend) },
    creator: new NewProfileFlow(backend.callApi.bind(backend)),
    newProfileName: "Office", newProfileOpen: true, creationRevision: 0, entries: [], translations: {},
    copy: { created: "Profile created", createdKept: "Created profile kept", notLoaded: "Retry loading", error: "Error" },
    updateControls: () => {}, render: () => {}, readValues: () => ({}),
  };
  for (const name of ["action", "createNewProfile", "loadCreatedEntry", "openEntry", "showStep", "closeWizard", "readEntries", "applyEntries"]) {
    panel[name] = CircadianCalibrationPanel.prototype[name].bind(panel);
  }
  await panel.createNewProfile();
  assert.equal(panel.error, "");
  assert.equal(panel.newProfileOpen, false);
  assert.equal(panel.catalogLoaded, true);
  assert.equal(panel.configuredProfiles, 1, "creation polling keeps status metadata while returning an array to its caller");
  assert.equal(panel.flow.current.step_id, "init");
  assert.deepEqual(panel.values.lights, []);
  assert.equal(panel.createdProfile.entry_id, "server-returned-entry-id");
  const loadedRead = backend.calls.findIndex((call) => call.type === API + "entries");
  const optionsStart = backend.calls.findIndex((call) => call.path === FLOW);
  assert.ok(loadedRead >= 0 && optionsStart > loadedRead);
  assert.equal(backend.optionsSaved, 0);
  await panel.closeWizard();
  assert.equal(panel.flow, null);
  assert.equal(panel.message, "Created profile kept");
  assert.equal(backend.profiles.length, 1);
  assert.equal(backend.calls.at(-1).path, FLOW + "/server-options-flow");
  assert.equal(backend.calls.at(-1).method, "DELETE");
  assert.ok(backend.calls.every((call) => !call.path?.startsWith("config/config_entries/entry/")));
});

function metadataConnection() {
  const state = { callback: null, unsubscribes: 0, subscriptions: 0, readyListeners: new Set(), disconnectedListeners: new Set() };
  state.connection = {
    addEventListener(event, callback) {
      assert.ok(["ready", "disconnected"].includes(event));
      (event === "ready" ? state.readyListeners : state.disconnectedListeners).add(callback);
    },
    removeEventListener(event, callback) {
      assert.ok(["ready", "disconnected"].includes(event));
      (event === "ready" ? state.readyListeners : state.disconnectedListeners).delete(callback);
    },
    async subscribeMessage(callback, request, options) {
      assert.deepEqual(request, { type: "config_entries/subscribe" });
      assert.deepEqual(options, { resubscribe: false });
      state.subscriptions++;
      state.callback = callback;
      callback([]); // HA's initial snapshot is separate from the initial read.
      return async () => { state.unsubscribes++; };
    },
  };
  state.ready = () => { for (const callback of state.readyListeners) callback(); };
  state.disconnect = () => { for (const callback of state.disconnectedListeners) callback(); };
  return state;
}

test("native metadata subscription filters other domains and coalesces changes without polling", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  let reads = 0;
  const catalog = new EntryCatalog(async () => { reads++; return [{ entry_id: "one", title: "Office" }]; }, () => {});
  t.after(() => catalog.stop());
  assert.equal(await catalog.connect(metadata.connection), true);
  assert.equal(catalog.subscribed, true);
  await catalog.read();
  metadata.callback([{ type: "updated", entry: { domain: "hue" } }]);
  t.mock.timers.tick(1000);
  await flushMicrotasks();
  assert.equal(reads, 1);
  for (let index = 0; index < 30; index++) metadata.callback([{ type: "updated", entry: { domain: "circadian_pro", entry_id: "one" } }]);
  t.mock.timers.tick(149);
  assert.equal(reads, 1);
  t.mock.timers.tick(1);
  await catalog.inFlight;
  assert.equal(reads, 2);
  t.mock.timers.tick(60000);
  await flushMicrotasks();
  assert.equal(reads, 2);
});

test("shared in-flight reads and a burst during a request cause one serial follow-up", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const first = deferred();
  const metadata = metadataConnection();
  let reads = 0;
  let activeReads = 0;
  let peakReads = 0;
  const catalog = new EntryCatalog(async () => {
    reads++; activeReads++; peakReads = Math.max(peakReads, activeReads);
    const result = reads === 1 ? await first.promise : [];
    activeReads--;
    return result;
  }, () => {});
  t.after(() => catalog.stop());
  await catalog.connect(metadata.connection);
  const pending = catalog.read();
  assert.equal(catalog.read(), pending);
  await flushMicrotasks();
  for (let index = 0; index < 20; index++) metadata.callback([{ type: "updated", entry: { domain: "circadian_pro" } }]);
  t.mock.timers.tick(1000);
  assert.equal(reads, 1);
  first.resolve([]);
  await pending;
  t.mock.timers.tick(149);
  assert.equal(reads, 1);
  t.mock.timers.tick(1);
  await catalog.inFlight;
  assert.equal(reads, 2);
  assert.equal(peakReads, 1);
});

test("reconnect snapshots refresh metadata even when the snapshot contains no profiles", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  let reads = 0;
  let displayed = [{ entry_id: "one" }];
  const catalog = new EntryCatalog(async () => { reads++; return []; }, (entries) => { displayed = entries; }, displayed);
  t.after(() => catalog.stop());
  await catalog.connect(metadata.connection);
  assert.equal(reads, 0);
  metadata.callback([]); // A second snapshot follows an automatic reconnect.
  t.mock.timers.tick(150);
  await catalog.inFlight;
  assert.equal(reads, 1);
  assert.deepEqual(displayed, []);
});

test("temporary refresh errors preserve the last good catalog", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  const good = [{ entry_id: "one", title: "Office" }];
  const errors = [];
  let fail = false;
  let displayed;
  const catalog = new EntryCatalog(async () => {
    if (fail) throw new Error("Temporary connection failure");
    return good;
  }, (entries) => { displayed = entries; }, [], (error) => errors.push(error));
  t.after(() => catalog.stop());
  await catalog.connect(metadata.connection);
  await catalog.read();
  fail = true;
  metadata.callback([{ type: "updated", entry: { domain: "circadian_pro" } }]);
  t.mock.timers.tick(150);
  await flushMicrotasks();
  assert.equal(displayed, good);
  assert.equal(catalog.entries, good);
  assert.equal(errors.length, 1);
});

test("disconnect cancels debounce, unsubscribes and ignores late callbacks and responses", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  const response = deferred();
  const good = [{ entry_id: "last-good" }];
  const applied = [];
  let reads = 0;
  const catalog = new EntryCatalog(() => { reads++; return response.promise; }, (entries) => applied.push(entries), good);
  await catalog.connect(metadata.connection);
  const pending = catalog.read();
  await flushMicrotasks();
  metadata.callback([{ type: "updated", entry: { domain: "circadian_pro" } }]);
  catalog.stop();
  metadata.callback([{ type: "added", entry: { domain: "circadian_pro" } }]);
  response.resolve([{ entry_id: "stale" }]);
  assert.equal(await pending, good);
  t.mock.timers.tick(1000);
  await flushMicrotasks();
  assert.equal(metadata.unsubscribes, 1);
  assert.equal(metadata.readyListeners.size, 0);
  assert.equal(catalog.subscribed, false);
  assert.equal(reads, 1);
  assert.deepEqual(applied, []);
});

test("a subscription that resolves after detachment is immediately unsubscribed", async () => {
  const reply = deferred();
  let callback;
  let unsubscribes = 0;
  const catalog = new EntryCatalog(async () => { throw new Error("Unexpected fetch"); }, () => {});
  const connecting = catalog.connect({ subscribeMessage: (handler) => { callback = handler; return reply.promise; } });
  catalog.stop();
  callback([{ type: "updated", entry: { domain: "circadian_pro" } }]);
  reply.resolve(async () => { unsubscribes++; });
  assert.equal(await connecting, false);
  assert.equal(unsubscribes, 1);
  assert.equal(catalog.subscribed, false);
});

test("catalog updates preserve the chosen profile and never replace or save edited drafts", () => {
  let renders = 0;
  const values = { lights: ["light.draft"], interval: 99 };
  const settings = { lights: ["light.saved"], interval: 60 };
  const flow = { current: { step_id: "init" }, drafts: { init: { interval: 99 } } };
  const panel = { selectedEntryId: "chosen", selectedEntryTitle: "Old name", values, settings, flow, render: () => { renders++; }, updateControls: () => {} };
  CircadianCalibrationPanel.prototype.applyEntries.call(panel, [{ entry_id: "other" }, { entry_id: "chosen", title: "Renamed" }]);
  assert.equal(panel.selectedEntryId, "chosen");
  assert.equal(panel.selectedEntryTitle, "Renamed");
  assert.equal(panel.values, values);
  assert.equal(panel.settings, settings);
  assert.equal(panel.flow, flow);
  assert.equal(renders, 0);
  panel.flow = null;
  CircadianCalibrationPanel.prototype.applyEntries.call(panel, [{ entry_id: "other" }]);
  assert.equal(panel.selectedEntryId, "chosen");
  assert.equal(renders, 1);
});

test("ordinary hass property updates do not re-read the profile registry", () => {
  let initializations = 0;
  const panel = { initialized: true, isConnected: true, language: "en", initialize: () => { initializations++; } };
  const setter = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "hass").set;
  for (let index = 0; index < 50; index++) setter.call(panel, { states: { changing: index } });
  assert.equal(initializations, 0);
});

test("unavailable native subscription remains a recoverable manual-refresh mode", async () => {
  let reads = 0;
  const catalog = new EntryCatalog(async () => { reads++; return []; }, () => {});
  assert.equal(await catalog.connect({ subscribeMessage: async () => { throw new Error("Unsupported command"); } }), false);
  assert.equal(catalog.subscribed, false);
  await catalog.read();
  assert.equal(reads, 1);
  catalog.stop();
});

test("ready reconnects with a single explicit subscription and refreshes the catalog", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  let reads = 0;
  const catalog = new EntryCatalog(async () => { reads++; return []; }, () => {});
  t.after(() => catalog.stop());
  await catalog.connect(metadata.connection);
  await catalog.read();
  const oldCallback = metadata.callback;
  metadata.ready();
  await flushMicrotasks();
  assert.equal(metadata.subscriptions, 2);
  assert.equal(metadata.unsubscribes, 0);
  assert.equal(catalog.subscribed, true);
  t.mock.timers.tick(150);
  await catalog.inFlight;
  assert.equal(reads, 2);
  oldCallback([{ type: "updated", entry: { domain: "circadian_pro" } }]);
  t.mock.timers.tick(500);
  await flushMicrotasks();
  assert.equal(reads, 2);
});

test("leaving while offline removes ready listeners and cannot reopen a subscription", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  const autoResubscriptions = [];
  const subscribe = metadata.connection.subscribeMessage;
  metadata.connection.subscribeMessage = async (callback, request, options) => {
    if (options?.resubscribe !== false) autoResubscriptions.push(callback);
    return subscribe(callback, request, options);
  };
  let reads = 0;
  const catalog = new EntryCatalog(async () => { reads++; return []; }, () => {});
  await catalog.connect(metadata.connection);
  catalog.stop(); // Unsubscribe can occur while HA's socket is disconnected.
  await flushMicrotasks();
  metadata.ready();
  for (const callback of autoResubscriptions) callback([]);
  t.mock.timers.tick(1000);
  await flushMicrotasks();
  assert.equal(metadata.readyListeners.size, 0);
  assert.equal(metadata.disconnectedListeners.size, 0);
  assert.equal(autoResubscriptions.length, 0);
  assert.equal(metadata.subscriptions, 1);
  assert.equal(metadata.unsubscribes, 1);
  assert.equal(reads, 0);
});

test("catalog refresh failure is visible without replacing draft inputs and clears after recovery", () => {
  const warningText = { textContent: "" };
  const warning = { hidden: true, querySelector: () => warningText };
  const draft = { lights: ["light.unsaved"] };
  const panel = {
    copy: { catalogError: "Could not refresh profiles" },
    shadowRoot: { querySelector: (selector) => selector === "#catalog-warning" ? warning : null, querySelectorAll: () => [] },
    flow: { current: { step_id: "init" } }, values: draft, settings: draft,
    entries: [{ entry_id: "chosen" }], selectedEntryId: "chosen",
    updateControls() { CircadianCalibrationPanel.prototype.updateControls.call(this); },
    render: () => { throw new Error("Do not rebuild draft inputs"); },
  };
  CircadianCalibrationPanel.prototype.catalogFailure.call(panel);
  assert.equal(warning.hidden, false);
  assert.equal(warningText.textContent, "Could not refresh profiles");
  assert.equal(panel.values, draft);
  CircadianCalibrationPanel.prototype.applyEntries.call(panel, [{ entry_id: "chosen", title: "Office" }]);
  assert.equal(warning.hidden, true);
  assert.equal(panel.catalogError, "");
  assert.equal(panel.values, draft);
  assert.equal(panel.selectedEntryId, "chosen");
});

test("detaching clears closed flow references before the same panel reconnects", async (t) => {
  const previousWindow = globalThis.window;
  const events = [];
  globalThis.window = { removeEventListener: (event) => events.push(event), addEventListener: () => {} };
  t.after(() => { if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow; });
  const flow = { current: { step_id: "cal_warm" }, close: async () => { events.push("close-options"); flow.current = null; } };
  const panel = {
    flow, session: { enabled: true }, creator: { close: async () => { events.push("close-create"); } },
    creationRevision: 0, newProfileOpen: true, initialized: true,
    catalog: { stop: () => events.push("unsubscribe") },
    _hass: {}, initialize: () => { events.push("initialize"); assert.equal(panel.flow, null); },
  };
  CircadianCalibrationPanel.prototype.disconnectedCallback.call(panel);
  assert.equal(panel.flow, null);
  assert.equal(panel.session, null);
  assert.equal(panel.creator, null);
  assert.equal(panel.newProfileOpen, false);
  assert.equal(panel.initialized, false);
  CircadianCalibrationPanel.prototype.connectedCallback.call(panel);
  await flushMicrotasks();
  assert.deepEqual(events, ["pagehide", "location-changed", "popstate", "close-options", "close-create", "unsubscribe", "initialize"]);
});

test("header keeps the mobile menu while New profile appears below the profile table", (t) => {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
    matches(selector) {
      return selector.startsWith("#") ? this.id === selector.slice(1) : selector.startsWith(".")
        ? this.className?.split(" ").includes(selector.slice(1)) : this.tag === selector;
    }
    querySelector(selector) { return this.descendants().find((node) => node.matches(selector)); }
    querySelectorAll(selector) { return this.descendants().filter((node) => node.matches(selector)); }
  }
  const previousDocument = globalThis.document;
  globalThis.document = { createElement: (tag) => new Node(tag) };
  t.after(() => { if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument; });
  const events = [];
  const panel = {
    shadowRoot: new Node("shadow"), entries: [{ entry_id: "one", title: "Office" }], selectedEntryId: "one",
    catalog: { subscribed: true }, catalogError: "", translations: {},
    copy: { title: "Circadian Pro", menu: "Open menu", integration: "Go to integration", newProfile: "New profile", start: "Configure and calibrate", retry: "Retry" },
    _hass: { user: { is_admin: true } }, error: "",
    dispatchEvent: (event) => events.push(event),
    updateControls() { CircadianCalibrationPanel.prototype.updateControls.call(this); },
  };
  CircadianCalibrationPanel.prototype.render.call(panel);
  const header = panel.shadowRoot.querySelector("header");
  const mobileMenu = header.querySelector(".mobile-menu");
  assert.equal(mobileMenu.attributes["aria-label"], "Open menu");
  mobileMenu.listeners.click();
  assert.equal(events[0].type, "hass-toggle-menu");
  assert.equal(events[0].bubbles, true);
  assert.equal(events[0].composed, true);
  assert.equal(header.querySelector("strong").textContent, "Circadian Pro");
  assert.equal(header.querySelector(".integration-link").textContent, "Go to integration");
  assert.equal(header.querySelector(".integration-link").href, "/config/integrations/integration/circadian_pro");
  const article = panel.shadowRoot.querySelector("article");
  const table = article.querySelector(".profile-table");
  const create = article.querySelector(".profile-create");
  assert.equal(table.querySelector(".profile-settings").attributes["aria-label"], "Configure and calibrate: Office");
  assert.equal(create.children[0].textContent, "New profile");
  assert.ok(article.children.indexOf(create) > article.children.indexOf(table));
  assert.equal(article.querySelector(".actions"), undefined);
  const css = panel.shadowRoot.querySelector("style").textContent;
  assert.match(css, /\.mobile-menu \{ display:none;/);
  assert.match(css, /@media\(max-width:870px\) \{ \.mobile-menu \{ display:block;/);
  assert.match(css, /button\.primary \{[^}]*color:var\(--primary-background-color,#081018\)/);
});

test("reused subscription IDs on a new socket are never cancelled by the old socket's cleanup", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  const activeCommands = new Map();
  let cancellations = 0;
  const baseSubscribe = metadata.connection.subscribeMessage;
  metadata.connection.subscribeMessage = async (callback, request, options) => {
    await baseSubscribe(callback, request, options);
    activeCommands.set(2, callback); // HA's command counter starts again after reconnect.
    return async () => { cancellations++; activeCommands.delete(2); };
  };
  const catalog = new EntryCatalog(async () => [], () => {});
  await catalog.connect(metadata.connection);
  const oldCallback = activeCommands.get(2);
  activeCommands.clear(); // The server discards subscriptions on the closed socket.
  metadata.disconnect();
  metadata.ready();
  await flushMicrotasks();
  assert.equal(metadata.subscriptions, 2);
  assert.equal(cancellations, 0);
  assert.equal(activeCommands.size, 1);
  assert.notEqual(activeCommands.get(2), oldCallback);
  assert.equal(catalog.subscribed, true);
  catalog.stop();
  await flushMicrotasks();
  assert.equal(cancellations, 1);
  assert.equal(activeCommands.size, 0);
});

test("a late old ACK does not unsubscribe a newer transport", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  const oldAck = deferred();
  let attempts = 0;
  let oldCancellations = 0;
  let newActive = false;
  metadata.connection.subscribeMessage = async (_callback, _request, options) => {
    assert.deepEqual(options, { resubscribe: false });
    if (++attempts === 1) return oldAck.promise;
    newActive = true;
    return async () => { newActive = false; };
  };
  const catalog = new EntryCatalog(async () => [], () => {});
  const connecting = catalog.connect(metadata.connection);
  metadata.disconnect();
  metadata.ready();
  await flushMicrotasks();
  oldAck.resolve(async () => { oldCancellations++; newActive = false; });
  assert.equal(await connecting, false);
  assert.equal(oldCancellations, 0);
  assert.equal(newActive, true);
  assert.equal(catalog.subscribed, true);
  catalog.stop();
  await flushMicrotasks();
  assert.equal(newActive, false);
});

test("a lost initial subscription ACK does not block panel loading or reconnect recovery", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  const lostAck = deferred();
  let attempts = 0;
  let reads = 0;
  let renders = 0;
  const subscribe = metadata.connection.subscribeMessage;
  metadata.connection.subscribeMessage = (callback, request, options) => {
    if (++attempts === 1) return lostAck.promise;
    return subscribe(callback, request, options);
  };
  const good = [{ entry_id: "one", title: "Office", settings: { lights: [] } }];
  const panel = {
    initialized: false, busy: false, isConnected: true, entries: [], translationRevision: 0,
    _hass: { user: { is_admin: true }, connection: metadata.connection, callWS: async (message) => {
      if (message.type === API + "entries") {
        assert.equal(message.include_status, true);
        reads++;
        return { entries: good, configured_profiles: 2 };
      }
      if (message.type === API + "translations") return { step: { init: {} }, selector: {} };
      throw new Error("Unexpected message");
    } },
    copy: { admin: "Administrator required", error: "Error", catalogError: "Refresh error" },
    language: "en", render: () => { renders++; }, updateControls: () => {},
  };
  for (const name of ["initialize", "action", "applyEntries", "catalogFailure", "loadTranslations", "applyLocalization"]) {
    panel[name] = CircadianCalibrationPanel.prototype[name].bind(panel);
  }
  await panel.initialize();
  assert.equal(panel.busy, false);
  assert.equal(panel.entries, good);
  assert.equal(panel.configuredProfiles, 2);
  assert.equal(reads, 1);
  metadata.disconnect();
  metadata.ready();
  await flushMicrotasks();
  assert.equal(panel.catalog.subscribed, true);
  t.mock.timers.tick(150);
  await panel.catalog.inFlight;
  assert.equal(panel.busy, false);
  assert.equal(reads, 2);
  assert.equal(attempts, 2);
  assert.ok(renders >= 2);
  panel.catalog.stop();
});

test("initializing on an offline connection waits for ready before sending a subscription", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const metadata = metadataConnection();
  metadata.connection.connected = false;
  const catalog = new EntryCatalog(async () => [], () => {});
  assert.equal(await catalog.connect(metadata.connection), false);
  assert.equal(metadata.subscriptions, 0);
  metadata.connection.connected = true;
  metadata.ready();
  await flushMicrotasks();
  assert.equal(metadata.subscriptions, 1);
  assert.equal(catalog.subscribed, true);
  catalog.stop();
});

async function panelModuleFixtures(t, registry = new Map()) {
  const directory = await mkdtemp(join(tmpdir(), "circadian-panel-module-"));
  const files = [];
  const previousHTMLElement = globalThis.HTMLElement;
  const previousCustomElements = globalThis.customElements;
  globalThis.HTMLElement = class {};
  globalThis.customElements = {
    get: (name) => registry.get(name),
    define(name, constructor) {
      assert.equal(registry.has(name), false, "A registered element cannot be redefined");
      assert.ok(![...registry.values()].includes(constructor), "Each constructor can be registered only once");
      registry.set(name, constructor);
    },
  };
  t.after(async () => {
    try {
      for (const file of files) await unlink(file);
      await rmdir(directory);
    } finally {
      if (previousHTMLElement === undefined) delete globalThis.HTMLElement;
      else globalThis.HTMLElement = previousHTMLElement;
      if (previousCustomElements === undefined) delete globalThis.customElements;
      else globalThis.customElements = previousCustomElements;
    }
  });
  return async (filename) => {
    const file = join(directory, filename);
    await writeFile(file, source, "utf8");
    files.push(file);
    return pathToFileURL(file).href;
  };
}

test("hashed panel modules register fresh components alongside an already loaded legacy panel", async (t) => {
  class OldPanel {}
  const registry = new Map([["circadian-pro-calibration", OldPanel]]);
  const fixture = await panelModuleFixtures(t, registry);
  const hashA = "aabbccddeeff0011";
  const hashB = "1122334455667788";
  const urlA = await fixture(`calibration-panel-${hashA}.js`);
  const urlB = await fixture(`calibration-panel-${hashB}.js`);
  const moduleA = await import(urlA);
  assert.equal(registry.get("circadian-pro-calibration"), OldPanel);
  assert.equal(registry.get(`circadian-pro-calibration-${hashA}`), moduleA.CircadianCalibrationPanel);
  const moduleB = await import(urlB);
  assert.equal(registry.get(`circadian-pro-calibration-${hashB}`), moduleB.CircadianCalibrationPanel);
  assert.notEqual(moduleA.CircadianCalibrationPanel, moduleB.CircadianCalibrationPanel);
  assert.equal(registry.size, 3);
  assert.equal(await import(urlA), moduleA); // One URL remains one module in the same document.
  const repeated = await import(urlA + "?repeat=1");
  assert.notEqual(repeated.CircadianCalibrationPanel, moduleA.CircadianCalibrationPanel);
  assert.equal(registry.get(`circadian-pro-calibration-${hashA}`), moduleA.CircadianCalibrationPanel);
  assert.equal(registry.size, 3); // Another evaluation cannot redefine an existing hashed tag.
});

test("the unversioned legacy module still registers its original component name", async (t) => {
  const registry = new Map();
  const fixture = await panelModuleFixtures(t, registry);
  const legacy = await import(await fixture("calibration-panel.js"));
  assert.equal(registry.get("circadian-pro-calibration"), legacy.CircadianCalibrationPanel);
  assert.equal(registry.size, 1);
});
