/** Profile-list ordering and row actions use metadata only, never change lights. */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../custom_components/circadian_pro/frontend/calibration-panel.js", import.meta.url), "utf8");
const previousHTMLElement = globalThis.HTMLElement;
const previousCustomElements = globalThis.customElements;
globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => undefined, define: () => {} };
const { CircadianCalibrationPanel, sortedProfileEntries } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
if (previousHTMLElement === undefined) delete globalThis.HTMLElement;
else globalThis.HTMLElement = previousHTMLElement;
if (previousCustomElements === undefined) delete globalThis.customElements;
else globalThis.customElements = previousCustomElements;

const ids = (entries) => entries.map((entry) => entry.entry_id);

test("recent profiles use modified time, fall back to creation time and put unknown dates last", () => {
  const entries = [
    { entry_id: "missing", title: "Alpha", modified_at: null },
    { entry_id: "old", title: "Old", modified_at: "2026-10-12T09:00:00Z" },
    { entry_id: "new", title: "New", created_at: "2026-10-13T10:01:00Z" },
    { entry_id: "edited", title: "Edited", modified_at: "2026-10-13T10:00:00Z", created_at: "2026-10-01T10:00:00Z" },
    { entry_id: "invalid", title: "Invalid", modified_at: "broken date", created_at: "2026-10-13T09:00:00Z" },
    { entry_id: "unknown", title: "Zulu", modified_at: "", created_at: "invalid" },
  ];
  const before = structuredClone(entries);
  assert.deepEqual(ids(sortedProfileEntries(entries)), ["new", "edited", "invalid", "old", "missing", "unknown"]);
  assert.deepEqual(entries, before, "sorting must not mutate catalog order or metadata");
});

test("recent sorting respects timezone offsets and resolves equal timestamps deterministically", () => {
  const entries = [
    { entry_id: "z", title: "Profile C", modified_at: "2026-10-13T10:00:00Z" },
    { entry_id: "later", title: "Más tarde", modified_at: "2026-10-13T11:01:00+01:00" },
    { entry_id: "a", title: "Profile C", modified_at: "2026-10-13T11:00:00+01:00" },
    { entry_id: "office", title: "Profile B", modified_at: "2026-10-13T10:00:00Z" },
  ];
  assert.deepEqual(ids(sortedProfileEntries(entries, "recent", "es")), ["later", "office", "a", "z"]);
});

test("A–Z and Z–A use localized names and numeric ordering independently of modified dates", () => {
  const entries = [
    { entry_id: "z", title: "Zona 2", modified_at: "2026-10-13T10:00:00Z" },
    { entry_id: "a", title: "Ático" }, { entry_id: "z10", title: "Zona 10" }, { entry_id: "b", title: "Baño" },
  ];
  assert.deepEqual(ids(sortedProfileEntries(entries, "name_asc", "es")), ["a", "b", "z", "z10"]);
  assert.deepEqual(ids(sortedProfileEntries(entries, "name_desc", "es")), ["z10", "z", "b", "a"]);
});

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  matches(selector) {
    if (selector.includes(",")) return selector.split(",").some((part) => this.matches(part.trim()));
    return selector.startsWith("#") ? this.id === selector.slice(1) : selector.startsWith(".")
      ? this.className?.split(" ").includes(selector.slice(1)) : this.tag === selector;
  }
  querySelector(selector) { return this.descendants().find((node) => node.matches(selector)); }
  querySelectorAll(selector) { return this.descendants().filter((node) => node.matches(selector)); }
  focus() { this.focused = true; }
}

function panelHarness(t, entries) {
  const previousDocument = globalThis.document;
  globalThis.document = { createElement: (tag) => new Node(tag) };
  t.after(() => { if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument; });
  const opened = [];
  const panel = {
    shadowRoot: new Node("shadow"), entries, profileSort: "recent", language: "es", translations: {},
    catalog: { subscribed: true }, catalogLoaded: true, catalogError: "", error: "", _hass: { user: { is_admin: true } },
    copy: {
      title: "Circadian Pro", newProfile: "Nuevo perfil", start: "Configurar y calibrar", profile: "Perfil", profiles: "Perfiles",
      actions: "Acciones", sortProfiles: "Ordenar por", sortRecent: "Modificados recientemente", sortNameAsc: "Nombre: A–Z", sortNameDesc: "Nombre: Z–A",
      empty: "No hay perfiles", firstProfile: "Crear primer perfil", loading: "Cargando…", catalogError: "No se pudo actualizar la lista",
      noAvailableProfiles: "No hay perfiles disponibles",
      refreshProfiles: "Actualizar lista", retryLoading: "Reintentar carga",
    },
    start: (entryId) => opened.push(entryId), openNewProfile: () => opened.push("new-profile"),
    action: async (job) => job(),
  };
  panel.render = CircadianCalibrationPanel.prototype.render.bind(panel);
  panel.updateControls = CircadianCalibrationPanel.prototype.updateControls.bind(panel);
  panel.applyEntries = CircadianCalibrationPanel.prototype.applyEntries.bind(panel);
  panel.catalogFailure = CircadianCalibrationPanel.prototype.catalogFailure.bind(panel);
  panel.render();
  return { panel, opened, rows: () => panel.shadowRoot.querySelector("tbody")?.children || [] };
}

test("all profile rows are visible with accessible configuration actions bound to their own ID", (t) => {
  const { panel, opened, rows } = panelHarness(t, [
    { entry_id: "office", title: "Profile A", modified_at: "2026-10-13T11:00:00Z" },
    { entry_id: "panels", title: "Profile B", modified_at: "2026-10-13T10:00:00Z" },
  ]);
  assert.deepEqual(rows().map((row) => row.children[0].textContent), ["Profile A", "Profile B"]);
  for (const [index, title] of ["Profile A", "Profile B"].entries()) {
    const button = rows()[index].querySelector("button");
    assert.equal(button.attributes["aria-label"], `Configurar y calibrar: ${title}`);
    assert.equal(button.querySelector(".tune-icon").children.length, 3);
    assert.equal(button.querySelector(".tune-icon").attributes["aria-hidden"], "true");
    button.listeners.click();
  }
  assert.deepEqual(opened, ["office", "panels"]);
  assert.equal(panel.shadowRoot.querySelectorAll("select").length, 1, "only sorting remains a dropdown");
  assert.equal(panel.shadowRoot.querySelector("select").id, "profile-sort");
});

test("sorting changes visible rows, retains focus and survives automatic catalog refresh", (t) => {
  const { panel, rows } = panelHarness(t, [
    { entry_id: "z", title: "Profile C" }, { entry_id: "a", title: "Profile A" },
  ]);
  const selector = panel.shadowRoot.querySelector("#profile-sort");
  selector.value = "name_desc";
  selector.listeners.change();
  assert.deepEqual(rows().map((row) => row.children[0].textContent), ["Profile C", "Profile A"]);
  assert.equal(panel.shadowRoot.querySelector("#profile-sort").focused, true);
  panel.applyEntries([{ entry_id: "a", title: "Profile A" }, { entry_id: "m", title: "Profile B" }, { entry_id: "z", title: "Profile C" }]);
  assert.equal(panel.profileSort, "name_desc");
  assert.equal(panel.shadowRoot.querySelector("#profile-sort").value, "name_desc");
  assert.deepEqual(rows().map((row) => row.children[0].textContent), ["Profile C", "Profile B", "Profile A"]);
});

test("recent catalog updates insert new profiles first and remove deleted rows", (t) => {
  const { panel, rows } = panelHarness(t, [{ entry_id: "old", title: "Existing", modified_at: "2026-10-13T09:00:00Z" }]);
  panel.applyEntries([...panel.entries, { entry_id: "new", title: "New", created_at: "2026-10-13T10:00:00Z" }]);
  assert.deepEqual(rows().map((row) => row.children[0].textContent), ["New", "Existing"]);
  panel.applyEntries([panel.entries[1]]);
  assert.deepEqual(rows().map((row) => row.children[0].textContent), ["New"]);
});

test("New profile is below the table, remains available for an empty catalog and opens locally", (t) => {
  const { panel, opened } = panelHarness(t, [{ entry_id: "one", title: "Office" }]);
  const article = panel.shadowRoot.querySelector("article");
  const table = article.querySelector("table");
  const create = article.querySelector(".profile-create");
  assert.ok(article.children.indexOf(create) > article.children.indexOf(table));
  assert.equal(panel.shadowRoot.querySelector("header").querySelector(".profile-create"), undefined);
  create.querySelector("button").listeners.click();
  assert.deepEqual(opened, ["new-profile"]);
  panel.applyEntries({ entries: [], configured_profiles: 0 });
  assert.equal(panel.shadowRoot.querySelector("table"), undefined);
  assert.equal(panel.shadowRoot.querySelector(".profile-create").querySelector("button").textContent, "Crear primer perfil");
});

test("first-profile onboarding requires a successful empty catalog, never a loading or failed request", (t) => {
  const { panel, opened } = panelHarness(t, []);
  panel.catalogLoaded = false;
  panel.busy = true;
  panel.render();
  assert.equal(panel.shadowRoot.querySelector("#catalog-status").textContent, "Cargando…");
  assert.equal(panel.shadowRoot.querySelector(".profile-create"), undefined);
  panel.busy = false;
  panel.catalogFailure();
  assert.equal(panel.shadowRoot.querySelector("#catalog-status"), undefined);
  assert.equal(panel.shadowRoot.querySelector(".profile-create"), undefined);
  assert.equal(panel.shadowRoot.querySelector("#catalog-warning").hidden, false);

  panel.applyEntries({ entries: [], configured_profiles: 0 });
  assert.equal(panel.shadowRoot.querySelector("#catalog-status").textContent, "No hay perfiles");
  assert.equal(panel.shadowRoot.querySelector("#catalog-status").attributes.role, "status");
  panel.shadowRoot.querySelector(".profile-create").querySelector("button").listeners.click();
  assert.deepEqual(opened, ["new-profile"]);

  panel.catalogFailure();
  assert.equal(panel.shadowRoot.querySelector("#catalog-status"), undefined, "a later failure must remove the previous empty-state message");
  assert.equal(panel.shadowRoot.querySelector(".profile-create"), undefined);
  panel.applyEntries([{ entry_id: "first", title: "Office" }]);
  assert.equal(panel.shadowRoot.querySelector("#catalog-status"), undefined);
  assert.equal(panel.shadowRoot.querySelector(".profile-create").querySelector("button").textContent, "Nuevo perfil");
});

test("empty onboarding does not appear while a known created profile is loading", (t) => {
  const { panel } = panelHarness(t, []);
  panel.pendingCreated = { entry_id: "new", title: "Office" };
  panel.render();
  assert.equal(panel.shadowRoot.querySelector("#catalog-status"), undefined);
  assert.equal(panel.shadowRoot.querySelector(".profile-create"), undefined);
  assert.ok(panel.shadowRoot.querySelectorAll("button").some((button) => button.textContent === "Reintentar carga"));
});

test("Spanish and English explain first-profile setup inside the empty panel", (t) => {
  const { panel } = panelHarness(t, []);
  panel.applyEntries({ entries: [], configured_profiles: 0 });
  const getCopy = Object.getOwnPropertyDescriptor(CircadianCalibrationPanel.prototype, "copy").get;
  for (const [language, action, explanation] of [
    ["es", "Crear primer perfil", "Todavía no hay perfiles. Crea el primero para elegir sus luces y configurar la adaptación."],
    ["en", "Create first profile", "There are no profiles yet. Create the first one to choose its lights and configure adaptation."],
  ]) {
    panel.language = language;
    panel.copy = getCopy.call(panel);
    panel.render();
    assert.equal(panel.shadowRoot.querySelector("#catalog-status").textContent, explanation);
    assert.equal(panel.shadowRoot.querySelector(".profile-create").querySelector("button").textContent, action);
  }
});

test("disabled or failed profiles do not become first-profile onboarding when no loaded rows are available", (t) => {
  const { panel } = panelHarness(t, []);
  panel.applyEntries({ entries: [], configured_profiles: 0 });
  assert.equal(panel.shadowRoot.querySelector(".profile-create").querySelector("button").textContent, "Crear primer perfil");
  panel.applyEntries({ entries: [], configured_profiles: 2 });
  assert.equal(panel.configuredProfiles, 2);
  assert.equal(panel.shadowRoot.querySelector("#catalog-status").textContent, "No hay perfiles disponibles");
  assert.equal(panel.shadowRoot.querySelector(".profile-create").querySelector("button").textContent, "Nuevo perfil");
  assert.equal(panel.shadowRoot.querySelector("table"), undefined);
  assert.equal(panel.shadowRoot.querySelector(".integration-link").href, "/config/integrations/integration/circadian_pro");
});

test("a legacy empty array has an unknown configured count and cannot claim there are no profiles", (t) => {
  const { panel } = panelHarness(t, []);
  panel.applyEntries({ entries: [], configured_profiles: 0 });
  panel.applyEntries([]);
  assert.equal(panel.configuredProfiles, null);
  assert.equal(panel.shadowRoot.querySelector("#catalog-status").textContent, "No hay perfiles disponibles");
  assert.equal(panel.shadowRoot.querySelector(".profile-create").querySelector("button").textContent, "Nuevo perfil");
});

test("creation recovery hides New profile and row actions freeze during pending requests", (t) => {
  const { panel } = panelHarness(t, [{ entry_id: "one", title: "Office" }]);
  panel.pendingCreated = { entry_id: "waiting", title: "Waiting for load" };
  panel.render();
  assert.equal(panel.shadowRoot.querySelector(".profile-create"), undefined);
  assert.ok(panel.shadowRoot.querySelector("article").querySelectorAll("button").some((button) => button.textContent === "Reintentar carga"));
  panel.busy = true;
  panel.updateControls();
  assert.equal(panel.shadowRoot.querySelector(".profile-settings").disabled, true);
  assert.equal(panel.shadowRoot.querySelector("#profile-sort").disabled, true);
  panel.busy = false;
  panel.updateControls();
  assert.equal(panel.shadowRoot.querySelector(".profile-settings").disabled, false);
});
