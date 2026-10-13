/** Exercise the calibration controls and navigation without contacting live lights. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const previousHTMLElement = globalThis.HTMLElement;
const previousCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => undefined, define: () => {} };
const { CalibrationFlow, CircadianCalibrationPanel, PreviewSession } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
if (previousHTMLElement === undefined) delete globalThis.HTMLElement;
else globalThis.HTMLElement = previousHTMLElement;
if (previousCustomElements === undefined) delete globalThis.customElements;
else globalThis.customElements = previousCustomElements;

class Node {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.style = { setProperty() {} };
    this.value = "";
  }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  get valueAsNumber() { return this.value === "" ? NaN : Number(this.value); }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatchEvent(event) { for (const callback of this.listeners[event.type] || []) callback(event); }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector.includes(",")) return selector.split(",").some((part) => this.matches(part.trim()));
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return this.className?.split(" ").includes(selector.slice(1));
    if (selector === "[data-field]") return Object.hasOwn(this.dataset, "field");
    if (selector === '[data-kind="entity"]') return this.dataset.kind === "entity";
    return this.tag === selector;
  }
  querySelector(selector) { return this.descendants().find((node) => node.matches(selector)); }
  querySelectorAll(selector) { return this.descendants().filter((node) => node.matches(selector)); }
  checkValidity() {
    if (this.disabled) return true;
    if (this.required && !this.value) return false;
    if (!["number", "range"].includes(this.type) || !this.value) return true;
    const value = Number(this.value);
    if (!Number.isFinite(value)) return false;
    if (this.min !== undefined && value < Number(this.min)) return false;
    if (this.max !== undefined && value > Number(this.max)) return false;
    return !this.step || this.step === "any" || Number.isInteger((value - Number(this.min || 0)) / Number(this.step));
  }
  get validity() { return { valid: this.checkValidity() }; }
  reportValidity() { return this.tag === "form" ? this.querySelectorAll("input").every((input) => input.checkValidity()) : this.checkValidity(); }
}

function installDocument(t) {
  const original = globalThis.document;
  globalThis.document = { createElement: (tag) => new Node(tag) };
  t.after(() => { if (original === undefined) delete globalThis.document; else globalThis.document = original; });
}

function panelHarness(t, step = "cal_neutral") {
  installDocument(t);
  let refreshes = 0;
  const panel = {
    shadowRoot: new Node("shadow"), flow: { current: { step_id: step } },
    values: {}, settings: {}, translations: { step: {} },
    copy: { invalid: "Invalid value", warmWhite: "Warm", coldWhite: "Cold" },
    session: { enabled: false, refresh() { refreshes++; } },
  };
  panel.readValues = CircadianCalibrationPanel.prototype.readValues.bind(panel);
  const form = new Node("form");
  panel.shadowRoot.append(form);
  return {
    panel, form, refreshes: () => refreshes,
    add(name, value, min, max, step = 1) {
      panel.values[name] = value;
      const field = CircadianCalibrationPanel.prototype.field.call(panel, { name, selector: { number: { min, max, step } } });
      form.append(field);
      const controls = field.querySelectorAll("input");
      return { field, number: controls.find((input) => input.type === "number"), range: controls.find((input) => input.type === "range") };
    },
  };
}

test("calibration slider and number share an accessible name and submit one numeric field", (t) => {
  const harness = panelHarness(t);
  harness.panel.translations.step.cal_neutral = { data: { cal_rgb_neutral_r: "Red channel" } };
  const { field, number, range } = harness.add("cal_rgb_neutral_r", 137, 0, 255);
  assert.ok(range, "calibration field must offer a range control");
  assert.equal(field.tag, "div", "two controls must not be wrapped in a single implicit label");
  assert.equal(number.value, "137");
  assert.equal(range.value, "137");
  assert.equal(number.getAttribute("aria-labelledby"), range.getAttribute("aria-labelledby"));
  assert.ok(number.getAttribute("aria-labelledby"));
  const labelId = number.getAttribute("aria-labelledby");
  assert.equal(field.querySelector(`#${labelId}`).textContent, "Red channel");
  assert.equal(number.dataset.field, "cal_rgb_neutral_r");
  assert.equal(range.dataset.field, undefined);
  assert.deepEqual(harness.panel.readValues(), { cal_rgb_neutral_r: 137 });
});

test("dragging or typing updates the other control, draft and preview refresh once", (t) => {
  const harness = panelHarness(t);
  const { number, range } = harness.add("cal_rgb_neutral_g", 102, 0, 255);
  assert.ok(range);
  range.value = "217";
  range.dispatchEvent(new Event("input"));
  assert.equal(number.value, "217");
  assert.equal(harness.panel.values.cal_rgb_neutral_g, 217);
  assert.equal(harness.panel.settings.cal_rgb_neutral_g, 217);
  assert.equal(harness.refreshes(), 1);
  number.value = "63";
  number.dispatchEvent(new Event("input"));
  assert.equal(range.value, "63");
  assert.equal(harness.panel.values.cal_rgb_neutral_g, 63);
  assert.equal(harness.panel.settings.cal_rgb_neutral_g, 63);
  assert.equal(harness.refreshes(), 2);
});

test("cleared and out-of-range numbers remain invalid and can be repaired using the slider", (t) => {
  const harness = panelHarness(t, "cal_warm");
  const { number, range } = harness.add("cal_rgb_warm_b", 25, 0, 255);
  assert.ok(range);
  for (const [value, stored] of [["", null], ["256", 256], ["-1", -1], ["1.5", 1.5]]) {
    number.value = value;
    number.dispatchEvent(new Event("input"));
    assert.equal(number.value, value);
    assert.equal(range.value, "25", "an invalid draft must not move the slider to a different valid value");
    assert.equal(harness.panel.readValues(false).cal_rgb_warm_b, stored);
    assert.throws(() => harness.panel.readValues(), /Invalid value/);
  }
  range.value = "0";
  range.dispatchEvent(new Event("input"));
  assert.equal(number.value, "0");
  assert.deepEqual(harness.panel.readValues(), { cal_rgb_warm_b: 0 });
});

test("white mixture exposes exact warm and cold endpoints and editable intermediate kelvin", (t) => {
  const harness = panelHarness(t);
  const { number, range } = harness.add("cal_white_kelvin_neutral", 4123, 2873, 6471);
  assert.ok(range);
  assert.equal(Number(range.min), 2873);
  assert.equal(Number(range.max), 6471);
  assert.equal(Number(range.step), 1);
  assert.equal(range.value, "4123");
  for (const kelvin of [2873, 6471]) {
    range.value = kelvin;
    range.dispatchEvent(new Event("input"));
    assert.equal(Number(number.value), kelvin);
    assert.equal(harness.panel.readValues().cal_white_kelvin_neutral, kelvin);
  }
  number.value = "4567";
  number.dispatchEvent(new Event("input"));
  assert.equal(range.value, "4567");
});

test("RGB and white intensity keep their own limits in both calibration steps", (t) => {
  const harness = panelHarness(t);
  for (const mode of ["neutral", "warm"]) {
    harness.panel.flow.current.step_id = `cal_${mode}`;
    for (const channel of ["r", "g", "b"]) {
      const { number, range } = harness.add(`cal_rgb_${mode}_${channel}`, 0, 0, 255);
      assert.ok(range);
      assert.deepEqual([Number(range.min), Number(range.max), Number(number.min), Number(number.max)], [0, 255, 0, 255]);
    }
    const { number, range } = harness.add(`cal_white_intensity_${mode}`, 100, 0, 100);
    assert.ok(range);
    assert.deepEqual([Number(range.min), Number(range.max), Number(number.min), Number(number.max)], [0, 100, 0, 100]);
  }
});

test("general settings keep their existing number controls", (t) => {
  const harness = panelHarness(t, "init");
  const { number, range } = harness.add("interval", 60, 1, 3600);
  assert.equal(range, undefined);
  assert.equal(number.value, "60");
});

test("sliders stay discreet by default and active preview coalesces slider and number edits", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const harness = panelHarness(t);
  const requests = [];
  const session = new PreviewSession(async (message) => {
    requests.push(message);
    return { expires_in: 60 };
  }, "office", () => ({ settings: harness.panel.readValues(), kelvin: 4000 }));
  harness.panel.session = session;
  const { number, range } = harness.add("cal_rgb_neutral_r", 128, 0, 255);
  range.value = "191";
  range.dispatchEvent(new Event("input"));
  t.mock.timers.tick(180);
  await session.queue;
  assert.deepEqual(requests, []);
  await session.setEnabled(true);
  assert.equal(requests[0].settings.cal_rgb_neutral_r, 191);
  range.value = "209";
  range.dispatchEvent(new Event("input"));
  number.value = "234";
  number.dispatchEvent(new Event("input"));
  t.mock.timers.tick(180);
  await session.queue;
  assert.equal(requests.length, 2);
  assert.equal(requests[1].settings.cal_rgb_neutral_r, 234);
  assert.equal(requests[1].kelvin, 4000);
  await session.setEnabled(false);
});

test("a pending navigation disables both controls and unlocks them afterward", (t) => {
  const harness = panelHarness(t);
  const { number, range } = harness.add("cal_white_intensity_warm", 0, 0, 100);
  harness.panel.busy = true;
  CircadianCalibrationPanel.prototype.updateControls.call(harness.panel);
  assert.equal(number.disabled, true);
  assert.equal(range.disabled, true);
  harness.panel.busy = false;
  CircadianCalibrationPanel.prototype.updateControls.call(harness.panel);
  assert.equal(number.disabled, false);
  assert.equal(range.disabled, false);
});

test("last calibration screen renders Back and Finish, and Back dispatches backwards navigation", (t) => {
  installDocument(t);
  const navigations = [];
  const panel = {
    shadowRoot: new Node("shadow"),
    flow: { index: 3, current: { step_id: "cal_warm", data_schema: [] } },
    totalSteps: 4, entry: { title: "Office" }, translations: {},
    copy: { title: "Circadian Pro", back: "Back", finish: "Finish", next: "Next" },
    session: { enabled: false }, error: "", navigate: (back) => navigations.push(back), updateControls() {},
  };
  CircadianCalibrationPanel.prototype.render.call(panel);
  const actions = panel.shadowRoot.querySelector(".actions");
  assert.deepEqual(actions.children.map((node) => node.textContent), ["Back", "Finish"]);
  actions.children[0].dispatchEvent(new Event("click"));
  assert.deepEqual(navigations, [true]);
});

test("Back from 1000 K restores before replay, retains its draft and never submits Finish", async () => {
  const path = "config/config_entries/options/flow";
  const steps = ["init", "white_channels", "cal_neutral", "cal_warm"];
  const flows = new Map();
  const events = [];
  let sequence = 0;
  let saves = 0;
  const callApi = async (method, url, data) => {
    events.push({ method, url, data });
    let id = url.slice(path.length + 1);
    if (method === "DELETE") { flows.delete(id); return {}; }
    if (url === path) { id = `flow-${++sequence}`; flows.set(id, 0); }
    else {
      const previous = flows.get(id);
      if (previous === 3) { saves++; return { type: "create_entry" }; }
      flows.set(id, previous + 1);
    }
    return { type: "form", flow_id: id, step_id: steps[flows.get(id)] };
  };
  const preview = { async setEnabled(enabled) { assert.equal(enabled, false); events.push({ method: "restore" }); } };
  const flow = new CalibrationFlow(callApi, preview, "office");
  await flow.start();
  await flow.next({ lights: ["light.office"] });
  await flow.next({ white_w_kelvin: 3000, white_cw_kelvin: 6500 });
  await flow.next({ cal_rgb_neutral_r: 199 });
  events.length = 0;
  const warmDraft = { cal_rgb_warm_r: 210, cal_rgb_warm_g: 42, cal_rgb_warm_b: 0, cal_white_intensity_warm: 5 };
  assert.equal((await flow.back(warmDraft)).step_id, "cal_neutral");
  assert.equal(flow.index, 2);
  assert.equal(events[0].method, "restore");
  assert.deepEqual(events.filter((event) => event.method === "POST" && event.url !== path).map((event) => event.data), [
    { lights: ["light.office"] }, { white_w_kelvin: 3000, white_cw_kelvin: 6500 },
  ]);
  assert.equal(saves, 0);
  await flow.next({ cal_rgb_neutral_r: 190 });
  assert.equal(flow.current.step_id, "cal_warm");
  assert.deepEqual(flow.drafts.cal_warm, warmDraft);
  assert.equal(saves, 0);
});
