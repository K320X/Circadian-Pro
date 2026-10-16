/** Native integration links use the real panel controller, with no live HA. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const previousHTMLElement = globalThis.HTMLElement;
const previousCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = { querySelector: () => null }; }
};
globalThis.customElements = { get: () => undefined, define: () => {} };
const { CircadianCalibrationPanel } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
if (previousHTMLElement === undefined) delete globalThis.HTMLElement;
else globalThis.HTMLElement = previousHTMLElement;
if (previousCustomElements === undefined) delete globalThis.customElements;
else globalThis.customElements = previousCustomElements;

const FLOW = "config/config_entries/options/flow";
const API = "circadian_pro/calibration/";
const form = (id) => ({ type: "form", flow_id: id, step_id: "init", data_schema: [] });
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function flush() { for (let index = 0; index < 40; index++) await Promise.resolve(); }

function harness(t, search = "?config_entry=one", overrides = {}) {
  const previousWindow = globalThis.window;
  const browser = new EventTarget();
  browser.location = { search };
  globalThis.window = browser;
  const calls = [];
  const panel = new CircadianCalibrationPanel();
  Object.assign(panel, {
    isConnected: true, initialized: true, catalogLoaded: true,
    entries: ["one", "two", "three"].map((entry_id) => ({ entry_id, title: entry_id, settings: { lights: [] } })),
    render() {}, updateControls() {},
    _hass: {
      language: "en", user: { is_admin: true }, states: {},
      callApi: async (method, path, data) => {
        calls.push({ method, path, data });
        if (overrides.callApi) return overrides.callApi(method, path, data);
        return method === "POST" ? form(data.handler) : {};
      },
      callWS: async (message) => {
        calls.push(message);
        return overrides.callWS?.(message) || { expires_in: 60 };
      },
    },
  });
  const starts = () => calls.filter((call) => call.path === FLOW).map((call) => call.data.handler);
  const navigate = (entryId) => {
    browser.location.search = entryId === null ? "" : `?config_entry=${entryId}`;
    return panel.syncRoute();
  };
  t.after(async () => {
    panel.isConnected = false;
    panel.disconnectedCallback();
    await flush();
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  });
  return { panel, browser, calls, starts, navigate };
}

test("a native profile link opens its wizard once and HA updates preserve the draft", async (t) => {
  const { panel, starts } = harness(t);
  await panel.syncRoute();
  assert.deepEqual(starts(), ["one"]);
  assert.equal(panel.entry.entry_id, "one");
  const flow = panel.flow;
  panel.values.min_brightness = 27;
  panel.hass = { ...panel.hass };
  panel.route = { path: "" };
  await panel.syncRoute();
  assert.equal(panel.flow, flow);
  assert.equal(panel.values.min_brightness, 27);
  assert.deepEqual(starts(), ["one"]);
  await panel.closeWizard();
  await panel.syncRoute();
  assert.equal(panel.flow, null, "closing a wizard must not reopen the URL target");
});

test("the panel anchor, a deleted entry and an ordinary sidebar URL open the catalog", async (t) => {
  const { panel, starts, navigate } = harness(t, "?config_entry=panel-anchor");
  await panel.syncRoute();
  assert.equal(panel.flow, undefined);
  await navigate("deleted-profile");
  await navigate(null);
  assert.deepEqual(starts(), []);
  assert.equal(panel.entry, null);
  assert.equal(panel.error, "");
});

test("changing profile waits for preview restoration before opening the next wizard", async (t) => {
  const restoration = deferred();
  let delayStop = false;
  const { panel, starts, navigate, calls } = harness(t, undefined, {
    callWS: (message) => message.type === API + "stop" && delayStop ? restoration.promise : undefined,
  });
  await panel.syncRoute();
  await panel.session.setEnabled(true);
  delayStop = true;
  const moving = navigate("two");
  await flush();
  assert.deepEqual(starts(), ["one"]);
  assert.equal(panel.session.enabled, false);
  restoration.resolve({});
  await moving;
  assert.deepEqual(starts(), ["one", "two"]);
  assert.equal(panel.flow.entryId, "two");
  const oldDelete = calls.findIndex((call) => call.method === "DELETE" && call.path === FLOW + "/one");
  const newStart = calls.findIndex((call) => call.path === FLOW && call.data.handler === "two");
  assert.ok(oldDelete >= 0 && oldDelete < newStart);
});

test("rapid route changes cancel a pending open and only the latest target is opened", async (t) => {
  const first = deferred();
  const { panel, navigate, starts, calls } = harness(t, undefined, {
    callApi: async (method, path, data) => method === "POST"
      ? (data.handler === "one" ? first.promise : form(data.handler)) : {},
  });
  const opening = panel.syncRoute();
  await flush();
  await navigate("two");
  await navigate("three");
  first.resolve(form("late-one"));
  await opening;
  await flush();
  assert.deepEqual(starts(), ["one", "three"]);
  assert.equal(panel.entry.entry_id, "three");
  assert.equal(panel.flow.entryId, "three");
  assert.ok(calls.some((call) => call.method === "DELETE" && call.path === FLOW + "/late-one"));
});

test("removing the query through browser history closes the wizard and its preview", async (t) => {
  const { panel, browser, calls, starts } = harness(t);
  panel.connectedCallback();
  await panel.syncRoute();
  await panel.session.setEnabled(true);
  browser.location.search = "";
  browser.dispatchEvent(new Event("popstate"));
  await flush();
  assert.equal(panel.flow, null);
  assert.equal(panel.entry, null);
  assert.ok(calls.some((call) => call.type === API + "stop"));
  browser.location.search = "?config_entry=two";
  browser.dispatchEvent(new Event("location-changed"));
  await flush();
  assert.deepEqual(starts(), ["one", "two"]);
});

test("a failed preview restoration is shown and never opens another profile", async (t) => {
  let failStop = false;
  const { panel, navigate, starts } = harness(t, undefined, {
    callWS: async (message) => {
      if (message.type === API + "stop" && failStop) throw new Error("Restoration offline");
    },
  });
  await panel.syncRoute();
  failStop = true;
  await navigate("two");
  assert.equal(panel.flow, null);
  assert.equal(panel.error, "Restoration offline");
  assert.deepEqual(starts(), ["one"]);
});

test("detaching cancels late route results and reconnecting consumes the URL anew", async (t) => {
  const first = deferred();
  let requests = 0;
  const { panel, starts } = harness(t, undefined, {
    callApi: async (method) => method === "POST" ? (++requests === 1 ? first.promise : form("reconnected")) : {},
  });
  const opening = panel.syncRoute();
  await flush();
  panel.isConnected = false;
  panel.disconnectedCallback();
  first.resolve(form("detached"));
  await opening;
  assert.equal(panel.flow, null);
  assert.equal(panel.pendingRoute, null);
  panel.isConnected = panel.initialized = true;
  await panel.syncRoute();
  assert.deepEqual(starts(), ["one", "one"]);
  assert.equal(panel.flow.current.flow_id, "reconnected");
});

test("deep links wait for initial metadata and are unavailable to non-administrators", async (t) => {
  const { panel, starts } = harness(t);
  panel.catalogLoaded = false;
  await panel.syncRoute();
  assert.deepEqual(starts(), []);
  panel.catalogLoaded = true;
  panel._hass.user.is_admin = false;
  await panel.syncRoute();
  assert.deepEqual(starts(), []);
  panel._hass.user.is_admin = true;
  await panel.action(async () => {});
  await flush();
  assert.deepEqual(starts(), ["one"]);
});

test("catalog recovery after an initial read failure consumes the pending deep link", async (t) => {
  const { panel, starts } = harness(t);
  panel.catalogLoaded = false;
  panel.catalogFailure();
  await panel.syncRoute();
  assert.deepEqual(starts(), []);
  panel.applyEntries({ entries: panel.entries, configured_profiles: 3 });
  await flush();
  assert.deepEqual(starts(), ["one"]);
  assert.equal(panel.catalogError, "");
  assert.equal(panel.flow.entryId, "one");
});
