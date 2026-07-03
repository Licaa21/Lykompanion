const QS_DONE_KEY = "lyko-setup-done";

const quickSetupModal = document.getElementById("quick-setup-modal");
const qsProgressEl = document.getElementById("qs-progress");
const qsSteps = [...quickSetupModal.querySelectorAll(".qs-step")];
const qsBackBtn = document.getElementById("qs-back");
const qsNextBtn = document.getElementById("qs-next");
const qsSkipBtn = document.getElementById("qs-skip");
const qsNameInput = document.getElementById("qs-name");
const qsApiKeyInput = document.getElementById("qs-api-key");
const qsCustomUrlField = document.getElementById("qs-custom-url-field");
const qsCustomUrlInput = document.getElementById("qs-custom-url");
const qsKeyHintEl = document.getElementById("qs-key-hint");
const qsModelSearch = document.getElementById("qs-model-search");
const qsModelSelect = document.getElementById("qs-model");
const qsNarrateInput = document.getElementById("qs-narrate");
const qsTtsField = document.getElementById("qs-tts-field");
const qsTtsProviderSelect = document.getElementById("qs-tts-provider");

const QS_KEY_HINTS = {
  openrouter: '<a href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer">Get an OpenRouter API key ↗</a>',
  google_ai_studio: '<a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer">Get a Google AI Studio API key ↗</a>',
  custom: "Enter the base URL and key of your OpenAI-compatible endpoint.",
};

let qsStep = 0;
let qsProvider = "openrouter";
let qsKeyAlreadySet = false;
let qsModelOptions = [];

function providerKeyMissing(cfg) {
  const provider = cfg.llm_provider || "openrouter";
  if (provider === "google_ai_studio") return !cfg.google_ai_studio_api_key_set;
  if (provider === "custom") return !cfg.custom_openai_api_key_set;
  return !cfg.openrouter_api_key_set;
}

// GET the full current config, overlay a partial change, PUT it back. Key fields sent as null
// are kept server-side, so echoing the GET response back is patch-safe.
async function saveConfigPatch(patch) {
  const current = await fetch("/api/config").then((r) => r.json());
  const response = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...current, ...patch }),
  });
  if (!response.ok) throw new Error("Saving settings failed.");
  return response.json();
}

function qsRender() {
  qsSteps.forEach((step) => { step.hidden = Number(step.dataset.step) !== qsStep; });
  qsProgressEl.innerHTML = "";
  for (let i = 0; i < qsSteps.length; i++) {
    const dot = document.createElement("span");
    dot.className = "qs-dot" + (i === qsStep ? " active" : "") + (i < qsStep ? " done" : "");
    qsProgressEl.appendChild(dot);
  }
  qsBackBtn.hidden = qsStep === 0;
  qsSkipBtn.hidden = qsStep === qsSteps.length - 1;
  qsNextBtn.textContent = qsStep === qsSteps.length - 1 ? "Start chatting" : "Next";
}

function qsSelectProvider(provider) {
  qsProvider = provider;
  quickSetupModal.querySelectorAll(".qs-provider-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.provider === provider);
  });
  qsCustomUrlField.hidden = provider !== "custom";
  qsKeyHintEl.innerHTML = QS_KEY_HINTS[provider] || "";
}

quickSetupModal.querySelectorAll(".qs-provider-card").forEach((card) => {
  card.addEventListener("click", () => qsSelectProvider(card.dataset.provider));
});

function qsPopulateModels() {
  const query = qsModelSearch.value.toLowerCase();
  const filtered = qsModelOptions.filter((o) => o.label.toLowerCase().includes(query));
  const currentValue = qsModelSelect.value;
  populateSelect(qsModelSelect, filtered, filtered.some((o) => o.value === currentValue) ? currentValue : undefined);
  if (filtered.length === 0) {
    qsModelSelect.appendChild(new Option(query ? "No models match your search" : "No models found — check your API key", ""));
  }
}

qsModelSearch.addEventListener("input", qsPopulateModels);

async function qsLoadModels(selectedValue) {
  qsModelOptions = [];
  try {
    const response = await fetch(`/api/models/llm?provider=${encodeURIComponent(qsProvider)}`);
    if (response.ok) {
      const models = await response.json();
      qsModelOptions = sortByLabel(models.map((m) => ({ value: m.id, label: m.name })));
    }
  } catch (err) {
    // handled by the empty-list hint in qsPopulateModels
  }
  qsModelSearch.value = "";
  qsPopulateModels();
  if (selectedValue && qsModelOptions.some((o) => o.value === selectedValue)) {
    qsModelSelect.value = selectedValue;
  }
}

async function qsAdvance() {
  if (qsStep === 1) {
    // Persist provider + key now - the model list on the next page needs them server-side.
    const key = qsApiKeyInput.value.trim();
    if (!key && !qsKeyAlreadySet) {
      qsKeyHintEl.innerHTML = `<span class="qs-error">Add an API key to continue (or skip setup for now).</span><br>${QS_KEY_HINTS[qsProvider] || ""}`;
      return;
    }
    const patch = {
      user_display_name: qsNameInput.value.trim() || "You",
      llm_provider: qsProvider,
    };
    if (key) {
      if (qsProvider === "google_ai_studio") patch.google_ai_studio_api_key = key;
      else if (qsProvider === "custom") patch.custom_openai_api_key = key;
      else patch.openrouter_api_key = key;
    }
    if (qsProvider === "custom" && qsCustomUrlInput.value.trim()) {
      patch.custom_openai_base_url = qsCustomUrlInput.value.trim();
    }
    qsNextBtn.disabled = true;
    qsNextBtn.textContent = "Connecting…";
    try {
      await saveConfigPatch(patch);
      qsApiKeyInput.value = "";
      qsKeyAlreadySet = true;
      qsStep++;
      qsRender();
      await qsLoadModels();
    } catch (err) {
      qsKeyHintEl.innerHTML = `<span class="qs-error">Couldn't save — is the server running?</span>`;
    } finally {
      qsNextBtn.disabled = false;
      qsRender();
    }
    return;
  }

  if (qsStep === 2 && qsModelSelect.value) {
    qsNextBtn.disabled = true;
    try {
      await saveConfigPatch({ openrouter_model: qsModelSelect.value });
    } catch (err) { /* keep going - the model can be set later in Settings */ }
    qsNextBtn.disabled = false;
  }

  if (qsStep === 3) {
    document.getElementById("cfg-narrate").checked = qsNarrateInput.checked;
    qsNextBtn.disabled = true;
    try {
      await saveConfigPatch({ tts_provider: qsTtsProviderSelect.value, narrate_enabled: qsNarrateInput.checked });
    } catch (err) { /* recoverable later in Settings */ }
    qsNextBtn.disabled = false;
  }

  if (qsStep === qsSteps.length - 1) {
    qsFinish();
    return;
  }

  qsStep++;
  qsRender();
}

function qsFinish() {
  localStorage.setItem(QS_DONE_KEY, "true");
  closeModal(quickSetupModal);
  loadConfig(); // re-sync the Settings form, model lists, and the setup banner
}

qsNextBtn.addEventListener("click", qsAdvance);
qsBackBtn.addEventListener("click", () => { if (qsStep > 0) { qsStep--; qsRender(); } });
qsSkipBtn.addEventListener("click", qsFinish);
quickSetupModal.addEventListener("click", (event) => {
  if (event.target === quickSetupModal) qsFinish();
});

qsNarrateInput.addEventListener("change", () => { qsTtsField.hidden = !qsNarrateInput.checked; });

async function openQuickSetup() {
  qsStep = 0;
  qsNameInput.value = userDisplayName === "You" ? "" : userDisplayName;
  qsApiKeyInput.value = "";
  const cfg = await fetch("/api/config").then((r) => r.json()).catch(() => null);
  if (cfg) {
    qsSelectProvider(cfg.llm_provider || "openrouter");
    qsCustomUrlInput.value = cfg.custom_openai_base_url || "";
    qsTtsProviderSelect.value = cfg.tts_provider || "kokoro";
    qsKeyAlreadySet = !providerKeyMissing(cfg);
    qsApiKeyInput.placeholder = qsKeyAlreadySet ? "•••••••• (already set — leave blank to keep)" : "Paste your API key";
  } else {
    qsSelectProvider("openrouter");
    qsKeyAlreadySet = false;
  }
  qsRender();
  openModal(quickSetupModal);
}

document.getElementById("cfg-quick-setup").addEventListener("click", () => {
  closeModal(settingsModal);
  openQuickSetup();
});

// Setup banner — visible while the active provider has no API key; opens the wizard.
const setupBanner = document.getElementById("setup-banner");
document.getElementById("setup-banner-btn").addEventListener("click", openQuickSetup);

function updateSetupBanner(cfg) {
  setupBanner.hidden = !providerKeyMissing(cfg);
}

// Escape closes whichever modal is open (wizard counts as "skip" - it can be re-run any time).
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!quickSetupModal.hidden) { qsFinish(); return; }
  for (const modal of [settingsModal, personalDataModal, diagnosticsModal]) {
    if (!modal.hidden) { closeModal(modal); return; }
  }
});

// --- Init ---

