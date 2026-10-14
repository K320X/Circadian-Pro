/** Circadian Pro's calibration wizard. Preview changes never submit the form. */

const API = "circadian_pro/calibration/";
const FLOW = "config/config_entries/options/flow";
const CONFIG_FLOW = "config/config_entries/flow";
const OPTIONAL_TIMES = new Set([
  "sunrise_time", "sunset_time", "min_sunrise_time", "max_sunrise_time", "min_sunset_time", "max_sunset_time",
]);
const BRIGHTNESS_FIELDS = new Set(["min_brightness", "max_brightness", "sleep_brightness"]);
// Presentation only: the server still supplies the fields, defaults and validation.
// Each row contains related settings; future unknown fields stay visible below.
const GENERAL_GROUPS = [
  { id: "adaptation", rows: [["interval", "adapt_delay"], ["transition", "initial_transition"], ["only_once"]] },
  { id: "limits", rows: [["min_brightness", "max_brightness"], ["min_color_temp", "max_color_temp"],
    ["brightness_mode", "apply_temperature_curve"], ["brightness_mode_time_light_minutes", "brightness_mode_time_dark_minutes"]] },
  { id: "night", rows: [["sleep_activation_mode"], ["sleep_schedule_start", "sleep_schedule_end"],
    ["sleep_brightness", "sleep_transition"], ["sleep_rgb_or_color_temp", "sleep_color_temp", "sleep_rgb_color"]] },
  { id: "solar", rows: [["sunrise_time", "sunrise_offset_minutes"], ["min_sunrise_time", "max_sunrise_time"],
    ["sunset_time", "sunset_offset_minutes"], ["min_sunset_time", "max_sunset_time"]] },
  { id: "manual", rows: [["take_over_control"], ["detect_non_ha_changes"], ["autoreset_control_seconds"]] },
  { id: "compatibility", rows: [["intercept"], ["multi_light_intercept"], ["separate_turn_on_commands"],
    ["send_split_delay"], ["skip_redundant_commands"]] },
];
let previewSessionCounter = 0;

function previewSessionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  // randomUUID requires a secure context; local HA can run over HTTP.
  if (globalThis.crypto?.getRandomValues) {
    return [...globalThis.crypto.getRandomValues(new Uint8Array(16))]
      .map((value) => value.toString(16).padStart(2, "0")).join("");
  }
  return `${Date.now().toString(36)}-${++previewSessionCounter}-${Math.random().toString(36).slice(2)}`;
}

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
    this.sessionId = previewSessionId();
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
      return this.enqueue(() => this.callWS({ type: API + "stop", entry_id: this.entryId, session_id: this.sessionId }));
    }
    try {
      return await this.enqueue(async () => {
        if (!this.enabled || revision !== this.revision) return;
        const draft = this.getDraft();
        const result = await this.callWS({
          type: API + "preview", entry_id: this.entryId, session_id: this.sessionId,
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

function closedFlowError() {
  return Object.assign(new Error("This configuration flow has been closed."), { reason: "flow_closed" });
}

function deleteFlow(callApi, path, deletions, flowId) {
  if (!deletions.has(flowId)) {
    const deleting = Promise.resolve().then(() => callApi("DELETE", `${path}/${flowId}`)).catch((error) => {
      // HA removes completed/aborted flows. Cleanup of an already absent flow
      // is successful, including after a save whose response was lost.
      if (error?.status_code === 404) return;
      deletions.delete(flowId); // A later explicit close may retry a failed deletion.
      throw error;
    });
    deletions.set(flowId, deleting);
  }
  return deletions.get(flowId);
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
    this.closed = false;
    this.deletions = new Map();
    this.pending = null;
    this.rebuilding = false;
    this.pendingSubmission = null;
  }

  assertOpen() {
    if (this.closed) throw closedFlowError();
  }

  async accept(result, publish = true) {
    if (this.closed) {
      if (result.type === "form") await deleteFlow(this.callApi, FLOW, this.deletions, result.flow_id);
      throw closedFlowError();
    }
    if (publish) this.current = result;
    else this.pending = result;
    return result;
  }

  async start() {
    this.assertOpen();
    await this.accept(await this.callApi("POST", FLOW, { handler: this.entryId }));
    this.steps = [this.current.step_id];
    this.index = 0;
    return this.current;
  }

  async next(data) {
    this.assertOpen();
    await this.preview.setEnabled(false);
    this.assertOpen();
    const previous = this.current.step_id;
    this.drafts[previous] = { ...data };
    // An uncertain POST is reconciled through GET before another write is
    // allowed. A retry must never submit the previous screen to a later step.
    if (this.pendingSubmission) return this.reconcileSubmission();
    if (this.rebuilding) {
      const restored = await this.recreate(this.index);
      if (restored.type !== "form" || restored.step_id !== previous || Object.keys(restored.errors || {}).length) return restored;
    }
    const flowId = this.current.flow_id;
    this.pendingSubmission = { flowId, previous };
    let result;
    try { result = await this.accept(await this.callApi("POST", `${FLOW}/${flowId}`, data)); }
    catch (error) {
      if (this.closed) throw error;
      return this.reconcileSubmission(error);
    }
    this.pendingSubmission = null;
    this.recordStep(result, previous);
    return result;
  }

  recordStep(result, previous) {
    if (result.type === "form" && result.step_id !== previous) {
      this.index++;
      this.steps[this.index] = result.step_id;
      this.steps.length = this.index + 1;
    }
  }

  async reconcileSubmission(submissionError) {
    this.assertOpen();
    const pending = this.pendingSubmission;
    let result;
    try { result = await this.accept(await this.callApi("GET", `${FLOW}/${pending.flowId}`)); }
    catch (error) {
      if (this.closed) throw error;
      if (error?.status_code === 404) {
        this.pendingSubmission = null;
        if (pending.previous === "cal_warm") {
          // The flow may have completed, expired, or been aborted elsewhere.
          // Refresh the profile, without asserting success or repeating Save.
          this.closed = true;
          this.current = null;
          return { type: "save_uncertain" };
        }
        this.rebuilding = true;
        throw Object.assign(new Error("flow_expired", { cause: error }), { reason: "flow_expired" });
      }
      throw Object.assign(new Error("submission_uncertain", { cause: submissionError || error }), { reason: "submission_uncertain" });
    }
    this.pendingSubmission = null;
    this.recordStep(result, pending.previous);
    if (result.type === "form" && result.step_id === pending.previous && submissionError) throw submissionError;
    return result;
  }

  async back(data) {
    this.assertOpen();
    if (!this.index) return this.current;
    await this.preview.setEnabled(false);
    this.assertOpen();
    this.drafts[this.current.step_id] = { ...data };
    const destination = this.index - 1;
    // Back never repeats a possibly completed final submission.
    if (this.pendingSubmission) return this.reconcileSubmission();
    return this.recreate(destination);
  }

  async recreate(destination) {
    this.rebuilding = true;
    // Replay only completed steps in a fresh, unsaved options flow. No final
    // step is submitted, so going back cannot save or reload an integration.
    if (this.pending?.type === "form") {
      await deleteFlow(this.callApi, FLOW, this.deletions, this.pending.flow_id);
    }
    await deleteFlow(this.callApi, FLOW, this.deletions, this.current.flow_id);
    this.pending = null;
    this.assertOpen();
    // Keep the displayed form and its draft until the entire replay succeeds.
    // A failed creation/replay can then be retried or closed safely.
    let result = await this.accept(await this.callApi("POST", FLOW, { handler: this.entryId }), false);
    for (let index = 0; index < destination && result.type === "form" && !Object.keys(result.errors || {}).length; index++) {
      this.assertOpen();
      result = await this.accept(await this.callApi("POST", `${FLOW}/${result.flow_id}`, this.drafts[result.step_id]), false);
      if (result.type !== "form" || Object.keys(result.errors || {}).length) break;
    }
    this.current = result;
    this.pending = null;
    this.rebuilding = false;
    this.index = this.steps.indexOf(result.step_id);
    if (this.index < 0) this.index = 0;
    return result;
  }

  async close() {
    this.closed = true;
    let failure;
    try { await this.preview.setEnabled(false); }
    catch (error) { failure = error; }
    const flowIds = new Set([this.current, this.pending].filter((result) => result?.type === "form").map((result) => result.flow_id));
    for (const flowId of flowIds) {
      try { await deleteFlow(this.callApi, FLOW, this.deletions, flowId); }
      catch (error) { failure ||= error; }
    }
    this.current = this.pending = null;
    if (failure) throw failure;
  }
}

export class NewProfileFlow {
  constructor(callApi) {
    this.callApi = callApi;
    this.current = null;
    this.name = "";
    this.createdEntryId = null;
    this.uncertain = false;
    this.closed = false;
    this.deletions = new Map();
  }

  async accept(result) {
    if (this.closed) {
      if (result.type === "form") await deleteFlow(this.callApi, CONFIG_FLOW, this.deletions, result.flow_id);
      if (result.type === "create_entry") this.createdEntryId = result.result?.entry_id || null;
      throw closedFlowError();
    }
    this.current = result;
    return result;
  }

  async create(name) {
    if (this.createdEntryId) throw new Error("This profile has already been created.");
    if (this.closed) throw closedFlowError();
    this.name = name.trim();
    if (!this.name) throw Object.assign(new Error("name_required"), { reason: "name_required" });
    if (this.uncertain) throw Object.assign(new Error("creation_uncertain"), { reason: "creation_uncertain" });
    if (this.current?.type !== "form" || !["user", "menu"].includes(this.current.step_id)) {
      await this.accept(await this.callApi("POST", CONFIG_FLOW, {
        handler: "circadian_pro", show_advanced_options: false, source: "user",
      }));
    }
    if (this.current.type === "form" && this.current.step_id === "menu") {
      await this.accept(await this.callApi("POST", `${CONFIG_FLOW}/${this.current.flow_id}`, { action: "new" }));
    }
    if (this.current.type === "form" && this.current.step_id === "user") {
      try { await this.accept(await this.callApi("POST", `${CONFIG_FLOW}/${this.current.flow_id}`, { name: this.name })); }
      catch (error) {
        if (this.closed) throw error;
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
    this.closed = true;
    if (this.current?.type === "form") {
      try { await deleteFlow(this.callApi, CONFIG_FLOW, this.deletions, this.current.flow_id); }
      catch (error) { if (!this.uncertain) throw error; }
    }
    this.current = null;
  }

  async recover() {
    if (this.closed) return null;
    if (!this.uncertain || !this.current?.flow_id) return this.current;
    try {
      const result = await this.callApi("GET", `${CONFIG_FLOW}/${this.current.flow_id}`);
      if (this.closed) return null;
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
    title: "Circadian Pro", profile: "Perfil", profiles: "Perfiles", actions: "Acciones", start: "Configurar y calibrar", menu: "Abrir menú",
    integration: "Ir a la integración",
    sortProfiles: "Ordenar por", sortRecent: "Modificados recientemente", sortNameAsc: "Nombre: A–Z", sortNameDesc: "Nombre: Z–A",
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
    submission_uncertain: "No se pudo confirmar el último paso. Pulsa Comprobar estado antes de continuar.",
    flow_expired: "La sesión terminó. Pulsa Siguiente para recuperar este borrador.",
    save_uncertain: "No se pudo confirmar el guardado. Comprueba la configuración actual del perfil antes de repetir los cambios.",
    checkStep: "Comprobar estado",
    choose: "Selecciona un perfil", next: "Siguiente", back: "Atrás", finish: "Terminar",
    warmWhite: "Cálido", coldWhite: "Frío",
    brightnessRangeHint: "Rango: 1–100 %.",
    timingDiagram: {
      sunrise: "Amanecer", sunset: "Atardecer", minimum: "Mín.", maximum: "Máx.",
      title: "Vista de la curva", brightness: "Brillo", temperature: "Temperatura de color",
      modes: { default: "Natural", linear: "Lineal", tanh: "Sigmoide" },
      caption: "{date} · {timezone}",
      loading: "Calculando los horarios de hoy…", unavailable: "No se pudieron calcular los horarios solares de hoy.",
      invalidSolar: "Revisa los horarios y desplazamientos del amanecer y atardecer.", retry: "Reintentar",
      natural: "El brillo llega al máximo al amanecer y se mantiene hasta el atardecer. La temperatura de color sigue su propia curva solar.",
      independent: "La temperatura de color sigue su propia curva solar.",
      synchronized: "La temperatura de color sigue esta misma curva entre sus propios límites.",
      temperatureScale: "El fondo representa la temperatura de color: naranja en el mínimo y blanco en el máximo. Los colores son orientativos.",
      nightScale: "El fondo muestra el color normal y el color nocturno elegido. Los colores y las transiciones son orientativos; la línea usa brillo de 0 a 100 %.",
      transitions: "Transiciones", night: "Modo noche", nightAllDay: "Modo noche durante todo el día.",
      nightStart: "Inicio modo noche", nightEnd: "Fin modo noche",
      nightStartShort: "IN · Noche", nightEndShort: "OUT · Noche",
      nightTiming: "Horario previsto. Transición general: {transition} s. La detección puede retrasarse hasta {interval} s, o más si hay otra transición en curso. La luz determina cómo suaviza el cambio.",
      nightInvalid: "Revisa el brillo, el color nocturno y la transición general.",
      brightnessRange: "Introduce un brillo entero entre 1 y 100 %. El 0 % no está permitido.",
      brightnessOrder: "El brillo mínimo no puede superar al máximo.",
      rise: "Subida: {dark} min antes + {light} min después del amanecer.",
      fall: "Bajada: {light} min antes + {dark} min después del atardecer.",
      linear: "Cada transición dura {total} min.",
      tanh: "Cada tramo principal (5–95 %) dura {total} min. La curva se prolonga suavemente a ambos lados.",
      step: "Con ambos tiempos a 0, el objetivo cambia al amanecer y al atardecer. Se conserva la transición configurada para las luces.",
      invalid: "Introduce dos tiempos enteros entre 0 y 240 minutos para ver la curva.",
    },
    groups: { adaptation: "Ritmo de adaptación", limits: "Brillo y temperatura", night: "Modo noche",
      solar: "Amanecer y atardecer", manual: "Cambios manuales", compatibility: "Compatibilidad y envío", other: "Otros ajustes" },
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
    title: "Circadian Pro", profile: "Profile", profiles: "Profiles", actions: "Actions", start: "Configure and calibrate", menu: "Open menu",
    integration: "Go to integration",
    sortProfiles: "Sort by", sortRecent: "Recently modified", sortNameAsc: "Name: A–Z", sortNameDesc: "Name: Z–A",
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
    submission_uncertain: "The last step could not be confirmed. Click Check status before continuing.",
    flow_expired: "The session ended. Click Next to recover this draft.",
    save_uncertain: "The save could not be confirmed. Check the profile's current configuration before repeating the changes.",
    checkStep: "Check status",
    choose: "Choose a profile", next: "Next", back: "Back", finish: "Finish",
    warmWhite: "Warm", coldWhite: "Cold",
    brightnessRangeHint: "Range: 1–100%.",
    timingDiagram: {
      sunrise: "Sunrise", sunset: "Sunset", minimum: "Min.", maximum: "Max.",
      title: "Curve preview", brightness: "Brightness", temperature: "Color temperature",
      modes: { default: "Natural", linear: "Linear", tanh: "Sigmoid" },
      caption: "{date} · {timezone}",
      loading: "Calculating today’s schedule…", unavailable: "Today’s solar schedule could not be calculated.",
      invalidSolar: "Check sunrise and sunset times and offsets.", retry: "Retry",
      natural: "Brightness reaches its maximum at sunrise and stays there until sunset. Color temperature follows its own solar curve.",
      independent: "Color temperature follows its own solar curve.",
      synchronized: "Color temperature follows this same curve between its own limits.",
      temperatureScale: "The background represents color temperature: orange at its minimum and white at its maximum. Colors are illustrative.",
      nightScale: "The background shows the normal color and selected sleep color. Colors and transitions are illustrative; the line uses brightness from 0 to 100%.",
      transitions: "Transitions", night: "Sleep mode", nightAllDay: "Sleep mode throughout the day.",
      nightStart: "Sleep mode starts", nightEnd: "Sleep mode ends",
      nightStartShort: "IN · Night", nightEndShort: "OUT · Night",
      nightTiming: "Planned schedule. General transition: {transition} s. Detection may take up to {interval} s, or longer while another transition is running. The light determines how the change is smoothed.",
      nightInvalid: "Check brightness, sleep color and the general transition.",
      brightnessRange: "Enter a whole brightness percentage from 1 to 100%. A value of 0% is not allowed.",
      brightnessOrder: "Minimum brightness cannot exceed maximum brightness.",
      rise: "Rise: {dark} min before + {light} min after sunrise.",
      fall: "Fall: {light} min before + {dark} min after sunset.",
      linear: "Each transition lasts {total} min.",
      tanh: "Each main portion (5–95%) lasts {total} min. The curve continues gently beyond both ends.",
      step: "With both times at 0, the target changes at sunrise and sunset. The configured light transition still applies.",
      invalid: "Enter two whole-minute times between 0 and 240 to see the curve.",
    },
    groups: { adaptation: "Adaptation timing", limits: "Brightness and temperature", night: "Sleep mode",
      solar: "Sunrise and sunset", manual: "Manual changes", compatibility: "Compatibility and commands", other: "Other settings" },
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

// Solar context is supplied by HA in its own timezone. Plot x values are elapsed
// minutes from that local midnight, so DST days naturally span 23 or 25 hours.
const SOLAR_FIELDS = [...OPTIONAL_TIMES, "sunrise_offset_minutes", "sunset_offset_minutes",
  "sleep_activation_mode", "sleep_schedule_start", "sleep_schedule_end"];

export function solarDraft(values) {
  return Object.fromEntries(SOLAR_FIELDS.filter((name) => Object.hasOwn(values, name)).map((name) => [name, values[name]]));
}

export function validSolarContext(context) {
  if (!context || !Number.isFinite(context.day_start) || !Number.isFinite(context.day_end)
    || context.day_end <= context.day_start || typeof context.timezone !== "string" || !context.timezone
    || !/^\d{4}-\d{2}-\d{2}$/.test(context.local_date || "")
    || !Number.isFinite(context.refresh_after) || context.refresh_after <= 0 || context.refresh_after > 90000
    || !Array.isArray(context.events) || context.events.length < 4) return false;
  try { new Intl.DateTimeFormat("en", { timeZone: context.timezone }).format(0); }
  catch { return false; }
  const next = { sunrise: "solar_noon", solar_noon: "sunset", sunset: "solar_midnight", solar_midnight: "sunrise" };
  if (context.events.some((item, index, events) => !Object.hasOwn(next, item.event) || !Number.isFinite(item.timestamp)
    || (index && (item.timestamp <= events[index - 1].timestamp || item.event !== next[events[index - 1].event])))) return false;
  if (context.events[0].timestamp > context.day_start || context.events.at(-1).timestamp <= context.day_end) return false;
  return ["sunrise", "sunset"].every((name) => Number.isFinite(context[name]?.timestamp)
    && /^\d{2}:\d{2}$/.test(context[name]?.local_time || "")
    && context.events.some((event) => event.event === name && event.timestamp === context[name].timestamp));
}

// Requests are limited to solar edits and lifecycle changes, never slider ticks.
// Each invalidation retires the old result immediately, including in-flight reads.
export class SolarContextSession {
  constructor(callWS, entryId, onChange, environment = {}) {
    this.callWS = callWS;
    this.entryId = entryId;
    this.onChange = onChange;
    this.document = environment.document ?? globalThis.document;
    this.window = environment.window ?? globalThis.window;
    this.connection = environment.connection;
    this.revision = 0;
    this.closed = false;
    this.draft = {};
    this.key = null;
    this.onVisible = () => { if (!this.document?.hidden) this.refresh(); };
    this.onReady = () => this.refresh();
    this.onDisconnected = () => { this.invalidate("unavailable"); };
    this.document?.addEventListener?.("visibilitychange", this.onVisible);
    this.window?.addEventListener?.("pageshow", this.onVisible);
    this.connection?.addEventListener?.("ready", this.onReady);
    this.connection?.addEventListener?.("disconnected", this.onDisconnected);
  }

  invalidate(status = "loading") {
    clearTimeout(this.debounce);
    clearTimeout(this.midnight);
    clearTimeout(this.requestTimeout);
    this.revision++;
    this.cancelRead?.();
    this.cancelRead = null;
    if (!this.closed) this.onChange(null, status);
    return this.revision;
  }

  update(values) {
    const draft = solarDraft(values);
    const key = JSON.stringify(draft);
    if (key === this.key || this.closed) return;
    const first = this.key === null;
    this.key = key;
    this.draft = draft;
    this.invalidate();
    if (first) this.refresh();
    else this.debounce = setTimeout(() => this.refresh(), 300);
  }

  async refresh() {
    if (this.closed) return;
    const revision = this.invalidate();
    try {
      const timeout = new Promise((resolve, reject) => {
        this.cancelRead = () => reject(new Error("Solar read retired"));
        this.requestTimeout = setTimeout(() => reject(new Error("Solar read timed out")), 10000);
      });
      const context = await Promise.race([
        this.callWS({ type: API + "solar", entry_id: this.entryId, settings: { ...this.draft } }), timeout,
      ]);
      if (this.closed || revision !== this.revision) return;
      if (!validSolarContext(context)) throw new Error("Invalid solar context");
      this.onChange(context, "ready");
      // Relative HA time avoids assuming the phone clock or timezone is right.
      this.midnight = setTimeout(() => this.refresh(), context.refresh_after * 1000 + 250);
    } catch (error) {
      if (!this.closed && revision === this.revision) {
        this.onChange(null, error?.code === "invalid_options" ? "invalidSolar" : "unavailable");
      }
    } finally {
      if (revision === this.revision) {
        clearTimeout(this.requestTimeout);
        this.cancelRead = null;
      }
    }
  }

  close() {
    this.closed = true;
    this.invalidate();
    this.document?.removeEventListener?.("visibilitychange", this.onVisible);
    this.window?.removeEventListener?.("pageshow", this.onVisible);
    this.connection?.removeEventListener?.("ready", this.onReady);
    this.connection?.removeEventListener?.("disconnected", this.onDisconnected);
  }
}

function solarPair(minute, context) {
  const timestamp = context.day_start + minute * 60;
  const index = context.events.findIndex((item) => item.timestamp > timestamp);
  return index > 0 ? context.events.slice(index - 1, index + 1) : null;
}

function solarPosition(minute, context) {
  const [previous, next] = solarPair(minute, context);
  const timestamp = context.day_start + minute * 60;
  const nextIsHorizon = ["sunrise", "sunset"].includes(next.event);
  const h = nextIsHorizon ? previous.timestamp : next.timestamp;
  const x = nextIsHorizon ? next.timestamp : previous.timestamp;
  const sign = ["sunset", "solar_noon"].includes(next.event) ? 1 : -1;
  return sign * (1 - ((timestamp - h) / (h - x)) ** 2);
}

// Mirror SunLightSettings._timed_curve_value and SunEvents.sun_position, using
// the same bracketing events (solar noon need not be the midpoint of daylight).
export function brightnessTimingValue(minute, mode, light, dark, context) {
  if (mode === "default") return Math.min(1, 1 + solarPosition(minute, context));
  const pair = solarPair(minute, context);
  const event = pair.find((item) => item.event === "sunrise") || pair.find((item) => item.event === "sunset");
  const elapsed = minute - (event.timestamp - context.day_start) / 60;
  const rising = event.event === "sunrise";
  const total = light + dark;
  if (total === 0) return (rising ? elapsed >= 0 : elapsed < 0) ? 1 : 0;
  const progress = (rising ? elapsed + dark : dark - elapsed) / total;
  return mode === "linear" ? Math.max(0, Math.min(1, progress))
    : (Math.tanh(Math.atanh(0.9) * (2 * progress - 1)) + 1) / 2;
}

export function brightnessTimingPoints(mode, light, dark, context) {
  if (!validSolarContext(context) || !["default", "linear", "tanh"].includes(mode)) return null;
  if (mode !== "default" && [light, dark].some((value) => !Number.isInteger(value) || value < 0 || value > 240)) return null;
  const endOfDay = (context.day_end - context.day_start) / 60;
  const eventMinutes = context.events.map((item) => ({ ...item, minute: (item.timestamp - context.day_start) / 60 }));
  if (mode !== "default" && light + dark === 0) {
    const points = [[0, brightnessTimingValue(0, mode, light, dark, context)]];
    for (const { event, minute } of eventMinutes) {
      if (minute <= 0 || minute >= endOfDay || !["sunrise", "sunset"].includes(event)) continue;
      points.push([minute, event === "sunrise" ? 0 : 1], [minute, event === "sunrise" ? 1 : 0]);
    }
    return [...points, [endOfDay, brightnessTimingValue(endOfDay, mode, light, dark, context)]];
  }
  const times = new Set([0, endOfDay]);
  const add = (minute) => { if (minute >= 0 && minute <= endOfDay) times.add(minute); };
  for (const { minute } of eventMinutes) add(minute);
  if (mode !== "linear") for (let minute = 0; minute <= endOfDay; minute += 4) add(minute);
  if (mode !== "default") {
    const total = light + dark;
    for (const { event, minute } of eventMinutes) {
      if (!["sunrise", "sunset"].includes(event)) continue;
      const start = minute - (event === "sunrise" ? dark : light);
      const end = minute + (event === "sunrise" ? light : dark);
      const midpoint = (start + end) / 2;
      for (const value of [start, midpoint, end]) add(value);
      if (mode === "tanh") for (let index = -64; index <= 64; index++) add(midpoint + total * index / 32);
    }
  }
  const points = [];
  for (const minute of [...times].sort((a, b) => a - b)) {
    // Closest-event selection changes at actual noon/midnight. Very long
    // windows may disagree there; preserve the runtime edge instead of a ramp.
    if (minute > 0 && mode !== "default" && eventMinutes.some((item) => item.minute === minute
      && ["solar_noon", "solar_midnight"].includes(item.event))) {
      const before = brightnessTimingValue(minute - 1e-6, mode, light, dark, context);
      const after = brightnessTimingValue(minute, mode, light, dark, context);
      if (Math.abs(before - after) > 1e-7) points.push([minute, before]);
    }
    points.push([minute, brightnessTimingValue(minute, mode, light, dark, context)]);
  }
  return points;
}

export function temperatureTimingPoints(mode, light, dark, shared, context) {
  if (!validSolarContext(context)) return null;
  if (shared && ["linear", "tanh"].includes(mode)) return brightnessTimingPoints(mode, light, dark, context);
  const endOfDay = (context.day_end - context.day_start) / 60;
  const times = new Set([0, endOfDay]);
  for (let minute = 0; minute <= endOfDay; minute += 4) times.add(minute);
  for (const event of context.events) {
    const minute = (event.timestamp - context.day_start) / 60;
    if (minute >= 0 && minute <= endOfDay) times.add(minute);
  }
  return [...times].sort((a, b) => a - b).map((minute) => [minute, Math.max(0, solarPosition(minute, context))]);
}

function normalGraphColor(progress) {
  return [255, 138 + 117 * progress, 26 + 229 * progress];
}

const graphDefault = (value, fallback) => value === undefined ? fallback : value;

function brightnessGraphError(values) {
  if ([...BRIGHTNESS_FIELDS].some((name) => values[name] !== undefined
    && (!Number.isInteger(values[name]) || values[name] < 1 || values[name] > 100))) return "brightnessRange";
  return values.min_brightness !== undefined && values.max_brightness !== undefined
    && values.min_brightness > values.max_brightness ? "brightnessOrder" : null;
}

// Approximate black-body sRGB for the sleep CCT swatch. It is not a prediction
// of LED spectra or calibration. Normal daytime colors keep the existing key.
export function sleepGraphColor(values) {
  if (values.sleep_rgb_or_color_temp !== "color_temp") {
    const rgb = graphDefault(values.sleep_rgb_color, [255, 56, 0]);
    return Array.isArray(rgb) && rgb.length === 3 && rgb.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255)
      ? [...rgb] : null;
  }
  const kelvin = graphDefault(values.sleep_color_temp, 1000);
  if (!Number.isFinite(kelvin) || kelvin < 1000 || kelvin > 10000) return null;
  const t = kelvin / 100;
  return [t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592,
    t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492,
    t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307]
    .map((channel) => Math.max(0, Math.min(255, channel)));
}

function validNightContext(context) {
  return ["sun", "schedule"].includes(context.sleep_mode) && Array.isArray(context.sleep_windows)
    && context.sleep_windows.every((window, index, windows) => Number.isFinite(window.start) && Number.isFinite(window.end)
      && window.end > window.start && (!index || window.start > windows[index - 1].end));
}

// Programmed automatic behavior only. The polling phase and device transition
// implementation cannot be known here. Boundary fades are nominal linear
// illustrations, starting from the previous fade's value if it is interrupted.
export function nightTimingPoints(values, context) {
  if (!validSolarContext(context) || !validNightContext(context)) return null;
  const mode = values.brightness_mode;
  const light = values.brightness_mode_time_light_minutes;
  const dark = values.brightness_mode_time_dark_minutes;
  const shared = mode !== "default" && values.apply_temperature_curve === true;
  const brightness = brightnessTimingPoints(mode, light, dark, context);
  const temperature = temperatureTimingPoints(mode, light, dark, shared, context);
  const minimum = graphDefault(values.min_brightness, 50), maximum = graphDefault(values.max_brightness, 100);
  const sleep = graphDefault(values.sleep_brightness, 25), duration = graphDefault(values.transition, 45);
  const nightColor = sleepGraphColor(values);
  if (!brightness || !temperature || !nightColor || [minimum, maximum, sleep].some((value) => !Number.isFinite(value) || value < 1 || value > 100)
    || maximum < minimum || !Number.isFinite(duration) || duration < 0 || duration > 6553) return null;
  const sleepValue = [sleep / 100, ...nightColor];
  const start = context.day_start, end = context.day_end;
  const normal = (timestamp, before = false) => {
    // The server brackets the plotted day. For an unusually long fade whose
    // endpoint falls outside those events, use the nearest available endpoint.
    const left = before && mode !== "default" && context.events.some((event) => event.timestamp === timestamp);
    const safe = Math.max(context.events[0].timestamp, Math.min(context.events.at(-1).timestamp - 0.0001, timestamp - (left ? 0.00001 : 0)));
    const minute = (safe - start) / 60;
    const progress = brightnessTimingValue(minute, mode, light, dark, context);
    const color = shared ? progress : Math.max(0, solarPosition(minute, context));
    return [(minimum + (maximum - minimum) * progress) / 100, ...normalGraphColor(color)];
  };
  const boundaries = context.sleep_all_day ? [] : context.sleep_windows.flatMap((window) => [
    { timestamp: window.start, sleeping: true }, { timestamp: window.end, sleeping: false },
  ]);
  const segments = [];
  const valueIn = (timestamp, segment, before = false) => {
    if (!segment) return normal(timestamp, before);
    const progress = duration === 0 ? 1 : Math.min(1, Math.max(0, (timestamp - segment.timestamp) / duration));
    if (progress === 1) return segment.sleeping ? sleepValue : normal(timestamp, before);
    return segment.from.map((value, index) => value + (segment.to[index] - value) * progress);
  };
  for (const boundary of boundaries) {
    const from = valueIn(boundary.timestamp, segments.at(-1), true);
    // Zero-window modes use the current goal, matching the runtime's explicit
    // step handling; continuous curves aim for the end of the transition.
    const targetTime = mode !== "default" && light + dark === 0 ? boundary.timestamp : boundary.timestamp + duration;
    segments.push({ ...boundary, from, to: boundary.sleeping ? sleepValue : normal(targetTime) });
  }
  const at = (timestamp) => context.sleep_all_day ? sleepValue
    : valueIn(timestamp, segments.findLast((segment) => segment.timestamp <= timestamp));
  const times = new Set([start, end]);
  const jumps = new Set();
  const add = (timestamp) => { if (timestamp >= start && timestamp <= end) times.add(timestamp); };
  for (const [minute] of [...brightness, ...temperature]) add(start + minute * 60);
  for (const series of [brightness, temperature]) series.forEach(([minute], index) => {
    if (index && minute === series[index - 1][0]) jumps.add(start + minute * 60);
  });
  for (const segment of segments) {
    add(segment.timestamp); add(segment.timestamp + duration);
    if (duration === 0) jumps.add(segment.timestamp);
    else if (!segment.sleeping && segment.to.some((value, index) => Math.abs(value - normal(segment.timestamp + duration)[index]) > 1e-7)) {
      jumps.add(segment.timestamp + duration);
    }
  }
  const points = [];
  for (const timestamp of [...times].sort((a, b) => a - b)) {
    const value = at(timestamp);
    if (timestamp > start && jumps.has(timestamp)) {
      const before = at(timestamp - 0.00001);
      if (value.some((channel, index) => Math.abs(channel - before[index]) > 0.00001)) {
        points.push([(timestamp - start) / 60, ...before]);
      }
    }
    points.push([(timestamp - start) / 60, ...value]);
  }
  return points;
}

let timingGradientId = 0;

export function layoutTimelineLabels(items, width, gap = 8) {
  const positions = items.map((item) => ({ ...item, width: Math.min(width, item.width) }));
  let row = [], used = 0, rowIndex = 0;
  const place = () => {
    let right = -gap;
    for (const item of row) {
      item.row = rowIndex;
      item.left = Math.max(right + gap, item.anchor - item.width / 2);
      right = item.left + item.width;
    }
    right = width + gap;
    for (const item of [...row].reverse()) {
      item.left = Math.min(item.left, right - gap - item.width);
      right = item.left;
    }
  };
  for (const item of positions) {
    if (row.length && used + gap + item.width > width) { place(); row = []; used = 0; rowIndex++; }
    used += (row.length ? gap : 0) + item.width;
    row.push(item);
  }
  place();
  return positions;
}

export function brightnessTimingDiagram(copy, language = "en", onRetry = () => {}) {
  const figure = element("figure", undefined, "timing-diagram");
  const svgNode = (tag, attributes, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const heading = element("div", undefined, "timing-heading");
  const legend = element("span", undefined, "timing-legend");
  const legends = element("div", undefined, "timing-legends");
  const colorLegend = element("span", copy.temperature, "timing-temperature-legend");
  legends.append(legend, colorLegend);
  heading.append(element("strong", copy.title), legends);
  const plot = element("div", undefined, "timing-plot");
  const bounds = element("div", undefined, "timing-bounds");
  bounds.append(element("span", copy.maximum), element("span", copy.minimum));
  const area = element("div", undefined, "timing-plot-area");
  const labels = element("div", undefined, "timing-event-labels");
  const midpointLabels = element("div", undefined, "timing-midpoint-labels");
  const nightLabels = element("div", undefined, "timing-night-labels");
  let nightLabelItems = [], lastWidth = 0;
  const layoutNightLabels = () => {
    const width = area.clientWidth || 600;
    lastWidth = width;
    const positions = layoutTimelineLabels(nightLabelItems.map(({ label, position }) => ({
      anchor: position * width, width: label.getBoundingClientRect?.().width || 80,
    })), width);
    positions.forEach(({ left, row, anchor, width: labelWidth }, index) => {
      const { label, leader } = nightLabelItems[index];
      label.style.left = `${left}px`;
      label.style.top = `${row * 38}px`;
      const offset = left + labelWidth / 2 - anchor;
      leader.hidden = row !== 0 || Math.abs(offset) < 8;
      leader.style.left = `${anchor - left}px`;
      leader.style.width = `${Math.hypot(offset, 8)}px`;
      leader.style.transform = `rotate(${Math.atan2(8, offset)}rad)`;
    });
    const height = positions.length ? (Math.max(...positions.map((item) => item.row)) + 1) * 38 : 0;
    nightLabels.style.height = `${height}px`;
    bounds.style.setProperty("--night-bottom", `${height ? height + 6 : 0}px`);
  };
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
    if (area.clientWidth !== lastWidth) layoutNightLabels();
  }) : null;
  observer?.observe(area);
  const eventLabels = ["sunrise", "sunset"].map((name) => {
    const label = element("span", undefined, "timing-event-label");
    label.append(element("span", copy[name]), element("time"));
    labels.append(label);
    return label;
  });
  const svg = svgNode("svg", { viewBox: "0 0 600 176", preserveAspectRatio: "none", role: "img", focusable: "false" });
  const title = svgNode("title", {});
  const path = svgNode("path", { class: "timing-curve" });
  const gradientId = `timing-temperature-${++timingGradientId}`;
  const gradient = svgNode("linearGradient", { id: gradientId, x1: "0%", y1: "0%", x2: "100%", y2: "0%", "color-interpolation": "sRGB" });
  const eventLines = svgNode("path", { class: "timing-event" });
  const nightLines = svgNode("path", { class: "timing-night-event" });
  const definitions = svgNode("defs", {});
  definitions.append(gradient);
  svg.append(
    title, definitions,
    svgNode("rect", { x: 0, y: 8, width: 600, height: 160, class: "timing-temperature", fill: `url(#${gradientId})` }),
    svgNode("path", { d: "M0 8 H600 M0 168 H600", class: "timing-grid" }),
    eventLines, nightLines,
    path,
  );
  area.append(labels, midpointLabels, svg, nightLabels);
  plot.append(bounds, area);
  const status = element("p", undefined, "timing-status");
  status.setAttribute("role", "status");
  const caption = element("figcaption");
  const details = element("details", undefined, "timing-night-details");
  const detailText = element("p");
  details.append(element("summary", copy.transitions), detailText);
  const retry = element("button", copy.retry, "secondary timing-retry");
  retry.type = "button";
  retry.addEventListener("click", onRetry);
  figure.append(heading, plot, status, retry, caption, details);
  return { figure, close() { observer?.disconnect(); }, update(values, context, contextStatus = "loading") {
    const mode = values.brightness_mode;
    const light = values.brightness_mode_time_light_minutes;
    const dark = values.brightness_mode_time_dark_minutes;
    const validContext = validSolarContext(context);
    const night = ["sun", "schedule"].includes(values.sleep_activation_mode ?? context?.sleep_mode);
    const nightPoints = night && context?.sleep_mode === (values.sleep_activation_mode ?? context?.sleep_mode) ? nightTimingPoints(values, context) : null;
    const brightnessError = brightnessGraphError(values);
    const points = brightnessError ? null : night ? nightPoints : brightnessTimingPoints(mode, light, dark, context);
    const shared = mode !== "default" && values.apply_temperature_curve === true;
    legend.textContent = `${copy.modes[mode] || ""} · ${copy.brightness}`;
    plot.hidden = !points;
    status.hidden = Boolean(points);
    status.textContent = points ? "" : brightnessError ? copy[brightnessError]
      : validContext ? (night ? copy.nightInvalid : copy.invalid) : copy[contextStatus] || copy.unavailable;
    retry.hidden = validContext || contextStatus === "loading";
    caption.hidden = !points;
    details.hidden = !points || !night;
    if (!points) { path.removeAttribute("d"); gradient.replaceChildren(); return; }
    bounds.children[0].textContent = night ? "100 %" : copy.maximum;
    bounds.children[1].textContent = night ? "0 %" : copy.minimum;
    const dayMinutes = (context.day_end - context.day_start) / 60;
    path.setAttribute("d", points.map(([minute, value], index) => `${index ? "L" : "M"}${minute / dayMinutes * 600} ${168 - value * 160}`).join(" "));
    const positions = ["sunrise", "sunset"].map((name, index) => {
      const position = (context[name].timestamp - context.day_start) / (context.day_end - context.day_start);
      const label = eventLabels[index];
      label.hidden = position < 0 || position > 1;
      label.style.left = `${Math.max(0, Math.min(1, position)) * 100}%`;
      label.style.transform = position < 0.12 ? "none" : position > 0.88 ? "translateX(-100%)" : "translateX(-50%)";
      label.querySelector("time").textContent = context[name].local_time;
      return position;
    });
    const closeLabels = Math.abs(positions[1] - positions[0]) < 0.24;
    labels.style.height = closeLabels ? "78px" : "44px";
    const midpointTimes = (context.midpoints || []).filter((point) => Number.isFinite(point.timestamp)
      && point.timestamp >= context.day_start && point.timestamp < context.day_end && /^\d{2}:\d{2}$/.test(point.local_time || ""));
    midpointLabels.replaceChildren(...midpointTimes.map((point) => {
      const label = element("time", point.local_time);
      const position = (point.timestamp - context.day_start) / (context.day_end - context.day_start);
      label.style.left = `${position * 100}%`;
      label.style.transform = position < 0.08 ? "none" : position > 0.92 ? "translateX(-100%)" : "translateX(-50%)";
      return label;
    }));
    midpointLabels.hidden = midpointTimes.length === 0;
    bounds.style.paddingTop = `${(closeLabels ? 78 : 44) + (midpointTimes.length ? 20 : 0)}px`;
    eventLabels[1].style.top = closeLabels ? "34px" : "0";
    eventLines.setAttribute("d", positions.filter((position) => position >= 0 && position <= 1).map((position) => `M${position * 600} 0 V176`).join(" "));
    const clock = (timestamp) => new Intl.DateTimeFormat(language, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: context.timezone }).format(new Date(timestamp * 1000));
    const edges = night && context.sleep_mode === "schedule" && !context.sleep_all_day
      ? context.sleep_windows.flatMap((window) => [{ timestamp: window.start, name: copy.nightStart, short: copy.nightStartShort },
        { timestamp: window.end, name: copy.nightEnd, short: copy.nightEndShort }])
        .filter((edge) => edge.timestamp >= context.day_start && edge.timestamp < context.day_end) : [];
    nightLabelItems = edges.map((edge) => {
      const position = (edge.timestamp - context.day_start) / (context.day_end - context.day_start);
      const label = element("span", undefined, "timing-event-label");
      const leader = element("span", undefined, "timing-night-leader");
      leader.setAttribute("aria-hidden", "true");
      label.append(element("span", edge.short), element("time", clock(edge.timestamp)));
      label.append(leader);
      label.setAttribute("aria-label", `${edge.name} ${clock(edge.timestamp)}`);
      return { label, position, leader };
    });
    nightLabels.replaceChildren(...nightLabelItems.map((item) => item.label));
    nightLabels.hidden = edges.length === 0;
    layoutNightLabels();
    nightLines.setAttribute("d", edges.map((edge) => `M${(edge.timestamp - context.day_start) / (context.day_end - context.day_start) * 600} 0 V176`).join(" "));
    const date = new Intl.DateTimeFormat(language, { day: "numeric", month: "short", year: "numeric", timeZone: context.timezone }).format(new Date((context.day_start + context.day_end) * 500));
    caption.textContent = copy.caption.replace("{date}", date).replace("{timezone}", context.timezone);
    // Shared timing samples include repeated offsets for a true 0/0 step.
    // Explicit sRGB interpolation keeps color progress linear between stops;
    // dense samples retain the independent parabola and sigmoid's shape.
    const colors = night ? nightPoints.map(([minute, , ...rgb]) => [minute, rgb])
      : temperatureTimingPoints(mode, light, dark, shared, context).map(([minute, value]) => [minute, normalGraphColor(value)]);
    gradient.replaceChildren(...colors.map(([minute, rgb]) => svgNode("stop", { offset: minute / dayMinutes,
      "stop-color": `rgb(${rgb.map(Math.round).join(", ")})` })));
    colorLegend.textContent = night ? `${copy.temperature} · ${copy.night}` : copy.temperature;
    colorLegend.style.setProperty("--night-color", night ? `rgb(${sleepGraphColor(values).map(Math.round).join(", ")})` : "#ff8a1a");
    const nightTiming = (copy.nightTiming || "").replace("{transition}", String(values.transition ?? 45)).replace("{interval}", String(values.interval ?? 90));
    detailText.textContent = context.sleep_all_day ? `${copy.nightAllDay} ${copy.nightScale}` : `${nightTiming} ${copy.nightScale}`;
    // Updating only the figure retains the edited input, focus and scroll.
    const format = (template) => template.replaceAll("{light}", String(light)).replaceAll("{dark}", String(dark)).replaceAll("{total}", String(light + dark));
    // Keep the detailed interpretation available to screen readers without
    // repeating the form's explanations beneath the visible graph.
    const duration = mode === "default" ? copy.natural : light + dark === 0 ? copy.step : format(copy[mode]);
    const description = [legend.textContent,
      ...(mode === "default" || light + dark === 0 ? [] : [format(copy.rise), format(copy.fall)]), duration,
      ...(mode === "default" ? [] : [shared ? copy.synchronized : copy.independent]), night ? detailText.textContent : copy.temperatureScale,
      caption.textContent, `${copy.sunrise} ${context.sunrise.local_time}. ${copy.sunset} ${context.sunset.local_time}.`,
      ...edges.map((edge) => `${edge.name} ${clock(edge.timestamp)}.`)].join(" ");
    title.textContent = description;
    svg.setAttribute("aria-label", description);
  } };
}

function generalSettings(fields, renderField, titles, timingDiagram) {
  const remaining = new Map(fields.map((field) => [field.name, field]));
  const nodes = [];
  if (remaining.has("lights")) {
    nodes.push(renderField(remaining.get("lights")));
    remaining.delete("lights");
  }
  for (const group of [...GENERAL_GROUPS, { id: "other", rows: fields.map((field) => [field.name]) }]) {
    const section = element("fieldset", undefined, "settings-group");
    section.append(element("legend", titles[group.id]));
    let timingRow, modeSelector, activeMode;
    const updateTimingVisibility = () => {
      if (!timingRow || !modeSelector) return;
      timingRow.hidden = modeSelector.value === "default";
      for (const input of timingRow.querySelectorAll("input")) {
        // Hidden Natural windows are retained in the draft, but cannot block
        // native validation of a legacy value outside the new panel bounds.
        input.dataset.timingInactive = String(timingRow.hidden);
        input.disabled = timingRow.hidden;
      }
    };
    for (const names of group.rows) {
      const available = names.filter((name) => remaining.has(name));
      if (!available.length) continue;
      const row = element("div", undefined, available.length > 1 ? "settings-row paired" : "settings-row");
      if (available[0] === "brightness_mode") {
        // Keep the selector and explanation full width. Only Linear and
        // Sigmoid offer a shared temperature curve; Natural keeps its own
        // solar color behavior. Unmounted fields remain in readValues' draft.
        row.className = "settings-row";
        const choice = renderField(remaining.get("brightness_mode"), (input) => {
          // Do not conceal an invalid edit when switching to Natural. Keep
          // the editable control visible and let native validation explain it.
          if (input.value === "default" && timingRow
            && ![...timingRow.querySelectorAll("input")].every((control) => control.reportValidity())) {
            input.value = activeMode;
            return false;
          }
          return true;
        });
        const temperatureField = remaining.get("apply_temperature_curve");
        const selector = choice.querySelector("select");
        modeSelector = selector;
        activeMode = selector.value;
        let temperatureControl;
        row.append(choice);
        const update = () => {
          // field() captures the outgoing checkbox before this listener.
          // Rebuilding from that draft preserves it across mode changes.
          // Keep the selector mounted so keyboard focus does not move.
          if (temperatureControl) row.removeChild(temperatureControl);
          temperatureControl = temperatureField && ["linear", "tanh"].includes(selector.value) ? renderField(temperatureField) : null;
          if (temperatureControl) row.append(temperatureControl);
          activeMode = selector.value;
          updateTimingVisibility();
        };
        selector.addEventListener("input", update);
        update();
        for (const name of available) remaining.delete(name);
        section.append(row);
        continue;
      }
      if (available.length === 3 && available[0] === "sleep_rgb_or_color_temp") {
        const fields = new Map(available.map((name) => [name, remaining.get(name)]));
        let activeMode;
        let activeField;
        const choice = renderField(fields.get("sleep_rgb_or_color_temp"), (input) => {
          // HA validates both saved colors. Never hide an invalid draft where
          // the user cannot repair it before continuing to the next step.
          if (activeField && !activeField.querySelector("input").reportValidity()) {
            input.value = activeMode;
            return false;
          }
          return true;
        });
        const selector = choice.querySelector("select");
        row.append(choice);
        const update = () => {
          if (selector.value === activeMode) return;
          // field() captures the outgoing value before this listener runs.
          // Only the chosen control is mounted; readValues retains both drafts.
          const name = selector.value === "rgb_color" ? "sleep_rgb_color" : "sleep_color_temp";
          activeMode = selector.value;
          const nextField = renderField(fields.get(name));
          if (activeField) row.replaceChild(nextField, activeField);
          else row.append(nextField);
          activeField = nextField;
        };
        selector.addEventListener("input", update);
        update();
        for (const name of available) remaining.delete(name);
        section.append(row);
        continue;
      }
      for (const name of available) {
        row.append(renderField(remaining.get(name)));
        remaining.delete(name);
      }
      section.append(row);
      if (timingDiagram && names[0] === "brightness_mode_time_light_minutes") {
        timingRow = row;
        section.append(timingDiagram);
        updateTimingVisibility();
      }
    }
    if (section.children.length > 1) nodes.push(section);
  }
  return nodes;
}

export function sortedProfileEntries(entries, order = "recent", language = "en") {
  const compareNames = (left, right) => String(left.title ?? "").localeCompare(String(right.title ?? ""), language, { sensitivity: "base", numeric: true });
  const modified = (entry) => {
    for (const value of [entry.modified_at, entry.created_at]) {
      if (typeof value !== "string" || !value.trim()) continue;
      const timestamp = Date.parse(value);
      if (Number.isFinite(timestamp)) return timestamp;
    }
    return -Infinity;
  };
  return [...entries].sort((left, right) => {
    if (order === "name_asc" || order === "name_desc") {
      const comparison = compareNames(left, right);
      if (comparison) return order === "name_desc" ? -comparison : comparison;
    } else {
      const leftTime = modified(left);
      const rightTime = modified(right);
      if (leftTime !== rightTime) return leftTime > rightTime ? -1 : 1;
      const comparison = compareNames(left, right);
      if (comparison) return comparison;
    }
    return String(left.entry_id).localeCompare(String(right.entry_id));
  });
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
  const caret = element("span", undefined, "entity-caret");
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
    this.saveUncertain = false;
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
    this.profileSort = "recent";
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
    this.solarContext?.close();
    this.solarContext = null;
    this.timingChart?.close();
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
    catch (error) { if (error?.reason !== "flow_closed") this.error = error?.message || this.copy.error; }
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
      this.saveUncertain = false;
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
      catch (error) {
        if (revision !== this.creationRevision || this.creator !== creator) return;
        throw new Error(this.copy[error.reason] || error.message || this.copy.error);
      }
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
      const creator = this.creator;
      const revision = this.creationRevision;
      this.applyEntries(await this.readEntries());
      if (revision !== this.creationRevision || this.creator !== creator) return;
      const result = await creator.recover();
      if (revision !== this.creationRevision || this.creator !== creator) return;
      if (result?.type === "form") this.error = this.copy.recoveredForm;
      else {
        await creator.close();
        this.newProfileOpen = false;
        this.message = this.copy.uncertainList;
      }
    });
  }

  async closeWizard() {
    this.solarContext?.close();
    this.solarContext = null;
    this.timingChart?.close();
    const flow = this.flow;
    if (!flow) return;
    try { await flow.close(); }
    finally {
      if (this.flow === flow) {
        this.flow = this.session = null;
        this.message = this.createdProfile ? this.copy.createdKept : "";
        this.createdProfile = null;
      }
    }
  }

  showStep(result) {
    if (result.type === "abort") {
      if (this.flow) this.flow.closed = true;
      this.flow = this.session = null;
      this.createdProfile = null;
      this.message = "";
      this.error = this.translations.abort?.[result.reason] || result.reason || this.copy.error;
      return;
    }
    if (["create_entry", "save_uncertain"].includes(result.type)) {
      this.flow = null;
      this.session = null;
      this.saveUncertain = result.type === "save_uncertain";
      this.message = this.saveUncertain ? this.copy.save_uncertain : this.copy.saved;
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
    if (this.busy) return;
    // Native constraint validation excludes disabled controls. Validate and
    // capture the form before action freezes its inputs for the request.
    let data;
    try { data = this.readValues(!back); }
    catch (error) {
      this.error = error?.message || this.copy.error;
      this.updateControls();
      return;
    }
    await this.action(async () => {
      Object.assign(this.settings, data);
      const flow = this.flow;
      let result;
      try { result = back ? await flow.back(data) : await flow.next(data); }
      catch (error) {
        if (["submission_uncertain", "flow_expired"].includes(error?.reason)) throw new Error(this.copy[error.reason]);
        throw error;
      }
      if (this.flow !== flow) return;
      this.showStep(result);
      this.shadowRoot.querySelector("main")?.scrollTo(0, 0);
    });
    if (!this.initialized && !this.flow) this.initialize();
  }

  field(field, beforeChange) {
    const name = field.name;
    const step = this.flow.current.step_id;
    const text = this.translations.step?.[step] || {};
    const selector = field.selector || {};
    const entityField = Boolean(selector.entity || field.type === "multi_select");
    const calibration = ["cal_neutral", "cal_warm"].includes(step);
    const timingPortion = step === "init" && name.match(/^brightness_mode_time_(light|dark)_minutes$/)?.[1];
    const wrapper = element(entityField || calibration || timingPortion ? "div" : "label", undefined, "field");
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
      const optionLabels = this.translations.selector?.[selector.select?.translation_key]?.options || {};
      for (const choice of options) {
        const [id, label] = Array.isArray(choice) ? choice : typeof choice === "string" ? [choice, choice] : [choice.value, choice.label];
        const option = element("option", optionLabels[id] ?? label ?? id);
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
    const brightnessField = step === "init" && BRIGHTNESS_FIELDS.has(name) && input.type === "number";
    const brightnessLimit = brightnessField && ["min_brightness", "max_brightness"].includes(name);
    if (brightnessField) {
      input.min = 1;
      input.max = 100;
      input.step = 1;
      if (brightnessLimit) {
        wrapper.className += " brightness-limit";
        label.className += " brightness-limit-title";
        const range = element("span", "1–100", "brightness-limit-range");
        range.id = `field-${name}-range`;
        label.append(range);
        input.setAttribute("aria-describedby", range.id);
      }
    }
    if (timingPortion) input.setAttribute("aria-labelledby", label.id);
    if (input.type === "checkbox") wrapper.className += " checkbox-field";
    input.required = Boolean(field.required) || input.type === "number";
    const changed = () => {
      if (beforeChange && !beforeChange(input)) return;
      this.values = this.readValues(false);
      Object.assign(this.settings, this.values);
      this.solarContext?.update({ ...this.settings, ...this.values });
      this.timingChart?.update({ ...this.settings, ...this.values }, this.chartSolarContext, this.chartSolarStatus);
      this.session.refresh();
    };
    if ((calibration || timingPortion) && input.type === "number" && input.min !== "" && input.max !== ""
      && Number.isFinite(Number(input.min)) && Number.isFinite(Number(input.max))) {
      // The number remains the only submitted field. The slider is another
      // way to edit it, never a second source of settings or preview requests.
      const mixture = name === "cal_white_kelvin_neutral";
      const channel = name.match(/^cal_rgb_(?:neutral|warm)_([rgb])$/)?.[1] || (mixture ? "mixture" : "white");
      wrapper.className += timingPortion ? " timing-control" : ` calibration-${channel}`;
      wrapper.setAttribute("role", "group");
      wrapper.setAttribute("aria-labelledby", label.id);
      input.setAttribute("aria-labelledby", label.id);
      if (!timingPortion) {
        const dot = element("span", undefined, "channel-dot");
        dot.setAttribute("aria-hidden", "true");
        label.prepend(dot);
      }
      const controls = element("div", undefined, "calibration-controls");
      const rail = element("div", undefined, "calibration-rail");
      const slider = element("input");
      slider.type = "range";
      slider.min = input.min;
      slider.max = input.max;
      slider.step = input.step;
      slider.value = input.value;
      slider.setAttribute("aria-labelledby", label.id);
      const endpoints = element("div", undefined, "range-endpoints");
      endpoints.setAttribute("aria-hidden", "true");
      endpoints.append(
        element("span", mixture ? `${this.copy.warmWhite} · ${input.min} K` : timingPortion ? `${input.min} min` : input.min),
        element("span", mixture ? `${this.copy.coldWhite} · ${input.max} K` : timingPortion ? `${input.max} min` : input.max),
      );
      const syncSlider = () => {
        // Keep invalid/empty drafts visible for native validation; do not
        // replace them with the range control's automatic clamped value.
        if (input.value !== "" && input.validity.valid) slider.value = input.value;
        if (mixture) slider.setAttribute("aria-valuetext", `${slider.value} K`);
        if (timingPortion) slider.setAttribute("aria-valuetext", `${slider.value} min`);
      };
      syncSlider();
      slider.addEventListener("input", () => { input.value = slider.value; syncSlider(); changed(); });
      input.addEventListener("input", () => { syncSlider(); changed(); });
      rail.append(slider, endpoints);
      controls.append(rail, input);
      wrapper.append(controls);
    } else {
      input.addEventListener("input", changed);
      wrapper.append(input);
    }
    const description = text.data_description?.[name];
    if (brightnessField && !brightnessLimit) {
      // Paired fields share three subgrid rows: title, control, help. Keep the
      // range in the same help element so it cannot create an overlapping row.
      const help = element("small", [description, this.copy.brightnessRangeHint || "1–100 %"].filter(Boolean).join(" "), "brightness-range-help");
      help.id = `field-${name}-help`;
      input.setAttribute("aria-describedby", help.id);
      wrapper.append(help);
    } else if (description && name === "brightness_mode") {
      const help = element("small", undefined, "mode-description");
      const modeNames = Object.values(this.translations.selector?.brightness_mode?.options || this.copy.timingDiagram?.modes || {});
      for (const line of description.split("\n")) {
        const paragraph = element("span");
        const separator = line.indexOf(":");
        if (separator >= 0 && modeNames.includes(line.slice(0, separator).trim())) {
          paragraph.append(element("strong", line.slice(0, separator + 1)), element("span", line.slice(separator + 1)));
        } else paragraph.textContent = line;
        help.append(paragraph);
      }
      wrapper.append(help);
    } else if (description && !brightnessLimit) wrapper.append(element("small", description));
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
    // Freeze submitted inputs while the request is pending, so edits cannot
    // disappear when the next step replaces the form. Navigation stays usable.
    for (const input of this.shadowRoot.querySelectorAll("input,select")) input.disabled = this.busy || input.dataset.timingInactive === "true";
    for (const button of this.shadowRoot.querySelectorAll("button")) {
      button.disabled = this.busy && !String(button.className || "").split(" ").includes("mobile-menu");
    }
    const create = this.shadowRoot.querySelector("#create-profile");
    if (create && this.creator?.uncertain) create.disabled = true;
  }

  render() {
    this.timingChart?.close();
    this.shadowRoot.replaceChildren();
    this.solarContext?.close();
    this.solarContext = null;
    this.timingChart = null;
    const style = element("style");
    style.textContent = `
      :host { display:block; height:100%; color:var(--primary-text-color,#202124); background:var(--primary-background-color,#fafafa); font:14px system-ui,sans-serif; }
      * { box-sizing:border-box; } header { height:64px; display:flex; align-items:center; gap:16px; padding:0 20px; border-bottom:1px solid var(--divider-color,#ddd); background:var(--card-background-color,#fff); }
      header strong { font-size:19px; } main { height:calc(100% - 64px); overflow:auto; padding:28px 20px; }
      article { max-width:850px; margin:auto; padding:28px; border:1px solid var(--divider-color,#ddd); border-radius:14px; background:var(--card-background-color,#fff); container-type:inline-size; }
      h1 { margin:0 0 12px; font-size:24px; } p { line-height:1.55; white-space:pre-line; } .muted,small { color:var(--secondary-text-color,#606770); }
      form { display:grid; gap:22px; margin-top:24px; min-width:0; } .field { display:grid; gap:8px; min-width:0; align-content:start; } .label { font-weight:600; } small { line-height:1.55; }
      .field small { white-space:pre-line; }
      .mode-description { display:grid; gap:7px; }
      .timing-diagram { min-width:0; margin:20px 0 0; padding:16px; border:1px solid var(--divider-color,#ddd); border-radius:10px; background:var(--card-background-color,#fff); }
      .timing-heading { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px 16px; margin-bottom:18px; }
      .timing-legends { display:flex; flex-wrap:wrap; gap:8px 16px; }
      .timing-legend,.timing-temperature-legend { display:flex; align-items:center; gap:7px; color:var(--secondary-text-color,#606770); font-size:12px; }
      .timing-legend::before { content:""; width:20px; height:3px; border-radius:2px; background:#063f42; box-shadow:0 0 0 1px #a9c5c7; }
      .timing-temperature-legend::before { content:""; width:24px; height:9px; border:1px solid #808080; border-radius:2px; background:linear-gradient(to right,var(--night-color,#ff8a1a),#fff); }
      .timing-plot { display:grid; grid-template-columns:36px minmax(0,1fr); gap:5px; }
      .timing-bounds { display:flex; flex-direction:column; justify-content:space-between; padding:44px 0 var(--night-bottom,0px); color:var(--secondary-text-color,#606770); font-size:12px; line-height:16px; }
      .timing-plot-area { min-width:0; }
      .timing-event-labels { position:relative; height:44px; font-size:12px; line-height:18px; color:var(--secondary-text-color,#606770); }
      .timing-event-label { position:absolute; display:flex; flex-direction:column; align-items:center; white-space:nowrap; }
      .timing-event-label time { font-variant-numeric:tabular-nums; font-size:11px; }
      .timing-midpoint-labels { position:relative; height:20px; font-size:11px; line-height:16px; font-variant-numeric:tabular-nums; color:var(--secondary-text-color,#606770); }
      .timing-midpoint-labels time { position:absolute; white-space:nowrap; }
      .timing-night-labels { position:relative; margin-top:6px; font-size:11px; line-height:17px; color:var(--secondary-text-color,#606770); }
      .timing-night-leader { position:absolute; top:-8px; height:1px; border-top:1px solid #c62828; opacity:.55; transform-origin:left center; pointer-events:none; }
      .timing-diagram svg { display:block; width:100%; height:176px; overflow:visible; }
      .timing-grid,.timing-event,.timing-night-event,.timing-curve { fill:none; vector-effect:non-scaling-stroke; }
      .timing-grid { stroke:#34445a; stroke-opacity:.3; stroke-width:1; }
      .timing-event { stroke:#34445a; stroke-opacity:.7; stroke-width:1; stroke-dasharray:3 4; }
      .timing-night-event { stroke:#c62828; stroke-width:1.5; stroke-dasharray:2 4; }
      .timing-curve { stroke:#063f42; stroke-width:2.5; stroke-linejoin:round; stroke-linecap:round; }
      .timing-status { font-size:13px; line-height:1.5; }
      .timing-diagram figcaption { margin-top:12px; color:var(--secondary-text-color,#606770); font-size:12px; line-height:1.45; }
      .timing-night-details { margin-top:10px; color:var(--secondary-text-color,#606770); font-size:12px; line-height:1.5; }
      .timing-night-details summary { cursor:pointer; width:fit-content; padding:4px 0; }
      .timing-night-details summary:focus-visible { outline:2px solid var(--primary-color,#009f8a); outline-offset:3px; border-radius:2px; }
      .timing-night-details p { margin:6px 0 0; }
      .timing-control { --channel-track:linear-gradient(to right,var(--divider-color,#ddd),var(--primary-color,#009f8a)); }
      .timing-control .calibration-controls { grid-template-columns:minmax(0,1fr) 82px; gap:12px; }
      .settings-row[hidden],.timing-diagram [hidden] { display:none; }
      .settings-group { min-width:0; margin:0; padding:20px; border:1px solid var(--divider-color,#ddd); border-radius:12px; background:var(--secondary-background-color,#f1f4f7); background:color-mix(in srgb,var(--card-background-color,#fff) 94%,var(--primary-text-color,#202124)); }
      .settings-group legend { max-width:100%; padding:0 8px; font-size:16px; font-weight:650; }
      .settings-row { display:grid; gap:8px 24px; min-width:0; } .settings-row + .settings-row { margin-top:22px; }
      .settings-row.paired { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .settings-row.paired > .field { grid-template-rows:subgrid; grid-row:span 3; }
      .settings-row.paired > .brightness-limit { grid-row:span 2; }
      .brightness-limit-title { display:flex; justify-content:flex-start; align-items:baseline; gap:8px; }
      .brightness-limit-range { flex-shrink:0; font-weight:400; color:var(--secondary-text-color,#606770); font-variant-numeric:tabular-nums; }
      .settings-group .label,.settings-group small { overflow-wrap:anywhere; }
      .settings-group .checkbox-field { grid-template-columns:20px minmax(0,1fr); align-items:center; }
      .settings-group .checkbox-field .label { grid-column:2; grid-row:1; }
      .settings-group .checkbox-field input { grid-column:1; grid-row:1; }
      .settings-group .checkbox-field small { grid-column:2; }
      .settings-group input[type=number],.settings-group input[type=time] { max-width:220px; min-width:0; }
      @container(max-width:540px) { .settings-group { padding:16px; } .settings-row.paired { grid-template-columns:minmax(0,1fr); gap:22px; } .settings-row.paired > .field { grid-template-rows:none; grid-row:auto; gap:8px; } }
      input:not([type=checkbox]):not([type=range]),select { color:inherit; background:var(--card-background-color,#fff); font:inherit; width:100%; min-height:42px; padding:9px 12px; border:1px solid var(--divider-color,#aeb4bc); border-radius:8px; }
      .calibration-controls { display:grid; grid-template-columns:minmax(0,1fr) 96px; gap:18px; align-items:start; }
      .calibration-rail { min-width:0; } .calibration-controls input[type=number] { text-align:center; }
      .calibration-r { --channel-color:#ff0000; } .calibration-g { --channel-color:#00ff00; } .calibration-b { --channel-color:#0000ff; }
      .calibration-white { --channel-color:#ffffff; } .calibration-mixture { --channel-color:#edbb70; --channel-track:linear-gradient(to right,#efb665 0%,#ffffff 50%,#8fc7ff 100%); }
      .channel-dot { display:inline-block; width:12px; height:12px; margin-right:9px; border-radius:50%; background:var(--channel-track,var(--channel-color)); border:1px solid #808080; }
      input[type=range] { appearance:none; -webkit-appearance:none; display:block; width:100%; height:42px; margin:0; background:transparent; cursor:pointer; }
      input[type=range]::-webkit-slider-runnable-track { height:8px; border-radius:5px; background:var(--channel-track,linear-gradient(to right,#000000 0%,var(--channel-color) 100%)); border:1px solid #808080; }
      input[type=range]::-moz-range-track { height:6px; border-radius:5px; background:var(--channel-track,linear-gradient(to right,#000000 0%,var(--channel-color) 100%)); border:1px solid #808080; }
      input[type=range]::-webkit-slider-thumb { appearance:none; -webkit-appearance:none; width:22px; height:22px; margin-top:-8px; border-radius:50%; background:var(--card-background-color,#fff); border:3px solid var(--primary-text-color,#202124); }
      input[type=range]::-moz-range-thumb { width:16px; height:16px; border-radius:50%; background:var(--card-background-color,#fff); border:3px solid var(--primary-text-color,#202124); }
      input[type=range]:focus-visible { outline:2px solid var(--primary-color,#0077ab); outline-offset:2px; border-radius:6px; }
      input[type=range]:disabled { opacity:.55; cursor:wait; } .range-endpoints { display:flex; justify-content:space-between; gap:8px; color:var(--secondary-text-color,#606770); font-size:12px; }
      input[type=checkbox] { width:20px; height:20px; accent-color:var(--primary-color,#03a9f4); margin:0; } label.preview { display:flex; align-items:center; gap:10px; font-weight:600; }
      .entity-picker { border:1px solid var(--divider-color,#aeb4bc); border-radius:8px; background:var(--card-background-color,#fff); }
      .entity-picker summary { display:flex; align-items:center; gap:12px; min-height:42px; padding:8px 12px; cursor:pointer; list-style:none; }
      .entity-picker summary::-webkit-details-marker { display:none; } .entity-picker summary:focus-visible { outline:2px solid var(--primary-color,#0077ab); outline-offset:2px; border-radius:8px; }
      .entity-selection { display:flex; align-items:center; flex-wrap:wrap; gap:6px; min-width:0; flex:1; } .entity-chip { max-width:100%; padding:4px 9px; border-radius:6px; background:var(--secondary-background-color,#f1f4f7); overflow-wrap:anywhere; }
      .entity-caret { flex-shrink:0; width:8px; height:8px; margin:-4px 3px 0; border-right:1.5px solid currentColor; border-bottom:1.5px solid currentColor; transform:rotate(45deg); } .entity-picker[open] .entity-caret { margin-top:4px; transform:rotate(225deg); }
      .entity-choices { max-height:240px; overflow:auto; padding:6px; border-top:1px solid var(--divider-color,#ddd); }
      .entity-choice { display:flex; align-items:center; gap:12px; padding:10px; border-radius:6px; cursor:pointer; } .entity-choice:hover { background:var(--secondary-background-color,#f1f4f7); } .entity-choice input { flex-shrink:0; }
      .entity-choice-text { display:grid; gap:3px; min-width:0; overflow-wrap:anywhere; } .entity-choice small { font-size:12px; }
      .preview-box { padding:18px; margin:24px 0; border-radius:10px; background:var(--secondary-background-color,#f1f4f7); } .preview-box p { margin:8px 0 0; }
      .actions { display:flex; justify-content:flex-end; gap:12px; margin-top:28px; } button { font:inherit; font-weight:600; border-radius:8px; padding:10px 18px; border:1px solid var(--divider-color,#ddd); cursor:pointer; background:var(--card-background-color,#fff); color:var(--primary-color,#0077ab); }
      .profiles-toolbar { display:flex; align-items:end; justify-content:space-between; flex-wrap:wrap; gap:12px; margin:24px 0 18px; }
      .profile-sort { display:grid; gap:7px; max-width:100%; } .profile-sort select { width:auto; max-width:100%; }
      .profile-table { width:100%; border-collapse:collapse; table-layout:fixed; } .profile-table th { text-align:left; font-weight:600; }
      .profile-table thead th { padding:0 12px 12px; color:var(--secondary-text-color,#606770); font-size:13px; }
      .profile-table thead th:last-child { width:72px; } .profile-table tbody th { padding:16px 12px; overflow-wrap:anywhere; }
      .profile-table td { padding:10px 0 10px 12px; text-align:right; } .profile-table tbody tr { border-top:1px solid var(--divider-color,#ddd); }
      .profile-settings { width:44px; height:44px; display:inline-flex; align-items:center; justify-content:center; padding:9px; border-color:var(--secondary-text-color,#606770); border-color:color-mix(in srgb,var(--secondary-text-color,#606770) 65%,var(--card-background-color,#fff)); }
      .profile-settings:hover { border-color:var(--primary-color,#0077ab); background:var(--secondary-background-color,#f1f4f7); }
      .tune-icon { display:grid; grid-template-columns:repeat(3,1.5px); justify-content:space-between; width:24px; height:24px; padding:2px 3.25px; }
      .tune-icon span { position:relative; border-radius:1px; background:currentColor; }
      .tune-icon span::after { content:""; position:absolute; left:50%; transform:translateX(-50%); top:4px; width:6px; height:2px; border-radius:1px; background:currentColor; }
      .tune-icon span:nth-child(2)::after { top:13px; } .tune-icon span:nth-child(3)::after { top:8px; }
      .profile-settings:focus-visible { outline:2px solid var(--primary-color,#0077ab); outline-offset:2px; }
      .profile-create { display:flex; justify-content:center; margin-top:28px; } .visually-hidden { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
      button.primary { background:var(--primary-color,#0077ab); color:var(--primary-background-color,#081018); border-color:transparent; } button:disabled { opacity:.55; cursor:wait; } .close { margin-left:auto; font-size:13px; }
      .integration-link { display:flex; align-items:center; justify-content:center; margin-left:auto; min-height:44px; padding:10px 14px; border:1px solid var(--divider-color,#ddd); border-radius:8px; color:var(--primary-color,#0077ab); font-size:13px; font-weight:600; line-height:1.3; text-align:center; text-decoration:none; }
      .integration-link:hover { background:var(--secondary-background-color,#f1f4f7); border-color:var(--primary-color,#0077ab); } .integration-link:focus-visible { outline:2px solid var(--primary-color,#0077ab); outline-offset:2px; }
      .mobile-menu { display:none; padding:8px 10px; font-size:22px; line-height:1; } @media(max-width:870px) { .mobile-menu { display:block; } }
      #catalog-warning { margin-bottom:20px; padding:12px; border:1px solid var(--warning-color,#e69500); border-radius:8px; } #catalog-warning p { margin:0 0 10px; }
      #error { background:#b3261e; color:white; padding:12px; border-radius:8px; white-space:pre-line; } .step { margin:0 0 10px; font-size:13px; } .saved { color:var(--success-color,#16814b); } [hidden] { display:none !important; }
      @media(max-width:600px) { article { padding:20px; } main { padding:16px 10px; } header { padding:0 12px; gap:8px; } header strong { font-size:16px; flex-shrink:0; } .close,.integration-link { padding:8px; } .timing-diagram svg { height:117px; } }
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
    } else if (!this.newProfileOpen) {
      const integration = element("a", this.copy.integration, "integration-link");
      integration.href = "/config/integrations/integration/circadian_pro";
      header.append(integration);
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
      const fields = step.data_schema || [];
      if (step.step_id === "white_channels") form.className = "settings-row paired";
      if (step.step_id === "init") {
        if (["brightness_mode", "brightness_mode_time_light_minutes", "brightness_mode_time_dark_minutes"].every((name) => fields.some((field) => field.name === name))) {
          this.chartSolarContext = null;
          this.chartSolarStatus = "loading";
          this.timingChart = brightnessTimingDiagram(this.copy.timingDiagram, this.language, () => this.solarContext?.refresh());
          this.timingChart.update({ ...this.settings, ...this.values }, null);
          const chart = this.timingChart;
          this.solarContext = new SolarContextSession((message) => this._hass.callWS(message), this.entry.entry_id, (context, status) => {
            if (this.timingChart !== chart) return;
            this.chartSolarContext = context;
            this.chartSolarStatus = status;
            chart.update({ ...this.settings, ...this.values }, context, status);
          }, { connection: this._hass?.connection });
          this.solarContext.update({ ...this.settings, ...this.values });
        }
        form.append(...generalSettings(fields, (field, beforeChange) => this.field(field, beforeChange), this.copy.groups, this.timingChart?.figure));
      }
      else for (const field of fields) form.append(this.field(field));
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
      if (this.flow.index > 0) {
        const back = element("button", this.copy.back);
        back.addEventListener("click", () => this.navigate(true));
        actions.append(back);
      }
      const next = element("button", this.flow.pendingSubmission ? this.copy.checkStep : last ? this.copy.finish : this.copy.next, "primary");
      next.addEventListener("click", () => this.navigate());
      actions.append(next);
      article.append(error, actions);
    } else {
      article.append(element("h1", this.copy.title));
      if (this.message) article.append(element("p", this.message, this.saveUncertain ? "muted" : "saved"));
      if (this.pendingCreated) {
        article.append(element("p", this.pendingCreated.title, "label"));
        const retry = element("button", this.copy.retryLoading, "primary");
        retry.addEventListener("click", () => this.action(() => this.loadCreatedEntry()));
        article.append(retry);
      }
      if (this.entries.length) {
        const toolbar = element("div", undefined, "profiles-toolbar");
        const sortLabel = element("label", undefined, "profile-sort");
        sortLabel.append(element("span", this.copy.sortProfiles, "label"));
        const sort = element("select");
        sort.id = "profile-sort";
        for (const [value, label] of [["recent", this.copy.sortRecent], ["name_asc", this.copy.sortNameAsc], ["name_desc", this.copy.sortNameDesc]]) {
          const option = element("option", label);
          option.value = value;
          sort.append(option);
        }
        sort.value = this.profileSort || "recent";
        sort.addEventListener("change", () => {
          this.profileSort = sort.value;
          this.render();
          this.shadowRoot.querySelector("#profile-sort")?.focus?.();
        });
        sortLabel.append(sort);
        toolbar.append(sortLabel);
        if (!this.catalog?.subscribed) {
          const refresh = element("button", this.copy.refreshProfiles);
          refresh.addEventListener("click", () => this.action(async () => { this.applyEntries(await this.readEntries()); }));
          toolbar.append(refresh);
        }
        const table = element("table", undefined, "profile-table");
        table.append(element("caption", this.copy.profiles, "visually-hidden"));
        const headings = element("tr");
        const nameHeading = element("th", this.copy.profile);
        nameHeading.setAttribute("scope", "col");
        const actionHeading = element("th");
        actionHeading.setAttribute("scope", "col");
        actionHeading.append(element("span", this.copy.actions, "visually-hidden"));
        headings.append(nameHeading, actionHeading);
        const head = element("thead");
        head.append(headings);
        const body = element("tbody");
        for (const entry of sortedProfileEntries(this.entries, this.profileSort, this.language)) {
          const row = element("tr");
          const name = element("th", entry.title);
          name.setAttribute("scope", "row");
          const actions = element("td");
          const configure = element("button", undefined, "profile-settings");
          configure.type = "button";
          configure.title = `${this.copy.start}: ${entry.title}`;
          configure.setAttribute("aria-label", configure.title);
          const icon = element("span", undefined, "tune-icon");
          icon.setAttribute("aria-hidden", "true");
          icon.append(element("span"), element("span"), element("span"));
          configure.append(icon);
          configure.addEventListener("click", () => this.start(entry.entry_id));
          actions.append(configure);
          row.append(name, actions);
          body.append(row);
        }
        table.append(head, body);
        article.append(toolbar, table);
      } else if (!this.error) article.append(element("p", this.busy ? this.copy.loading : this.copy.empty, "muted"));
      if (!this.pendingCreated && this._hass?.user?.is_admin) {
        const actions = element("div", undefined, "profile-create");
        const create = element("button", this.copy.newProfile, "primary");
        create.type = "button";
        create.addEventListener("click", () => this.openNewProfile());
        actions.append(create);
        article.append(actions);
      }
      article.append(error);
    }
    main.append(article);
    this.shadowRoot.append(style, header, main);
    this.updateControls();
  }
}

const panelAssetHash = new URL(import.meta.url).pathname.match(/\/calibration-panel-([0-9a-f]{16})\.js$/)?.[1];
const panelElementName = "circadian-pro-calibration" + (panelAssetHash ? "-" + panelAssetHash : "");
if (!customElements.get(panelElementName)) {
  customElements.define(panelElementName, CircadianCalibrationPanel);
}
