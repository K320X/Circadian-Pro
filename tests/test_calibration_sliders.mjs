/** Exercise the calibration controls and navigation without contacting live lights. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const previousHTMLElement = globalThis.HTMLElement;
const previousCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => undefined, define: () => {} };
const { CalibrationFlow, CircadianCalibrationPanel, PreviewSession, brightnessTimingValue, brightnessTimingPoints } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
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
  set innerHTML(value) { this.children = []; }
  get valueAsNumber() { return this.value === "" ? NaN : Number(this.value); }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  replaceChild(child, previous) {
    const index = this.children.indexOf(previous);
    if (index < 0) throw new Error("The replaced node is not a child");
    this.children[index] = child;
    return previous;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index < 0) throw new Error("The removed node is not a child");
    this.children.splice(index, 1);
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatchEvent(event) { for (const callback of this.listeners[event.type] || []) callback(event); }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector.includes(",")) return selector.split(",").some((part) => this.matches(part.trim()));
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return (this.className ?? this.getAttribute("class"))?.split(" ").includes(selector.slice(1));
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
  reportValidity() {
    this.validityReports = (this.validityReports || 0) + 1;
    return this.tag === "form" ? this.querySelectorAll("input").every((input) => input.checkValidity()) : this.checkValidity();
  }
  scrollTo() {}
}

function installDocument(t) {
  const original = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new Node(tag),
    createElementNS: (namespace, tag) => Object.assign(new Node(tag), { namespaceURI: namespace }),
  };
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

test("timing controls share one figure rather than embedding figures inside fields", (t) => {
  const harness = panelHarness(t, "init");
  for (const name of ["interval", "sunrise_offset_minutes", "brightness_mode_time_light", "brightness_mode_time_light_minutes", "cal_white_kelvin_neutral"]) {
    assert.equal(harness.add(name, 60, 1, 600).field.querySelector("figure"), undefined);
  }
});

test("timing sliders use whole minutes and retain invalid fractional drafts for correction", (t) => {
  const harness = panelHarness(t, "init");
  const { field, number, range } = harness.add("brightness_mode_time_dark_minutes", 160, 0, 240, 1);
  assert.ok(range);
  assert.equal(Number(range.step), 1);
  assert.equal(Number(number.step), 1);
  assert.equal(range.getAttribute("aria-labelledby"), number.getAttribute("aria-labelledby"));
  assert.equal(field.querySelectorAll("[data-field]").length, 1);
  number.value = "200";
  number.dispatchEvent(new Event("input"));
  assert.equal(range.value, "200");
  assert.equal(range.getAttribute("aria-valuetext"), "200 min");
  for (const value of ["", "-1", "200.125", "240.5"]) {
    number.value = value;
    number.dispatchEvent(new Event("input"));
    assert.equal(number.value, value);
    assert.equal(range.value, "200");
    assert.throws(() => harness.panel.readValues(), /Invalid value/);
  }
  range.value = "240";
  range.dispatchEvent(new Event("input"));
  assert.equal(number.value, "240");
  assert.equal(harness.panel.readValues().brightness_mode_time_dark_minutes, 240);
});

test("solar offset number fields accept signed whole minutes and retain fractional errors", (t) => {
  const harness = panelHarness(t, "init");
  for (const name of ["sunrise_offset_minutes", "sunset_offset_minutes"]) {
    const { number, range } = harness.add(name, -60, -720, 720, 1);
    assert.equal(range, undefined);
    assert.equal(Number(number.step), 1);
    number.value = "-61";
    number.dispatchEvent(new Event("input"));
    assert.equal(harness.panel.readValues()[name], -61);
    number.value = "-61.5";
    number.dispatchEvent(new Event("input"));
    assert.equal(number.value, "-61.5");
    assert.throws(() => harness.panel.readValues(), /Invalid value/);
    number.value = "0";
    number.dispatchEvent(new Event("input"));
    assert.equal(harness.panel.readValues()[name], 0);
  }
});

function generalSettingsHarness(t, fields) {
  const harness = panelHarness(t, "init");
  const { panel } = harness;
  panel.flow = { current: { type: "form", step_id: "init", data_schema: fields }, index: 0, drafts: {} };
  Object.assign(panel, { totalSteps: 4, entry: { title: "Office" }, error: "", initialized: true });
  Object.assign(panel.copy, { next: "Next", back: "Back", groups: { limits: "Brightness and temperature", night: "Sleep mode", other: "Other settings" } });
  panel.copy.timingDiagram = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get.call({ language: "en" }).timingDiagram;
  panel.field = CircadianCalibrationPanel.prototype.field.bind(panel);
  panel.render = CircadianCalibrationPanel.prototype.render.bind(panel);
  panel.showStep = CircadianCalibrationPanel.prototype.showStep.bind(panel);
  panel.navigate = CircadianCalibrationPanel.prototype.navigate.bind(panel);
  panel.action = async (callback) => { await callback(); panel.render(); };
  panel.updateControls = () => {};
  panel.showStep(panel.flow.current);
  panel.render();
  const input = (name) => panel.shadowRoot.querySelectorAll("[data-field]").find((node) => node.dataset.field === name);
  const edit = (name, value) => {
    const control = input(name);
    assert.ok(control, `${name} must be editable`);
    control.value = value;
    control.dispatchEvent(new Event("input"));
    return control;
  };
  return { ...harness, fields, input, edit };
}

function brightnessSettingsHarness(t, mode = "default", checked = false, omitted = []) {
  return generalSettingsHarness(t, [
    { name: "brightness_mode", default: mode, selector: { select: { options: ["default", "linear", "tanh"] } } },
    { name: "apply_temperature_curve", default: checked, selector: { boolean: {} } },
    ...["light", "dark"].map((portion) => ({ name: `brightness_mode_time_${portion}_minutes`, default: 0,
      selector: { number: { min: 0, max: 240, step: 1 } } })),
  ].filter((field) => !omitted.includes(field.name)));
}

test("one accessible chart updates in place from times, mode and temperature checkbox", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear");
  const figure = panel.shadowRoot.querySelector("figure");
  assert.equal(panel.shadowRoot.querySelectorAll("figure").length, 1);
  const svg = figure.querySelector("svg");
  const path = svg.querySelector(".timing-curve");
  assert.equal(svg.namespaceURI, "http://www.w3.org/2000/svg");
  assert.equal(svg.getAttribute("role"), "img");
  assert.equal(svg.getAttribute("focusable"), "false");
  assert.match(svg.getAttribute("aria-label"), /both times at 0/);
  assert.equal(figure.querySelector("figcaption").textContent, "Illustrative diagram: 12 h of daylight and 12 h of night.");
  assert.equal(figure.querySelector(".timing-summary"), undefined, "only the short caption belongs beneath the visible chart");
  assert.deepEqual(figure.querySelector(".timing-event-labels").children.map((node) => node.textContent), ["Sunrise", "Sunset"]);
  const timeInput = input("brightness_mode_time_light_minutes");
  const stepPath = path.getAttribute("d");
  edit("brightness_mode_time_light_minutes", "200");
  edit("brightness_mode_time_dark_minutes", "200");
  assert.equal(input("brightness_mode_time_light_minutes"), timeInput, "editing must preserve the focused input node");
  assert.equal(figure.querySelector(".timing-curve"), path, "the path must update without rebuilding the form");
  assert.notEqual(path.getAttribute("d"), stepPath);
  assert.match(svg.getAttribute("aria-label"), /200 min before \+ 200 min after sunrise/);
  assert.match(svg.getAttribute("aria-label"), /Each transition lasts 400 min/);
  const linearPath = path.getAttribute("d");
  edit("brightness_mode", "tanh");
  assert.notEqual(path.getAttribute("d"), linearPath);
  assert.match(svg.getAttribute("aria-label"), /5–95%/);
  const sigmoidPath = path.getAttribute("d");
  input("apply_temperature_curve").checked = true;
  input("apply_temperature_curve").dispatchEvent(new Event("input"));
  assert.equal(path.getAttribute("d"), sigmoidPath, "sharing the curve must not change its normalized shape");
  assert.match(figure.querySelector(".timing-legend").textContent, /Brightness and temperature/);
  assert.match(svg.getAttribute("aria-label"), /same curve between its own limits/);
  assert.equal(panel.flow.current.step_id, "init", "editing the graph must not advance or save the flow");
  assert.equal(panel.session.enabled, false, "editing the graph must not activate the physical light preview");
});

test("mode descriptions emphasize localized names and keep all explanation text inert", async (t) => {
  for (const [language, names] of [["es", ["Natural:", "Lineal:", "Sigmoide:"]], ["en", ["Natural:", "Linear:", "Sigmoid:"]]]) {
    await t.test(language, async (t) => {
      const { panel, form } = panelHarness(t, "init");
      const translations = JSON.parse(await readFile(new URL(`../custom_components/circadian_pro/translations/${language}.json`, import.meta.url), "utf8"));
      panel.translations = { ...translations.options, selector: translations.selector };
      panel.values.brightness_mode = "linear";
      const literal = "<b>Literal explanation</b>";
      panel.translations.step.init.data_description.brightness_mode += `\n${literal}`;
      const field = CircadianCalibrationPanel.prototype.field.call(panel, {
        name: "brightness_mode", selector: { select: { options: ["default", "linear", "tanh"], translation_key: "brightness_mode" } },
      });
      form.append(field);
      assert.deepEqual(field.querySelectorAll("strong").map((node) => node.textContent), names);
      assert.equal(field.querySelector(".mode-description").children.at(-1).textContent, literal);
      assert.equal(field.querySelector("b"), undefined);
      assert.equal(field.querySelector("select").value, "linear");
    });
  }
});

test("invalid timing drafts hide the plot until corrected without changing the input", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear");
  const figure = panel.shadowRoot.querySelector("figure");
  for (const value of ["", "-1", "100.5", "240.01"]) {
    edit("brightness_mode_time_dark_minutes", value);
    assert.equal(input("brightness_mode_time_dark_minutes").value, value);
    assert.equal(figure.querySelector(".timing-plot").hidden, true);
    assert.equal(figure.querySelector(".timing-status").hidden, false);
    assert.equal(figure.querySelector(".timing-curve").getAttribute("d"), null);
    assert.throws(() => panel.readValues(), /Invalid value/);
    edit("brightness_mode_time_dark_minutes", "0");
    assert.equal(figure.querySelector(".timing-plot").hidden, false);
  }
});

test("Natural hides timing controls and retains their draft when returning to a timed mode", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear", true);
  edit("brightness_mode_time_light_minutes", "200");
  edit("brightness_mode_time_dark_minutes", "166");
  const timeInput = input("brightness_mode_time_light_minutes");
  const timingRow = panel.shadowRoot.querySelectorAll(".settings-row").find((row) => row.descendants().includes(timeInput));
  edit("brightness_mode", "default");
  assert.equal(timingRow.hidden, true);
  assert.ok(timingRow.querySelectorAll("input").every((control) => control.disabled));
  assert.equal(input("apply_temperature_curve"), undefined);
  assert.equal(panel.readValues().brightness_mode_time_light_minutes, 200);
  assert.equal(panel.readValues().brightness_mode_time_dark_minutes, 166);
  assert.match(panel.shadowRoot.querySelector("svg").getAttribute("aria-label"), /Temperature follows its own solar curve/);
  assert.equal(panel.shadowRoot.querySelector(".timing-plot").hidden, false);
  CircadianCalibrationPanel.prototype.updateControls.call(panel);
  assert.equal(timeInput.disabled, true, "busy-state updates must keep hidden timing controls disabled");
  edit("brightness_mode", "tanh");
  assert.equal(timingRow.hidden, false);
  assert.equal(timeInput.disabled, false);
  assert.equal(timeInput.value, "200");
  assert.equal(input("apply_temperature_curve").checked, true);
});

test("invalid visible timing edits cannot be concealed by switching to Natural", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear");
  edit("brightness_mode_time_dark_minutes", "241");
  edit("brightness_mode", "default");
  assert.equal(input("brightness_mode").value, "linear");
  assert.equal(panel.values.brightness_mode, "linear");
  assert.equal(input("brightness_mode_time_dark_minutes").value, "241");
  assert.ok(input("brightness_mode_time_dark_minutes").validityReports > 0);
  assert.equal(input("brightness_mode_time_dark_minutes").disabled, false);
});

test("legacy Natural windows above the panel limit stay untouched until a timed mode is selected", (t) => {
  const { panel, input, edit } = generalSettingsHarness(t, [
    { name: "brightness_mode", default: "default", selector: { select: { options: ["default", "linear", "tanh"] } } },
    ...["light", "dark"].map((portion) => ({ name: `brightness_mode_time_${portion}_minutes`, default: 300,
      selector: { number: { min: 0, max: 240, step: 1 } } })),
  ]);
  assert.equal(panel.readValues().brightness_mode_time_light_minutes, 300);
  assert.equal(panel.shadowRoot.querySelector(".timing-plot").hidden, false);
  edit("brightness_mode", "linear");
  assert.equal(input("brightness_mode_time_light_minutes").value, "300");
  assert.equal(input("brightness_mode_time_light_minutes").disabled, false);
  assert.equal(panel.shadowRoot.querySelector(".timing-plot").hidden, true);
  assert.throws(() => panel.readValues(), /Invalid value/);
});

test("chart formula matches linear and sigmoid endpoints, asymmetric timing and equal-window midpoints", () => {
  const close = (value, expected) => assert.ok(Math.abs(value - expected) < 1e-9, `${value} != ${expected}`);
  for (const mode of ["linear", "tanh"]) {
    const lower = mode === "linear" ? 0 : 0.05;
    const upper = mode === "linear" ? 1 : 0.95;
    for (const [light, dark] of [[200, 200], [1, 166], [0, 240], [240, 0], [1, 0]]) {
      for (const [minute, expected] of [[360 - dark, lower], [360 + (light - dark) / 2, 0.5], [360 + light, upper],
        [1080 - light, upper], [1080 + (dark - light) / 2, 0.5], [1080 + dark, lower]]) {
        close(brightnessTimingValue(minute, mode, light, dark), expected);
        assert.ok(brightnessTimingPoints(mode, light, dark).some(([sample]) => Math.abs(sample - minute) < 1e-12), "critical instants must survive sampling");
      }
    }
    close(brightnessTimingValue(360, mode, 200, 200), 0.5);
    close(brightnessTimingValue(1080, mode, 200, 200), 0.5);
    close(brightnessTimingValue(360, mode, 0, 240), upper);
    close(brightnessTimingValue(360, mode, 240, 0), lower);
  }
  assert.ok(brightnessTimingValue(159, "tanh", 200, 200) > 0, "sigmoid must retain its asymptotic tails");
  assert.ok(brightnessTimingValue(561, "tanh", 200, 200) < 1);
});

test("chart draws zero windows as vertical steps and Natural as the runtime night parabola", () => {
  for (const mode of ["linear", "tanh"]) {
    assert.deepEqual(brightnessTimingPoints(mode, 0, 0), [[0, 0], [360, 0], [360, 1], [1080, 1], [1080, 0], [1440, 0]]);
    assert.equal(brightnessTimingValue(359.999, mode, 0, 0), 0);
    assert.equal(brightnessTimingValue(360, mode, 0, 0), 1);
    assert.equal(brightnessTimingValue(1080, mode, 0, 0), 0);
  }
  for (const [minute, value] of [[0, 0], [90, 0.0625], [180, 0.25], [270, 0.5625], [360, 1], [720, 1], [1080, 1], [1260, 0.25], [1440, 0]]) {
    assert.equal(brightnessTimingValue(minute, "default", 0, 0), value);
  }
  assert.deepEqual(brightnessTimingPoints("default", 0, 0), brightnessTimingPoints("default", 300, null), "Natural ignores hidden timing values");
  for (const invalid of [null, "", -1, 0.5, 239.5, 240.01, NaN, Infinity, "200"]) assert.equal(brightnessTimingPoints("linear", 100, invalid), null);
});

test("shared temperature curve is opt-in and only offered for Linear and Sigmoid", async (t) => {
  for (const mode of ["default", "linear", "tanh"]) {
    await t.test(mode, (t) => {
      const { panel, input } = brightnessSettingsHarness(t, mode);
      assert.equal(Boolean(input("apply_temperature_curve")), mode !== "default");
      if (mode !== "default") assert.equal(input("apply_temperature_curve").checked, false);
      assert.deepEqual(panel.readValues(), { brightness_mode: mode, apply_temperature_curve: false,
        brightness_mode_time_light_minutes: 0, brightness_mode_time_dark_minutes: 0 });
    });
  }
});

test("changing curve modes preserves the checkbox draft even while Natural hides it", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear");
  // Mode input must capture the outgoing checkbox even before its input event.
  input("apply_temperature_curve").checked = true;
  edit("brightness_mode", "default");
  assert.equal(input("apply_temperature_curve"), undefined);
  assert.equal(panel.readValues().apply_temperature_curve, true);
  edit("brightness_mode", "tanh");
  assert.equal(input("apply_temperature_curve").checked, true);
  input("apply_temperature_curve").checked = false;
  input("apply_temperature_curve").dispatchEvent(new Event("input"));
  edit("brightness_mode", "linear");
  assert.equal(input("apply_temperature_curve").checked, false);
  assert.equal(panel.settings.apply_temperature_curve, false);
});

test("an enabled saved temperature curve is retained while opening Natural", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "default", true);
  assert.equal(input("apply_temperature_curve"), undefined);
  assert.equal(panel.readValues().apply_temperature_curve, true);
  edit("brightness_mode", "linear");
  assert.equal(input("apply_temperature_curve").checked, true);
});

test("both durations accept zero and require whole minutes without hiding invalid drafts", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear", true);
  assert.equal(panel.readValues().brightness_mode_time_light_minutes, 0);
  assert.equal(panel.readValues().brightness_mode_time_dark_minutes, 0);
  edit("brightness_mode_time_light_minutes", "0.5");
  assert.equal(input("brightness_mode_time_light_minutes").value, "0.5");
  assert.throws(() => panel.readValues(), /Invalid value/);
  edit("brightness_mode_time_light_minutes", "1");
  assert.equal(panel.readValues().brightness_mode_time_light_minutes, 1);
  edit("brightness_mode_time_dark_minutes", "-1");
  edit("brightness_mode", "tanh");
  assert.equal(input("brightness_mode_time_dark_minutes").value, "-1");
  assert.throws(() => panel.readValues(), /Invalid value/);
  edit("brightness_mode_time_dark_minutes", "0");
  assert.equal(panel.readValues().brightness_mode_time_dark_minutes, 0);
});

test("older schemas without a shared temperature option still expose the mode selector", (t) => {
  const { panel, input } = brightnessSettingsHarness(t, "linear", false, ["apply_temperature_curve"]);
  assert.equal(input("brightness_mode").value, "linear");
  assert.equal(input("apply_temperature_curve"), undefined);
  assert.equal(Object.hasOwn(panel.readValues(), "apply_temperature_curve"), false);
});

function nightSettingsHarness(t, mode = "rgb_color", omitted = []) {
  const defaults = { sleep_rgb_or_color_temp: mode, sleep_color_temp: 2200, sleep_rgb_color: [255, 56, 0] };
  return generalSettingsHarness(t, [
    { name: "sleep_rgb_or_color_temp", selector: { select: { options: ["rgb_color", "color_temp"] } } },
    { name: "sleep_color_temp", selector: { number: { min: 1000, max: 10000, step: 1 } } },
    { name: "sleep_rgb_color", selector: { color_rgb: {} } },
  ].filter((field) => !omitted.includes(field.name)).map((field) => ({ ...field, default: defaults[field.name] })));
}

test("sleep color initially exposes only the selected color control while retaining both saved values", async (t) => {
  for (const mode of ["rgb_color", "color_temp"]) {
    await t.test(mode, (t) => {
      const { panel, input } = nightSettingsHarness(t, mode);
      const active = mode === "rgb_color" ? "sleep_rgb_color" : "sleep_color_temp";
      const inactive = mode === "rgb_color" ? "sleep_color_temp" : "sleep_rgb_color";
      assert.equal(input("sleep_rgb_or_color_temp").value, mode);
      assert.ok(input(active));
      assert.equal(input(inactive), undefined);
      assert.deepEqual(panel.readValues(), {
        sleep_rgb_or_color_temp: mode, sleep_color_temp: 2200, sleep_rgb_color: [255, 56, 0],
      });
    });
  }
});

test("switching sleep color modes captures the outgoing edit and restores both independent drafts", (t) => {
  const { panel, input, edit } = nightSettingsHarness(t, "color_temp");
  // Leave the value in the DOM without a prior input event: changing mode must
  // capture that outgoing control before replacing it.
  input("sleep_color_temp").value = "3123";
  edit("sleep_rgb_or_color_temp", "rgb_color");
  assert.equal(input("sleep_color_temp"), undefined);
  assert.equal(panel.values.sleep_color_temp, 3123);
  edit("sleep_rgb_color", "#d13f08");
  edit("sleep_rgb_or_color_temp", "color_temp");
  assert.equal(input("sleep_color_temp").value, "3123");
  assert.equal(input("sleep_rgb_color"), undefined);
  edit("sleep_rgb_or_color_temp", "rgb_color");
  assert.equal(input("sleep_rgb_color").value, "#d13f08");
  assert.deepEqual(panel.readValues(), {
    sleep_rgb_or_color_temp: "rgb_color", sleep_color_temp: 3123, sleep_rgb_color: [209, 63, 8],
  });
});

test("invalid sleep Kelvin stays visible when changing modes and can be corrected before continuing", async (t) => {
  for (const value of ["", "999", "10001", "2200.5"]) {
    await t.test(value || "empty", (t) => {
      const { panel, input, edit } = nightSettingsHarness(t, "color_temp");
      const kelvin = edit("sleep_color_temp", value);
      edit("sleep_rgb_or_color_temp", "rgb_color");
      assert.equal(input("sleep_rgb_or_color_temp").value, "color_temp");
      assert.equal(panel.values.sleep_rgb_or_color_temp, "color_temp");
      assert.equal(panel.settings.sleep_rgb_or_color_temp, "color_temp");
      assert.equal(input("sleep_color_temp"), kelvin);
      assert.equal(kelvin.value, value);
      assert.equal(input("sleep_rgb_color"), undefined);
      assert.ok(kelvin.validityReports > 0, "the invalid visible field should explain why the switch was refused");
      assert.throws(() => panel.readValues(), /Invalid value/);
      edit("sleep_color_temp", "2700");
      edit("sleep_rgb_or_color_temp", "rgb_color");
      assert.ok(input("sleep_rgb_color"));
      assert.equal(input("sleep_color_temp"), undefined);
      assert.equal(panel.readValues().sleep_color_temp, 2700);
    });
  }
});

test("sleep color drafts are submitted together and restored when navigating back", async (t) => {
  const harness = nightSettingsHarness(t, "color_temp");
  const { panel, fields, input, edit } = harness;
  const path = "config/config_entries/options/flow";
  const submitted = [];
  let sequence = 0;
  const callApi = async (method, url, data) => {
    if (method === "DELETE") return {};
    if (url === path) return { type: "form", flow_id: `night-${++sequence}`, step_id: "init", data_schema: fields };
    submitted.push(structuredClone(data));
    return { type: "form", flow_id: `night-${sequence}`, step_id: "cal_neutral", data_schema: [] };
  };
  const flow = new CalibrationFlow(callApi, { async setEnabled() {} }, "office");
  panel.flow = flow;
  panel.showStep(await flow.start());
  panel.render();
  edit("sleep_color_temp", "2900");
  edit("sleep_rgb_or_color_temp", "rgb_color");
  edit("sleep_rgb_color", "#c83504");
  await panel.navigate();
  assert.deepEqual(submitted, [{
    sleep_rgb_or_color_temp: "rgb_color", sleep_color_temp: 2900, sleep_rgb_color: [200, 53, 4],
  }]);
  assert.equal(flow.current.step_id, "cal_neutral");
  await panel.navigate(true);
  assert.equal(flow.current.step_id, "init");
  assert.equal(input("sleep_rgb_or_color_temp").value, "rgb_color");
  assert.equal(input("sleep_rgb_color").value, "#c83504");
  edit("sleep_rgb_or_color_temp", "color_temp");
  assert.equal(input("sleep_color_temp").value, "2900");
  assert.equal(submitted.length, 1, "Back must not submit or save another settings change");
});

test("partial sleep schemas keep every available setting editable", async (t) => {
  for (const omitted of ["sleep_rgb_or_color_temp", "sleep_color_temp", "sleep_rgb_color"]) {
    await t.test(`without ${omitted}`, (t) => {
      const { panel, fields, input } = nightSettingsHarness(t, "rgb_color", [omitted]);
      const available = fields.map((field) => field.name).sort();
      assert.deepEqual(panel.shadowRoot.querySelectorAll("[data-field]").map((node) => node.dataset.field).sort(), available);
      assert.equal(input(omitted), undefined);
      assert.deepEqual(Object.keys(panel.readValues()).sort(), available);
    });
  }
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
