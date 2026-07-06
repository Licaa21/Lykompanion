function openModal(modal) {
  modal.hidden = false;
  // Double rAF: first lets display:flex paint, second triggers the CSS transition
  requestAnimationFrame(() => requestAnimationFrame(() => modal.classList.add('modal-animate-in')));
}

function closeModal(modal) {
  // Closing the Gaming Journal while "Customize Overlay Layout" is active would otherwise leave a
  // design-only overlay process running in edit mode with nothing in Settings to stop it.
  if (modal.id === "gaming-journal-modal" && overlayDesignModeActive) {
    setOverlayDesignMode(false);
  }
  modal.classList.remove('modal-animate-in');
  setTimeout(() => { modal.hidden = true; }, 240);
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(document.getElementById(btn.dataset.close)));
});

[settingsModal, personalDataModal, diagnosticsModal, debugDetailModal, sendImageModal, document.getElementById("gaming-journal-modal"), document.getElementById("provider-routing-modal")].forEach((modal) => {
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal(modal);
  });
});

// Scoped to the closest .modal so two open-at-different-times modals with their own tab sets
// (Settings, Personal Data, Diagnostics) don't clobber each other's hidden state.
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const modal = btn.closest(".modal");
    modal.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    modal.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.hidden = panel.dataset.tab !== tab;
    });
  });
});

// Secondary pill nav within a tab panel - same show/hide idea as the main tabs, scoped to
// whichever .tab-panel the clicked bar lives in so identical subtab names don't collide.
document.querySelectorAll(".subtab-bar").forEach((bar) => {
  const panel = bar.closest(".tab-panel");
  bar.querySelectorAll(".subtab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      bar.querySelectorAll(".subtab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const subtab = btn.dataset.subtab;
      panel.querySelectorAll(".subtab-panel").forEach((sp) => {
        sp.hidden = sp.dataset.subtab !== subtab;
      });
    });
  });
});

settingsBtn.addEventListener("click", () => {
  openModal(settingsModal);
  populateAudioDevices();
});

// --- Audio device pickers (Voice tab). Devices are machine-specific, so the choice lives in
// localStorage (see core.js helpers), not server config. ---
const micDeviceSelect = document.getElementById("cfg-mic-device");
const outputDeviceSelect = document.getElementById("cfg-output-device");

async function populateAudioDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  let devices = [];
  try {
    devices = await navigator.mediaDevices.enumerateDevices();
  } catch (err) {
    return;  // permission/enumeration failed — leave the selects as-is
  }
  const fill = (selectEl, kind, savedId, defaultLabel, genericName) => {
    const matching = devices.filter((d) => d.kind === kind);
    selectEl.innerHTML = "";
    const def = document.createElement("option");
    def.value = "";
    def.textContent = defaultLabel;
    selectEl.appendChild(def);
    matching.forEach((d, i) => {
      const opt = document.createElement("option");
      opt.value = d.deviceId;
      // Labels are blank until mic permission is granted this session — fall back to a number.
      opt.textContent = d.label || `${genericName} ${i + 1}`;
      selectEl.appendChild(opt);
    });
    // Restore the saved choice if that device is still present; otherwise fall back to default.
    selectEl.value = savedId;
    if (selectEl.value !== savedId) selectEl.value = "";
  };
  fill(micDeviceSelect, "audioinput", getSelectedMicId(), "System default microphone", "Microphone");
  fill(outputDeviceSelect, "audiooutput", getSelectedOutputId(), "System default output", "Output");
}

micDeviceSelect.addEventListener("change", () => {
  setSelectedMicId(micDeviceSelect.value);
  // Restart hands-free capture on the new device if it's currently live.
  if (typeof restartLiveMicForDeviceChange === "function") restartLiveMicForDeviceChange();
});
outputDeviceSelect.addEventListener("change", async () => {
  setSelectedOutputId(outputDeviceSelect.value);
  await applyOutputDevice(narrationAudio);   // persistent narration player
  await applyOutputToAudioContext();          // beeps / wake chime / SFX
});
document.getElementById("cfg-refresh-devices").addEventListener("click", populateAudioDevices);
// A device being plugged/unplugged while Settings is open re-syncs the lists.
if (navigator.mediaDevices) navigator.mediaDevices.addEventListener?.("devicechange", populateAudioDevices);

// --- Overlay edit hotkey capture ---
// No native <select>/text-entry fits a key combo well, so this is a small self-contained
// "press a key combo" recorder: one-shot keydown listener, builds a canonical string, rejects
// combos with no modifier (so it never steals a plain letter the user might type elsewhere).
const overlayHotkeyDisplay = document.getElementById("cfg-overlay-hotkey-display");
const overlayHotkeyRecordBtn = document.getElementById("cfg-overlay-hotkey-record");

function formatHotkeyEvent(event) {
  const mods = [];
  if (event.ctrlKey) mods.push("Ctrl");
  if (event.shiftKey) mods.push("Shift");
  if (event.altKey) mods.push("Alt");
  if (event.metaKey) mods.push("Win");
  const key = event.key.length === 1 ? event.key.toUpperCase() : event.key;
  return { mods, key, combo: [...mods, key].join("+") };
}

const HOTKEY_BARE_MODIFIERS = new Set(["Control", "Shift", "Alt", "Meta"]);

overlayHotkeyRecordBtn.addEventListener("click", () => {
  overlayHotkeyDisplay.textContent = "Press a key combo…";
  overlayHotkeyDisplay.classList.add("recording");
  overlayHotkeyRecordBtn.disabled = true;

  const onKeydown = (event) => {
    if (HOTKEY_BARE_MODIFIERS.has(event.key)) return;  // wait for a real key
    event.preventDefault();
    document.removeEventListener("keydown", onKeydown, true);
    overlayHotkeyRecordBtn.disabled = false;
    overlayHotkeyDisplay.classList.remove("recording");

    const { mods, combo } = formatHotkeyEvent(event);
    if (mods.length === 0) {
      // Reject combos with no modifier — display reverts, nothing is captured.
      overlayHotkeyDisplay.textContent = overlayEditHotkey;
      return;
    }
    overlayEditHotkey = combo;
    overlayHotkeyDisplay.textContent = combo;
  };
  document.addEventListener("keydown", onKeydown, true);
});

// --- Overlay design mode ("Customize Overlay Layout") ---
// Lets the user open the overlay's edit mode straight from Settings, without a game running, so
// there's no controller double-action risk (nothing else is reading the gamepad). Toggled by one
// button; also auto-stopped if the Gaming Journal modal is closed while still active, so we never
// leave a design-only overlay process running in the background.
const overlayDesignModeBtn = document.getElementById("cfg-overlay-design-mode");
let overlayDesignModeActive = false;

let overlayDesignModePoll = null;

async function setOverlayDesignMode(active) {
  overlayDesignModeActive = active;
  overlayDesignModeBtn.textContent = active ? "Stop Editing" : "Start Editing";
  overlayDesignModeBtn.classList.toggle("active", active);
  clearInterval(overlayDesignModePoll);
  overlayDesignModePoll = null;
  try {
    const res = await fetch(`/api/overlay/design-mode/${active ? "start" : "stop"}`, { method: "POST" });
    if (active && res.ok) {
      // The backend downgrades to a restricted (move/save/switch-preset only) session instead of
      // full customization if a game is actually being tracked right now - surface that so the
      // button label doesn't silently lie about what just opened.
      const { full } = await res.json();
      if (full === false) overlayDesignModeBtn.textContent = "Stop Editing (restricted — game active)";

      // Save/Discard/B inside the overlay itself can end the session with no message back to us
      // (the pipe is write-only) - poll its actual window visibility so this button doesn't stay
      // stuck reading "Stop Editing" after the user already closed it from inside the overlay.
      overlayDesignModePoll = setInterval(async () => {
        try {
          const statusRes = await fetch("/api/overlay/design-mode/status");
          const { active: stillActive } = await statusRes.json();
          if (!stillActive) setOverlayDesignMode(false);
        } catch (err) {
          // best-effort — a transient failure just means we check again next tick
        }
      }, 2000);
    }
  } catch (err) {
    // best-effort — overlay may be disabled/missing
  }
}

overlayDesignModeBtn.addEventListener("click", () => setOverlayDesignMode(!overlayDesignModeActive));

function populateSelect(selectEl, options, selectedValue) {
  selectEl.innerHTML = "";
  for (const { value, label } of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    selectEl.appendChild(option);
  }
  if (selectedValue) {
    if (!options.some((o) => o.value === selectedValue)) {
      selectEl.appendChild(new Option(selectedValue, selectedValue));
    }
    selectEl.value = selectedValue;
  }
}

function sortByLabel(options) {
  return [...options].sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
}

// Full (unfiltered, sorted) option lists per dropdown, kept around so the search
// boxes can re-filter without needing to re-fetch from the backend.
const modelOptionsCache = {
  llm: [],
  memoryModel: [],
  gameStateModel: [],
  transcriptionModel: [],
  kokoroVoice: [],
  openrouterTts: [],
  chirp3Voice: [],
};

// Each OpenRouter Speech model has its own voice catalog (e.g. Gemini uses
// "Kore"/"Puck", Kokoro uses "af_heart", etc.) — keyed by model id so the
// voice dropdown can be rebuilt whenever the TTS model selection changes.
let openrouterSpeechModelsById = {};

function updateOpenrouterVoiceOptions(modelId, selectedVoice) {
  const model = openrouterSpeechModelsById[modelId];
  const voices = (model && model.supported_voices) || [];
  populateSelect(
    document.getElementById("cfg-openrouter-voice"),
    voices.map((v) => ({ value: v, label: v })),
    selectedVoice
  );
}

document.getElementById("cfg-openrouter-tts-model").addEventListener("change", (event) => {
  updateOpenrouterVoiceOptions(event.target.value, null);
});

function setupModelSearch(searchInputId, selectId, cacheKey, pinnedOption) {
  const searchInput = document.getElementById(searchInputId);
  const selectEl = document.getElementById(selectId);

  searchInput.addEventListener("input", () => {
    const query = searchInput.value.toLowerCase();
    const filtered = modelOptionsCache[cacheKey].filter((o) => o.label.toLowerCase().includes(query));
    const options = pinnedOption ? [pinnedOption, ...filtered] : filtered;
    const currentValue = selectEl.value;
    populateSelect(selectEl, options, options.some((o) => o.value === currentValue) ? currentValue : undefined);
  });
}

setupModelSearch("cfg-model-search", "cfg-model", "llm");
setupModelSearch("cfg-memory-model-search", "cfg-memory-model", "memoryModel", {
  value: "",
  label: "(use main chat model)",
});
setupModelSearch("cfg-game-state-model-search", "cfg-game-state-model", "gameStateModel", {
  value: "",
  label: "(use main chat model)",
});
setupModelSearch("cfg-transcription-model-search", "cfg-transcription-model", "transcriptionModel");
setupModelSearch("cfg-kokoro-voice-search", "cfg-kokoro-voice", "kokoroVoice");
setupModelSearch("cfg-chirp3-voice-search", "cfg-chirp3-voice", "chirp3Voice");
setupModelSearch("cfg-openrouter-tts-model-search", "cfg-openrouter-tts-model", "openrouterTts");

// Each LLM feature (main chat, memory extraction, game-state) picks its own provider
// independently - an empty feature provider select means "inherit the main chat provider."
const PROVIDER_FEATURES = {
  // Transcription mode strips audio (and, deliberately, image) off the main model's job -
  // voice messages arrive as plain transcribed text, so the model only needs to talk.
  llm: {
    endpoint: () => (transcriptionEnabledInput.checked ? "/api/models/llm/text" : "/api/models/llm"),
    cacheKey: "llm",
    providerSelectId: "cfg-llm-provider",
    selectId: "cfg-model",
    pinned: null,
  },
  memory: {
    endpoint: "/api/models/llm/text",
    cacheKey: "memoryModel",
    providerSelectId: "cfg-memory-provider",
    selectId: "cfg-memory-model",
    pinned: { value: "", label: "(use main chat model)" },
  },
  // Vision-capable list: the extraction pass is sent each poll window's first/last frames as
  // actual screenshots alongside the OCR text, so the model must accept image input.
  gameState: {
    endpoint: "/api/models/llm/vision",
    cacheKey: "gameStateModel",
    providerSelectId: "cfg-game-state-provider",
    selectId: "cfg-game-state-model",
    pinned: { value: "", label: "(use main chat model)" },
  },
  // No providerSelectId - dedicated transcription (ASR) models are an OpenRouter-only catalog,
  // called through a separate transcription API rather than chat completions.
  transcription: {
    endpoint: "/api/models/llm/audio",
    cacheKey: "transcriptionModel",
    providerSelectId: null,
    selectId: "cfg-transcription-model",
    pinned: null,
  },
};

function effectiveProvider(providerSelectId) {
  if (!providerSelectId) return "openrouter";
  const value = document.getElementById(providerSelectId).value;
  if (providerSelectId === "cfg-llm-provider") return value || "openrouter";
  return value || document.getElementById("cfg-llm-provider").value || "openrouter";
}

async function reloadModelSelect(featureKey, selectedValue, force = false) {
  const spec = PROVIDER_FEATURES[featureKey];
  const selectEl = document.getElementById(spec.selectId);
  const currentValue = selectedValue !== undefined ? selectedValue : selectEl.value;
  const provider = effectiveProvider(spec.providerSelectId);
  const endpoint = typeof spec.endpoint === "function" ? spec.endpoint() : spec.endpoint;

  let models = [];
  try {
    const response = await fetch(`${endpoint}?provider=${encodeURIComponent(provider)}${force ? "&force=true" : ""}`);
    if (response.ok) models = await response.json();
  } catch (err) {
    models = [];
  }

  modelOptionsCache[spec.cacheKey] = sortByLabel(models.map((m) => ({ value: m.id, label: m.name })));
  populateSelect(
    selectEl,
    spec.pinned ? [spec.pinned, ...modelOptionsCache[spec.cacheKey]] : modelOptionsCache[spec.cacheKey],
    currentValue
  );
}

for (const featureKey of Object.keys(PROVIDER_FEATURES)) {
  const providerSelectId = PROVIDER_FEATURES[featureKey].providerSelectId;
  if (!providerSelectId) continue;
  document.getElementById(providerSelectId).addEventListener("change", () => {
    reloadModelSelect(featureKey);
    // Main chat provider changing also affects any feature currently inheriting it.
    if (featureKey === "llm") {
      for (const other of ["memory", "gameState"]) {
        if (!document.getElementById(PROVIDER_FEATURES[other].providerSelectId).value) reloadModelSelect(other);
      }
    }
  });
}

// --- Per-model OpenRouter "Providers" picker ---
// OpenRouter routes each model across several underlying providers with their own price/context/
// quantization/reliability - this lets the user restrict routing to specific providers per model
// (main chat, memory extraction, game-state), rather than a single site-wide preference, since
// which providers are worth using genuinely differs per model.

const providerRoutingModal = document.getElementById("provider-routing-modal");
const prModelNameEl = document.getElementById("pr-model-name");
const prTableBody = document.getElementById("pr-table-body");
const prEmptyHint = document.getElementById("pr-empty-hint");
const prSortSelect = document.getElementById("pr-sort");
const prAllowFallbacksInput = document.getElementById("pr-allow-fallbacks");
const prMaxPricePromptInput = document.getElementById("pr-max-price-prompt");
const prMaxPriceCompletionInput = document.getElementById("pr-max-price-completion");

let prCurrentModelId = null;
let prEndpoints = [];
let prChecked = new Set();
let prSortColumn = "pricing_prompt";
let prSortAsc = true;

function fmtMoney(perToken) {
  return perToken == null ? "—" : (perToken * 1e6).toFixed(3);
}
function fmtPercent(value) {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}
function fmtLatency(value) {
  return value == null ? "—" : `${Math.round(value)}ms`;
}
function fmtThroughput(value) {
  return value == null ? "—" : `${value.toFixed(1)} t/s`;
}

function renderProviderTable() {
  const sorted = [...prEndpoints].sort((a, b) => {
    const av = a[prSortColumn];
    const bv = b[prSortColumn];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === "string" ? av.localeCompare(bv) : av - bv;
    return prSortAsc ? cmp : -cmp;
  });

  prTableBody.innerHTML = "";
  for (const ep of sorted) {
    const row = document.createElement("tr");
    const checkboxCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.tag = ep.tag;
    checkbox.checked = prChecked.has(ep.tag);
    checkbox.addEventListener("change", (event) => {
      if (event.target.checked) prChecked.add(ep.tag);
      else prChecked.delete(ep.tag);
    });
    checkboxCell.appendChild(checkbox);
    row.appendChild(checkboxCell);

    const cellValues = [
      ep.provider_name,
      fmtMoney(ep.pricing_prompt),
      fmtMoney(ep.pricing_completion),
      ep.context_length ? ep.context_length.toLocaleString() : "—",
      ep.quantization || "—",
      fmtPercent(ep.uptime_last_30m),
      fmtLatency(ep.latency_last_30m),
      fmtThroughput(ep.throughput_last_30m),
    ];
    for (const value of cellValues) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    prTableBody.appendChild(row);
  }

  document.querySelectorAll("#pr-table th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === prSortColumn);
    th.classList.toggle("sort-asc", th.dataset.sort === prSortColumn && prSortAsc);
  });
}

document.querySelectorAll("#pr-table th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    if (prSortColumn === th.dataset.sort) {
      prSortAsc = !prSortAsc;
    } else {
      prSortColumn = th.dataset.sort;
      prSortAsc = true;
    }
    renderProviderTable();
  });
});

async function openProviderPicker(featureKey) {
  const spec = PROVIDER_FEATURES[featureKey];
  const provider = effectiveProvider(spec.providerSelectId);
  if (provider !== "openrouter") {
    showAlert("Provider routing is an OpenRouter-only feature - this model's feature is currently set to a different provider.");
    return;
  }
  const selectedModel = document.getElementById(spec.selectId).value;
  // An empty selection on memory/gameState means "inherit the main chat model" - resolve to the
  // actual model id so the picker (and the saved routing config) targets what's really called.
  const modelId = selectedModel || document.getElementById("cfg-model").value;
  if (!modelId) {
    showAlert("Pick a model first.");
    return;
  }

  prCurrentModelId = modelId;
  prModelNameEl.textContent = modelId;
  prEndpoints = [];
  prChecked = new Set();
  prTableBody.innerHTML = "";
  prEmptyHint.hidden = true;
  openModal(providerRoutingModal);

  const [endpoints, routing] = await Promise.all([
    fetch(`/api/models/providers/${modelId}`).then((r) => (r.ok ? r.json() : [])),
    fetch(`/api/provider-routing/${modelId}`).then((r) => (r.ok ? r.json() : null)),
  ]);

  prEndpoints = endpoints;
  prEmptyHint.hidden = endpoints.length > 0;

  if (routing) {
    prChecked = new Set(routing.only || []);
    prSortSelect.value = routing.sort || "";
    prAllowFallbacksInput.checked = routing.allow_fallbacks !== false;
    prMaxPricePromptInput.value = routing.max_price_prompt ?? "";
    prMaxPriceCompletionInput.value = routing.max_price_completion ?? "";
  }
  renderProviderTable();
}

document.querySelectorAll(".provider-picker-btn").forEach((btn) => {
  btn.addEventListener("click", () => openProviderPicker(btn.dataset.providerFeature));
});

document.getElementById("pr-save").addEventListener("click", async (event) => {
  if (!prCurrentModelId) return;
  // Capture the button before the await - event.currentTarget is only valid during synchronous
  // event dispatch and is already null by the time an awaited fetch() resolves.
  const button = event.currentTarget;
  const body = {
    only: [...prChecked],
    sort: prSortSelect.value || null,
    allow_fallbacks: prAllowFallbacksInput.checked,
    max_price_prompt: prMaxPricePromptInput.value ? parseFloat(prMaxPricePromptInput.value) : null,
    max_price_completion: prMaxPriceCompletionInput.value ? parseFloat(prMaxPriceCompletionInput.value) : null,
  };
  await fetch(`/api/provider-routing/${prCurrentModelId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  flashSaved(button);
  setTimeout(() => closeModal(providerRoutingModal), 650);
});

async function loadModels(
  selectedLlm,
  selectedKokoroVoice,
  selectedOpenrouterTts,
  selectedOpenrouterVoice,
  selectedMemoryModel,
  selectedChirp3Voice,
  selectedGameStateModel,
  selectedTranscriptionModel,
  force = false
) {
  const [ttsModels] = await Promise.all([
    fetch(`/api/models/tts${force ? "?force=true" : ""}`).then((r) => r.json()),
    reloadModelSelect("llm", selectedLlm, force),
    reloadModelSelect("memory", selectedMemoryModel, force),
    reloadModelSelect("gameState", selectedGameStateModel, force),
    reloadModelSelect("transcription", selectedTranscriptionModel, force),
  ]);

  const speechModels = ttsModels.openrouter_speech_models || [];
  openrouterSpeechModelsById = {};
  for (const m of speechModels) {
    openrouterSpeechModelsById[m.id] = m;
  }

  modelOptionsCache.kokoroVoice = sortByLabel(
    (ttsModels.kokoro_voices || []).map((v) => ({ value: v.id, label: v.name }))
  );
  modelOptionsCache.openrouterTts = sortByLabel(speechModels.map((m) => ({ value: m.id, label: m.name })));
  modelOptionsCache.chirp3Voice = sortByLabel((ttsModels.chirp3_voices || []).map((v) => ({ value: v.id, label: v.name })));

  populateSelect(document.getElementById("cfg-kokoro-voice"), modelOptionsCache.kokoroVoice, selectedKokoroVoice);
  populateSelect(document.getElementById("cfg-chirp3-voice"), modelOptionsCache.chirp3Voice, selectedChirp3Voice);
  populateSelect(
    document.getElementById("cfg-openrouter-tts-model"),
    modelOptionsCache.openrouterTts,
    selectedOpenrouterTts
  );
  updateOpenrouterVoiceOptions(
    selectedOpenrouterTts || document.getElementById("cfg-openrouter-tts-model").value,
    selectedOpenrouterVoice
  );
}

const narrationSpeedInput = document.getElementById("cfg-narration-speed");
const narrationSpeedValue = document.getElementById("cfg-narration-speed-value");

narrationSpeedInput.addEventListener("input", () => {
  narrationSpeedValue.textContent = narrationSpeedInput.value;
});

const narrationVolumeInput = document.getElementById("cfg-narration-volume");
const narrationVolumeValue = document.getElementById("cfg-narration-volume-value");

narrationVolumeInput.addEventListener("input", () => {
  narrationVolumeValue.textContent = narrationVolumeInput.value;
});

// Applies a volume change at the browser/audio level immediately - including to whatever's
// playing right now - rather than only taking effect on the next narrated sentence.
function applyNarrationVolume(volume) {
  narrationVolume = volume;
  const percent = Math.round(volume * 100);
  narrationVolumeInput.value = percent;
  narrationVolumeValue.textContent = percent;
  narrationAudio.volume = narrationVolume;
}

const contextWindowInput = document.getElementById("cfg-context-window");
const contextWindowValue = document.getElementById("cfg-context-window-value");

contextWindowInput.addEventListener("input", () => {
  contextWindowValue.textContent = contextWindowInput.value === "0" ? "all" : contextWindowInput.value;
});

const screenshotWidthInput = document.getElementById("cfg-screenshot-width");
const screenshotWidthValue = document.getElementById("cfg-screenshot-width-value");
const screenshotQualityInput = document.getElementById("cfg-screenshot-quality");
const screenshotQualityValue = document.getElementById("cfg-screenshot-quality-value");

screenshotWidthInput.addEventListener("input", () => {
  screenshotWidthValue.textContent = screenshotWidthInput.value;
});

screenshotQualityInput.addEventListener("input", () => {
  screenshotQualityValue.textContent = screenshotQualityInput.value;
});

const gameStateIntervalInput = document.getElementById("cfg-game-state-interval");
const gameStateIntervalValue = document.getElementById("cfg-game-state-interval-value");

gameStateIntervalInput.addEventListener("input", () => {
  gameStateIntervalValue.textContent = gameStateIntervalInput.value;
});

const memoryRagLimitInput = document.getElementById("cfg-memory-rag-limit");
const memoryRagLimitValue = document.getElementById("cfg-memory-rag-limit-value");

memoryRagLimitInput.addEventListener("input", () => {
  memoryRagLimitValue.textContent = memoryRagLimitInput.value === "0" ? "all" : memoryRagLimitInput.value;
});

const proactiveIntervalInput = document.getElementById("cfg-proactive-interval");
const proactiveIntervalValue = document.getElementById("cfg-proactive-interval-value");

proactiveIntervalInput.addEventListener("input", () => {
  proactiveIntervalValue.textContent = proactiveIntervalInput.value;
});

const gameStateCaptureIntervalInput = document.getElementById("cfg-game-state-capture-interval");
const gameStateCaptureIntervalValue = document.getElementById("cfg-game-state-capture-interval-value");

gameStateCaptureIntervalInput.addEventListener("input", () => {
  gameStateCaptureIntervalValue.textContent = gameStateCaptureIntervalInput.value;
});

const gameStateVisualDiffInput = document.getElementById("cfg-game-state-visual-diff");
const gameStateVisualDiffValue = document.getElementById("cfg-game-state-visual-diff-value");

gameStateVisualDiffInput.addEventListener("input", () => {
  gameStateVisualDiffValue.textContent = gameStateVisualDiffInput.value;
});

const gameStateOcrSimilarityInput = document.getElementById("cfg-game-state-ocr-similarity");
const gameStateOcrSimilarityValue = document.getElementById("cfg-game-state-ocr-similarity-value");

gameStateOcrSimilarityInput.addEventListener("input", () => {
  gameStateOcrSimilarityValue.textContent = parseFloat(gameStateOcrSimilarityInput.value).toFixed(2);
});

const gameStateVisualDiffNoiseFloorInput = document.getElementById("cfg-game-state-visual-diff-noise-floor");
const gameStateVisualDiffNoiseFloorValue = document.getElementById("cfg-game-state-visual-diff-noise-floor-value");

gameStateVisualDiffNoiseFloorInput.addEventListener("input", () => {
  gameStateVisualDiffNoiseFloorValue.textContent = gameStateVisualDiffNoiseFloorInput.value;
});

const gameStateMaxSkipsInput = document.getElementById("cfg-game-state-max-skips");
const gameStateMaxSkipsValue = document.getElementById("cfg-game-state-max-skips-value");

gameStateMaxSkipsInput.addEventListener("input", () => {
  gameStateMaxSkipsValue.textContent = gameStateMaxSkipsInput.value;
});

const gameStateOcrWidthInput = document.getElementById("cfg-game-state-ocr-width");
const gameStateOcrWidthValue = document.getElementById("cfg-game-state-ocr-width-value");

gameStateOcrWidthInput.addEventListener("input", () => {
  gameStateOcrWidthValue.textContent = gameStateOcrWidthInput.value;
});

const gameStateVisualDiffThumbInput = document.getElementById("cfg-game-state-visual-diff-thumb");
const gameStateVisualDiffThumbValue = document.getElementById("cfg-game-state-visual-diff-thumb-value");

gameStateVisualDiffThumbInput.addEventListener("input", () => {
  gameStateVisualDiffThumbValue.textContent = gameStateVisualDiffThumbInput.value;
});

const gameStateFrameTimeoutInput = document.getElementById("cfg-game-state-frame-timeout");
const gameStateFrameTimeoutValue = document.getElementById("cfg-game-state-frame-timeout-value");

gameStateFrameTimeoutInput.addEventListener("input", () => {
  gameStateFrameTimeoutValue.textContent = gameStateFrameTimeoutInput.value;
});

const gameStateEmptyWarnInput = document.getElementById("cfg-game-state-empty-warn");
const gameStateEmptyWarnValue = document.getElementById("cfg-game-state-empty-warn-value");

gameStateEmptyWarnInput.addEventListener("input", () => {
  gameStateEmptyWarnValue.textContent = gameStateEmptyWarnInput.value;
});

const gameStateCaptureCursorInput = document.getElementById("cfg-game-state-capture-cursor");

const avatarPreviewEl = document.getElementById("cfg-avatar-preview");
const avatarInputEl = document.getElementById("cfg-avatar-input");
const avatarUploadBtn = document.getElementById("cfg-avatar-upload-btn");
const avatarRemoveBtn = document.getElementById("cfg-avatar-remove-btn");

function renderAvatarPreview() {
  avatarPreviewEl.innerHTML = userAvatarMarkup();
}

avatarUploadBtn.addEventListener("click", () => avatarInputEl.click());

avatarInputEl.addEventListener("change", async () => {
  const file = avatarInputEl.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("avatar", file);
  const res = await fetch("/api/profile/avatar", { method: "POST", body: formData });
  avatarInputEl.value = "";
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    showToast("avatar-error", { title: "Couldn't set picture", body: error.detail || "Upload failed.", duration: 4000 });
    return;
  }
  hasUserAvatar = true;
  avatarVersion = Date.now();
  renderAvatarPreview();
  refreshVisibleUserIdentity();
});

avatarRemoveBtn.addEventListener("click", async () => {
  await fetch("/api/profile/avatar", { method: "DELETE" });
  hasUserAvatar = false;
  renderAvatarPreview();
  refreshVisibleUserIdentity();
});

const userNameInput = document.getElementById("cfg-user-name");

userNameInput.addEventListener("input", () => {
  userDisplayName = userNameInput.value.trim() || "You";
  refreshVisibleUserIdentity();
  renderAvatarPreview();
});

const gameStateEnabledInput = document.getElementById("cfg-game-state-enabled");
const gameStateDependentEl = document.getElementById("game-state-dependent");

function updateGameStateDependentVisibility() {
  gameStateDependentEl.hidden = !gameStateEnabledInput.checked;
}

gameStateEnabledInput.addEventListener("change", updateGameStateDependentVisibility);

const transcriptionEnabledInput = document.getElementById("cfg-transcription-enabled");
const transcriptionDependentEl = document.getElementById("transcription-dependent");

function updateTranscriptionDependentVisibility() {
  transcriptionDependentEl.hidden = !transcriptionEnabledInput.checked;
}

transcriptionEnabledInput.addEventListener("change", () => {
  updateTranscriptionDependentVisibility();
  // Toggling transcription mode changes which filter the main model list uses (drops the
  // audio+image requirement in favor of text-only), so the dropdown needs to be refreshed.
  reloadModelSelect("llm");
});

const vadThresholdInput = document.getElementById("cfg-vad-threshold");
const vadThresholdValue = document.getElementById("cfg-vad-threshold-value");
const vadSilenceInput = document.getElementById("cfg-vad-silence");
const vadSilenceValue = document.getElementById("cfg-vad-silence-value");
const vadMinSpeechInput = document.getElementById("cfg-vad-min-speech");
const vadMinSpeechValue = document.getElementById("cfg-vad-min-speech-value");
const preRollInput = document.getElementById("cfg-pre-roll");
const preRollValue = document.getElementById("cfg-pre-roll-value");
const postRollInput = document.getElementById("cfg-post-roll");
const postRollValue = document.getElementById("cfg-post-roll-value");

preRollInput.addEventListener("input", () => {
  preRollMs = Number(preRollInput.value);
  preRollValue.textContent = preRollMs;
});

postRollInput.addEventListener("input", () => {
  postRollMs = Number(postRollInput.value);
  postRollValue.textContent = postRollMs;
});

vadThresholdInput.value = vadThreshold;
vadThresholdValue.textContent = vadThreshold;
vadSilenceInput.value = vadSilenceMs;
vadSilenceValue.textContent = vadSilenceMs;
// Slider display values are also updated in loadConfig() once server config is fetched.
vadMinSpeechInput.value = vadMinSpeechMs;
vadMinSpeechValue.textContent = vadMinSpeechMs;

vadThresholdInput.addEventListener("input", () => {
  vadThreshold = Number(vadThresholdInput.value);
  vadThresholdValue.textContent = vadThreshold;
});

vadSilenceInput.addEventListener("input", () => {
  vadSilenceMs = Number(vadSilenceInput.value);
  vadSilenceValue.textContent = vadSilenceMs;
});

vadMinSpeechInput.addEventListener("input", () => {
  vadMinSpeechMs = Number(vadMinSpeechInput.value);
  vadMinSpeechValue.textContent = vadMinSpeechMs;
});

function updateTtsProviderVisibility() {
  const provider = document.getElementById("cfg-tts-provider").value;
  document.querySelectorAll("[data-tts-provider]").forEach((el) => {
    el.hidden = el.dataset.ttsProvider !== provider;
  });
}

document.getElementById("cfg-tts-provider").addEventListener("change", updateTtsProviderVisibility);

function updateWebSearchProviderVisibility() {
  const provider = document.getElementById("cfg-web-search-provider").value;
  document.querySelectorAll("[data-web-search-provider]").forEach((el) => {
    el.hidden = el.dataset.webSearchProvider !== provider;
  });
}

document.getElementById("cfg-web-search-provider").addEventListener("change", updateWebSearchProviderVisibility);

// Applies a CompanionConfig payload (from GET /api/config or the PUT response) to the Settings
// form. Split out from loadConfig() so saving can re-apply the server's response directly instead
// of re-fetching config and re-loading the full OpenRouter model catalog (a slow external API
// call) on every save - the model list can't have changed just because settings were saved.
function applyConfigToForm(cfg) {
  userDisplayName = cfg.user_display_name || "You";
  userNameInput.value = userDisplayName;
  renderAvatarPreview();
  refreshVisibleUserIdentity();
  document.getElementById("cfg-web-search-provider").value = cfg.web_search_provider;
  document.getElementById("cfg-searxng-base-url").value = cfg.searxng_base_url || "";
  updateWebSearchProviderVisibility();
  document.getElementById("cfg-tts-provider").value = cfg.tts_provider;
  updateTtsProviderVisibility();
  transcriptionEnabledInput.checked = cfg.transcription_enabled;
  updateTranscriptionDependentVisibility();
  document.getElementById("cfg-api-key").placeholder = cfg.openrouter_api_key_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-management-key").placeholder = cfg.openrouter_management_key_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-google-ai-studio-key").placeholder = cfg.google_ai_studio_api_key_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-custom-openai-base-url").value = cfg.custom_openai_base_url || "";
  document.getElementById("cfg-custom-openai-key").placeholder = cfg.custom_openai_api_key_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-llm-provider").value = cfg.llm_provider || "openrouter";
  document.getElementById("cfg-memory-provider").value = cfg.memory_extraction_provider || "";
  document.getElementById("cfg-game-state-provider").value = cfg.game_state_provider || "";

  narrationSpeed = cfg.narration_speed;
  narrationSpeedInput.value = cfg.narration_speed;
  narrationSpeedValue.textContent = cfg.narration_speed;

  applyNarrationVolume(cfg.narration_volume);
  document.getElementById("cfg-narrate").checked = cfg.narrate_enabled !== false;
  document.getElementById("cfg-sfx-enabled").checked = cfg.sfx_enabled !== false;

  contextWindowInput.value = cfg.context_window_messages;
  contextWindowValue.textContent = cfg.context_window_messages === 0 ? "all" : cfg.context_window_messages;

  screenshotWidthInput.value = cfg.screenshot_max_width;
  screenshotWidthValue.textContent = cfg.screenshot_max_width;
  screenshotQualityInput.value = cfg.screenshot_jpeg_quality;
  screenshotQualityValue.textContent = cfg.screenshot_jpeg_quality;

  gameStateEnabledInput.checked = cfg.game_state_ocr_enabled;
  updateGameStateDependentVisibility();
  document.getElementById("cfg-overlay-enabled").checked = cfg.overlay_enabled;
  overlayEditHotkey = cfg.overlay_edit_hotkey || "Ctrl+Shift+O";
  document.getElementById("cfg-overlay-hotkey-display").textContent = overlayEditHotkey;
  document.getElementById("cfg-game-state-training-enabled").checked = cfg.game_state_training_enabled;
  document.getElementById("cfg-proactive-enabled").checked = cfg.proactive_messages_enabled;
  document.getElementById("cfg-proactive-interval").value = cfg.proactive_min_interval_minutes ?? 15;
  document.getElementById("cfg-proactive-interval-value").textContent = cfg.proactive_min_interval_minutes ?? 15;
  document.getElementById("cfg-memory-rag-limit").value = cfg.memory_rag_limit ?? 30;
  document.getElementById("cfg-memory-rag-limit-value").textContent = (cfg.memory_rag_limit ?? 30) === 0 ? "all" : cfg.memory_rag_limit ?? 30;
  document.getElementById("cfg-openrouter-base-url").value = cfg.openrouter_base_url || "";
  document.getElementById("cfg-kokoro-base-url").value = cfg.kokoro_base_url || "";
  gameStateIntervalInput.value = cfg.game_state_poll_interval_seconds;
  gameStateIntervalValue.textContent = cfg.game_state_poll_interval_seconds;
  gameStateCaptureIntervalInput.value = cfg.game_state_capture_interval_seconds;
  gameStateCaptureIntervalValue.textContent = cfg.game_state_capture_interval_seconds;
  gameStateVisualDiffInput.value = cfg.game_state_visual_diff_threshold_percent ?? 12;
  gameStateVisualDiffValue.textContent = cfg.game_state_visual_diff_threshold_percent ?? 12;
  gameStateVisualDiffNoiseFloorInput.value = cfg.game_state_visual_diff_noise_floor_percent ?? 1.5;
  gameStateVisualDiffNoiseFloorValue.textContent = cfg.game_state_visual_diff_noise_floor_percent ?? 1.5;
  gameStateMaxSkipsInput.value = cfg.game_state_max_consecutive_skips ?? 0;
  gameStateMaxSkipsValue.textContent = cfg.game_state_max_consecutive_skips ?? 0;
  gameStateOcrSimilarityInput.value = cfg.game_state_ocr_similarity_threshold ?? 0.9;
  gameStateOcrSimilarityValue.textContent = (cfg.game_state_ocr_similarity_threshold ?? 0.9).toFixed(2);
  gameStateOcrWidthInput.value = cfg.game_state_ocr_max_width ?? 1600;
  gameStateOcrWidthValue.textContent = cfg.game_state_ocr_max_width ?? 1600;
  gameStateVisualDiffThumbInput.value = cfg.game_state_visual_diff_thumbnail_size ?? 64;
  gameStateVisualDiffThumbValue.textContent = cfg.game_state_visual_diff_thumbnail_size ?? 64;
  gameStateFrameTimeoutInput.value = cfg.game_state_capture_frame_timeout_seconds ?? 6;
  gameStateFrameTimeoutValue.textContent = cfg.game_state_capture_frame_timeout_seconds ?? 6;
  gameStateEmptyWarnInput.value = cfg.game_state_empty_ocr_warn_threshold ?? 10;
  gameStateEmptyWarnValue.textContent = cfg.game_state_empty_ocr_warn_threshold ?? 10;
  gameStateCaptureCursorInput.checked = cfg.game_state_capture_cursor_enabled ?? false;
  restartPendingApprovalPolling(cfg.game_state_poll_interval_seconds);

  document.getElementById("cfg-debug-mode-enabled").checked = cfg.debug_mode_enabled;

  wakeWordEnabled = cfg.wake_word_enabled;
  wakeWordPhrase = cfg.wake_word_phrase || "Hey Buddy";
  wakeWordMaxFailures = cfg.wake_word_max_failures ?? 3;
  wakeWordMaxFailuresInput.value = wakeWordMaxFailures;
  wakeWordMaxFailuresValue.textContent = wakeWordMaxFailures;
  sleepWordEnabled = cfg.sleep_word_enabled;
  sleepWordPhrase = cfg.sleep_word_phrase || "Go to sleep";
  overlayEditPhraseEnabled = cfg.overlay_edit_phrase_enabled;
  overlayEditPhrase = cfg.overlay_edit_phrase || "Edit overlay";
  handsfreeMode = cfg.handsfree_mode || "handsfree";
  handsfreeModeSelect.value = handsfreeMode;
  if (wakeWordSupported) {
    wakeWordEnabledInput.checked = wakeWordEnabled;
    wakeWordPhraseInput.value = wakeWordPhrase;
    sleepWordEnabledInput.checked = sleepWordEnabled;
    sleepWordPhraseInput.value = sleepWordPhrase;
    overlayEditPhraseEnabledInput.checked = overlayEditPhraseEnabled;
    overlayEditPhraseInput.value = overlayEditPhrase;
    updateWakeWordListenerState();
  }
  updateLiveMicToggleLabel();
  updateVoiceHints();

  vadThreshold = cfg.vad_threshold ?? 8;
  vadSilenceMs = cfg.vad_silence_ms ?? 1200;
  vadMinSpeechMs = cfg.vad_min_speech_ms ?? 300;
  vadThresholdInput.value = vadThreshold;
  vadThresholdValue.textContent = vadThreshold;
  vadSilenceInput.value = vadSilenceMs;
  vadSilenceValue.textContent = vadSilenceMs;
  vadMinSpeechInput.value = vadMinSpeechMs;
  vadMinSpeechValue.textContent = vadMinSpeechMs;

  preRollMs = cfg.pre_roll_ms ?? 1000;
  postRollMs = cfg.post_roll_ms ?? 500;
  preRollInput.value = preRollMs;
  preRollValue.textContent = preRollMs;
  postRollInput.value = postRollMs;
  postRollValue.textContent = postRollMs;

  document.getElementById("cfg-google-tts-api-key").placeholder = cfg.google_tts_api_key_set
    ? "•••••••• (set)"
    : "Not set (uses Application Default Credentials)";
  document.getElementById("cfg-igdb-client-id").value = cfg.igdb_client_id || "";
  document.getElementById("cfg-igdb-client-secret").placeholder = cfg.igdb_client_secret_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-steam-api-key").placeholder = cfg.steam_api_key_set ? "•••••••• (set)" : "Not set";
  document.getElementById("cfg-steam-id").value = cfg.steam_id || "";
  document.getElementById("cfg-steamgriddb-api-key").placeholder = cfg.steamgriddb_api_key_set ? "•••••••• (set)" : "Not set";
  document.getElementById("cfg-spotify-client-id").value = cfg.spotify_client_id || "";
  document.getElementById("cfg-spotify-account-status").textContent = cfg.spotify_connected
    ? `Connected${cfg.spotify_display_name ? " as " + cfg.spotify_display_name : ""}`
    : "Not connected";
  document.getElementById("cfg-spotify-connect").hidden = cfg.spotify_connected;
  document.getElementById("cfg-spotify-disconnect").hidden = !cfg.spotify_connected;
  document.getElementById("cfg-youtube-client-id").value = cfg.youtube_client_id || "";
  document.getElementById("cfg-youtube-client-secret").placeholder = cfg.youtube_client_secret_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-youtube-account-status").textContent = cfg.youtube_connected
    ? `Connected${cfg.youtube_channel_title ? " as " + cfg.youtube_channel_title : ""}`
    : "Not connected";
  document.getElementById("cfg-youtube-connect").hidden = cfg.youtube_connected;
  document.getElementById("cfg-youtube-disconnect").hidden = !cfg.youtube_connected;

  updateSetupBanner(cfg);
}

// --- Spotify account connect/disconnect (OAuth) ---
// The Connect button asks the backend to open the user's real browser to Spotify's consent
// screen (never inside the desktop app's embedded webview - Spotify's login page refuses to load
// there). We have no way to know the moment they finish in that separate browser tab, so poll
// /api/config briefly afterward and refresh the form the instant spotify_connected flips true.
let spotifyConnectPoll = null;

document.getElementById("cfg-spotify-connect").addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  btn.disabled = true;
  try {
    const response = await fetch("/api/spotify/oauth/start", { method: "POST" });
    const result = await response.json();
    if (result.ok === false) {
      alert(result.error || "Couldn't start Spotify connection.");
      return;
    }
  } catch (err) {
    alert("Couldn't reach the server to start Spotify connection.");
    return;
  } finally {
    btn.disabled = false;
  }

  clearInterval(spotifyConnectPoll);
  let attempts = 0;
  spotifyConnectPoll = setInterval(async () => {
    attempts += 1;
    const cfg = await loadConfig();
    if ((cfg && cfg.spotify_connected) || attempts >= 60) {  // ~2 minutes at 2s intervals
      clearInterval(spotifyConnectPoll);
    }
  }, 2000);
});

document.getElementById("cfg-spotify-disconnect").addEventListener("click", async () => {
  await fetch("/api/spotify/oauth/disconnect", { method: "POST" });
  await loadConfig();
});

// --- YouTube account connect/disconnect (OAuth) --- same pattern as Spotify above, except the
// Connect button first does a normal Save so a freshly-typed Client ID/Secret are persisted
// before /api/youtube/oauth/start reads them from the live settings singleton.
let youtubeConnectPoll = null;

document.getElementById("cfg-youtube-connect").addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  btn.disabled = true;
  try {
    await saveSettings(null);
    const response = await fetch("/api/youtube/oauth/start", { method: "POST" });
    const result = await response.json();
    if (result.ok === false) {
      alert(result.error || "Couldn't start YouTube connection.");
      return;
    }
  } catch (err) {
    alert("Couldn't reach the server to start YouTube connection.");
    return;
  } finally {
    btn.disabled = false;
  }

  clearInterval(youtubeConnectPoll);
  let attempts = 0;
  youtubeConnectPoll = setInterval(async () => {
    attempts += 1;
    const cfg = await loadConfig();
    if ((cfg && cfg.youtube_connected) || attempts >= 60) {  // ~2 minutes at 2s intervals
      clearInterval(youtubeConnectPoll);
    }
  }, 2000);
});

document.getElementById("cfg-youtube-disconnect").addEventListener("click", async () => {
  await fetch("/api/youtube/oauth/disconnect", { method: "POST" });
  await loadConfig();
});

async function loadConfig() {
  let cfg;
  try {
    const response = await fetch("/api/config");
    cfg = await response.json();
  } catch (err) {
    return null;
  }
  applyConfigToForm(cfg);
  await loadModels(
    cfg.openrouter_model,
    cfg.kokoro_voice,
    cfg.openrouter_tts_model,
    cfg.openrouter_voice,
    cfg.memory_extraction_model,
    cfg.google_tts_voice,
    cfg.game_state_model,
    cfg.transcription_model
  );
  return cfg;
}

document.getElementById("cfg-refresh-models").addEventListener("click", () => {
  loadModels(
    document.getElementById("cfg-model").value,
    document.getElementById("cfg-kokoro-voice").value,
    document.getElementById("cfg-openrouter-tts-model").value,
    document.getElementById("cfg-openrouter-voice").value,
    document.getElementById("cfg-memory-model").value,
    document.getElementById("cfg-chirp3-voice").value,
    document.getElementById("cfg-game-state-model").value,
    document.getElementById("cfg-transcription-model").value,
    true // bypass the server-side catalog cache - that's the whole point of this button
  );
});

// Returns "" when the user explicitly cleared the field (signals server to wipe it),
// the typed value when they entered a new key, or null when untouched (server keeps existing).
function keyFieldValue(id) {
  const el = document.getElementById(id);
  if (el.dataset.cleared) return "";
  return el.value || null;
}

// Shared by the Settings footer button and the Gaming Journal's "Save Awareness Settings"
// button - the config PUT collects every cfg-* input by id regardless of which modal it
// lives in, so both buttons save the full settings form.
async function saveSettings(saveButton) {
  const apiKeyInput = document.getElementById("cfg-api-key");
  const igdbSecretInput = document.getElementById("cfg-igdb-client-secret");
  const steamApiKeyInput = document.getElementById("cfg-steam-api-key");
  const body = {
    user_display_name: userNameInput.value.trim() || "You",
    openrouter_model: document.getElementById("cfg-model").value,
    openrouter_base_url: document.getElementById("cfg-openrouter-base-url").value.trim() || "https://openrouter.ai/api/v1",
    llm_provider: document.getElementById("cfg-llm-provider").value,
    memory_extraction_provider: document.getElementById("cfg-memory-provider").value,
    game_state_provider: document.getElementById("cfg-game-state-provider").value,
    google_ai_studio_api_key: keyFieldValue("cfg-google-ai-studio-key"),
    custom_openai_base_url: document.getElementById("cfg-custom-openai-base-url").value.trim(),
    custom_openai_api_key: keyFieldValue("cfg-custom-openai-key"),
    memory_extraction_model: document.getElementById("cfg-memory-model").value,
    web_search_provider: document.getElementById("cfg-web-search-provider").value,
    searxng_base_url: document.getElementById("cfg-searxng-base-url").value.trim() || "http://localhost:8080",
    tts_provider: document.getElementById("cfg-tts-provider").value,
    google_tts_api_key: keyFieldValue("cfg-google-tts-api-key"),
    google_tts_voice: document.getElementById("cfg-chirp3-voice").value,
    transcription_enabled: transcriptionEnabledInput.checked,
    transcription_model: document.getElementById("cfg-transcription-model").value,
    kokoro_base_url: document.getElementById("cfg-kokoro-base-url").value.trim() || "http://localhost:8880/v1",
    kokoro_voice: document.getElementById("cfg-kokoro-voice").value,
    openrouter_tts_model: document.getElementById("cfg-openrouter-tts-model").value,
    openrouter_voice: document.getElementById("cfg-openrouter-voice").value,
    openrouter_api_key: keyFieldValue("cfg-api-key"),
    openrouter_management_key: keyFieldValue("cfg-management-key"),
    narration_speed: parseFloat(narrationSpeedInput.value),
    narration_volume: parseInt(narrationVolumeInput.value, 10) / 100,
    narrate_enabled: document.getElementById("cfg-narrate").checked,
    sfx_enabled: document.getElementById("cfg-sfx-enabled").checked,
    context_window_messages: parseInt(contextWindowInput.value, 10),
    screenshot_max_width: parseInt(screenshotWidthInput.value, 10),
    screenshot_jpeg_quality: parseInt(screenshotQualityInput.value, 10),
    igdb_client_id: document.getElementById("cfg-igdb-client-id").value,
    igdb_client_secret: keyFieldValue("cfg-igdb-client-secret"),
    steam_api_key: keyFieldValue("cfg-steam-api-key"),
    steam_id: document.getElementById("cfg-steam-id").value,
    steamgriddb_api_key: keyFieldValue("cfg-steamgriddb-api-key"),
    spotify_client_id: document.getElementById("cfg-spotify-client-id").value,
    youtube_client_id: document.getElementById("cfg-youtube-client-id").value,
    youtube_client_secret: keyFieldValue("cfg-youtube-client-secret"),
    overlay_enabled: document.getElementById("cfg-overlay-enabled").checked,
    overlay_edit_hotkey: overlayEditHotkey || "Ctrl+Shift+O",
    overlay_edit_phrase_enabled: overlayEditPhraseEnabledInput.checked,
    overlay_edit_phrase: overlayEditPhraseInput.value.trim() || "Edit overlay",
    game_state_ocr_enabled: gameStateEnabledInput.checked,
    game_state_poll_interval_seconds: parseInt(gameStateIntervalInput.value, 10),
    game_state_capture_interval_seconds: parseInt(gameStateCaptureIntervalInput.value, 10),
    game_state_visual_diff_threshold_percent: parseFloat(gameStateVisualDiffInput.value),
    game_state_visual_diff_noise_floor_percent: parseFloat(gameStateVisualDiffNoiseFloorInput.value),
    game_state_max_consecutive_skips: parseInt(gameStateMaxSkipsInput.value, 10),
    game_state_model: document.getElementById("cfg-game-state-model").value,
    game_state_ocr_similarity_threshold: parseFloat(gameStateOcrSimilarityInput.value),
    game_state_ocr_max_width: parseInt(gameStateOcrWidthInput.value, 10),
    game_state_visual_diff_thumbnail_size: parseInt(gameStateVisualDiffThumbInput.value, 10),
    game_state_capture_frame_timeout_seconds: parseFloat(gameStateFrameTimeoutInput.value),
    game_state_empty_ocr_warn_threshold: parseInt(gameStateEmptyWarnInput.value, 10),
    game_state_capture_cursor_enabled: gameStateCaptureCursorInput.checked,
    game_state_training_enabled: document.getElementById("cfg-game-state-training-enabled").checked,
    proactive_messages_enabled: document.getElementById("cfg-proactive-enabled").checked,
    proactive_min_interval_minutes: parseInt(document.getElementById("cfg-proactive-interval").value, 10),
    memory_rag_limit: parseInt(document.getElementById("cfg-memory-rag-limit").value, 10),
    wake_word_enabled: wakeWordEnabledInput.checked,
    wake_word_phrase: wakeWordPhraseInput.value.trim() || "Hey Buddy",
    sleep_word_enabled: sleepWordEnabledInput.checked,
    sleep_word_phrase: sleepWordPhraseInput.value.trim() || "Go to sleep",
    handsfree_mode: handsfreeModeSelect.value,
    wake_word_max_failures: wakeWordMaxFailures,
    vad_threshold: vadThreshold,
    vad_silence_ms: vadSilenceMs,
    vad_min_speech_ms: vadMinSpeechMs,
    pre_roll_ms: preRollMs,
    post_roll_ms: postRollMs,
    debug_mode_enabled: document.getElementById("cfg-debug-mode-enabled").checked,
  };
  const response = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const updatedCfg = await response.json();
  // Clear values and reset the "explicitly cleared" flag on all key fields.
  const keyInputIds = [
    "cfg-api-key", "cfg-management-key", "cfg-google-ai-studio-key",
    "cfg-google-tts-api-key", "cfg-custom-openai-key",
    "cfg-igdb-client-secret", "cfg-steam-api-key", "cfg-steamgriddb-api-key", "cfg-youtube-client-secret",
  ];
  for (const id of keyInputIds) {
    const el = document.getElementById(id);
    el.value = "";
    delete el.dataset.cleared;
  }
  applyConfigToForm(updatedCfg);
  if (!saveButton) return;
  flashSaved(saveButton);
  // Close the modal this Save lives in (Settings, or the Gaming Journal's Game Awareness tab)
  // after a beat, so the "Saved" confirmation is visible before it dismisses.
  const modal = saveButton.closest(".modal-overlay");
  if (modal) setTimeout(() => closeModal(modal), 650);
}

document.getElementById("cfg-save").addEventListener("click", (event) => saveSettings(event.currentTarget));
document.getElementById("ga-save").addEventListener("click", (event) => saveSettings(event.currentTarget));
document.getElementById("overlay-save").addEventListener("click", (event) => saveSettings(event.currentTarget));

// --- Personal Data modal (Instructions + Memory) ---

personalDataBtn.addEventListener("click", async () => {
  openModal(personalDataModal);
  const response = await fetch("/api/instructions");
  const data = await response.json();
  document.getElementById("instructions-text").value = data.instructions;
  await loadMemories();
  await loadReminders();
  await loadAlarms();
});

document.getElementById("instructions-save").addEventListener("click", async (event) => {
  const saveButton = event.currentTarget;
  const text = document.getElementById("instructions-text").value;
  await fetch("/api/instructions", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instructions: text }),
  });
  flashSaved(saveButton);
});

// --- Personal Data > Memory tab ---
// User-scope facts only: things about the person (preferences, life context, cross-game
// habits) that are always shown to the companion. Game- and session-scoped facts are
// managed per game in the Gaming Journal modal instead.

