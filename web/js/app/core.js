// --- API auth token ---
// The desktop launcher (run_app.py) generates a per-launch token, hands it over via the
// initial URL, and the server rejects /api/* requests without it. Stored in sessionStorage
// so in-app reloads keep working; stripped from the address bar immediately.
const API_TOKEN = (() => {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl) {
    sessionStorage.setItem("lyko-api-token", fromUrl);
    history.replaceState(null, "", location.pathname);
    return fromUrl;
  }
  return sessionStorage.getItem("lyko-api-token") || "";
})();

if (API_TOKEN) {
  const _origFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const headers = new Headers(init.headers || {});
    headers.set("X-Lyko-Token", API_TOKEN);
    return _origFetch(input, { ...init, headers });
  };
}

// For URLs loaded by <img>/<audio> elements, which can't send headers - the server also
// accepts the token as a query param.
function apiUrl(path) {
  if (!API_TOKEN) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(API_TOKEN);
}

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendImageBtn = document.getElementById("send-image-btn");
const attachedImageIndicator = document.getElementById("attached-image-indicator");
const attachedImageThumb = document.getElementById("attached-image-thumb");
const attachedImageRemoveBtn = document.getElementById("attached-image-remove");
const sendImageModal = document.getElementById("send-image-modal");
const imageDropzone = document.getElementById("image-dropzone");
const imageFileInput = document.getElementById("image-file-input");
const stopNarrationBtn = document.getElementById("stop-narration-btn");
const micBtn = document.getElementById("mic-btn");
const liveMicToggle = document.getElementById("live-mic-toggle");
const narrationAudio = document.getElementById("narration-audio");
const voiceStatus = document.getElementById("voice-status");
const sleepWordHintEl = document.getElementById("sleep-word-hint");
const overlayEditHintEl = document.getElementById("overlay-edit-hint");

// --- Audio device selection (machine-specific, so client-side in localStorage, not server settings) ---
const MIC_DEVICE_KEY = "lyko-mic-device";
const OUTPUT_DEVICE_KEY = "lyko-output-device";

function getSelectedMicId() { return localStorage.getItem(MIC_DEVICE_KEY) || ""; }
function getSelectedOutputId() { return localStorage.getItem(OUTPUT_DEVICE_KEY) || ""; }
function setSelectedMicId(id) { localStorage.setItem(MIC_DEVICE_KEY, id || ""); }
function setSelectedOutputId(id) { localStorage.setItem(OUTPUT_DEVICE_KEY, id || ""); }

// Base capture constraints plus the chosen input device (if the user picked one; empty = system default).
// echoCancellation is deliberately left OFF too (2026-07-05): Chromium's WebRTC audio processing
// pipeline it enables includes a transient/click suppressor meant to filter keyboard-typing noise
// during calls, which reproducibly ate the unvoiced "t's" consonant cluster in "What's" (and likely
// other short plosive/fricative sounds) - a documented WebRTC quirk, not something specific to our
// code. Losing that trades away narration.js's barge-in protection against speaker bleed-through
// false-triggering an interrupt - acceptable, since dropped consonants corrupt every recording.
// noiseSuppression also stays OFF - same class of gate/duck behavior, different dropouts.
// autoGainControl stays ON - the hands-free live-mic VAD gate (voice.js's computeAmplitude >
// vadThreshold/127) is calibrated assuming AGC-normalized levels; turning it off silently broke
// hands-free detection because raw mic amplitude fell below the threshold and "loud" never triggered.
function micAudioConstraints() {
  const constraints = { echoCancellation: false, noiseSuppression: false, autoGainControl: true };
  const id = getSelectedMicId();
  if (id) constraints.deviceId = { exact: id };
  return constraints;
}

// Route an HTMLAudioElement to the chosen output device. No-op if none picked or setSinkId is
// unsupported (older engines) or the device vanished. Best-effort — never throws into callers.
async function applyOutputDevice(audioEl) {
  const id = getSelectedOutputId();
  if (!id || !audioEl || typeof audioEl.setSinkId !== "function") return;
  try {
    await audioEl.setSinkId(id);
  } catch (err) {
    /* device unplugged / not permitted — fall back to default silently */
  }
}

const newChatBtn = document.getElementById("new-chat-btn");
const chatListEl = document.getElementById("chat-list");
const personalDataBtn = document.getElementById("personal-data-btn");
const diagnosticsBtn = document.getElementById("diagnostics-btn");
const settingsBtn = document.getElementById("settings-btn");
const gameStatePanel = document.getElementById("game-state-panel");
const gameStatePanelHeader = document.getElementById("game-state-panel-header");
const gameStatePanelDot = document.getElementById("game-state-panel-dot");
const gameStatePanelCloseBtn = document.getElementById("game-state-panel-close");
const gameStateFields = document.getElementById("game-state-fields");
const gameStateStats = document.getElementById("game-state-stats");
const gameStateSessionBar = document.getElementById("game-state-session-bar");
const gameStateSessionToggle = document.getElementById("game-state-session-toggle");
const gameStateSessionNameEl = document.getElementById("game-state-session-name");
const gameStateSessionChevron = document.getElementById("game-state-session-chevron");
const gameStateNewSessionBtn = document.getElementById("game-state-new-session-btn");
const gameStateSessionList = document.getElementById("game-state-session-list");
const settingsModal = document.getElementById("settings-modal");
const personalDataModal = document.getElementById("personal-data-modal");
const diagnosticsModal = document.getElementById("diagnostics-modal");
const debugDetailModal = document.getElementById("debug-detail-modal");
const debugDetailTitleEl = document.getElementById("debug-detail-title");
const debugDetailBodyEl = document.getElementById("debug-detail-body");

let narrationSpeed = 1.0;
let narrationVolume = 1.0;
let attachedImageDataUrl = null;

// User's uploaded profile picture (shown in chat in place of the initial-letter fallback).
// Cache-busted with a version stamp each time it's changed, since the URL itself never changes.
let hasUserAvatar = false;
let avatarVersion = Date.now();
let userDisplayName = "You";

function userAvatarFallback() {
  return (userDisplayName.trim().charAt(0) || "Y").toUpperCase();
}

function userAvatarMarkup() {
  return hasUserAvatar ? `<img src="${apiUrl(`/api/profile/avatar?v=${avatarVersion}`)}" alt="" />` : userAvatarFallback();
}

// Already-rendered messages don't re-run appendMessage when the user changes their picture or
// name, so update the DOM in place instead of requiring a chat switch to pick up the change.
function emptyStateGreeting() {
  return userDisplayName === "You" ? "What's up?" : `What's up, ${userDisplayName}?`;
}

function refreshVisibleUserIdentity() {
  document.querySelectorAll(".message.user").forEach((el) => {
    const avatarEl = el.querySelector(".msg-avatar");
    if (avatarEl) avatarEl.innerHTML = userAvatarMarkup();
    const nameEl = el.querySelector(".msg-role-name");
    if (nameEl) nameEl.textContent = userDisplayName;
  });
  // The empty-state greeting renders during init, before the display name arrives from
  // /api/config - refresh it in place too, or the first screen greets "You" forever.
  const emptyHeading = document.querySelector(".chat-empty-heading");
  if (emptyHeading) emptyHeading.textContent = emptyStateGreeting();
}

async function refreshAvatarStatus() {
  const res = await fetch("/api/profile/avatar/status");
  const data = await res.json();
  hasUserAvatar = data.has_avatar;
}

// Live-mic voice activity detection tuning, persisted server-side via /api/config.
let vadThreshold = 8;
let vadSilenceMs = 1200;
let vadMinSpeechMs = 300;

// Wake word - re-enables hands-free listening by voice after it's been turned off, since
// touching the keyboard/mouse defeats the point of hands-free. Persisted server-side via /api/config.
let wakeWordEnabled = false;
let wakeWordPhrase = "Hey Buddy";

// Sleep word - the mirror of the wake word: a spoken phrase that turns hands-free OFF while it's
// on. Detected in-browser; the matching utterance is suppressed so it's never sent to the model.
let sleepWordEnabled = false;
let sleepWordPhrase = "Go to sleep";

// "Edit overlay" phrase - opens the native overlay's edit mode directly, bypassing the LLM
// entirely. Detected the same way as wake/sleep word, but independent of hands-free mic state.
let overlayEditPhraseEnabled = false;
let overlayEditPhrase = "Edit overlay";

// Configurable global hotkey (Ctrl+Shift+O by default) that toggles the native overlay's edit
// mode; the display string shown/edited in Settings.
let overlayEditHotkey = "Ctrl+Shift+O";

// Keeps the composer's phrase hints in sync with whatever's currently configured. Call this
// any time a phrase/enabled flag changes (Settings inputs, config load/save) or hands-free
// toggles - text baked in once and never revisited is exactly the bug that used to make the
// wake-word hint show a stale phrase until something unrelated happened to refresh it.
// `force` is true at a genuine hands-free-just-turned-off transition (stopLiveMic, mic-denied),
// where the wake hint is always the right thing to show; it's false when only a phrase/enabled
// setting changed, where voiceStatus might currently hold an unrelated in-progress status (e.g.
// "Converting audio...") that must not be clobbered - only overwrite if it's blank or already
// showing our own hint.
function updateVoiceHints(force = false) {
  // All three phrases rely on the browser's SpeechRecognition API - on an unsupported browser
  // none of them are ever actually detected, so showing the hints would be misleading.
  const supported = typeof wakeWordSupported === "undefined" || wakeWordSupported;

  if (supported && typeof liveMicEnabled !== "undefined" && !liveMicEnabled) {
    const current = voiceStatus.textContent;
    const isWakeHint = current === "" || /^Say ".*" to resume$/.test(current);
    if (force || isWakeHint) setVoiceStatus(wakeWordEnabled ? `Say "${wakeWordPhrase}" to resume` : "");
  }

  if (sleepWordHintEl) {
    const showSleepHint = supported && sleepWordEnabled && typeof liveMicEnabled !== "undefined" && liveMicEnabled;
    sleepWordHintEl.hidden = !showSleepHint;
    if (showSleepHint) sleepWordHintEl.textContent = `Say "${sleepWordPhrase}" to stop listening`;
  }

  if (overlayEditHintEl) {
    const showOverlayHint = supported && overlayEditPhraseEnabled;
    overlayEditHintEl.hidden = !showOverlayHint;
    if (showOverlayHint) {
      overlayEditHintEl.textContent = `Say "${overlayEditPhrase}" to edit the overlay (must be said in-game)`;
    }
  }
}

// Toast manager — max 3 visible, queues the rest as "+N more", deduplicates by id.
const _toasts = (() => {
  const MAX = 3;
  let container = null;
  const active = []; // { id, el, timer }
  const queue  = []; // { id, opts }
  let overflowEl = null;

  function _box() {
    if (!container) {
      container = document.createElement("div");
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    return container;
  }

  function _updateOverflow() {
    const n = queue.length;
    if (n > 0) {
      if (!overflowEl) {
        overflowEl = document.createElement("div");
        overflowEl.className = "toast-overflow";
        _box().prepend(overflowEl);
      }
      overflowEl.textContent = `+${n} more notification${n === 1 ? "" : "s"}`;
    } else if (overflowEl) {
      overflowEl.remove();
      overflowEl = null;
    }
  }

  function _dismiss(id) {
    const idx = active.findIndex(t => t.id === id);
    if (idx === -1) return;
    const { el, timer } = active.splice(idx, 1)[0];
    clearTimeout(timer);
    el.classList.remove("toast-in");
    el.addEventListener("transitionend", () => el.remove(), { once: true });
    if (queue.length) _render(queue.shift());
    _updateOverflow();
  }

  function _render({ id, opts }) {
    const { title = "", body = "", duration = 4000, actions = [] } = opts;
    const el = document.createElement("div");
    el.className = "toast";

    let inner = "";
    if (title) inner += `<div class="toast-title">${title}</div>`;
    if (body)  inner += `<div class="toast-body-text">${body}</div>`;
    if (actions.length) {
      inner += `<div class="toast-actions">` +
        actions.map((a, i) =>
          `<button class="toast-btn toast-btn-${a.variant || "default"}" data-i="${i}">${a.label}</button>`
        ).join("") +
        `</div>`;
    }

    el.innerHTML = inner;
    const x = document.createElement("button");
    x.className = "toast-x";
    x.innerHTML = "&times;";
    x.onclick = () => _dismiss(id);
    el.appendChild(x);

    el.querySelectorAll(".toast-btn").forEach(btn => {
      btn.onclick = () => { actions[+btn.dataset.i].onClick?.(); _dismiss(id); };
    });

    _box().appendChild(el);
    requestAnimationFrame(() => el.classList.add("toast-in"));

    const timer = duration > 0 ? setTimeout(() => _dismiss(id), duration) : null;
    active.push({ id, el, timer });
    _updateOverflow();
  }

  return {
    show(id, opts) {
      if (active.some(t => t.id === id) || queue.some(t => t.id === id)) return;
      if (active.length < MAX) _render({ id, opts });
      else { queue.push({ id, opts }); _updateOverflow(); }
    },
    dismiss(id) { _dismiss(id); },
  };
})();

function showToast(id, opts) { _toasts.show(id, opts); }

// Brief green "Saved" confirmation on a save button, e.g. after a PUT/POST resolves. Safe to
// call repeatedly in quick succession - each call restarts the revert timer instead of stacking.
const SAVED_CHECK_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 12 4 9"/></svg> Saved`;

function flashSaved(button) {
  if (button._savedOriginal === undefined) button._savedOriginal = button.innerHTML;
  clearTimeout(button._savedTimeout);
  button.innerHTML = SAVED_CHECK_ICON;
  button.classList.add("btn-saved");
  button._savedTimeout = setTimeout(() => {
    button.innerHTML = button._savedOriginal;
    button.classList.remove("btn-saved");
  }, 1000);
}

// Clear (✕) button for API key fields. If a key is actually stored server-side (its box shows
// the "(set)" placeholder and holds no freshly-typed text), confirm and delete it immediately —
// no full "Save changes" needed. Otherwise just discard the unsaved typed text locally.
const CLEARABLE_KEY_FIELDS = {
  "cfg-api-key": "openrouter_api_key",
  "cfg-management-key": "openrouter_management_key",
  "cfg-google-ai-studio-key": "google_ai_studio_api_key",
  "cfg-google-tts-api-key": "google_tts_api_key",
  "cfg-custom-openai-key": "custom_openai_api_key",
  "cfg-igdb-client-secret": "igdb_client_secret",
  "cfg-steam-api-key": "steam_api_key",
  "cfg-steamgriddb-api-key": "steamgriddb_api_key",
  "cfg-youtube-client-secret": "youtube_client_secret",
};

settingsModal.addEventListener("click", async (e) => {
  const btn = e.target.closest(".clear-key-btn");
  if (!btn) return;
  const input = btn.closest(".key-field")?.querySelector("input");
  if (!input) return;

  const field = CLEARABLE_KEY_FIELDS[input.id];
  const isStored = input.placeholder.includes("(set)");

  // A key that's actually saved: confirm, then delete it right away.
  if (field && isStored && !input.value) {
    if (!(await showConfirm("Delete this saved key? This takes effect immediately — no need to press Save changes.", { title: "Delete key", danger: true, confirmText: "Delete" }))) return;
    const response = await fetch(`/api/config/key/${field}`, { method: "DELETE" });
    if (!response.ok) return;
    const cfg = await response.json();
    input.value = "";
    input.placeholder = "Not set";
    delete input.dataset.cleared;
    updateSetupBanner(cfg);  // the cleared key may have been the active provider's
    return;
  }

  // Otherwise: just clear the unsaved typed text (a Save will persist the empty value).
  input.value = "";
  input.dataset.cleared = "true";
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

// Floating help tooltip for .cfg-help buttons - positioned in JS (not pure CSS ::after) so it can
// flip above/below the trigger and clamp horizontally, since a fixed "always open upward,
// centered" popup gets clipped by the Settings modal's overflow:auto body or runs off-screen
// near the top/edges of the viewport.
const helpTooltipEl = document.createElement("div");
helpTooltipEl.className = "help-tooltip";
document.body.appendChild(helpTooltipEl);

// Any element carrying a native `title` also gets the themed tooltip instead of the OS one:
// on hover we migrate its title into data-tip and strip the attribute (re-migrated each hover so
// dynamically-updated titles stay fresh) so the native browser tooltip never fires.
const TIP_SELECTOR = ".cfg-help, [data-tip], [title]";
function resolveTipTarget(node) {
  const t = node && node.closest && node.closest(TIP_SELECTOR);
  return t || null;
}

function showHelpTooltip(target) {
  if (target.hasAttribute("title")) {
    const t = target.getAttribute("title");
    if (t) target.dataset.tip = t;
    target.removeAttribute("title");
  }
  const tip = target.dataset.tip;
  if (!tip) return;
  helpTooltipEl.textContent = tip;
  helpTooltipEl.classList.add("visible");

  const margin = 8;
  const rect = target.getBoundingClientRect();
  const tipRect = helpTooltipEl.getBoundingClientRect();

  const spaceAbove = rect.top;
  const spaceBelow = window.innerHeight - rect.bottom;
  const openBelow = spaceAbove < tipRect.height + margin && spaceBelow > spaceAbove;
  const top = openBelow ? rect.bottom + margin : rect.top - tipRect.height - margin;
  helpTooltipEl.style.top = `${Math.max(margin, top)}px`;

  const left = rect.left + rect.width / 2 - tipRect.width / 2;
  const clampedLeft = Math.min(Math.max(left, margin), window.innerWidth - tipRect.width - margin);
  helpTooltipEl.style.left = `${clampedLeft}px`;
}

function hideHelpTooltip() {
  helpTooltipEl.classList.remove("visible");
}

document.addEventListener("mouseover", (e) => {
  const target = resolveTipTarget(e.target);
  if (target) showHelpTooltip(target);
});
document.addEventListener("mouseout", (e) => {
  if (resolveTipTarget(e.target)) hideHelpTooltip();
});
document.addEventListener("focusin", (e) => {
  const target = resolveTipTarget(e.target);
  if (target) showHelpTooltip(target);
});
document.addEventListener("focusout", (e) => {
  if (resolveTipTarget(e.target)) hideHelpTooltip();
});
// Scroll position isn't tracked live (fixed tooltip would otherwise drift from its trigger as the
// modal body scrolls underneath it) - just dismiss it instead.
document.addEventListener("scroll", hideHelpTooltip, true);

// --- Themed dialogs (custom replacements for native alert()/confirm()) ---
// Both return a Promise: showConfirm resolves true/false, showAlert resolves when dismissed.
// One reusable overlay is created lazily and reused across calls.
let _dialogOverlay = null;
function _ensureDialogOverlay() {
  if (_dialogOverlay) return _dialogOverlay;
  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.hidden = true;
  overlay.innerHTML =
    '<div class="dialog" role="dialog" aria-modal="true">' +
    '<div class="dialog-title"></div>' +
    '<div class="dialog-body"></div>' +
    '<div class="dialog-actions"></div>' +
    "</div>";
  document.body.appendChild(overlay);
  _dialogOverlay = overlay;
  return overlay;
}

// opts: { title, body, confirmText, cancelText, danger, showCancel }
function showDialog(opts) {
  const overlay = _ensureDialogOverlay();
  const titleEl = overlay.querySelector(".dialog-title");
  const bodyEl = overlay.querySelector(".dialog-body");
  const actionsEl = overlay.querySelector(".dialog-actions");

  titleEl.textContent = opts.title || "";
  titleEl.hidden = !opts.title;
  bodyEl.textContent = opts.body || "";
  actionsEl.innerHTML = "";

  return new Promise((resolve) => {
    let settled = false;
    const close = (result) => {
      if (settled) return;
      settled = true;
      overlay.classList.remove("visible");
      document.removeEventListener("keydown", onKey, true);
      setTimeout(() => { overlay.hidden = true; }, 200);
      resolve(result);
    };

    if (opts.showCancel !== false) {
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "dialog-btn";
      cancelBtn.textContent = opts.cancelText || "Cancel";
      cancelBtn.addEventListener("click", () => close(false));
      actionsEl.appendChild(cancelBtn);
    }

    const okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "dialog-btn " + (opts.danger ? "dialog-btn--danger" : "dialog-btn--primary");
    okBtn.textContent = opts.confirmText || "OK";
    okBtn.addEventListener("click", () => close(true));
    actionsEl.appendChild(okBtn);

    const onKey = (e) => {
      // Capture-phase + stopPropagation so a modal's own Escape handler underneath doesn't also fire.
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(opts.showCancel === false ? true : false); }
      else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); close(true); }
    };
    document.addEventListener("keydown", onKey, true);
    overlay.onclick = (e) => { if (e.target === overlay) close(opts.showCancel === false ? true : false); };

    overlay.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => overlay.classList.add("visible")));
    requestAnimationFrame(() => okBtn.focus());
  });
}

// Drop-in async replacements. `confirm()`/`alert()` were synchronous; every call site is (or is now)
// awaited. Signature kept simple: message string, plus optional overrides.
function showConfirm(message, opts = {}) {
  return showDialog({ title: opts.title || "Please confirm", body: message, showCancel: true, ...opts });
}
function showAlert(message, opts = {}) {
  return showDialog({ title: opts.title || "", body: message, showCancel: false, confirmText: opts.confirmText || "OK", ...opts });
}

function setVoiceStatus(text, variant) {
  voiceStatus.textContent = text;
  voiceStatus.classList.remove("recording", "error");
  if (variant) {
    voiceStatus.classList.add(variant);
  }
}

