/** Language changes exercise the real controller without Home Assistant or lights. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const oldElement = globalThis.HTMLElement, oldRegistry = globalThis.customElements;
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = { querySelector: () => null }; } };
globalThis.customElements = { get: () => undefined, define() {} };
const { CircadianCalibrationPanel, panelLanguage, panelErrorMessage, localizeValidation } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
if (oldElement === undefined) delete globalThis.HTMLElement; else globalThis.HTMLElement = oldElement;
if (oldRegistry === undefined) delete globalThis.customElements; else globalThis.customElements = oldRegistry;

const copies = Object.fromEntries(["en", "es"].map((language) => [language,
  Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get.call({ language })]));
const translations = Object.fromEntries(await Promise.all(["en", "es"].map(async (language) => {
  const file = JSON.parse(await readFile(new URL(`../custom_components/circadian_pro/translations/${language}.json`, import.meta.url), "utf8"));
  return [language, { ...file.options, selector: file.selector, config: file.config }];
})));

function leaves(value, prefix = "", found = {}) {
  for (const [key, text] of Object.entries(value)) {
    if (typeof text === "string") found[`${prefix}.${key}`] = text;
    else leaves(text, `${prefix}.${key}`, found);
  }
  return found;
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function flush() { for (let index = 0; index < 20; index++) await Promise.resolve(); }
function harness(language = "es") {
  const panel = new CircadianCalibrationPanel(), requests = [], rendered = [];
  Object.assign(panel, { initialized: true, isConnected: true, displayLanguage: language, translations: translations[language], translationsReady: true });
  panel.render = () => rendered.push({ language: panel.language, copy: panel.copy, translations: panel.translations, values: { ...panel.values } });
  panel.updateControls = () => {};
  const hass = (nextLanguage) => ({ language: nextLanguage, callWS: (message) => {
    const pending = deferred(); requests.push({ message, ...pending }); return pending.promise;
  } });
  panel._hass = hass(language);
  return { panel, requests, rendered, change: (nextLanguage) => { panel.hass = hass(nextLanguage); } };
}

test("Spanish region codes are normalized and every other language falls back to English", () => {
  for (const language of ["es", "es-ES", "es-MX", "ES_ar"]) assert.equal(panelLanguage(language), "es");
  for (const language of ["en", "en-GB", "fr", "de", "estonian", "", null, undefined]) assert.equal(panelLanguage(language), "en");
});

test("both panel dictionaries have identical nonempty keys and interpolation placeholders", () => {
  const en = leaves(copies.en), es = leaves(copies.es);
  assert.deepEqual(Object.keys(en).sort(), Object.keys(es).sort());
  for (const [key, text] of Object.entries(en)) {
    assert.ok(text.trim() && es[key].trim(), key);
    assert.deepEqual(text.match(/\{\w+\}/g) || [], es[key].match(/\{\w+\}/g) || [], key);
  }
});

test("websocket failures display translated actionable text without raw server messages", () => {
  const codes = ["not_loaded", "not_found", "invalid_options", "solar_unavailable", "preview_unavailable", "preview_timeout",
    "preview_no_lights", "preview_lights_busy", "preview_editor_busy", "preview_expired", "preview_unsupported_light", "unauthorized"];
  for (const language of ["es", "en"]) {
    for (const code of codes) {
      assert.equal(panelErrorMessage(copies[language], translations[language], { code, message: "Raw untranslated text" }), copies[language].apiErrors[code]);
      assert.notEqual(copies[language].apiErrors[code], copies[language === "es" ? "en" : "es"].apiErrors[code]);
    }
    assert.equal(panelErrorMessage(copies[language], translations[language], new Error("server trace with private data")), copies[language].error);
    assert.equal(panelErrorMessage(copies[language], translations[language], { reason: "new_unknown_error" }), copies[language].error);
    assert.equal(panelErrorMessage(copies[language], translations[language], new Error(copies[language].invalid)), copies[language].invalid);
    assert.equal(panelErrorMessage(copies[language], translations[language], { reason: "yaml_configuration" }), translations[language].abort.yaml_configuration);
  }
});

test("language switches atomically after translations load, preserving edits made while waiting and the active preview", async () => {
  const { panel, change, requests, rendered } = harness();
  let draft = { cal_rgb_neutral_r: 170 }, writes = 0;
  panel.flow = { current: { step_id: "cal_neutral" }, drafts: {} };
  panel.values = { cal_rgb_neutral_r: 100 };
  panel.settings = { lights: ["light.example"], cal_rgb_neutral_r: 100 };
  panel.shadowRoot.querySelector = () => ({});
  panel.readValues = (validate) => { assert.equal(validate, false); return { ...draft }; };
  const preview = { enabled: true, refresh() { writes++; }, setEnabled() { writes++; } };
  panel.session = preview;
  panel.message = copies.es.created;
  panel.error = copies.es.apiErrors.preview_expired;
  change("en-GB");
  assert.equal(panel.language, "es");
  assert.equal(panel.copy.next, "Siguiente");
  assert.equal(panel.translations, translations.es);
  assert.equal(rendered.length, 0);
  draft.cal_rgb_neutral_r = 205;
  requests[0].resolve(translations.en);
  await flush();
  assert.equal(panel.language, "en");
  assert.equal(panel.translations, translations.en);
  assert.equal(panel.values.cal_rgb_neutral_r, 205);
  assert.equal(panel.settings.cal_rgb_neutral_r, 205);
  assert.equal(panel.message, copies.en.created);
  assert.equal(panel.error, copies.en.apiErrors.preview_expired);
  assert.equal(panel.session, preview);
  assert.equal(preview.enabled, true);
  assert.equal(writes, 0);
  assert.equal(rendered.length, 1);
});

test("translations loaded during a pending save wait for the action to finish", async () => {
  const { panel, change, requests, rendered } = harness();
  const save = deferred();
  const action = panel.action(() => save.promise);
  change("en");
  requests[0].resolve(translations.en);
  await flush();
  assert.equal(panel.language, "es");
  assert.equal(rendered.length, 0);
  save.resolve();
  await action;
  assert.equal(panel.language, "en");
  assert.equal(rendered.length, 1);
});

test("switching back before a delayed translation response cannot apply the abandoned language", async () => {
  const { panel, change, requests, rendered } = harness();
  change("en");
  change("es-MX");
  requests[0].resolve(translations.en);
  await flush();
  assert.equal(panel.language, "es");
  assert.equal(panel.translations, translations.es);
  assert.equal(rendered.length, 0);
  change("en");
  requests[1].resolve(translations.en);
  await flush();
  assert.equal(panel.language, "en");
  assert.equal(panel.translations, translations.en);
});

test("translations arriving during Next never copy stale controls over the new step's values", async () => {
  const { panel, change, requests } = harness();
  const advance = deferred();
  panel.flow = { current: { step_id: "init" } };
  panel.settings = {};
  panel.values = { lights: ["light.example"] };
  panel.shadowRoot.querySelector = () => ({});
  panel.readValues = () => { throw new Error("The old form must not be read after advancing"); };
  const action = panel.action(async () => {
    await advance.promise;
    panel.flow.current.step_id = "white_channels";
    panel.values = { white_w_kelvin: 3000, white_cw_kelvin: 6500 };
  });
  change("en");
  requests[0].resolve(translations.en);
  await flush();
  advance.resolve();
  await action;
  assert.equal(panel.language, "en");
  assert.deepEqual(panel.values, { white_w_kelvin: 3000, white_cw_kelvin: 6500 });
});

test("a language load failure keeps the complete old language and does not retry on every state update", async () => {
  const { panel, change, requests, rendered } = harness();
  change("en");
  requests[0].reject(new Error("Network timeout"));
  await flush();
  assert.equal(panel.language, "es");
  assert.equal(panel.translations, translations.es);
  assert.equal(panel.error, copies.es.languageError);
  for (let index = 0; index < 30; index++) change("en");
  assert.equal(requests.length, 1);
  assert.equal(rendered.length, 0);
});

test("same-language regional changes and Home Assistant state updates do not fetch translations", () => {
  const { change, requests } = harness("es");
  for (const language of ["es", "ES", "es-MX", "es_ES", "es"]) change(language);
  assert.equal(requests.length, 0);
});

test("a failed initial language request blocks untranslated forms and creation until a successful retry", async (t) => {
  const originalWindow = globalThis.window;
  globalThis.window = { location: { search: "?config_entry=example" } };
  t.after(() => { if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow; });
  let fail = true;
  const writes = [], entry = { entry_id: "example", title: "My untouched profile", settings: {} };
  const panel = new CircadianCalibrationPanel();
  Object.assign(panel, {
    isConnected: true, render() {}, updateControls() {},
    _hass: {
      language: "es", user: { is_admin: true }, states: {},
      callWS: async (message) => {
        if (message.type.endsWith("/entries")) return { entries: [entry], configured_profiles: 1 };
        if (message.type.endsWith("/translations")) {
          if (fail) throw new Error("Translations offline");
          return translations.es;
        }
        return {};
      },
      callApi: async (...args) => { writes.push(args); return { type: "form", flow_id: "flow", step_id: "init", data_schema: [] }; },
    },
  });
  await panel.initialize();
  assert.equal(panel.catalogLoaded, true);
  assert.equal(panel.translationsReady, false);
  assert.equal(panel.flow, undefined);
  assert.equal(panel.error, copies.es.languageError);
  assert.deepEqual(writes, []);
  await panel.start("example");
  panel.openNewProfile();
  assert.equal(panel.newProfileOpen, false);
  assert.deepEqual(writes, []);
  fail = false;
  await panel.action(() => panel.loadTranslations());
  await flush();
  assert.equal(panel.translationsReady, true);
  assert.equal(panel.translations, translations.es);
  assert.equal(panel.flow.current.step_id, "init");
  assert.equal(writes.length, 1, "the pending deep link opens only after translations are ready");
  panel.catalog.stop();
});

test("native field validation follows the Home Assistant language instead of the browser language", () => {
  for (const language of ["es", "en"]) {
    for (const [flag, key] of [["valueMissing", "required"], ["badInput", "number"], ["rangeUnderflow", "minimum"], ["rangeOverflow", "maximum"], ["stepMismatch", "step"]]) {
      const messages = [];
      const input = { min: "1", max: "100", step: "1", validity: { valid: false, [flag]: true }, setCustomValidity: (message) => messages.push(message) };
      localizeValidation(input, copies[language]);
      assert.equal(messages[0], "", "clear the previous custom error before reading native validity");
      assert.equal(messages[1], copies[language].validation[key].replaceAll("{min}", "1").replaceAll("{max}", "100").replaceAll("{step}", "1"));
      input.validity = { valid: true };
      localizeValidation(input, copies[language]);
      assert.equal(messages.at(-1), "");
    }
  }
});
