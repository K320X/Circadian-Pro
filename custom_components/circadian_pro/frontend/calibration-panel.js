/** Circadian Pro's calibration wizard. Preview changes never submit the form. */

const API = "circadian_pro/calibration/";
const FLOW = "config/config_entries/options/flow";
const CONFIG_FLOW = "config/config_entries/flow";
const OPTIONAL_TIMES = new Set([
  "sunrise_time", "sunset_time", "min_sunrise_time", "max_sunrise_time", "min_sunset_time", "max_sunset_time",
]);

export function rgbToHex(rgb) {
  if (typeof rgb === "string" && /^#[0-9a-f]{6}$/i.test(rgb)) return rgb;
  return "#" + (Array.isArray(rgb) ? rgb : [255, 56, 0])
    .map((channel) => Math.min(255, Math.max(0, Number(channel))).toString(16).padStart(2, "0")).join("");
}

export function hexToRgb(hex) {
  return [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));
}

export class PreviewSession {
  constructor(callWS, entryId, getDraft, onChange = () => {}) {
    this.callWS = callWS;
    this.entryId = entryId;
    this.getDraft = getDraft;
    this.onChange = onChange;
    this.enabled = false;
    this.revision = 0;
    this.queue = Promise.resolve();
    this.timer = null;
    this.debounce = null;
  }

  enqueue(job) {
    const result = this.queue.then(job);
    this.queue = result.catch(() => {});
    return result;
  }

  clearTimers() {
    clearTimeout(this.timer);
    clearTimeout(this.debounce);
    this.timer = this.debounce = null;
  }

  async setEnabled(enabled) {
    this.clearTimers();
    this.enabled = enabled;
    const revision = ++this.revision;
    this.onChange();
    if (!enabled) {
      return this.enqueue(() => this.callWS({ type: API + "stop", entry_id: this.entryId }));
    }
    try {
      return await this.enqueue(async () => {
        if (!this.enabled || revision !== this.revision) return;
        const draft = this.getDraft();
        const result = await this.callWS({
          type: API + "preview", entry_id: this.entryId,
          settings: draft.settings, kelvin: draft.kelvin,
        });
        if (this.enabled && revision === this.revision) {
          this.timer = setTimeout(() => {
            this.setEnabled(false).catch((error) => this.onChange(error));
          }, (result?.expires_in ?? 60) * 1000);
        }
        return result;
      });
    } catch (error) {
      // An invalid draft can leave the preceding preview active. End it too.
      if (revision === this.revision) {
        await this.setEnabled(false).catch(() => {});
      }
      throw error;
    }
  }

  refresh() {
    if (!this.enabled) return;
    clearTimeout(this.debounce);
    this.debounce = setTimeout(() => {
      this.setEnabled(true).catch((error) => this.onChange(error));
    }, 180);
  }
}

export class CalibrationFlow {
  constructor(callApi, preview, entryId) {
    this.callApi = callApi;
    this.preview = preview;
    this.entryId = entryId;
    this.current = null;
    this.steps = [];
    this.drafts = {};
    this.index = 0;
  }

  async start() {
    this.current = await this.callApi("POST", FLOW, { handler: this.entryId });
    this.steps = [this.current.step_id];
    this.index = 0;
    return this.current;
  }

  async next(data) {
    await this.preview.setEnabled(false);
    const previous = this.current.step_id;
    this.drafts[previous] = { ...data };
    const result = await this.callApi("POST", `${FLOW}/${this.current.flow_id}`, data);
    this.current = result;
    if (result.type === "form" && result.step_id !== previous) {
      this.index++;
      this.steps[this.index] = result.step_id;
      this.steps.length = this.index + 1;
    }
    return result;
  }

  async back(data) {
    if (!this.index) return this.current;
    await this.preview.setEnabled(false);
    this.drafts[this.current.step_id] = { ...data };
    const destination = this.index - 1;
    // Replay only completed steps in a fresh, unsaved options flow. No final
    // step is submitted, so going back cannot save or reload an integration.
    await this.callApi("DELETE", `${FLOW}/${this.current.flow_id}`);
    let result = await this.callApi("POST", FLOW, { handler: this.entryId });
    for (let index = 0; index < destination; index++) {
      result = await this.callApi("POST", `${FLOW}/${result.flow_id}`, this.drafts[result.step_id]);
      if (result.type !== "form" || Object.keys(result.errors || {}).length) break;
    }
    this.current = result;
    this.index = this.steps.indexOf(result.step_id);
    if (this.index < 0) this.index = 0;
    return result;
  }

  async close() {
    await this.preview.setEnabled(false);
    if (this.current?.type === "form") {
      await this.callApi("DELETE", `${FLOW}/${this.current.flow_id}`);
    }
    this.current = null;
  }
}

export class NewProfileFlow {
  constructor(callApi) {
    this.callApi = callApi;
    this.current = null;
    this.name = "";
    this.createdEntryId = null;
    this.uncertain = false;
  }

  async create(name) {
    this.name = name.trim();
    if (!this.name) throw Object.assign(new Error("name_required"), { reason: "name_required" });
    if (this.createdEntryId) throw new Error("This profile has already been created.");
    if (this.uncertain) throw Object.assign(new Error("creation_uncertain"), { reason: "creation_uncertain" });
    if (this.current?.type !== "form" || !["user", "menu"].includes(this.current.step_id)) {
      this.current = await this.callApi("POST", CONFIG_FLOW, {
        handler: "circadian_pro", show_advanced_options: false, source: "user",
      });
    }
    if (this.current.type === "form" && this.current.step_id === "menu") {
      this.current = await this.callApi("POST", `${CONFIG_FLOW}/${this.current.flow_id}`, { action: "new" });
    }
    if (this.current.type === "form" && this.current.step_id === "user") {
      try { this.current = await this.callApi("POST", `${CONFIG_FLOW}/${this.current.flow_id}`, { name: this.name }); }
      catch (error) {
        this.uncertain = true;
        throw Object.assign(new Error("creation_uncertain", { cause: error }), { reason: "creation_uncertain" });
      }
    }
    if (this.current.type === "create_entry") {
      this.createdEntryId = this.current.result?.entry_id;
      if (!this.createdEntryId) {
        this.uncertain = true;
        throw Object.assign(new Error("creation_uncertain"), { reason: "creation_uncertain" });
      }
    }
    return this.current;
  }

  async close() {
    if (this.current?.type === "form") {
      try { await this.callApi("DELETE", `${CONFIG_FLOW}/${this.current.flow_id}`); }
      catch (error) { if (!this.uncertain) throw error; }
    }
    this.current = null;
  }

  async recover() {
    if (!this.uncertain || !this.current?.flow_id) return this.current;
    try {
      const result = await this.callApi("GET", `${CONFIG_FLOW}/${this.current.flow_id}`);
      if (result.type === "form" && ["user", "menu"].includes(result.step_id)) {
        this.current = result;
        this.uncertain = false;
        return result;
      }
    } catch { /* A completed flow is removed by HA; inspect entries separately. */ }
    return null;
  }
}

export async function waitForLoadedEntry(callWS, entryId, {
  delays = [0, 250, 500, 1000, 2000, 3000, 4000, 4000],
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  onEntries = () => {}, cancelled = () => false,
} = {}) {
  for (const delay of delays) {
    if (cancelled()) return null;
    if (delay) await wait(delay);
    if (cancelled()) return null;
    const entries = await callWS({ type: API + "entries" });
    if (cancelled()) return null;
    onEntries(entries);
    const entry = entries.find((item) => item.entry_id === entryId);
    if (entry) return entry;
  }
  return null;
}

export class EntryCatalog {
  constructor(fetchEntries, onEntries, initial = [], onError = () => {}, onConnectionChange = () => {}) {
    this.fetchEntries = fetchEntries;
    this.onEntries = onEntries;
    this.onError = onError;
    this.onConnectionChange = onConnectionChange;
    this.entries = initial;
    this.active = true;
    this.subscribed = false;
    this.generation = 0;
    this.inFlight = null;
    this.timer = null;
    this.dirty = false;
    this.unsubscribe = null;
    this.connection = null;
    this.readyListener = null;
    this.disconnectedListener = null;
    this.subscriptionRevision = 0;
    this.transportRevision = 0;
  }

  async connect(connection) {
    if (typeof connection?.subscribeMessage !== "function") return false;
    this.connection = connection;
    this.readyListener = () => {
      if (!this.active || this.connection !== connection) return;
      // The old socket has gone away. Its unsubscribe ID must never be sent
      // through the new socket, whose command IDs can start again from zero.
      this.transportRevision++;
      this.unsubscribe = null;
      this.subscribe(false);
    };
    this.disconnectedListener = () => {
      if (!this.active || this.connection !== connection) return;
      this.transportRevision++;
      this.subscriptionRevision++;
      this.unsubscribe = null;
      this.subscribed = false;
      this.onConnectionChange();
    };
    connection.addEventListener?.("ready", this.readyListener);
    connection.addEventListener?.("disconnected", this.disconnectedListener);
    if (connection.connected === false) return false;
    return this.subscribe(true);
  }

  async subscribe(initial) {
    const connection = this.connection;
    const generation = this.generation;
    const transportRevision = this.transportRevision;
    const revision = ++this.subscriptionRevision;
    const previous = this.unsubscribe;
    this.unsubscribe = null;
    this.subscribed = false;
    if (previous) Promise.resolve().then(previous).catch(() => {});
    let firstSnapshot = true;
    const current = () => this.active && generation === this.generation
      && revision === this.subscriptionRevision && this.connection === connection;
    try {
      const unsubscribe = await connection.subscribeMessage((changes) => {
        if (!current() || !Array.isArray(changes)) return;
        if (!changes.length || changes.every((change) => change.type === null)) {
          // Initial data is read after subscribing. Subsequent snapshots are
          // reconnects, whose settings must be read again even if IDs match.
          if (firstSnapshot && initial) { firstSnapshot = false; return; }
          firstSnapshot = false;
          this.schedule();
        } else if (changes.some((change) => change.entry?.domain === "circadian_pro")) {
          this.schedule();
        }
      }, { type: "config_entries/subscribe" }, { resubscribe: false });
      if (!current()) {
        // An ACK can arrive late, but cancellation is valid only on the
        // transport that created that subscription.
        if (transportRevision === this.transportRevision) await unsubscribe();
        return false;
      }
      this.unsubscribe = unsubscribe;
      this.subscribed = true;
      this.onConnectionChange();
      if (!initial) this.schedule();
      return true;
    } catch (error) {
      if (current()) this.onError(error);
      return false;
    }
  }

  schedule() {
    if (!this.active) return;
    if (this.inFlight) { this.dirty = true; return; }
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.read().catch(() => {});
    }, 150);
  }

  read() {
    if (!this.active) return Promise.resolve(this.entries);
    if (this.inFlight) return this.inFlight;
    clearTimeout(this.timer);
    this.timer = null;
    this.dirty = false;
    const generation = this.generation;
    const request = Promise.resolve().then(() => this.fetchEntries()).then((entries) => {
      if (!this.active || generation !== this.generation) return this.entries;
      this.entries = entries;
      this.onEntries(entries);
      return entries;
    }).catch((error) => {
      if (this.active && generation === this.generation) this.onError(error);
      throw error;
    }).finally(() => {
      if (this.inFlight !== request) return;
      this.inFlight = null;
      if (this.dirty && this.active) { this.dirty = false; this.schedule(); }
    });
    this.inFlight = request;
    return request;
  }

  stop() {
    this.active = false;
    this.subscribed = false;
    this.generation++;
    this.subscriptionRevision++;
    clearTimeout(this.timer);
    this.timer = null;
    this.dirty = false;
    const unsubscribe = this.unsubscribe;
    this.unsubscribe = null;
    this.connection?.removeEventListener?.("ready", this.readyListener);
    this.connection?.removeEventListener?.("disconnected", this.disconnectedListener);
    this.connection = this.readyListener = this.disconnectedListener = null;
    if (unsubscribe) Promise.resolve().then(unsubscribe).catch(() => {});
  }
}

const COPY = {
  es: {
    title: "Circadian Pro", profile: "Perfil", start: "Configurar y calibrar", menu: "Abrir menú",
    newProfile: "Nuevo perfil", profileName: "Nombre del perfil", createProfile: "Crear perfil", cancel: "Cancelar",
    newHint: "Crea un perfil sin luces seleccionadas. Después podrás elegir sus luces, configurarlo y calibrarlo.",
    created: "Perfil creado. Selecciona sus luces y termina la configuración.",
    createdKept: "El perfil creado se conserva. Los ajustes que no hayas terminado no se han guardado.",
    notLoaded: "El perfil se ha creado, pero aún no está listo para configurar. Puedes reintentar la carga sin crear otro perfil.",
    retryLoading: "Reintentar carga", name_required: "Escribe un nombre para el perfil.",
    profileUnavailable: "Perfil no disponible",
    unique_name: "Ya existe un perfil con ese nombre. Elige otro nombre.",
    creation_uncertain: "No se pudo confirmar la creación. Comprueba el estado antes de volver a crear el perfil.",
    checkCreation: "Comprobar estado", refreshProfiles: "Actualizar lista",
    catalogError: "No se pudo actualizar la lista de perfiles. Se conserva la última lista disponible.", retry: "Reintentar",
    recoveredForm: "El perfil aún no se ha creado. Puedes volver a pulsar Crear perfil.",
    uncertainList: "La creación no se pudo confirmar. Revisa los perfiles de la lista antes de crear otro con el mismo nombre.",
    choose: "Selecciona un perfil", next: "Siguiente", back: "Atrás", finish: "Terminar",
    selectEntities: "Seleccionar luces",
    close: "Cerrar sin guardar", preview: "Previsualizar luz", active: "Vista previa activa",
    idle: "Puedes ajustar los valores sin cambiar las luces.",
    hint: "Al marcar la casilla, la luz muestra este punto. Los ajustes se ven mientras está marcada. Se desactiva al cambiar de pantalla o después de 60 segundos sin ajustes.",
    restore: "Al terminar se recupera el estado anterior; un cambio manual de la luz se conserva.",
    loading: "Cargando…", saved: "Configuración guardada.", empty: "No hay perfiles activos de Circadian Pro.",
    admin: "Abre este panel con un usuario administrador.", error: "No se pudo completar la acción.",
    invalid: "Revisa los valores indicados antes de continuar.", missing: "Esta luz no está disponible en Home Assistant.",
    fallback: "Este formulario contiene un campo que todavía no admite este panel. Usa Configurar en la integración.",
  },
  en: {
    title: "Circadian Pro", profile: "Profile", start: "Configure and calibrate", menu: "Open menu",
    newProfile: "New profile", profileName: "Profile name", createProfile: "Create profile", cancel: "Cancel",
    newHint: "Create a profile with no lights selected. Then choose its lights, configure it and calibrate it.",
    created: "Profile created. Choose its lights and finish the configuration.",
    createdKept: "The created profile is kept. Settings you did not finish have not been saved.",
    notLoaded: "The profile was created but is not ready to configure yet. You can retry loading without creating another profile.",
    retryLoading: "Retry loading", name_required: "Enter a name for the profile.",
    profileUnavailable: "Profile unavailable",
    unique_name: "A profile with that name already exists. Choose another name.",
    creation_uncertain: "Creation could not be confirmed. Check its status before trying to create the profile again.",
    checkCreation: "Check status", refreshProfiles: "Refresh list",
    catalogError: "The profile list could not be updated. The last available list is kept.", retry: "Retry",
    recoveredForm: "The profile has not been created yet. You can click Create profile again.",
    uncertainList: "Creation could not be confirmed. Check the profiles in the list before creating another with the same name.",
    choose: "Choose a profile", next: "Next", back: "Back", finish: "Finish",
    selectEntities: "Select lights",
    close: "Close without saving", preview: "Preview light", active: "Preview active",
    idle: "You can adjust the values without changing the lights.",
    hint: "Check the box to show this calibration point. Edits are shown while checked. Changing steps or 60 seconds without edits ends the preview.",
    restore: "The previous state is restored when preview ends; manual light changes are preserved.",
    loading: "Loading…", saved: "Settings saved.", empty: "There are no active Circadian Pro profiles.",
    admin: "Open this panel as an administrator.", error: "The action could not be completed.",
    invalid: "Check the indicated values before continuing.", missing: "This light is unavailable in Home Assistant.",
    fallback: "This form contains a field this panel does not yet support. Use Configure on the integration.",
  },
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function selectedEntityValues(picker) {
  return [...picker.querySelectorAll("input[data-entity-choice]:checked")].map((input) => input.value);
}

function entityPicker(options, selected, labelId, copy, required) {
  const choices = new Map(options);
  const values = new Set(Array.isArray(selected) ? selected : []);
  // Retain a configured entity even when it is temporarily absent from HA.
  for (const id of values) if (!choices.has(id)) choices.set(id, copy.missing);
  const picker = element("details", undefined, "entity-picker");
  picker.dataset.kind = "entity";
  picker.dataset.required = String(Boolean(required));
  const summary = element("summary");
  const selection = element("span", undefined, "entity-selection");
  selection.id = labelId + "-selection";
  selection.setAttribute("aria-live", "polite");
  summary.setAttribute("aria-labelledby", `${labelId} ${selection.id}`);
  const caret = element("span", "⌄", "entity-caret");
  caret.setAttribute("aria-hidden", "true");
  summary.append(selection, caret);
  const list = element("div", undefined, "entity-choices");
  list.setAttribute("role", "group");
  list.setAttribute("aria-labelledby", labelId);
  const updateSummary = () => {
    selection.replaceChildren();
    const selectedValues = selectedEntityValues(picker);
    if (!selectedValues.length) selection.append(element("span", copy.selectEntities, "muted"));
    for (const id of selectedValues) selection.append(element("span", choices.get(id), "entity-chip"));
    summary.removeAttribute("aria-invalid");
  };
  for (const [id, name] of choices) {
    const label = element("label", undefined, "entity-choice");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.value = id;
    checkbox.checked = values.has(id);
    checkbox.dataset.entityChoice = "";
    const text = element("span", undefined, "entity-choice-text");
    text.append(element("span", name), element("small", id));
    checkbox.addEventListener("change", () => {
      updateSummary();
      picker.open = false;
      summary.focus();
    });
    label.append(checkbox, text);
    list.append(label);
  }
  picker.append(summary, list);
  picker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      picker.open = false;
      summary.focus();
      event.preventDefault();
    }
  });
  updateSummary();
  return picker;
}

export class CircadianCalibrationPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.entries = [];
    this.translations = {};
    this.busy = false;
    this.initialized = false;
    this.message = "";
    this.error = "";
    this.newProfileOpen = false;
    this.newProfileName = "";
    this.createdProfile = null;
    this.pendingCreated = null;
    this.creationRevision = 0;
    this.catalog = null;
    this.catalogError = "";
    this.selectedEntryId = null;
    this.selectedEntryTitle = "";
    this.onLeave = () => {
      this.session?.setEnabled(false).catch(() => {});
    };
  }

  set hass(value) {
    this._hass = value;
    if (!this.initialized && this.isConnected) this.initialize();
  }
  get hass() { return this._hass; }
  get language() { return this._hass?.language?.startsWith("es") ? "es" : "en"; }
  get copy() { return COPY[this.language]; }
  get totalSteps() {
    const hasWhite = (this.settings?.lights || []).some((light) =>
      (this._hass?.states[light]?.attributes.supported_color_modes || []).some((mode) => ["rgbw", "rgbww"].includes(mode))
    );
    return hasWhite || this.flow?.steps.some((step) => ["white_channels", "white_w_kelvin"].includes(step)) ? 4 : 3;
  }

  connectedCallback() {
    window.addEventListener("pagehide", this.onLeave);
    if (this._hass && !this.initialized) this.initialize();
  }

  disconnectedCallback() {
    window.removeEventListener("pagehide", this.onLeave);
    const flow = this.flow;
    this.flow = this.session = null;
    flow?.close().catch(() => {});
    this.creationRevision++;
    const creator = this.creator;
    this.creator = null;
    this.newProfileOpen = false;
    creator?.close().catch(() => {});
    this.catalog?.stop();
    this.initialized = false;
  }

  async initialize() {
    if (this.initialized || this.busy) return;
    this.initialized = true;
    if (!this._hass.user?.is_admin) { this.error = this.copy.admin; this.render(); return; }
    this.catalog?.stop();
    const catalog = new EntryCatalog(
      () => this._hass.callWS({ type: API + "entries" }),
      (entries) => {
        if (this.catalog === catalog && this.isConnected) this.applyEntries(entries);
      }, this.entries, () => {
        if (this.catalog === catalog && this.isConnected) this.catalogFailure();
      }, () => {
        if (this.catalog === catalog && this.isConnected && !this.busy && !this.flow && !this.newProfileOpen) this.render();
      },
    );
    this.catalog = catalog;
    await this.action(async () => {
      // Subscription ACKs can be lost during reconnect. Metadata loading must
      // not wait on one; the ready listener subscribes again on the new socket.
      catalog.connect(this._hass.connection).catch(() => {
        if (this.catalog === catalog && this.isConnected) this.catalogFailure();
      });
      if (this.catalog !== catalog || !this.isConnected) return;
      const [entries, translations] = await Promise.all([
        catalog.read(),
        this._hass.callWS({ type: API + "translations", language: this.language }),
      ]);
      if (this.catalog !== catalog || !this.isConnected) return;
      this.applyEntries(entries);
      this.translations = translations;
    });
  }

  applyEntries(entries) {
    this.entries = entries;
    this.catalogError = "";
    if (!this.selectedEntryId && entries.length) this.selectedEntryId = entries[0].entry_id;
    const selected = entries.find((entry) => entry.entry_id === this.selectedEntryId);
    if (selected) this.selectedEntryTitle = selected.title;
    // Metadata must never replace an unsaved form or its focused inputs.
    if (!this.busy && !this.flow && !this.newProfileOpen) this.render();
    else this.updateControls();
  }

  catalogFailure() {
    this.catalogError = this.copy.catalogError;
    this.updateControls();
  }

  async readEntries() {
    const catalog = this.catalog;
    if (!catalog) return this._hass.callWS({ type: API + "entries" });
    const entries = await catalog.read();
    return this.catalog === catalog && this.isConnected ? entries : this.entries;
  }

  async action(job) {
    if (this.busy) return;
    this.busy = true;
    this.error = "";
    this.updateControls();
    try { await job(); }
    catch (error) { this.error = error?.message || this.copy.error; }
    finally {
      this.busy = false;
      if (this.isConnected !== false) this.render();
      if (!this.initialized && this.isConnected) this.initialize();
    }
  }

  async start(entryId) {
    await this.action(() => this.openEntry(entryId));
  }

  async openEntry(entryId, created = false) {
    const entry = this.entries.find((item) => item.entry_id === entryId);
    if (!entry) return;
      this.entry = entry;
      this.selectedEntryId = entry.entry_id;
      this.selectedEntryTitle = entry.title;
      this.createdProfile = created ? entry : null;
      this.settings = { ...entry.settings };
      this.values = {};
      this.message = "";
      this.session = new PreviewSession(
        (message) => this._hass.callWS(message), entryId,
        () => ({ settings: { ...this.settings, ...this.readValues() }, kelvin: this.flow.current.step_id === "cal_warm" ? 1000 : 4000 }),
        (error) => {
          if (error) this.error = error?.message || this.copy.error;
          this.updateControls();
        },
      );
      const flow = new CalibrationFlow((...args) => this._hass.callApi(...args), this.session, entryId);
      this.flow = flow;
      try {
        const result = await flow.start();
        if (this.flow !== flow) { await flow.close(); return; }
        this.showStep(result);
      }
      catch (error) {
        if (this.flow !== flow) return;
        this.flow = this.session = null;
        if (created) {
          this.pendingCreated = { entry_id: entry.entry_id, title: entry.title };
          this.message = this.copy.createdKept;
        }
        throw error;
      }
  }

  openNewProfile() {
    this.creator = new NewProfileFlow((...args) => this._hass.callApi(...args));
    this.newProfileName = "";
    this.newProfileOpen = true;
    this.message = this.error = "";
    this.render();
  }

  creationError(result) {
    const errors = this.translations.config?.error || {};
    const reasons = result.type === "abort" ? [result.reason === "already_configured" ? "unique_name" : result.reason]
      : Object.values(result.errors || {});
    return reasons.map((reason) => errors[reason] || this.copy[reason] || this.copy.error).join("\n") || this.copy.error;
  }

  async createNewProfile() {
    await this.action(async () => {
      const revision = this.creationRevision;
      const creator = this.creator;
      let result;
      try { result = await creator.create(this.newProfileName); }
      catch (error) { throw new Error(this.copy[error.reason] || error.message || this.copy.error); }
      if (revision !== this.creationRevision || this.creator !== creator) { await creator.close(); return; }
      if (result.type !== "create_entry") { this.error = this.creationError(result); return; }
      this.pendingCreated = { entry_id: creator.createdEntryId, title: creator.name };
      this.newProfileOpen = false;
      this.message = this.copy.created;
      if (revision !== this.creationRevision) return;
      this.render();
      await this.loadCreatedEntry(revision);
    });
  }

  async loadCreatedEntry(revision = this.creationRevision) {
    const pending = this.pendingCreated;
    if (!pending) return;
    const entry = await waitForLoadedEntry(() => this.readEntries(), pending.entry_id, {
      onEntries: (entries) => { this.applyEntries(entries); },
      cancelled: () => revision !== this.creationRevision,
    });
    if (revision !== this.creationRevision) return;
    if (!entry) { this.message = this.copy.notLoaded; return; }
    this.pendingCreated = null;
    await this.openEntry(entry.entry_id, true);
  }

  async reconcileCreation() {
    await this.action(async () => {
      this.applyEntries(await this.readEntries());
      const result = await this.creator.recover();
      if (result?.type === "form") this.error = this.copy.recoveredForm;
      else {
        await this.creator.close();
        this.newProfileOpen = false;
        this.message = this.copy.uncertainList;
      }
    });
  }

  async closeWizard() {
    await this.flow.close();
    this.flow = this.session = null;
    this.message = this.createdProfile ? this.copy.createdKept : "";
    this.createdProfile = null;
  }

  showStep(result) {
    if (result.type === "create_entry") {
      this.flow = null;
      this.session = null;
      this.message = this.copy.saved;
      this.createdProfile = null;
      // Saving reloads the profile. Retain the last displayed values until a
      // fresh entries request, rather than starting another flow from old data.
      this.initialized = false;
      return;
    }
    if (result.type !== "form") throw new Error(result.reason || this.copy.error);
    this.values = {};
    for (const field of result.data_schema || []) {
      const saved = this.flow.drafts[result.step_id] || {};
      this.values[field.name] = Object.hasOwn(saved, field.name) ? saved[field.name]
        : (field.default ?? field.description?.suggested_value ?? this.settings[field.name] ?? null);
    }
    Object.assign(this.settings, this.values);
  }

  readValues(validate = true) {
    const form = this.shadowRoot.querySelector("form");
    if (validate) {
      for (const picker of form?.querySelectorAll('[data-kind="entity"]') || []) {
        if (picker.dataset.required === "true" && !selectedEntityValues(picker).length) {
          picker.open = true;
          const summary = picker.querySelector("summary");
          summary.setAttribute("aria-invalid", "true");
          summary.focus();
          throw new Error(this.copy.invalid);
        }
      }
    }
    if (validate && form && !form.reportValidity()) throw new Error(this.copy.invalid);
    const result = { ...this.values };
    for (const input of form?.querySelectorAll("[data-field]") || []) {
      if (input.dataset.kind === "entity") result[input.dataset.field] = selectedEntityValues(input);
      else if (input.type === "color") result[input.dataset.field] = hexToRgb(input.value);
      else if (input.type === "checkbox") result[input.dataset.field] = input.checked;
      else if (input.type === "number") result[input.dataset.field] = input.value === "" ? null : Number(input.value);
      else result[input.dataset.field] = input.value === ""
        ? (OPTIONAL_TIMES.has(input.dataset.field) ? "None" : null) : input.value;
    }
    return result;
  }

  async navigate(back = false) {
    await this.action(async () => {
      const data = this.readValues(!back);
      Object.assign(this.settings, data);
      const flow = this.flow;
      const result = back ? await flow.back(data) : await flow.next(data);
      if (this.flow !== flow) return;
      this.showStep(result);
      this.shadowRoot.querySelector("main")?.scrollTo(0, 0);
    });
    if (!this.initialized && !this.flow) this.initialize();
  }

  field(field) {
    const name = field.name;
    const step = this.flow.current.step_id;
    const text = this.translations.step?.[step] || {};
    const selector = field.selector || {};
    const entityField = Boolean(selector.entity || field.type === "multi_select");
    const wrapper = element(entityField ? "div" : "label", undefined, "field");
    const label = element("span", text.data?.[name] || name.replaceAll("_", " "), "label");
    label.id = `field-${name}-label`;
    wrapper.append(label);
    const value = this.values[name];
    let input;
    if (selector.color_rgb) {
      input = element("input");
      input.type = "color";
      input.value = rgbToHex(value);
    } else if (selector.entity || field.type === "multi_select") {
      const options = selector.entity ? Object.entries(this._hass.states).filter(([id]) => id.startsWith("light.")).map(([id, state]) => [id, state.attributes.friendly_name || id])
        : Object.entries(field.options || {});
      input = entityPicker(options, value, label.id, this.copy, field.required);
    } else if (selector.select || field.type === "select") {
      input = element("select");
      const options = selector.select?.options || field.options || [];
      for (const choice of options) {
        const [id, label] = Array.isArray(choice) ? choice : typeof choice === "string" ? [choice, choice] : [choice.value, choice.label];
        const option = element("option", label);
        option.value = id;
        input.append(option);
      }
      input.value = value ?? "";
    } else {
      input = element("input");
      if (selector.boolean || field.type === "boolean" || typeof value === "boolean") {
        input.type = "checkbox";
        input.checked = Boolean(value);
      } else if (selector.number || ["integer", "float"].includes(field.type) || typeof value === "number") {
        input.type = "number";
        const number = selector.number || {};
        input.step = number.step ?? (field.type === "integer" ? 1 : "any");
        if ((number.min ?? field.valueMin) !== undefined) input.min = number.min ?? field.valueMin;
        if ((number.max ?? field.valueMax) !== undefined) input.max = number.max ?? field.valueMax;
        input.value = value ?? "";
      } else {
        input.type = selector.time || OPTIONAL_TIMES.has(name) ? "time" : "text";
        if (input.type === "time") input.step = 1;
        input.value = value === "None" ? "" : value ?? "";
      }
    }
    input.dataset.field = name;
    input.required = Boolean(field.required) || input.type === "number";
    input.addEventListener("input", () => {
      this.values = this.readValues(false);
      Object.assign(this.settings, this.values);
      this.session.refresh();
    });
    wrapper.append(input);
    if (text.data_description?.[name]) wrapper.append(element("small", text.data_description[name]));
    return wrapper;
  }

  updateControls() {
    if (!this.shadowRoot) return;
    const checkbox = this.shadowRoot.querySelector("#preview");
    if (checkbox) { checkbox.checked = Boolean(this.session?.enabled); checkbox.disabled = this.busy; }
    const status = this.shadowRoot.querySelector("#preview-status");
    if (status) status.textContent = this.session?.enabled ? this.copy.active : this.copy.idle;
    const error = this.shadowRoot.querySelector("#error");
    if (error) { error.textContent = this.error; error.hidden = !this.error; }
    const catalogWarning = this.shadowRoot.querySelector("#catalog-warning");
    if (catalogWarning) {
      catalogWarning.hidden = !this.catalogError;
      catalogWarning.querySelector("p").textContent = this.catalogError;
    }
    for (const button of this.shadowRoot.querySelectorAll("button")) button.disabled = this.busy;
    const create = this.shadowRoot.querySelector("#create-profile");
    if (create && this.creator?.uncertain) create.disabled = true;
    const start = this.shadowRoot.querySelector("#start-profile");
    if (start && !this.entries.some((entry) => entry.entry_id === this.selectedEntryId)) start.disabled = true;
  }

  render() {
    this.shadowRoot.replaceChildren();
    const style = element("style");
    style.textContent = `
      :host { display:block; height:100%; color:var(--primary-text-color,#202124); background:var(--primary-background-color,#fafafa); font:14px system-ui,sans-serif; }
      * { box-sizing:border-box; } header { height:64px; display:flex; align-items:center; gap:16px; padding:0 20px; border-bottom:1px solid var(--divider-color,#ddd); background:var(--card-background-color,#fff); }
      header strong { font-size:19px; } main { height:calc(100% - 64px); overflow:auto; padding:28px 20px; }
      article { max-width:850px; margin:auto; padding:28px; border:1px solid var(--divider-color,#ddd); border-radius:14px; background:var(--card-background-color,#fff); }
      h1 { margin:0 0 12px; font-size:24px; } p { line-height:1.55; white-space:pre-line; } .muted,small { color:var(--secondary-text-color,#606770); }
      form { display:grid; gap:22px; margin-top:24px; } .field { display:grid; gap:8px; } .label { font-weight:600; } small { line-height:1.45; }
      input:not([type=checkbox]),select { color:inherit; background:var(--card-background-color,#fff); font:inherit; width:100%; min-height:42px; padding:9px 12px; border:1px solid var(--divider-color,#aeb4bc); border-radius:8px; }
      input[type=checkbox] { width:20px; height:20px; accent-color:var(--primary-color,#03a9f4); margin:0; } label.preview { display:flex; align-items:center; gap:10px; font-weight:600; }
      .entity-picker { border:1px solid var(--divider-color,#aeb4bc); border-radius:8px; background:var(--card-background-color,#fff); }
      .entity-picker summary { display:flex; align-items:center; gap:12px; min-height:42px; padding:8px 12px; cursor:pointer; list-style:none; }
      .entity-picker summary::-webkit-details-marker { display:none; } .entity-picker summary:focus-visible { outline:2px solid var(--primary-color,#0077ab); outline-offset:2px; border-radius:8px; }
      .entity-selection { display:flex; align-items:center; flex-wrap:wrap; gap:6px; min-width:0; flex:1; } .entity-chip { max-width:100%; padding:4px 9px; border-radius:6px; background:var(--secondary-background-color,#f1f4f7); overflow-wrap:anywhere; }
      .entity-caret { font-size:20px; line-height:1; color:var(--secondary-text-color,#606770); } .entity-picker[open] .entity-caret { transform:rotate(180deg); }
      .entity-choices { max-height:240px; overflow:auto; padding:6px; border-top:1px solid var(--divider-color,#ddd); }
      .entity-choice { display:flex; align-items:center; gap:12px; padding:10px; border-radius:6px; cursor:pointer; } .entity-choice:hover { background:var(--secondary-background-color,#f1f4f7); } .entity-choice input { flex-shrink:0; }
      .entity-choice-text { display:grid; gap:3px; min-width:0; overflow-wrap:anywhere; } .entity-choice small { font-size:12px; }
      .preview-box { padding:18px; margin:24px 0; border-radius:10px; background:var(--secondary-background-color,#f1f4f7); } .preview-box p { margin:8px 0 0; }
      .actions { display:flex; justify-content:flex-end; gap:12px; margin-top:28px; } button { font:inherit; font-weight:600; border-radius:8px; padding:10px 18px; border:1px solid var(--divider-color,#ddd); cursor:pointer; background:var(--card-background-color,#fff); color:var(--primary-color,#0077ab); }
      button.primary { background:var(--primary-color,#0077ab); color:var(--primary-background-color,#081018); border-color:transparent; } button:disabled { opacity:.55; cursor:wait; } .close { margin-left:auto; font-size:13px; }
      .mobile-menu { display:none; padding:8px 10px; font-size:22px; line-height:1; } @media(max-width:870px) { .mobile-menu { display:block; } }
      #catalog-warning { margin-bottom:20px; padding:12px; border:1px solid var(--warning-color,#e69500); border-radius:8px; } #catalog-warning p { margin:0 0 10px; }
      #error { background:#b3261e; color:white; padding:12px; border-radius:8px; white-space:pre-line; } .step { margin:0 0 10px; font-size:13px; } .saved { color:var(--success-color,#16814b); } [hidden] { display:none !important; }
      @media(max-width:600px) { article { padding:20px; } main { padding:16px 10px; } header { padding:0 12px; } header strong { font-size:16px; } .close { padding:8px; } }
    `;
    const header = element("header");
    const menu = element("button", "☰", "mobile-menu");
    menu.type = "button";
    menu.setAttribute("aria-label", this.copy.menu);
    menu.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })));
    header.append(menu);
    header.append(element("strong", this.copy.title));
    if (this.flow) {
      const close = element("button", this.copy.close, "close");
      close.addEventListener("click", () => this.action(() => this.closeWizard()));
      header.append(close);
    } else if (!this.newProfileOpen && !this.pendingCreated && this._hass?.user?.is_admin) {
      const create = element("button", this.copy.newProfile, "close");
      create.addEventListener("click", () => this.openNewProfile());
      header.append(create);
    }
    const main = element("main");
    const article = element("article");
    const catalogWarning = element("section");
    catalogWarning.id = "catalog-warning";
    catalogWarning.setAttribute("role", "alert");
    const catalogRetry = element("button", this.copy.retry);
    catalogRetry.type = "button";
    catalogRetry.addEventListener("click", () => this.action(async () => { this.applyEntries(await this.readEntries()); }));
    catalogWarning.append(element("p", this.catalogError), catalogRetry);
    article.append(catalogWarning);
    const error = element("p", this.error);
    error.id = "error";
    error.setAttribute("role", "alert");
    error.hidden = !this.error;
    if (this.newProfileOpen) {
      article.append(element("h1", this.copy.newProfile), element("p", this.copy.newHint, "muted"));
      const form = element("form");
      const label = element("label", undefined, "field");
      label.append(element("span", this.copy.profileName, "label"));
      const name = element("input");
      name.type = "text";
      name.required = true;
      name.autocomplete = "off";
      name.value = this.newProfileName;
      name.addEventListener("input", () => { this.newProfileName = name.value; });
      label.append(name);
      const actions = element("div", undefined, "actions");
      const cancel = element("button", this.copy.cancel);
      cancel.type = "button";
      cancel.addEventListener("click", () => this.action(async () => { await this.creator.close(); this.newProfileOpen = false; }));
      const create = element("button", this.copy.createProfile, "primary");
      create.id = "create-profile";
      create.type = "submit";
      create.disabled = this.creator?.uncertain;
      actions.append(cancel, create);
      if (this.creator?.uncertain) {
        const check = element("button", this.copy.checkCreation);
        check.type = "button";
        check.addEventListener("click", () => this.reconcileCreation());
        actions.append(check);
      }
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!this.creator?.uncertain && form.reportValidity()) this.createNewProfile();
      });
      form.append(label, error, actions);
      article.append(form);
    } else if (this.flow) {
      const step = this.flow.current;
      const text = this.translations.step?.[step.step_id] || {};
      article.append(element("p", `${this.flow.index + 1} / ${this.totalSteps} · ${this.entry.title}`, "muted step"), element("h1", text.title || step.step_id));
      if (this.createdProfile) article.append(element("p", this.copy.created, "saved"));
      const description = text.description?.replace(/ Para previsualizar las luces al instante.*$| El panel Calibración Circadian Pro permite.*$| To preview.*$| The Circadian Pro calibration panel.*$/s, "");
      if (description) article.append(element("p", description, "muted"));
      const form = element("form");
      form.addEventListener("submit", (event) => { event.preventDefault(); this.navigate(); });
      for (const field of step.data_schema || []) form.append(this.field(field));
      article.append(form);
      const errors = Object.entries(step.errors || {}).map(([key, value]) => {
        const label = text.data?.[key];
        return `${label ? label + ": " : ""}${this.translations.error?.[value] || value}`;
      });
      if (errors.length) { this.error = errors.join("\n"); error.textContent = this.error; error.hidden = false; }
      if (["cal_neutral", "cal_warm"].includes(step.step_id)) {
        const box = element("section", undefined, "preview-box");
        const label = element("label", undefined, "preview");
        const checkbox = element("input");
        checkbox.type = "checkbox";
        checkbox.id = "preview";
        checkbox.checked = this.session.enabled;
        checkbox.addEventListener("change", () => {
          this.error = "";
          this.session.setEnabled(checkbox.checked).catch((failure) => { this.error = failure?.message || this.copy.error; this.updateControls(); });
        });
        label.append(checkbox, element("span", this.copy.preview));
        const status = element("p", this.session.enabled ? this.copy.active : this.copy.idle);
        status.id = "preview-status";
        status.setAttribute("aria-live", "polite");
        box.append(label, status, element("p", this.copy.hint, "muted"), element("p", this.copy.restore, "muted"));
        article.append(box);
      }
      const actions = element("div", undefined, "actions");
      const last = step.step_id === "cal_warm";
      if (this.flow.index > 0 && !last) {
        const back = element("button", this.copy.back);
        back.addEventListener("click", () => this.navigate(true));
        actions.append(back);
      }
      const next = element("button", last ? this.copy.finish : this.copy.next, "primary");
      next.addEventListener("click", () => this.navigate());
      actions.append(next);
      article.append(error, actions);
    } else {
      article.append(element("h1", this.copy.title));
      if (this.message) article.append(element("p", this.message, "saved"));
      if (this.pendingCreated) {
        article.append(element("p", this.pendingCreated.title, "label"));
        const retry = element("button", this.copy.retryLoading, "primary");
        retry.addEventListener("click", () => this.action(() => this.loadCreatedEntry()));
        article.append(retry);
      }
      if (this.entries.length) {
        const label = element("label", undefined, "field");
        label.append(element("span", this.copy.profile, "label"));
        const select = element("select");
        if (this.selectedEntryId && !this.entries.some((entry) => entry.entry_id === this.selectedEntryId)) {
          const missing = element("option", `${this.selectedEntryTitle} · ${this.copy.profileUnavailable}`);
          missing.value = this.selectedEntryId;
          missing.disabled = missing.selected = true;
          select.append(missing);
        }
        for (const entry of this.entries) {
          const option = element("option", entry.title);
          option.value = entry.entry_id;
          option.selected = entry.entry_id === this.selectedEntryId;
          select.append(option);
        }
        select.addEventListener("change", () => {
          this.selectedEntryId = select.value;
          this.selectedEntryTitle = this.entries.find((entry) => entry.entry_id === select.value)?.title || "";
          this.updateControls();
        });
        label.append(select);
        const actions = element("div", undefined, "actions");
        const start = element("button", this.copy.start, "primary");
        start.id = "start-profile";
        start.addEventListener("click", () => this.start(select.value));
        actions.append(start);
        if (!this.catalog?.subscribed) {
          const refresh = element("button", this.copy.refreshProfiles);
          refresh.addEventListener("click", () => this.action(async () => { this.applyEntries(await this.readEntries()); }));
          actions.append(refresh);
        }
        article.append(label, actions);
      } else if (!this.error) article.append(element("p", this.busy ? this.copy.loading : this.copy.empty, "muted"));
      article.append(error);
    }
    main.append(article);
    this.shadowRoot.append(style, header, main);
    this.updateControls();
  }
}

if (!customElements.get("circadian-pro-calibration")) {
  customElements.define("circadian-pro-calibration", CircadianCalibrationPanel);
}
