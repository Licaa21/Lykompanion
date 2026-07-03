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
const screenshotToggle = document.getElementById("screenshot-toggle");
const screenshotIndicator = document.getElementById("screenshot-indicator");
const stopNarrationBtn = document.getElementById("stop-narration-btn");
const micBtn = document.getElementById("mic-btn");
const liveMicToggle = document.getElementById("live-mic-toggle");
const narrationAudio = document.getElementById("narration-audio");
const voiceStatus = document.getElementById("voice-status");

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
let includeScreenshot = false;

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

// Clear button for API key fields
settingsModal.addEventListener("click", (e) => {
  const btn = e.target.closest(".clear-key-btn");
  if (!btn) return;
  const input = btn.closest(".key-field")?.querySelector("input");
  if (!input) return;
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

function showHelpTooltip(target) {
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
  const target = e.target.closest(".cfg-help");
  if (target) showHelpTooltip(target);
});
document.addEventListener("mouseout", (e) => {
  if (e.target.closest(".cfg-help")) hideHelpTooltip();
});
document.addEventListener("focusin", (e) => {
  const target = e.target.closest(".cfg-help");
  if (target) showHelpTooltip(target);
});
document.addEventListener("focusout", (e) => {
  if (e.target.closest(".cfg-help")) hideHelpTooltip();
});
// Scroll position isn't tracked live (fixed tooltip would otherwise drift from its trigger as the
// modal body scrolls underneath it) - just dismiss it instead.
document.addEventListener("scroll", hideHelpTooltip, true);

function setVoiceStatus(text, variant) {
  voiceStatus.textContent = text;
  voiceStatus.classList.remove("recording", "error");
  if (variant) {
    voiceStatus.classList.add(variant);
  }
}

