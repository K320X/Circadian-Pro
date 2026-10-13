/** Cancellation races in the actual panel controllers; no DOM or live HA. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const previousHTMLElement = globalThis.HTMLElement;
const previousCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => undefined, define: () => {} };
const { CalibrationFlow, NewProfileFlow, CircadianCalibrationPanel, PreviewSession } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
if (previousHTMLElement === undefined) delete globalThis.HTMLElement;
else globalThis.HTMLElement = previousHTMLElement;
if (previousCustomElements === undefined) delete globalThis.customElements;
else globalThis.customElements = previousCustomElements;

const OPTIONS = "config/config_entries/options/flow";
const CONFIG = "config/config_entries/flow";
const form = (flow_id, step_id) => ({ type: "form", flow_id, step_id, data_schema: [] });
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
async function flush() { for (let index = 0; index < 12; index++) await Promise.resolve(); }

test("closing while Back creates a replacement flow deletes the late flow and stops replay", async () => {
  const creation = deferred();
  const calls = [];
  let starts = 0;
  let step = 0;
  const flow = new CalibrationFlow(async (method, path, data) => {
    calls.push({ method, path, data });
    if (method === "DELETE") return {};
    if (path === OPTIONS) return ++starts === 1 ? form("original", "init") : creation.promise;
    if (path === OPTIONS + "/original") return form("original", ++step === 1 ? "white_channels" : "cal_neutral");
    if (path === OPTIONS + "/replacement") return form("replacement", "white_channels");
    throw new Error("Unexpected call");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  await flow.next({ white_w_kelvin: 3000, white_cw_kelvin: 6500 });
  const backing = flow.back({ cal_rgb_neutral_r: 90 }).catch((error) => error);
  await flush();
  await flow.close();
  creation.resolve(form("replacement", "init"));
  await backing;
  assert.ok(calls.some((call) => call.method === "DELETE" && call.path === OPTIONS + "/replacement"));
  assert.ok(calls.every((call) => call.method !== "POST" || call.path !== OPTIONS + "/replacement"));
  assert.equal(flow.current, null);
});

test("cancelling before the initial config-flow reply prevents later menu/name writes and profile creation", async () => {
  const initialization = deferred();
  const calls = [];
  let profiles = 0;
  const creator = new NewProfileFlow(async (method, path, data) => {
    calls.push({ method, path, data });
    if (method === "DELETE") return {};
    if (path === CONFIG) return initialization.promise;
    if (data?.action === "new") return form("pending", "user");
    if (data?.name) { profiles++; return { type: "create_entry", result: { entry_id: "unexpected" } }; }
    throw new Error("Unexpected call");
  });
  const creating = creator.create("New profile").catch((error) => error);
  await flush();
  await creator.close();
  initialization.resolve(form("pending", "menu"));
  await creating;
  assert.equal(profiles, 0);
  assert.ok(calls.some((call) => call.method === "DELETE" && call.path === CONFIG + "/pending"));
  assert.ok(calls.every((call) => call.method !== "POST" || call.path === CONFIG));
  assert.equal(creator.current, null);
});

test("cancelling a pending menu step prevents the later name submission and deletes the flow only once", async () => {
  const menu = deferred();
  const calls = [];
  const creator = new NewProfileFlow(async (method, path, data) => {
    calls.push({ method, path, data });
    if (method === "DELETE") return {};
    if (path === CONFIG) return form("pending", "menu");
    if (data?.action === "new") return menu.promise;
    throw new Error("The name must never be submitted after cancellation");
  });
  const creating = creator.create("New profile").catch((error) => error);
  await flush();
  await creator.close();
  menu.resolve(form("pending", "user"));
  const outcome = await creating;
  assert.equal(outcome.reason, "flow_closed");
  assert.equal(calls.filter((call) => call.method === "DELETE").length, 1);
  assert.ok(calls.every((call) => !call.data?.name));
  assert.equal(creator.current, null);
});

test("a completed name write is kept when its response arrives after closing", async () => {
  const name = deferred();
  const calls = [];
  const creator = new NewProfileFlow(async (method, path, data) => {
    calls.push({ method, path, data });
    if (method === "DELETE") return {};
    if (path === CONFIG) return form("pending", "user");
    if (data?.name) return name.promise;
    throw new Error("Unexpected call");
  });
  const creating = creator.create("New profile").catch((error) => error);
  await flush();
  await creator.close();
  name.resolve({ type: "create_entry", result: { entry_id: "created-by-ha" } });
  assert.equal((await creating).reason, "flow_closed");
  assert.equal(creator.createdEntryId, "created-by-ha");
  assert.equal(creator.current, null);
  assert.ok(calls.every((call) => call.method !== "DELETE" || call.path === CONFIG + "/pending"));
  await assert.rejects(creator.create("Another name"), /already been created/);
});

test("closing while Next waits for restoration prevents the pending save", async () => {
  const stop = deferred();
  const calls = [];
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    return method === "DELETE" ? {} : form("existing", "cal_warm");
  }, { setEnabled: () => stop.promise }, "profile");
  await flow.start();
  const saving = flow.next({ cal_rgb_warm_r: 70 }).catch((error) => error);
  const closing = flow.close();
  stop.resolve();
  await closing;
  assert.equal((await saving).reason, "flow_closed");
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);
  assert.equal(calls.filter((call) => call.method === "DELETE").length, 1);
  assert.equal(flow.current, null);
});

test("pending navigation freezes editable controls while the mobile sidebar remains accessible", () => {
  const controls = [{ type: "number" }, { type: "color" }, { type: "checkbox" }, { tag: "select" }];
  const buttons = [{ className: "primary" }, { className: "close" }, { className: "mobile-menu" }];
  const panel = {
    busy: true,
    shadowRoot: {
      querySelector: () => null,
      querySelectorAll: (selector) => selector === "button" ? buttons : selector === "input,select" ? controls : [],
    },
  };
  CircadianCalibrationPanel.prototype.updateControls.call(panel);
  assert.ok(controls.every((control) => control.disabled));
  assert.equal(buttons[0].disabled, true);
  assert.equal(buttons[1].disabled, true);
  assert.equal(buttons[2].disabled, false);
  panel.busy = false;
  CircadianCalibrationPanel.prototype.updateControls.call(panel);
  assert.ok(controls.every((control) => !control.disabled));
  assert.ok(buttons.every((button) => !button.disabled));
});

test("Back keeps the displayed form and draft when replacement creation fails, and a retry can recover", async () => {
  let starts = 0;
  const calls = [];
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    if (method === "DELETE") return {};
    if (path === OPTIONS) {
      if (++starts === 2) throw new Error("Connection lost");
      return form(starts === 1 ? "original" : "recovered", "init");
    }
    return form("original", "cal_neutral");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  const displayed = flow.current;
  await assert.rejects(flow.back({ cal_rgb_neutral_r: 77 }), /Connection lost/);
  assert.equal(flow.current, displayed);
  assert.equal(flow.rebuilding, true);
  assert.equal(flow.drafts.cal_neutral.cal_rgb_neutral_r, 77);
  assert.equal((await flow.back({ cal_rgb_neutral_r: 78 })).step_id, "init");
  assert.equal(flow.rebuilding, false);
  assert.equal(flow.drafts.cal_neutral.cal_rgb_neutral_r, 78);
  assert.equal(calls.filter((call) => call.method === "DELETE" && call.path === OPTIONS + "/original").length, 1);
});

test("a failed Back replay keeps the old screen and deletes its partial replacement before retrying", async () => {
  let starts = 0;
  let originalStep = 0;
  const calls = [];
  const flow = new CalibrationFlow(async (method, path, data) => {
    calls.push({ method, path, data });
    if (method === "DELETE") return {};
    if (path === OPTIONS) return form(["original", "partial", "recovered"][starts++], "init");
    if (path === OPTIONS + "/original") return form("original", ++originalStep === 1 ? "white_channels" : "cal_neutral");
    if (path === OPTIONS + "/partial") throw new Error("Lost replay response");
    return form("recovered", "white_channels");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  await flow.next({ white_w_kelvin: 3000, white_cw_kelvin: 6500 });
  const displayed = flow.current;
  await assert.rejects(flow.back({ cal_rgb_neutral_r: 77 }), /Lost replay response/);
  assert.equal(flow.current, displayed);
  assert.equal(flow.pending.flow_id, "partial");
  assert.equal((await flow.back({ cal_rgb_neutral_r: 78 })).step_id, "white_channels");
  assert.ok(calls.some((call) => call.method === "DELETE" && call.path === OPTIONS + "/partial"));
  assert.ok(calls.every((call) => !call.data?.cal_rgb_neutral_r));
});

test("navigation validates required numeric fields before disabling controls", async () => {
  const input = { type: "number", value: "", required: true, disabled: false, dataset: { field: "cal_rgb_neutral_r" } };
  const validationStates = [];
  const submissions = [];
  const formElement = {
    querySelectorAll: (selector) => selector === "[data-field]" ? [input] : [],
    reportValidity() { validationStates.push(input.disabled); return input.disabled || input.value !== ""; },
  };
  const panel = {
    busy: false, initialized: true, isConnected: true, error: "", values: {}, settings: {},
    copy: { invalid: "Invalid", error: "Error" },
    shadowRoot: {
      querySelector: (selector) => selector === "form" ? formElement : null,
      querySelectorAll: (selector) => selector === "input,select" ? [input] : [],
    },
    flow: { next: async (data) => { assert.equal(input.disabled, true); submissions.push(data); return form("one", "cal_warm"); } },
    render: () => {}, showStep: () => {},
  };
  for (const name of ["navigate", "readValues", "action", "updateControls"]) panel[name] = CircadianCalibrationPanel.prototype[name].bind(panel);
  await panel.navigate();
  assert.equal(panel.error, "Invalid");
  assert.deepEqual(submissions, []);
  input.value = "77";
  await panel.navigate();
  assert.deepEqual(validationStates, [false, false]);
  assert.deepEqual(submissions, [{ cal_rgb_neutral_r: 77 }]);
  assert.equal(panel.busy, false);
});

test("a lost Next response uses GET to find the advanced step without another POST", async () => {
  const calls = [];
  let advanced = false;
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    if (path === OPTIONS) return form("one", "init");
    if (method === "POST") { advanced = true; throw new Error("Reply lost"); }
    assert.equal(method, "GET");
    assert.equal(advanced, true);
    return form("one", "cal_neutral");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  assert.equal((await flow.next({ lights: ["light.one"] })).step_id, "cal_neutral");
  assert.equal(flow.index, 1);
  assert.equal(flow.pendingSubmission, null);
  assert.deepEqual(calls.map(({ method }) => method), ["POST", "POST", "GET"]);
});

test("a failed reconciliation keeps its draft and retry reads state before any new write", async () => {
  const calls = [];
  let reads = 0;
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    if (path === OPTIONS) return form("one", "init");
    if (method === "POST") throw new Error("Reply lost");
    if (++reads === 1) throw new Error("Still offline");
    return form("one", "cal_neutral");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await assert.rejects(flow.next({ lights: ["light.one"] }), (error) => error.reason === "submission_uncertain");
  assert.equal(flow.current.step_id, "init");
  assert.ok(flow.pendingSubmission);
  assert.equal((await flow.next({ lights: ["light.one"] })).step_id, "cal_neutral");
  assert.deepEqual(calls.map(({ method }) => method), ["POST", "POST", "GET", "GET"]);
  assert.deepEqual(flow.drafts.init.lights, ["light.one"]);
});

test("reconciliation of an unchanged step never repeats the uncertain submission automatically", async () => {
  let writes = 0;
  const flow = new CalibrationFlow(async (method, path) => {
    if (path === OPTIONS || method === "GET") return form("one", "cal_warm");
    writes++;
    throw new Error("Request failed before reaching HA");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await assert.rejects(flow.next({ cal_rgb_warm_r: 77 }), /Request failed/);
  assert.equal(writes, 1);
  assert.equal(flow.pendingSubmission, null);
  assert.equal(flow.current.step_id, "cal_warm");
});

test("a lost Finish response and GET404 leave the save uncertain and refresh profile metadata", async () => {
  let saves = 0;
  const calls = [];
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    if (path === OPTIONS) return form("one", "cal_warm");
    if (method === "POST") { saves++; throw { error: "Request error" }; }
    throw { status_code: 404, body: { message: "Invalid flow specified" } };
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  const result = await flow.next({ cal_rgb_warm_r: 77 });
  assert.equal(result.type, "save_uncertain");
  assert.equal(saves, 1);
  assert.equal(flow.closed, true);
  assert.equal(flow.current, null);
  const panel = { flow, session: {}, copy: { saved: "Saved", save_uncertain: "Check actual settings" }, initialized: true };
  CircadianCalibrationPanel.prototype.showStep.call(panel, result);
  assert.equal(panel.flow, null);
  assert.equal(panel.session, null);
  assert.equal(panel.message, "Check actual settings");
  assert.equal(panel.saveUncertain, true);
  assert.equal(panel.initialized, false);
  await assert.rejects(flow.next({ cal_rgb_warm_r: 77 }), (error) => error.reason === "flow_closed");
  assert.deepEqual(calls.map(({ method }) => method), ["POST", "POST", "GET"]);
});

test("a late reconciliation response after closing cannot reopen or continue the wizard", async () => {
  const recovery = deferred();
  const calls = [];
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    if (path === OPTIONS) return form("one", "init");
    if (method === "POST") throw new Error("Reply lost");
    if (method === "GET") return recovery.promise;
    return {};
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  const next = flow.next({ lights: ["light.one"] }).catch((error) => error);
  await flush();
  await flow.close();
  recovery.resolve(form("one", "cal_neutral"));
  assert.equal((await next).reason, "flow_closed");
  assert.equal(flow.current, null);
  assert.equal(calls.filter((call) => call.method === "DELETE").length, 1);
});

test("closing an absent flow is idempotent with Home Assistant's real REST error shape", async () => {
  const flow = new CalibrationFlow(async (method) => {
    if (method === "POST") return form("one", "cal_warm");
    throw { status_code: 404, body: { message: "Invalid flow specified" } };
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await flow.close();
  assert.equal(flow.current, null);
});

test("transient cleanup errors are reported after detaching the closed wizard", async () => {
  const calls = [];
  const flow = new CalibrationFlow(async (method) => {
    calls.push(method);
    if (method === "POST") return form("one", "cal_neutral");
    throw new Error("DELETE offline");
  }, { setEnabled: async () => { throw new Error("Stop offline"); } }, "profile");
  await flow.start();
  const panel = { flow, session: {}, createdProfile: null, copy: {} };
  await assert.rejects(CircadianCalibrationPanel.prototype.closeWizard.call(panel), /Stop offline/);
  assert.deepEqual(calls, ["POST", "DELETE"]);
  assert.equal(flow.current, null);
  assert.equal(panel.flow, null);
  assert.equal(panel.session, null);
});

test("an absent flow after a lost intermediate response can rebuild the draft on a later explicit attempt", async () => {
  const calls = [];
  let starts = 0;
  const flow = new CalibrationFlow(async (method, path, data) => {
    calls.push({ method, path, data });
    if (path === OPTIONS) return form(++starts === 1 ? "expired" : "recovered", "init");
    if (path === OPTIONS + "/expired") throw { status_code: 404, body: { message: "Invalid flow specified" } };
    assert.equal(method, "POST");
    return form("recovered", "cal_neutral");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await assert.rejects(flow.next({ lights: ["light.one"] }), (error) => error.reason === "flow_expired");
  assert.equal(flow.pendingSubmission, null);
  assert.equal(flow.rebuilding, true);
  assert.equal(flow.current.step_id, "init");
  assert.deepEqual(flow.drafts.init.lights, ["light.one"]);
  assert.equal((await flow.next({ lights: ["light.one"] })).step_id, "cal_neutral");
  assert.equal(flow.pendingSubmission, null);
  assert.equal(flow.rebuilding, false);
  assert.deepEqual(calls.map(({ method }) => method), ["POST", "POST", "GET", "DELETE", "POST", "POST"]);
});

test("an options abort closes the wizard and explains YAML ownership using backend translations", () => {
  const flow = { closed: false, current: { type: "abort", reason: "yaml_configuration" } };
  const panel = {
    flow, session: {}, createdProfile: null, message: "", copy: {},
    translations: { abort: { yaml_configuration: "Edit the YAML configuration" } },
  };
  CircadianCalibrationPanel.prototype.showStep.call(panel, flow.current);
  assert.equal(flow.closed, true);
  assert.equal(panel.flow, null);
  assert.equal(panel.session, null);
  assert.equal(panel.error, "Edit the YAML configuration");
});

test("an abort while recreating a flow never submits replay data to the already removed flow", async () => {
  let starts = 0;
  const calls = [];
  const flow = new CalibrationFlow(async (method, path) => {
    calls.push({ method, path });
    if (method === "DELETE") return {};
    if (path === OPTIONS) return ++starts === 1 ? form("original", "init")
      : { type: "abort", flow_id: "aborted", reason: "yaml_configuration" };
    if (path === OPTIONS + "/original") return form("original", calls.filter((call) => call.path === path).length === 1 ? "white_channels" : "cal_neutral");
    throw new Error("No replay is valid after abort");
  }, { setEnabled: async () => {} }, "profile");
  await flow.start();
  await flow.next({ lights: ["light.one"] });
  await flow.next({ white_w_kelvin: 3000, white_cw_kelvin: 6500 });
  assert.equal((await flow.back({ cal_rgb_neutral_r: 77 })).type, "abort");
  assert.ok(calls.every((call) => call.path !== OPTIONS + "/aborted"));
});

test("every preview update and stop carries the same unique session token", async (t) => {
  const messages = [];
  const session = new PreviewSession(async (message) => { messages.push(message); return { expires_in: 60 }; }, "profile", () => ({ settings: {}, kelvin: 4000 }));
  t.after(() => session.clearTimers());
  const other = new PreviewSession(async () => {}, "profile", () => ({}));
  assert.equal(typeof session.sessionId, "string");
  assert.ok(session.sessionId.length > 16);
  assert.notEqual(session.sessionId, other.sessionId);
  await session.setEnabled(true);
  await session.setEnabled(true);
  await session.setEnabled(false);
  assert.deepEqual(messages.map((message) => message.session_id), [session.sessionId, session.sessionId, session.sessionId]);
});

test("a delayed stop from an old session cannot stop a new preview of the same entry", async (t) => {
  const oldReply = deferred();
  let owner = null;
  const messages = [];
  const callWS = async (message) => {
    messages.push(message);
    if (message.type.endsWith("preview")) {
      if (owner && owner !== message.session_id) throw new Error("Another session already previews this profile");
      owner = message.session_id;
      if (message.session_id === old.sessionId) return oldReply.promise;
      return { expires_in: 60 };
    }
    if (owner === message.session_id) owner = null;
    return { active: false };
  };
  const draft = () => ({ settings: {}, kelvin: 4000 });
  const old = new PreviewSession(callWS, "profile", draft);
  const current = new PreviewSession(callWS, "profile", draft);
  t.after(() => { old.clearTimers(); current.clearTimers(); });
  const oldEnable = old.setEnabled(true);
  await flush();
  const oldStop = old.setEnabled(false);
  owner = null; // The backend's TTL expired while the old browser reply was pending.
  await current.setEnabled(true);
  oldReply.resolve({ expires_in: 60 });
  await Promise.all([oldEnable, oldStop]);
  assert.equal(owner, current.sessionId);
  assert.equal(current.enabled, true);
  assert.equal(messages.at(-1).session_id, old.sessionId);
  await current.setEnabled(false);
});

test("a conflicting editor's preview error and cleanup preserve the active owner's preview", async (t) => {
  let owner = null;
  const callWS = async (message) => {
    if (message.type.endsWith("preview")) {
      if (owner && owner !== message.session_id) throw new Error("Another session already previews this profile");
      owner = message.session_id;
      return { expires_in: 60 };
    }
    if (owner === message.session_id) owner = null;
    return { active: false };
  };
  const draft = () => ({ settings: {}, kelvin: 4000 });
  const first = new PreviewSession(callWS, "profile", draft);
  const second = new PreviewSession(callWS, "profile", draft);
  t.after(() => { first.clearTimers(); second.clearTimers(); });
  await first.setEnabled(true);
  await assert.rejects(second.setEnabled(true), /Another session/);
  assert.equal(second.enabled, false);
  assert.equal(owner, first.sessionId);
  await first.setEnabled(false);
});

test("HTTP clients without randomUUID still get independent preview tokens", (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
  t.after(() => { if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor); else delete globalThis.crypto; });
  const sessions = [new PreviewSession(() => {}, "profile", () => ({})), new PreviewSession(() => {}, "profile", () => ({}))];
  assert.notEqual(sessions[0].sessionId, sessions[1].sessionId);
});

test("select options use the real English and Spanish selector translations while preserving values", async (t) => {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; }
    append(...children) { this.children.push(...children); }
    addEventListener() {}
  }
  const previousDocument = globalThis.document;
  globalThis.document = { createElement: (tag) => new Node(tag) };
  t.after(() => { if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument; });
  const cases = [
    ["brightness_mode", ["default", "linear", "tanh"], { en: ["Natural", "Linear", "Sigmoid"], es: ["Natural", "Lineal", "Sigmoide"] }],
    ["sleep_activation_mode", ["disabled", "sun", "schedule"], { en: ["Disabled", "By sunrise and sunset", "By schedule"], es: ["Desactivado", "Por amanecer y atardecer", "Por franja horaria"] }],
    ["sleep_rgb_or_color_temp", ["rgb_color", "color_temp"], { en: ["Define with RGB", "Define with color temperature"], es: ["Definir con RGB", "Definir con temperatura de color"] }],
  ];
  for (const language of ["en", "es"]) {
    const data = JSON.parse(await readFile(new URL(`../custom_components/circadian_pro/translations/${language}.json`, import.meta.url), "utf8"));
    const panel = { flow: { current: { step_id: "init" } }, translations: { ...data.options, selector: data.selector }, values: {}, copy: {} };
    for (const [name, options, expected] of cases) {
      panel.values[name] = options[1];
      // HA Selector.serialize supplies selector.select as its config object.
      const field = { name, selector: { select: { options, translation_key: name, multiple: false } } };
      const wrapper = CircadianCalibrationPanel.prototype.field.call(panel, field);
      const select = wrapper.children.find((child) => child.tag === "select");
      assert.deepEqual(select.children.map((option) => option.textContent), expected[language]);
      assert.deepEqual(select.children.map((option) => option.value), options);
      assert.equal(select.value, options[1]);
    }
    const wrapper = CircadianCalibrationPanel.prototype.field.call(panel, { name: "custom", type: "select", options: [["custom_value", "Custom label"]] });
    const select = wrapper.children.find((child) => child.tag === "select");
    assert.equal(select.children[0].textContent, "Custom label");
    assert.equal(select.children[0].value, "custom_value");
  }
});
