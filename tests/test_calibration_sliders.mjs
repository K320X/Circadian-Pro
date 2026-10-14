/** Exercise the calibration controls and navigation without contacting live lights. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const previousHTMLElement = globalThis.HTMLElement;
const previousCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => undefined, define: () => {} };
const { CalibrationFlow, CircadianCalibrationPanel, PreviewSession, brightnessTimingValue: rawBrightnessTimingValue, brightnessTimingPoints: rawBrightnessTimingPoints, temperatureTimingPoints: rawTemperatureTimingPoints, SolarContextSession, validSolarContext, nightTimingPoints, sleepGraphColor, brightnessTimingDiagram, layoutTimelineLabels } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
if (previousHTMLElement === undefined) delete globalThis.HTMLElement;
else globalThis.HTMLElement = previousHTMLElement;
if (previousCustomElements === undefined) delete globalThis.customElements;
else globalThis.customElements = previousCustomElements;

const REFERENCE_CONTEXT = {
  local_date: "1970-01-01", timezone: "UTC", day_start: 0, day_end: 86400, refresh_after: 86400,
  sunrise: { timestamp: 21600, local_time: "06:00" }, sunset: { timestamp: 64800, local_time: "18:00" },
  events: [-1440, 0, 1440].flatMap((day) => [
    { event: "solar_midnight", timestamp: day * 60 }, { event: "sunrise", timestamp: (day + 360) * 60 },
    { event: "solar_noon", timestamp: (day + 720) * 60 }, { event: "sunset", timestamp: (day + 1080) * 60 },
  ]),
};
const brightnessTimingValue = (...args) => rawBrightnessTimingValue(...args, REFERENCE_CONTEXT);
const brightnessTimingPoints = (...args) => rawBrightnessTimingPoints(...args, REFERENCE_CONTEXT);
const temperatureTimingPoints = (...args) => rawTemperatureTimingPoints(...args, REFERENCE_CONTEXT);
const NIGHT_VALUES = { brightness_mode: "linear", brightness_mode_time_light_minutes: 0, brightness_mode_time_dark_minutes: 0,
  min_brightness: 100, max_brightness: 100, sleep_brightness: 80, sleep_rgb_or_color_temp: "rgb_color", sleep_rgb_color: [255, 0, 0],
  transition: 5, interval: 60, sleep_activation_mode: "schedule", apply_temperature_curve: false };
const nightContext = (windows = [{ start: 21 * 3600, end: 30 * 3600 }], extra = {}) => ({ ...REFERENCE_CONTEXT,
  sleep_mode: "schedule", sleep_windows: windows, sleep_all_day: false,
  midpoints: [{ kind: "night", timestamp: 0, local_time: "00:00" }, { kind: "day", timestamp: 43200, local_time: "12:00" }], ...extra });

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

test("general brightness fields expose the canonical 1–100 range and keep invalid zero drafts editable", (t) => {
  const harness = panelHarness(t, "init");
  for (const name of ["min_brightness", "max_brightness", "sleep_brightness"]) {
    const { field, number } = harness.add(name, 50, 0, 100);
    assert.deepEqual([Number(number.min), Number(number.max), Number(number.step)], [1, 100, 1]);
    const hint = field.querySelector(".brightness-range-help");
    assert.equal(hint.textContent, "1–100 %");
    assert.equal(number.getAttribute("aria-describedby"), hint.id);
    number.value = "0";
    number.dispatchEvent(new Event("input"));
    assert.equal(number.value, "0", "the invalid draft is never silently clamped");
    assert.equal(harness.panel.values[name], 0);
    assert.equal(number.checkValidity(), false);
    assert.throws(() => harness.panel.readValues(), /Invalid value/);
    number.value = "1";
    number.dispatchEvent(new Event("input"));
    assert.equal(number.checkValidity(), true);
  }
});

test("zero brightness has an explicit graph error with automatic night on or off and recovers after correction", (t) => {
  installDocument(t);
  for (const language of ["en", "es"]) {
    const copy = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get.call({ language }).timingDiagram;
    for (const mode of ["default", "linear", "tanh"]) {
      for (const activation of ["disabled", "schedule"]) {
        const chart = brightnessTimingDiagram(copy, language);
        const context = nightContext([], { sleep_mode: activation });
        const values = { ...NIGHT_VALUES, brightness_mode: mode, sleep_activation_mode: activation };
        for (const field of ["min_brightness", "max_brightness", "sleep_brightness"]) {
          chart.update({ ...values, [field]: 0 }, context, "ready");
          assert.equal(chart.figure.querySelector(".timing-plot").hidden, true);
          assert.equal(chart.figure.querySelector(".timing-status").textContent, copy.brightnessRange);
          assert.match(chart.figure.querySelector(".timing-status").textContent, /1.*100/);
          assert.equal(chart.figure.querySelector(".timing-curve").getAttribute("d"), null);
        }
        chart.update({ ...values, min_brightness: 90, max_brightness: 20 }, context, "ready");
        assert.equal(chart.figure.querySelector(".timing-status").textContent, copy.brightnessOrder);
        chart.update({ ...values, min_brightness: 1 }, context, "ready");
        assert.equal(chart.figure.querySelector(".timing-plot").hidden, false);
        assert.equal(chart.figure.querySelector(".timing-status").hidden, true);
      }
    }
  }
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
  panel._hass = { callWS: () => new Promise(() => {}) };
  panel.render = () => {
    CircadianCalibrationPanel.prototype.render.call(panel);
    panel.chartSolarContext = REFERENCE_CONTEXT;
    panel.chartSolarStatus = "ready";
    panel.timingChart?.update(panel.values, REFERENCE_CONTEXT, "ready");
  };
  t.after(() => panel.solarContext?.close());
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
  assert.equal(figure.querySelector("figcaption").textContent, "Jan 1, 1970 · UTC");
  assert.equal(figure.querySelector(".timing-summary"), undefined, "only the short caption belongs beneath the visible chart");
  assert.deepEqual(figure.querySelector(".timing-event-labels").children.map((node) => [node.children[0].textContent, node.querySelector("time").textContent]), [["Sunrise", "06:00"], ["Sunset", "18:00"]]);
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
  assert.equal(figure.querySelector(".timing-legend").textContent, "Sigmoid · Brightness");
  assert.equal(figure.querySelector(".timing-temperature-legend").textContent, "Color temperature");
  assert.match(svg.getAttribute("aria-label"), /same curve between its own limits/);
  assert.equal(panel.flow.current.step_id, "init", "editing the graph must not advance or save the flow");
  assert.equal(panel.session.enabled, false, "editing the graph must not activate the physical light preview");
});

test("temperature background stays solar until shared, and returns to solar in Natural", (t) => {
  const { panel, input, edit } = brightnessSettingsHarness(t, "linear");
  const figure = panel.shadowRoot.querySelector("figure");
  const gradient = figure.querySelector("linearGradient");
  const stops = () => gradient.children.map((stop) => [Number(stop.getAttribute("offset")), stop.getAttribute("stop-color")]);
  const independent = stops();
  const at = (offset) => independent.find(([value]) => value === offset)?.[1];
  for (const offset of [0, 0.25, 0.75, 1]) assert.equal(at(offset), "rgb(255, 138, 26)");
  assert.equal(at(0.5), "rgb(255, 255, 255)");
  assert.equal(figure.querySelector(".timing-temperature").getAttribute("fill"), `url(#${gradient.getAttribute("id")})`);
  assert.equal(gradient.getAttribute("color-interpolation"), "sRGB");
  edit("brightness_mode_time_light_minutes", "200");
  edit("brightness_mode_time_dark_minutes", "200");
  edit("brightness_mode", "tanh");
  assert.deepEqual(stops(), independent, "brightness mode and its times must not alter independent color");
  input("apply_temperature_curve").checked = true;
  input("apply_temperature_curve").dispatchEvent(new Event("input"));
  const shared = stops();
  assert.notDeepEqual(shared, independent);
  assert.equal(shared.find(([offset]) => offset === 0.25)[1], "rgb(255, 197, 141)", "equal shared windows place sunrise halfway between orange and white");
  edit("brightness_mode", "default");
  assert.equal(panel.readValues().apply_temperature_curve, true, "Natural retains the checkbox draft");
  assert.deepEqual(stops(), independent, "Natural ignores a retained checked draft");
  edit("brightness_mode", "tanh");
  assert.deepEqual(stops(), shared, "returning to Sigmoid restores shared color");
  input("apply_temperature_curve").checked = false;
  input("apply_temperature_curve").dispatchEvent(new Event("input"));
  assert.deepEqual(stops(), independent);
  assert.equal(figure.querySelectorAll(".timing-curve").length, 1, "temperature must not add another curve");
  assert.match(figure.querySelector("svg").getAttribute("aria-label"), /background represents color temperature: orange at its minimum and white at its maximum/);
  assert.equal(panel.session.enabled, false);
});

test("shared zero windows make hard color edges without a sampling-width fade", (t) => {
  const { panel, edit } = brightnessSettingsHarness(t, "linear", true);
  const figure = panel.shadowRoot.querySelector("figure");
  const gradient = figure.querySelector("linearGradient");
  for (const mode of ["linear", "tanh"]) {
    edit("brightness_mode", mode);
    assert.deepEqual(gradient.children.map((stop) => [stop.getAttribute("offset"), stop.getAttribute("stop-color")]), [
      ["0", "rgb(255, 138, 26)"], ["0.25", "rgb(255, 138, 26)"], ["0.25", "rgb(255, 255, 255)"],
      ["0.75", "rgb(255, 255, 255)"], ["0.75", "rgb(255, 138, 26)"], ["1", "rgb(255, 138, 26)"],
    ]);
  }
  edit("brightness_mode_time_light_minutes", "1");
  assert.ok(gradient.children.every((stop) => Number.isFinite(Number(stop.getAttribute("offset")))));
  edit("brightness_mode_time_dark_minutes", "");
  assert.equal(gradient.children.length, 0, "invalid drafts cannot retain a misleading stale gradient");
  assert.equal(figure.querySelector(".timing-plot").hidden, true);
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
  assert.match(panel.shadowRoot.querySelector("svg").getAttribute("aria-label"), /Color temperature follows its own solar curve/);
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

test("independent color follows the runtime daylight parabola and shared color follows each timed curve", () => {
  const independent = temperatureTimingPoints("default", 0, 0, false);
  for (const [minute, expected] of [[0, 0], [180, 0], [360, 0], [540, 0.75], [720, 1], [900, 0.75], [1080, 0], [1260, 0], [1440, 0]]) {
    assert.equal(independent.find(([sample]) => sample === minute)?.[1], expected);
  }
  assert.deepEqual(temperatureTimingPoints("default", NaN, null, true), independent, "Natural ignores hidden timing and shared settings");
  for (const mode of ["linear", "tanh"]) {
    for (const [light, dark] of [[0, 0], [1, 0], [0, 240], [1, 166], [200, 200], [240, 240]]) {
      assert.deepEqual(temperatureTimingPoints(mode, light, dark, false), independent);
      const points = temperatureTimingPoints(mode, light, dark, true);
      assert.deepEqual(points, brightnessTimingPoints(mode, light, dark));
      for (const [minute, progress] of points) {
        assert.ok(Number.isFinite(minute) && minute >= 0 && minute <= 1440);
        assert.ok(Number.isFinite(progress) && progress >= 0 && progress <= 1);
      }
    }
  }
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


function shiftContext(context, seconds, overrides = {}) {
  return { ...context, day_start: context.day_start + seconds, day_end: context.day_end + seconds,
    sunrise: { ...context.sunrise, timestamp: context.sunrise.timestamp + seconds },
    sunset: { ...context.sunset, timestamp: context.sunset.timestamp + seconds },
    events: context.events.map((event) => ({ ...event, timestamp: event.timestamp + seconds })), ...overrides };
}

function eventTarget() {
  const listeners = new Map();
  return { hidden: false, listeners,
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(callback); },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    emit(type) { for (const callback of listeners.get(type) || []) callback(); },
  };
}

function solarSessionHarness(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const requests = [], updates = [];
  const environment = { document: eventTarget(), window: eventTarget(), connection: eventTarget() };
  const session = new SolarContextSession((message) => new Promise((resolve, reject) => requests.push({ message, resolve, reject })),
    "office", (context, status) => updates.push({ context, status }), environment);
  t.after(() => session.close());
  return { session, requests, updates, ...environment };
}
const settleSolar = async () => { await Promise.resolve(); await Promise.resolve(); };

test("solar requests debounce only changed solar drafts, retaining final unsaved offsets and fixed times", async (t) => {
  const { session, requests, updates } = solarSessionHarness(t);
  session.update({ sunrise_time: "None", sunrise_offset_minutes: 0, brightness_mode_time_light_minutes: 1 });
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].message, { type: "circadian_pro/calibration/solar", entry_id: "office",
    settings: { sunrise_time: "None", sunrise_offset_minutes: 0 } });
  requests[0].resolve(REFERENCE_CONTEXT);
  await settleSolar();
  assert.equal(updates.at(-1).status, "ready");
  for (let brightness = 1; brightness <= 240; brightness++) session.update({ sunrise_time: "None", sunrise_offset_minutes: 0, brightness_mode_time_light_minutes: brightness });
  t.mock.timers.tick(300);
  assert.equal(requests.length, 1, "slider moves must never trigger a solar request");
  session.update({ sunrise_time: "07:30:00", sunrise_offset_minutes: 20, max_sunrise_time: "08:00:00" });
  assert.equal(updates.at(-1).context, null, "retire misleading old schedules immediately");
  session.update({ sunrise_time: "07:35:00", sunrise_offset_minutes: 25, max_sunrise_time: "08:00:00" });
  t.mock.timers.tick(299);
  assert.equal(requests.length, 1);
  t.mock.timers.tick(1);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].message.settings, { sunrise_time: "07:35:00", max_sunrise_time: "08:00:00", sunrise_offset_minutes: 25 });
});

test("late solar reads cannot restore a previous draft or a closed profile", async (t) => {
  const { session, requests, updates } = solarSessionHarness(t);
  session.update({ sunrise_offset_minutes: 0 });
  session.update({ sunrise_offset_minutes: 30 });
  requests[0].resolve(REFERENCE_CONTEXT);
  await settleSolar();
  assert.equal(updates.at(-1).context, null);
  t.mock.timers.tick(300);
  const fresh = shiftContext(REFERENCE_CONTEXT, 60);
  requests[1].resolve(fresh);
  await settleSolar();
  assert.equal(updates.at(-1).context, fresh);
  const third = session.refresh();
  session.close();
  const closedCount = updates.length;
  requests[2].resolve(REFERENCE_CONTEXT);
  await third;
  assert.equal(updates.length, closedCount);
  t.mock.timers.tick(90000000);
  assert.equal(requests.length, 3, "close removes all pending refresh timers");
});

test("HA relative midnight TTL, visibility and reconnect refresh without browser-clock assumptions", async (t) => {
  const { session, requests, updates, document, window, connection } = solarSessionHarness(t);
  session.update({});
  requests[0].resolve({ ...REFERENCE_CONTEXT, refresh_after: 2 });
  await settleSolar();
  t.mock.timers.tick(2249);
  assert.equal(requests.length, 1);
  t.mock.timers.tick(1);
  assert.equal(requests.length, 2, "midnight refresh uses the server TTL even for an old fixture date");
  const tomorrow = shiftContext(REFERENCE_CONTEXT, 86400, { local_date: "1970-01-02" });
  requests[1].resolve(tomorrow);
  await settleSolar();
  assert.equal(updates.at(-1).context.local_date, "1970-01-02");
  document.hidden = true;
  document.emit("visibilitychange");
  assert.equal(requests.length, 2);
  document.hidden = false;
  document.emit("visibilitychange");
  assert.equal(requests.length, 3);
  connection.emit("disconnected");
  requests[2].resolve(REFERENCE_CONTEXT);
  await settleSolar();
  assert.equal(updates.at(-1).status, "unavailable");
  connection.emit("ready");
  assert.equal(requests.length, 4);
  requests[3].resolve(tomorrow);
  await settleSolar();
  window.emit("pageshow");
  assert.equal(requests.length, 5);
  session.close();
  for (const target of [document, window, connection]) assert.ok([...target.listeners.values()].every((items) => !items.size));
});

test("solar failures and malformed context hide the graph and permit an explicit retry", async (t) => {
  const { session, requests, updates } = solarSessionHarness(t);
  session.update({ sunrise_time: "invalid" });
  requests[0].reject({ code: "invalid_options" });
  await settleSolar();
  assert.deepEqual(updates.at(-1), { context: null, status: "invalidSolar" });
  const retry = session.refresh();
  requests[1].resolve({ ...REFERENCE_CONTEXT, timezone: undefined });
  await retry;
  assert.deepEqual(updates.at(-1), { context: null, status: "unavailable" });
  const retryAgain = session.refresh();
  requests[2].resolve(REFERENCE_CONTEXT);
  await retryAgain;
  assert.equal(updates.at(-1).status, "ready");
  for (const invalid of [null, {}, { ...REFERENCE_CONTEXT, events: [] }, { ...REFERENCE_CONTEXT, refresh_after: -1 },
    { ...REFERENCE_CONTEXT, timezone: "missing/timezone" }, { ...REFERENCE_CONTEXT, day_end: 0 }]) {
    assert.equal(rawBrightnessTimingPoints("default", 0, 0, invalid), null);
    assert.equal(rawTemperatureTimingPoints("default", 0, 0, false, invalid), null);
  }
});

test("real-day chart uses shifted HA labels and date without rebuilding controls, and hides missing context", (t) => {
  const { panel, input } = brightnessSettingsHarness(t, "linear");
  const figure = panel.shadowRoot.querySelector("figure");
  const originalInput = input("brightness_mode_time_light_minutes");
  const start = Date.parse("2026-10-13T11:00:00Z") / 1000;
  const context = shiftContext(REFERENCE_CONTEXT, start, { local_date: "2026-10-14", timezone: "Pacific/Auckland",
    sunrise: { timestamp: start + 300 * 60, local_time: "05:00" },
    sunset: { timestamp: start + 1100 * 60, local_time: "18:20" },
  });
  context.events = context.events.map((event) => ({ ...event,
    timestamp: event.timestamp + (event.event === "sunrise" ? -60 : event.event === "sunset" ? 20 : 0) * 60 }));
  panel.timingChart.update(panel.values, context, "ready");
  assert.equal(figure.querySelector("figcaption").textContent, "Oct 14, 2026 · Pacific/Auckland");
  assert.deepEqual(figure.querySelectorAll("time").map((node) => node.textContent), ["05:00", "18:20"]);
  assert.equal(figure.querySelector(".timing-event").getAttribute("d"), "M125 0 V176 M458.3333333333333 0 V176");
  assert.equal(input("brightness_mode_time_light_minutes"), originalInput);
  panel.timingChart.update(panel.values, null, "unavailable");
  assert.equal(figure.querySelector(".timing-plot").hidden, true);
  assert.equal(figure.querySelector(".timing-curve").getAttribute("d"), null);
  assert.equal(figure.querySelector("linearGradient").children.length, 0);
  assert.equal(figure.querySelector(".timing-retry").hidden, false);
  assert.equal(figure.querySelector("figcaption").hidden, true);
});

test("DST days scale the full 23 or 25 elapsed hours and retain exact zero-window edges", () => {
  for (const hours of [23, 25]) {
    const context = { ...REFERENCE_CONTEXT, day_end: hours * 3600 };
    assert.equal(validSolarContext(context), true);
    const points = rawBrightnessTimingPoints("linear", 0, 0, context);
    assert.deepEqual(points.slice(1, 5), [[360, 0], [360, 1], [1080, 1], [1080, 0]]);
    assert.deepEqual(points.at(-1), [hours * 60, 0]);
    assert.equal(rawTemperatureTimingPoints("default", 0, 0, false, context).at(-1)[0], hours * 60);
  }
});

test("asymmetric solar noon and midnight drive the runtime half-parabolas and nearest events", () => {
  const context = structuredClone(REFERENCE_CONTEXT);
  for (const event of context.events) {
    if (event.event === "solar_noon") event.timestamp += 60 * 60;
    if (event.event === "solar_midnight") event.timestamp += 30 * 60;
  }
  // At midnight the integration reaches minimum; halfway to sunrise its
  // nighttime parabola is .25. Noon at13:00 is deliberately not12:00.
  assert.equal(rawBrightnessTimingValue(30, "default", 0, 0, context), 0);
  assert.equal(rawBrightnessTimingValue(195, "default", 0, 0, context), 0.25);
  const points = rawTemperatureTimingPoints("default", 0, 0, false, context);
  assert.equal(points.find(([minute]) => minute === 780)[1], 1);
  assert.ok(points.find(([minute]) => minute === 720)[1] < 1);
  assert.equal(points.find(([minute]) => minute === 360)[1], 0);
  // A deliberately short day exposes the switch of nearest event at noon.
  const short = structuredClone(REFERENCE_CONTEXT);
  for (const event of short.events) {
    if (event.event === "sunrise") event.timestamp += 300 * 60;
    if (event.event === "sunset") event.timestamp -= 300 * 60;
    if (event.event === "solar_noon") event.timestamp += 20 * 60;
  }
  short.sunrise.timestamp += 300 * 60;
  short.sunset.timestamp -= 300 * 60;
  const edge = rawBrightnessTimingPoints("tanh", 240, 0, short).filter(([minute]) => minute === 740);
  assert.equal(edge.length, 2);
  assert.ok(Math.abs(edge[0][1] - edge[1][1]) > 0.05);
});


test("a missing solar response times out, offers retry and ignores the eventual old response", async (t) => {
  const { session, requests, updates } = solarSessionHarness(t);
  session.update({});
  t.mock.timers.tick(9999);
  await settleSolar();
  assert.equal(updates.at(-1).status, "loading");
  t.mock.timers.tick(1);
  await settleSolar();
  assert.deepEqual(updates.at(-1), { context: null, status: "unavailable" });
  const retry = session.refresh();
  requests[0].resolve(REFERENCE_CONTEXT);
  await settleSolar();
  assert.equal(updates.at(-1).status, "loading");
  const fresh = shiftContext(REFERENCE_CONTEXT, 86400, { local_date: "1970-01-02" });
  requests[1].resolve(fresh);
  await retry;
  assert.equal(updates.at(-1).context, fresh);
});

test("night overlay preserves absolute daytime100/night80 and the exact five-second fade endpoints", () => {
  const points = nightTimingPoints(NIGHT_VALUES, nightContext());
  const at = (seconds) => points.filter(([minute]) => Math.abs(minute * 60 - seconds) < 1e-7).at(-1)?.slice(1);
  assert.deepEqual(at(75600), [1, 255, 138, 26]);
  assert.deepEqual(at(75605), [0.8, 255, 0, 0]);
  assert.deepEqual(at(86400), [0.8, 255, 0, 0]);
  assert.ok(points.every(([minute, brightness]) => minute >= 0 && minute <= 1440 && brightness >= 0.8 && brightness <= 1));
});

test("zero night transition creates coincident sharp color and brightness edges without broadening them", () => {
  const points = nightTimingPoints({ ...NIGHT_VALUES, transition: 0 }, nightContext([{ start: 36000, end: 39600 }]));
  const entering = points.filter(([minute]) => minute === 600);
  const leaving = points.filter(([minute]) => minute === 660);
  assert.equal(entering.length, 2);
  assert.equal(leaving.length, 2);
  assert.equal(entering[0][1], 1);
  assert.deepEqual(entering[1].slice(1), [0.8, 255, 0, 0]);
  assert.deepEqual(leaving[0].slice(1), [0.8, 255, 0, 0]);
  assert.equal(leaving[1][1], 1);
});

test("night entry at a zero-window sunset fades from the preceding day value instead of jumping to the new normal minimum", () => {
  for (const mode of ["linear", "tanh"]) {
    const context = nightContext([{ start: 64800, end: 108000 }], { sleep_mode: "sun" });
    const values = { ...NIGHT_VALUES, brightness_mode: mode, min_brightness: 20, sleep_brightness: 10,
      apply_temperature_curve: true, transition: 60, sleep_activation_mode: "sun" };
    const points = nightTimingPoints(values, context);
    assert.deepEqual(points.filter(([minute]) => minute === 1080).at(-1), [1080, 1, 255, 255, 255]);
    assert.deepEqual(points.filter(([minute]) => minute === 1081).at(-1), [1081, 0.1, 255, 0, 0]);
  }
});

test("carry-in fade crosses midnight continuously and does not turn the previous night's tail into daytime", () => {
  const points = nightTimingPoints({ ...NIGHT_VALUES, transition: 120 }, nightContext([{ start: -60, end: 21600 }]));
  assert.deepEqual(points[0], [0, 0.9, 255, 69, 13]);
  assert.deepEqual(points.find(([minute]) => minute === 1), [1, 0.8, 255, 0, 0]);
  assert.equal(points.filter(([minute]) => minute === 362).at(-1)?.[1], 1);
});

test("long carry-in fade starts from the real previous evening curve rather than the nearest midnight", () => {
  const context = nightContext([{ start: -89 * 60, end: 21600 }], {
    sunrise: { timestamp: 3600, local_time: "01:00" }, sunset: { timestamp: 75600, local_time: "21:00" },
    events: [-2, -1, 0, 1, 2].flatMap((day) => [["sunrise", 1], ["solar_noon", 11], ["sunset", 21], ["solar_midnight", 23]]
      .map(([event, hour]) => ({ event, timestamp: (day * 24 + hour) * 3600 }))),
  });
  const values = { ...NIGHT_VALUES, min_brightness: 1, max_brightness: 100, sleep_brightness: 1,
    brightness_mode_time_dark_minutes: 240, transition: 6553 };
  const before = (1 + 99 * ((240 - 91) / 240)) / 100;
  const expected = before + (0.01 - before) * (89 * 60 / 6553);
  const points = nightTimingPoints(values, context);
  assert.ok(Math.abs(points[0][1] - expected) < 1e-12);
});

test("an interrupted long entry and exit retain their original evening source through midnight", () => {
  const context = nightContext([{ start: -10800, end: -5400 }, { start: 75600, end: 81000 }], {
    sunrise: { timestamp: 3600, local_time: "01:00" }, sunset: { timestamp: 64800, local_time: "18:00" },
    // The 4-hour context includes the sunset before the previous 21:00 entry.
    events: [["sunset", -6], ["solar_midnight", -2.5], ["sunrise", 1], ["solar_noon", 9.5],
      ["sunset", 18], ["solar_midnight", 21.5], ["sunrise", 25], ["solar_noon", 33.5]]
      .map(([event, hour]) => ({ event, timestamp: hour * 3600 })),
  });
  const values = { ...NIGHT_VALUES, min_brightness: 1, max_brightness: 100, sleep_brightness: 1,
    brightness_mode_time_dark_minutes: 240, transition: 6553, apply_temperature_curve: true };
  const midnight = nightTimingPoints(values, context)[0];
  assert.equal(midnight[0], 0);
  assert.ok(Math.abs(midnight[1] - 0.6948407822956411) < 1e-12);
});

test("a short night interrupted before its fade finishes resumes from the current intermediate brightness", () => {
  const points = nightTimingPoints({ ...NIGHT_VALUES, transition: 120 }, nightContext([{ start: 36000, end: 36060 }]));
  const boundary = points.filter(([minute]) => minute === 601).at(-1);
  assert.ok(Math.abs(boundary[1] - 0.9) < 1e-9);
  assert.equal(points.filter(([minute]) => minute === 603).at(-1)?.[1], 1);
  assert.ok(points.every((point) => point.every(Number.isFinite)));
});

test("leaving night returns to the contemporaneous linear or sigmoid ramp rather than daytime maximum", () => {
  for (const mode of ["linear", "tanh"]) {
    const values = { ...NIGHT_VALUES, brightness_mode: mode, min_brightness: 20, max_brightness: 100,
      brightness_mode_time_light_minutes: 60, brightness_mode_time_dark_minutes: 166, transition: 120 };
    const context = nightContext([{ start: -10800, end: 300 * 60 }]);
    const points = nightTimingPoints(values, context);
    const restored = points.filter(([minute]) => minute === 302).at(-1)[1];
    const expected = 0.2 + 0.8 * rawBrightnessTimingValue(302, mode, 60, 166, context);
    assert.ok(Math.abs(restored - expected) < 1e-12);
    assert.ok(restored < 0.8, `${mode} must restore the early ramp, even below sleep brightness`);
    assert.notEqual(restored, 1);
  }
});

test("all-day night is constant and long transitions remain finite at both daily edges", () => {
  const allDay = nightTimingPoints(NIGHT_VALUES, nightContext([{ start: -172800, end: 259200 }], { sleep_all_day: true }));
  assert.ok(allDay.every(([, ...value]) => JSON.stringify(value) === JSON.stringify([0.8, 255, 0, 0])));
  for (const mode of ["default", "linear", "tanh"]) {
    const points = nightTimingPoints({ ...NIGHT_VALUES, brightness_mode: mode, transition: 6553 }, nightContext([
      { start: -172799, end: -86401 }, { start: -60, end: 60 }, { start: 86399, end: 86401 },
    ]));
    assert.ok(points.length > 1);
    assert.ok(points.every((point) => point.every(Number.isFinite)));
  }
});

test("night CCT uses an independent warm/cool approximation and RGB honors the selected triplet", () => {
  assert.deepEqual(sleepGraphColor({ sleep_rgb_color: [11, 22, 33] }), [11, 22, 33]);
  const warm = sleepGraphColor({ sleep_rgb_or_color_temp: "color_temp", sleep_color_temp: 1500, min_color_temp: 4000 });
  const cool = sleepGraphColor({ sleep_rgb_or_color_temp: "color_temp", sleep_color_temp: 10000 });
  assert.equal(warm[2], 0);
  assert.ok(warm[0] > warm[1]);
  assert.ok(cool[2] > cool[0]);
  for (const values of [{ sleep_rgb_color: [256, 0, 0] }, { sleep_rgb_color: [255, 0] }]) {
    assert.equal(sleepGraphColor(values), null);
  }
});

test("invalid night targets and unmerged overlapping windows cannot render a misleading valid chart", () => {
  for (const changes of [{ sleep_brightness: -1 }, { min_brightness: 101 }, { transition: -1 }, { transition: 6554 },
    { transition: Infinity }, { transition: null }, { sleep_brightness: null }, { min_brightness: null }, { sleep_rgb_color: null },
    { sleep_rgb_color: [NaN, 0, 0] }, { sleep_rgb_or_color_temp: "color_temp", sleep_color_temp: 999 }, { sleep_rgb_or_color_temp: "color_temp", sleep_color_temp: null }]) {
    assert.equal(nightTimingPoints({ ...NIGHT_VALUES, ...changes }, nightContext()), null);
  }
  assert.equal(nightTimingPoints(NIGHT_VALUES, nightContext([{ start: 100, end: 300 }, { start: 200, end: 400 }])), null);
});

test("shared graph has arithmetic midpoint hours above and scheduled red boundaries with times below", (t) => {
  installDocument(t);
  const copy = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get.call({ language: "en" }).timingDiagram;
  const chart = brightnessTimingDiagram(copy);
  chart.update(NIGHT_VALUES, nightContext([{ start: -10800, end: 21600 }, { start: 75600, end: 108000 }]), "ready");
  assert.equal(chart.figure.querySelectorAll("svg").length, 1);
  assert.deepEqual(chart.figure.querySelector(".timing-bounds").children.map((node) => node.textContent), ["100 %", "0 %"]);
  assert.deepEqual(chart.figure.querySelector(".timing-midpoint-labels").children.map((node) => [node.textContent, node.style.left]), [["00:00", "0%"], ["12:00", "50%"]]);
  const labels = chart.figure.querySelector(".timing-night-labels");
  assert.deepEqual(labels.children.map((label) => [label.children[0].textContent, label.querySelector("time").textContent]), [["OUT · Night", "06:00"], ["IN · Night", "21:00"]]);
  assert.deepEqual(labels.children.map((label) => label.getAttribute("aria-label")), ["Sleep mode ends 06:00", "Sleep mode starts 21:00"]);
  assert.equal(chart.figure.querySelector(".timing-night-event").getAttribute("d"), "M150 0 V176 M525 0 V176");
  assert.match(chart.figure.querySelector(".timing-night-details").querySelector("p").textContent, /General transition: 5 s.*60 s/);
  assert.equal(chart.figure.querySelector(".timing-night-details").querySelector("summary").textContent, "Transitions");
  assert.equal(chart.figure.querySelectorAll(".timing-curve").length, 1);
  chart.update({ ...NIGHT_VALUES, sleep_activation_mode: "sun" }, nightContext([{ start: -21600, end: 21600 }], { sleep_mode: "sun" }), "ready");
  assert.equal(labels.hidden, true, "solar night must not duplicate sunrise/sunset labels");
  chart.update({ ...NIGHT_VALUES, sleep_activation_mode: "disabled" }, nightContext([], { sleep_mode: "disabled" }), "ready");
  assert.equal(chart.figure.querySelector(".timing-night-details").hidden, true);
  assert.deepEqual(chart.figure.querySelector(".timing-bounds").children.map((node) => node.textContent), ["Max.", "Min."]);
});

test("repeated scheduled boundaries add rows only when their actual label widths cannot fit", (t) => {
  installDocument(t);
  const copy = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get.call({ language: "es" }).timingDiagram;
  const chart = brightnessTimingDiagram(copy, "es");
  chart.figure.querySelector(".timing-plot-area").clientWidth = 180;
  chart.update(NIGHT_VALUES, nightContext([{ start: 3600, end: 3660 }, { start: 7200, end: 7260 }]));
  const labels = chart.figure.querySelector(".timing-night-labels");
  assert.equal(labels.children.length, 4);
  assert.deepEqual(labels.children.map((label) => label.style.top), ["0px", "0px", "38px", "38px"]);
  assert.equal(labels.children[0].children[0].textContent, "IN · Noche");
  assert.equal(labels.children[0].getAttribute("aria-label"), "Inicio modo noche 01:00");
  assert.equal(labels.style.height, "76px");
});

test("two close IN/OUT labels share one baseline on desktop and mobile without overlap or overflow", () => {
  for (const width of [180, 225, 600]) {
    for (const hours of [[0, 5], [5, 15 + 40 / 60], [23, 23.5]]) {
      const positions = layoutTimelineLabels(hours.map((hour) => ({ anchor: hour / 24 * width, width: 70 })), width);
      assert.deepEqual(positions.map((item) => item.row), [0, 0]);
      assert.ok(positions[0].left >= 0);
      assert.ok(positions[1].left >= positions[0].left + positions[0].width + 8);
      assert.ok(positions[1].left + positions[1].width <= width);
    }
  }
});

test("night-label layout measures width changes and disconnects its single observer when closed", (t) => {
  installDocument(t);
  const previous = globalThis.ResizeObserver;
  const observers = [];
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.closed = true; }
  };
  t.after(() => { if (previous === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = previous; });
  const copy = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get.call({ language: "es" }).timingDiagram;
  const chart = brightnessTimingDiagram(copy, "es");
  const area = chart.figure.querySelector(".timing-plot-area");
  area.clientWidth = 225;
  chart.update(NIGHT_VALUES, nightContext([{ start: 0, end: 18000 }]));
  const labels = chart.figure.querySelector(".timing-night-labels");
  assert.deepEqual(labels.children.map((label) => label.style.top), ["0px", "0px"]);
  assert.equal(observers.length, 1);
  assert.equal(observers[0].target, area);
  area.clientWidth = 140;
  observers[0].callback();
  assert.deepEqual(labels.children.map((label) => label.style.top), ["0px", "38px"]);
  chart.close();
  assert.equal(observers[0].closed, true);
});

test("only automatic mode and hours trigger solar reads; night colors, brightness and transition stay local", async (t) => {
  const { session, requests } = solarSessionHarness(t);
  const settings = { sleep_activation_mode: "schedule", sleep_schedule_start: "21:00:00", sleep_schedule_end: "06:00:00" };
  session.update({ ...NIGHT_VALUES, ...settings });
  assert.deepEqual(requests[0].message.settings, settings);
  requests[0].resolve(nightContext());
  await settleSolar();
  session.update({ ...NIGHT_VALUES, ...settings, sleep_brightness: 20, sleep_rgb_color: [0, 0, 255], transition: 60 });
  t.mock.timers.tick(300);
  assert.equal(requests.length, 1);
  session.update({ ...NIGHT_VALUES, ...settings, min_brightness: 0, max_brightness: 0, sleep_brightness: 0 });
  t.mock.timers.tick(300);
  assert.equal(requests.length, 1, "invalid brightness drafts must not trigger a WS request or a light command");
  session.update({ ...NIGHT_VALUES, ...settings, sleep_schedule_start: "22:00:00" });
  t.mock.timers.tick(300);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].message.settings.sleep_schedule_start, "22:00:00");
});
