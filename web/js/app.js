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

let audioCtx;

function beep(frequency, duration) {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const oscillator = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  oscillator.frequency.value = frequency;
  oscillator.connect(gain);
  gain.connect(audioCtx.destination);
  gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
  oscillator.start();
  oscillator.stop(audioCtx.currentTime + duration);
}

// Distinct two-note ascending chime for wake-word detection, clearly different from the single
// beeps used for recording start (880Hz) / utterance finalized (440Hz) - this confirms hands-free
// is back on without an LLM/TTS round-trip, since the main use case is couch/controller, eyes off
// the screen.
function playWakeChime() {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const notes = [
    { frequency: 660, start: 0 },
    { frequency: 990, start: 0.12 },
  ];
  const duration = 0.15;
  for (const { frequency, start } of notes) {
    const startTime = audioCtx.currentTime + start;
    const oscillator = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    oscillator.frequency.value = frequency;
    oscillator.connect(gain);
    gain.connect(audioCtx.destination);
    gain.gain.setValueAtTime(0.15, startTime);
    gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration);
    oscillator.start(startTime);
    oscillator.stop(startTime + duration);
  }
}

// --- Cosmetic sound effects (message sent, tool calls) - synthesized the same way as the
// mic beeps above, gated by the "Sound effects" setting since (unlike the mic beeps) they're
// purely decorative rather than functional feedback. ---

function sfxOn() {
  return document.getElementById("cfg-sfx-enabled")?.checked !== false;
}

function playSfxNote(frequency, startTime, duration, peakGain, type) {
  const oscillator = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  oscillator.type = type || "sine";
  oscillator.frequency.setValueAtTime(frequency, startTime);
  oscillator.connect(gain);
  gain.connect(audioCtx.destination);
  gain.gain.setValueAtTime(0.0001, startTime);
  gain.gain.exponentialRampToValueAtTime(peakGain, startTime + Math.min(0.015, duration / 3));
  gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  oscillator.start(startTime);
  oscillator.stop(startTime + duration + 0.02);
}

function playSfxSweep(startFreq, endFreq, startTime, duration, peakGain) {
  const oscillator = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  oscillator.type = "sawtooth";
  oscillator.frequency.setValueAtTime(startFreq, startTime);
  oscillator.frequency.linearRampToValueAtTime(endFreq, startTime + duration);
  oscillator.connect(gain);
  gain.connect(audioCtx.destination);
  gain.gain.setValueAtTime(0.0001, startTime);
  gain.gain.exponentialRampToValueAtTime(peakGain, startTime + duration * 0.3);
  gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  oscillator.start(startTime);
  oscillator.stop(startTime + duration + 0.02);
}

// A message was sent to the LLM - three quiet "dots".
function playSentSfx() {
  if (!sfxOn()) return;
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  for (let i = 0; i < 3; i++) {
    playSfxNote(1100, audioCtx.currentTime + i * 0.11, 0.05, 0.06, "sine");
  }
}

// A generic (unmapped) tool call - a single soft click.
function playGenericToolSfx() {
  playSfxNote(700, audioCtx.currentTime, 0.06, 0.05, "triangle");
}

// web_search - a low-to-high "flyby" sweep, like a plane passing overhead.
function playWebSearchSfx() {
  playSfxSweep(260, 1000, audioCtx.currentTime, 0.4, 0.08);
}

// A memory was saved - a short ascending, swelling arpeggio.
function playMemorySaveSfx() {
  const notes = [523, 659, 784];
  notes.forEach((freq, i) => {
    playSfxNote(freq, audioCtx.currentTime + i * 0.09, 0.16, 0.05 + i * 0.03, "sine");
  });
}

// A memory was removed - a short descending, fading arpeggio.
function playMemoryDeleteSfx() {
  const notes = [659, 523, 392];
  notes.forEach((freq, i) => {
    playSfxNote(freq, audioCtx.currentTime + i * 0.09, 0.16, 0.14 - i * 0.04, "sine");
  });
}

const TOOL_SFX = {
  web_search: playWebSearchSfx,
  save_user_memory: playMemorySaveSfx,
  save_game_memory: playMemorySaveSfx,
  save_session_memory: playMemorySaveSfx,
  remove_memory: playMemoryDeleteSfx,
  rollback_session_memories: playMemoryDeleteSfx,
};

function playToolSfx(toolName) {
  if (!sfxOn()) return;
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  (TOOL_SFX[toolName] || playGenericToolSfx)();
}

// --- Chat sessions (sidebar) ---
// Multiple conversations, persisted server-side via /api/chats (data/chats.json). Each chat
// holds its own messages array; "active" chat functions capture a direct
// reference to that array at call time (not a shared global) so that
// switching chats mid-request can't make an in-flight reply get appended to
// the wrong conversation.


let chats = [];
let activeChatId = null;

async function loadChatsFromStorage() {
  try {
    const data = await fetch("/api/chats").then((r) => r.json());
    chats = data.chats || [];
  } catch (err) {
    chats = [];
  }
}

// Saves ONE chat (upsert by id) - sending the whole history of every conversation on every
// message made the payload grow with total history size.
function saveChat(chat) {
  fetch(`/api/chats/${encodeURIComponent(chat.id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(chat),
  }).catch(() => {});
}

function getActiveChat() {
  return chats.find((c) => c.id === activeChatId) || null;
}

function createNewChat() {
  const chat = {
    id: (crypto.randomUUID && crypto.randomUUID()) || `chat-${Date.now()}`,
    title: "New Chat",
    messages: [],
    createdAt: Date.now(),
    titleGenerated: false,
  };
  chats.unshift(chat);
  activeChatId = chat.id;
  saveChat(chat);
  renderChatList();
  // Don't call renderChatLog here — the caller (sendMessage / sendDirectVoice)
  // immediately appends the first message, so renderChatLog would see an empty
  // chat and wrongly re-enter the empty/centered state mid-animation.
  chatPanel.classList.remove("empty");
  chatLog.innerHTML = "";
}

function switchChat(id) {
  if (!chats.some((c) => c.id === id)) return;
  activeChatId = id;
  renderChatList();
  renderChatLog();
}

function deleteChat(id) {
  const chat = chats.find((c) => c.id === id);
  if (chat) {
    for (const message of chat.messages) {
      if (message.audioId) deleteVoiceBlob(message.audioId);
    }
  }
  chats = chats.filter((c) => c.id !== id);
  fetch(`/api/chats/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
  if (activeChatId === id) {
    if (chats.length > 0) {
      switchChat(chats[0].id);
    } else {
      activeChatId = null;
      renderChatList();
      renderChatLog();
    }
  } else {
    renderChatList();
  }
}

function addMessageToChat(chat, role, content, audioId) {
  chat.messages.push({ role, content, audioId: audioId || undefined, ts: new Date().toISOString() });
  if (chat.title === "New Chat" && role === "user") {
    chat.title = content.slice(0, 40) || "New Chat";
    renderChatList();
  }
  saveChat(chat);
}

// --- Voice message audio storage (server-side) ---
// Voice messages are uploaded to /api/voice/{id} and served back on demand.
// Keyed by a random UUID referenced from the chat message itself.

function saveVoiceBlob(id, blob) {
  const form = new FormData();
  form.append("audio", blob, "voice.wav");
  fetch(`/api/voice/${id}`, { method: "POST", body: form }).catch(() => {});
}

function deleteVoiceBlob(id) {
  fetch(`/api/voice/${id}`, { method: "DELETE" }).catch(() => {});
}

// Voice messages have no real text ("🎤 (voice message)"), so the
// slice-based fallback title above is useless for the app's primary,
// voice-driven usage. Once the first exchange completes, ask the LLM for a
// proper title and replace the fallback. Runs once per chat.
async function maybeGenerateTitle(chat, userText, assistantText) {
  if (chat.titleGenerated || !assistantText) return;
  chat.titleGenerated = true;
  try {
    const response = await fetch("/api/chat/title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_message: userText, assistant_message: assistantText }),
    });
    if (!response.ok) return;
    const data = await response.json();
    if (data.title) {
      chat.title = data.title;
      saveChat(chat);
      renderChatList();
    }
  } catch (err) {
    // network/LLM failure: keep the fallback title, nothing to recover here
  }
}

let chatSearchQuery = "";

function renderChatList() {
  chatListEl.innerHTML = "";
  const query = chatSearchQuery.toLowerCase();
  // Only show chats that have at least one message — "New Chat" never appears until a message is sent.
  const withMessages = chats.filter((c) => c.messages.length > 0);
  const visible = query ? withMessages.filter((c) => c.title.toLowerCase().includes(query)) : withMessages;

  if (withMessages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "chat-list-empty";
    empty.textContent = "No chat history — your next chats will appear here";
    chatListEl.appendChild(empty);
    return;
  }

  if (visible.length === 0) {
    const empty = document.createElement("div");
    empty.className = "chat-list-empty";
    empty.textContent = "No chats match your search";
    chatListEl.appendChild(empty);
    return;
  }

  for (const chat of visible) {
    const item = document.createElement("div");
    item.className = "chat-list-item" + (chat.id === activeChatId ? " active" : "");

    const title = document.createElement("span");
    title.className = "chat-list-title";
    title.textContent = chat.title;
    title.addEventListener("click", () => switchChat(chat.id));

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "chat-list-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Delete chat";
    deleteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteChat(chat.id);
    });

    item.appendChild(title);
    item.appendChild(deleteBtn);
    chatListEl.appendChild(item);
  }
}

document.getElementById("chat-search").addEventListener("input", (e) => {
  chatSearchQuery = e.target.value;
  renderChatList();
});

const chatPanel = document.querySelector(".chat-panel");

// Empty state with suggestion chips — shown when there's no active chat or no messages yet.
const EMPTY_STATE_SUGGESTIONS = [
  "What can you do?",
  "Recommend me a game for tonight",
  "What's new in gaming this week?",
];

function renderEmptyState() {
  const wrap = document.createElement("div");
  wrap.className = "chat-empty-state";

  const logo = document.createElement("img");
  logo.src = "img/logo.png";
  logo.alt = "";
  logo.className = "chat-empty-logo";
  wrap.appendChild(logo);

  const heading = document.createElement("h1");
  heading.className = "chat-empty-heading";
  heading.textContent = emptyStateGreeting();
  wrap.appendChild(heading);

  const sub = document.createElement("p");
  sub.textContent =
    "Ask about a build, a boss, or just start talking.";
  wrap.appendChild(sub);

  const chips = document.createElement("div");
  chips.className = "chat-empty-chips";
  // Context-aware first chip while a game is tracked (lastTrackedProcess is kept fresh by the
  // game-state panel's 2s poll).
  const suggestions = lastTrackedProcess
    ? [`Catch me up on my ${lastTrackedProcess.replace(/\.exe$/i, "")} session`, ...EMPTY_STATE_SUGGESTIONS]
    : EMPTY_STATE_SUGGESTIONS;
  for (const suggestion of suggestions) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chat-empty-chip";
    chip.textContent = suggestion;
    chip.addEventListener("click", () => sendMessage(suggestion));
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);

  chatLog.appendChild(wrap);
}

// Blob URLs created for freshly-recorded voice bubbles - revoked whenever the log is wiped
// (chat switch/delete), since re-rendered bubbles stream from the server instead.
let _voicePlayerBlobUrls = [];

function renderChatLog() {
  for (const url of _voicePlayerBlobUrls) URL.revokeObjectURL(url);
  _voicePlayerBlobUrls = [];
  chatLog.innerHTML = "";
  const chat = getActiveChat();
  if (!chat || chat.messages.length === 0) {
    chatPanel.classList.add("empty");
    renderEmptyState();
    return;
  }
  chatPanel.classList.remove("empty");
  for (const message of chat.messages) {
    appendMessage(message.role, message.content, message.audioId, false, null, message.ts);
  }
}

// Exit the empty/centered state: FLIP-animate the composer from its current
// centered position down to its normal bottom position, then clear the class.
function exitEmptyState() {
  if (!chatPanel.classList.contains("empty")) return;
  const composer = document.querySelector(".composer");
  const first = composer.getBoundingClientRect();
  chatPanel.classList.remove("empty");
  const last = composer.getBoundingClientRect();
  const dy = first.top - last.top;
  if (Math.abs(dy) < 2) return;
  composer.style.transition = "none";
  composer.style.transform = `translateY(${dy}px)`;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      composer.style.transition = "transform 450ms cubic-bezier(0.16, 1, 0.3, 1)";
      composer.style.transform = "";
    });
  });
  composer.addEventListener("transitionend", () => {
    composer.style.transition = "";
    composer.style.transform = "";
  }, { once: true });
}

newChatBtn.addEventListener("click", () => {
  activeChatId = null;
  renderChatList();
  renderChatLog();
  chatInput.focus();
});

// --- Sentence-pipelined TTS queue ---
// Rather than waiting for the full LLM reply before synthesizing speech, each
// completed sentence is synthesized as soon as it's available while the rest
// of the reply is still streaming in. Synthesis for the next sentence starts
// immediately on enqueue (overlapping with current playback) so there's
// minimal gap between sentences once the first one is ready.

let ttsQueue = [];
let ttsPlaying = false;

// True only while audio is actually sounding (not just queued). While
// narrating, the live mic does NOT suppress — instead, any loud sound is
// treated as the user interrupting (barge-in): narration is cut immediately
// and a new recording begins. This trades off false interrupts from speaker
// bleed-through (mitigated by the echoCancellation constraint on getUserMedia
// below) for the ability to cut off the companion mid-sentence, e.g. to stop
// a spoiler.
let isNarrating = false;
let pendingNarrationResolve = null;

// While true, the live mic fully ignores volume input — there's no audio
// playing yet (we're waiting on the LLM request to even start responding), so
// there's nothing to interrupt and nothing useful to start recording either.
let awaitingReply = false;

async function synthesizeSentence(text) {
  const response = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, speed: narrationSpeed }),
  });
  return response.blob();
}

// The companion may embed markdown images/links (see renderMessageMarkup). Images are shown
// visually, so narration skips them entirely rather than reading the alt text aloud; links
// still speak their label text, just never the URL. Everything else markdown (emphasis,
// headers, bullets, code, fences) is stripped down to plain speakable text so the TTS never
// reads "asterisk" aloud. Also strips emojis as a safety net in case the model doesn't follow
// the "no emojis" system prompt rule. Runs once per complete sentence (in enqueueNarration) -
// never on the still-streaming buffer, where half-arrived URLs/links would be torn mid-pattern
// and their tails leaked to TTS on the next delta.
const EMOJI_REGEX = /[\u{1F1E6}-\u{1F1FF}\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2190}-\u{21FF}\u{FE0F}\u{200D}]/gu;

function stripMarkdownForNarration(text) {
  return text
    .replace(/^```[^\n]*$/gm, "")
    .replace(/!\[([^\]]*)\]\([^\s)]+\)/g, "")
    .replace(/\[([^\]]+)\]\([^\s)]+\)/g, (_m, label) => label)
    .replace(/https?:\/\/[^\s<>"']+/g, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^[ \t]*(?:[-*_][ \t]*){3,}$/gm, "")
    .replace(/[*_#`~]+/g, "")
    .replace(EMOJI_REGEX, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function enqueueNarration(text) {
  const cleaned = text ? stripMarkdownForNarration(text) : "";
  if (!cleaned) return Promise.resolve();
  const promise = synthesizeSentence(cleaned);
  return new Promise((resolveItem) => {
    ttsQueue.push({ promise, resolveItem });
    processTtsQueue();
  });
}

// Whether the currently selected TTS path needs the browser to enforce
// narration speed itself. Local Kokoro already applies speed server-side
// (confirmed working), so it's left alone — applying playbackRate on top
// would double it up. None of OpenRouter's Speech models currently declare
// "speed" in supported_parameters (it's silently ignored if sent), so for
// OpenRouter we check the selected model's actual capability and only kick
// in client-side if the model genuinely doesn't support it.
function shouldApplyClientSideSpeed() {
  const provider = document.getElementById("cfg-tts-provider").value;
  if (provider !== "openrouter") return false;
  const modelId = document.getElementById("cfg-openrouter-tts-model").value;
  const model = openrouterSpeechModelsById[modelId];
  const supportsSpeed = Boolean(model && model.supported_parameters && model.supported_parameters.includes("speed"));
  return !supportsSpeed;
}

async function processTtsQueue() {
  if (ttsPlaying || ttsQueue.length === 0) return;
  ttsPlaying = true;
  isNarrating = true;
  stopNarrationBtn.hidden = false;
  const item = ttsQueue.shift();
  let blobUrl = null;
  try {
    const blob = await item.promise;
    if (ttsPlaying) {
      blobUrl = URL.createObjectURL(blob);
      narrationAudio.src = blobUrl;
      narrationAudio.playbackRate = shouldApplyClientSideSpeed() ? narrationSpeed : 1;
      narrationAudio.volume = narrationVolume;
      await new Promise((resolve) => {
        pendingNarrationResolve = resolve;
        narrationAudio.onended = resolve;
        narrationAudio.play();
      });
    }
  } catch (err) {
    // synthesis/playback failed for this sentence; move on to the next
  } finally {
    // Each sentence gets its own blob URL - never revoked, they accumulate for the whole
    // session (one leaked audio buffer per narrated sentence).
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    item.resolveItem();
    pendingNarrationResolve = null;
    ttsPlaying = false;
    isNarrating = false;
    if (ttsQueue.length === 0) stopNarrationBtn.hidden = true;
    processTtsQueue();
  }
}

async function narrate(text) {
  await enqueueNarration(text);
}

function stopNarration() {
  const pending = ttsQueue;
  ttsQueue = [];
  pending.forEach((item) => item.resolveItem());
  stopNarrationBtn.hidden = true;

  if (!ttsPlaying) return;
  narrationAudio.onended = null;
  narrationAudio.pause();
  narrationAudio.currentTime = 0;
  ttsPlaying = false;
  isNarrating = false;
  if (pendingNarrationResolve) {
    const resolve = pendingNarrationResolve;
    pendingNarrationResolve = null;
    resolve();
  }
}

stopNarrationBtn.addEventListener("click", stopNarration);

// --- Minimal markdown rendering for chat content ---
// Only supports what the companion is instructed to use: images, links, and bare URLs.
// Text is HTML-escaped first so the LLM can never inject arbitrary markup, then the three
// patterns are substituted in an order where each one can't be re-matched by the next.

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// The companion sometimes embeds an image URL that turns out to be dead/hallucinated - without
// this, a failed load falls back to the browser's native broken-image icon with the alt text
// squished next to it inline, rather than a clean placeholder. "error" doesn't bubble, but it
// does fire during the capture phase, so one delegated listener covers every image.
chatLog.addEventListener(
  "error",
  (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.classList.contains("chat-inline-image")) return;
    const placeholder = document.createElement("span");
    placeholder.className = "chat-image-broken";
    placeholder.textContent = img.alt ? `[image unavailable: ${img.alt}]` : "[image unavailable]";
    img.replaceWith(placeholder);
  },
  true
);

function renderMessageMarkup(text) {
  let html = escapeHtml(text);

  // Images: ![alt](https://...) — also matches the relative /api/proxy/image?url=... URLs the
  // SearXNG image search returns (the https-only pattern rendered those as literal text), and
  // appends the API token those <img> loads need since elements can't send headers.
  html = html.replace(/!\[([^\]]*)\]\((\/api\/proxy\/image\?[^\s)]+)\)/g, (_m, alt, url) => {
    return `<img src="${apiUrl(url)}" alt="${alt}" class="chat-inline-image" loading="lazy" />`;
  });
  html = html.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, (_m, alt, url) => {
    return `<img src="${url}" alt="${alt}" class="chat-inline-image" loading="lazy" />`;
  });

  // Links: [text](https://...)
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  // Bare URLs left over (not already inside an href="..."/src="..." we just generated).
  html = html.replace(/(?<!["'])https?:\/\/[^\s<>"']+/g, (url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
  });

  return html;
}

function appendMessage(role, content, audioId, isNew = false, audioBlob = null, timestamp = null) {
  chatLog.querySelector(".chat-empty-state")?.remove();
  const el = document.createElement("div");
  el.className = `message ${role}`;
  if (isNew) el.classList.add("message-enter");

  // SVG icon helpers
  const copyIcon  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
  const checkIcon = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 12 4 9"/></svg>`;

  // ── Role header ───────────────────────────────────────────
  const meta = document.createElement("div");
  meta.className = "msg-meta";

  const avatar = document.createElement("span");
  avatar.className = "msg-avatar";
  avatar.innerHTML = role === "assistant" ? `<img src="img/logo.png" alt="" />` : userAvatarMarkup();
  meta.appendChild(avatar);

  const roleName = document.createElement("span");
  roleName.className = "msg-role-name";
  roleName.textContent = role === "user" ? userDisplayName : "Lykompanion";
  meta.appendChild(roleName);

  // ── Action buttons (hidden for pure voice bubbles) ────────
  const actionsEl = document.createElement("div");
  actionsEl.className = "msg-actions";

  if (!audioId) {
    // Copy — always shown for text messages
    const copyBtn = document.createElement("button");
    copyBtn.className = "msg-action-btn";
    copyBtn.title = "Copy";
    copyBtn.innerHTML = copyIcon;
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(contentDiv.textContent).then(() => {
        copyBtn.innerHTML = checkIcon;
        copyBtn.classList.add("copied");
        setTimeout(() => { copyBtn.innerHTML = copyIcon; copyBtn.classList.remove("copied"); }, 1500);
      });
    });
    actionsEl.appendChild(copyBtn);

    if (role === "user") {
      // Retry — resend the same text
      const retryBtn = document.createElement("button");
      retryBtn.className = "msg-action-btn";
      retryBtn.title = "Retry";
      retryBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>`;
      retryBtn.addEventListener("click", () => sendMessage(contentDiv.textContent));
      actionsEl.appendChild(retryBtn);
    }

    if (role === "assistant") {
      // Add to Memory — pre-fills the memory modal input
      const memBtn = document.createElement("button");
      memBtn.className = "msg-action-btn";
      memBtn.title = "Add to Memory";
      memBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>`;
      memBtn.addEventListener("click", async () => {
        document.getElementById("memory-add-input").value = contentDiv.textContent.trim().slice(0, 300);
        openModal(personalDataModal);
        personalDataModal.querySelector('.tab-btn[data-tab="memory"]').click();
              await loadMemories();
      });
      actionsEl.appendChild(memBtn);
    }
  }

  meta.appendChild(actionsEl);
  el.appendChild(meta);

  // ── Content area ──────────────────────────────────────────
  const contentDiv = document.createElement("div");
  contentDiv.className = "msg-content";
  contentDiv.innerHTML = renderMessageMarkup(content);
  // For voice messages the player IS the bubble; hide the text only while it's still the
  // 🎤 placeholder - once the transcript is swapped in it must survive re-renders/reloads.
  if (audioId && (!content || content.startsWith("🎤"))) contentDiv.hidden = true;
  el.appendChild(contentDiv);

  // ── Voice player — styled as the message bubble ───────────
  if (audioId) {
    // Use a local blob URL for immediate playback on new messages so the player
    // works instantly without waiting for the server upload to complete.
    let audioSrc;
    if (audioBlob) {
      audioSrc = URL.createObjectURL(audioBlob);
      _voicePlayerBlobUrls.push(audioSrc);
    } else {
      audioSrc = apiUrl(`/api/voice/${audioId}`);
    }
    const audio = new Audio(audioSrc);

    const player = document.createElement("div");
    // voice-bubble class makes it look like the role's message bubble
    player.className = `voice-player voice-bubble ${role}`;

    const playBtn = document.createElement("button");
    playBtn.className = "vp-play";
    playBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`;

    const barWrap = document.createElement("div");
    barWrap.className = "vp-bar-wrap";
    const bar = document.createElement("div");
    bar.className = "vp-bar";
    const fill = document.createElement("div");
    fill.className = "vp-fill";
    bar.appendChild(fill);
    barWrap.appendChild(bar);

    const timeEl = document.createElement("span");
    timeEl.className = "vp-time";
    timeEl.textContent = "0:00";

    const speeds = [0.5, 0.75, 1, 1.25, 1.5, 2];
    let speedIdx = 2;
    const speedBtn = document.createElement("button");
    speedBtn.className = "vp-speed";
    speedBtn.textContent = "1×";

    const dlBtn = document.createElement("button");
    dlBtn.className = "vp-dl";
    dlBtn.title = "Save to Downloads";
    dlBtn.innerHTML = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;

    player.appendChild(playBtn);
    player.appendChild(barWrap);
    player.appendChild(timeEl);
    player.appendChild(speedBtn);
    player.appendChild(dlBtn);
    el.appendChild(player);

    function fmt(s) {
      if (!isFinite(s)) return "0:00";
      const m = Math.floor(s / 60);
      return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
    }

    audio.addEventListener("loadedmetadata", () => { timeEl.textContent = fmt(audio.duration); });

    let rafId = null;
    function tick() {
      if (!audio.duration) return;
      fill.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
      timeEl.textContent = fmt(audio.currentTime);
      if (!audio.paused && !audio.ended) rafId = requestAnimationFrame(tick);
    }
    audio.addEventListener("play",  () => { rafId = requestAnimationFrame(tick); });
    audio.addEventListener("pause", () => { cancelAnimationFrame(rafId); });
    audio.addEventListener("ended", () => {
      cancelAnimationFrame(rafId);
      playBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`;
      fill.style.width = "0%";
      timeEl.textContent = fmt(audio.duration);
    });

    playBtn.addEventListener("click", () => {
      if (audio.paused) {
        audio.play();
        playBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;
      } else {
        audio.pause();
        playBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`;
      }
    });

    bar.addEventListener("click", (e) => {
      if (!audio.duration) return;
      const rect = bar.getBoundingClientRect();
      audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
    });

    speedBtn.addEventListener("click", () => {
      speedIdx = (speedIdx + 1) % speeds.length;
      audio.playbackRate = speeds[speedIdx];
      speedBtn.textContent = `${speeds[speedIdx]}×`;
    });

    dlBtn.addEventListener("click", async () => {
      if (window.pywebview?.api?.download_voice) {
        const result = await window.pywebview.api.download_voice(audioId);
        if (result?.ok) {
          showToast(`dl-${audioId}`, {
            title: result.already ? "Already in Downloads" : "Saved to Downloads",
            body: result.name,
            duration: 4000,
          });
        }
      } else {
        const res = await fetch(`/api/voice/${audioId}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `lykompanion-voice_${audioId.slice(0, 8)}.wav`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`dl-${audioId}`, { title: "Saved to Downloads", body: a.download, duration: 4000 });
      }
    });
  }
  // ── Timestamp ─────────────────────────────────────────────
  const tsEl = document.createElement("div");
  tsEl.className = "msg-timestamp";
  const tsDate = timestamp ? new Date(timestamp) : (isNew ? new Date() : null);
  if (tsDate) {
    const now = new Date();
    const sameDay = tsDate.toDateString() === now.toDateString();
    tsEl.textContent = sameDay
      ? tsDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : tsDate.toLocaleDateString([], { month: "short", day: "numeric" }) + " · " + tsDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  el.appendChild(tsEl);

  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  return contentDiv;
}

// Splits a growing text buffer into complete sentences plus a leftover
// remainder (incomplete sentence still being streamed in). Operates on RAW
// (unstripped) text: a sentence boundary is terminal punctuation followed by
// whitespace, or a newline - never punctuation followed by more text, so the
// periods inside a still-streaming URL ("example.com/pa...") are not cut
// through. A trailing markdown image/link that hasn't closed its ")" yet is
// held back whole, so stripMarkdownForNarration only ever sees complete
// patterns. Punctuation at the very end of the buffer stays in the remainder
// (more of the same token may still arrive); callers flush the remainder when
// the stream ends.
function extractCompleteSentences(buffer) {
  const holdAt = buffer.search(/!?\[[^\]]*(?:\]\([^)\s]*)?$/);
  const splittable = holdAt === -1 ? buffer : buffer.slice(0, holdAt);
  // Linear scan, NOT a regex: the old pattern /(?:[^.!?\n]+|[.!?](?!\s))*(?:[.!?]+\s+|\n+)/g
  // backtracked catastrophically whenever the buffer ended in a long incomplete sentence -
  // one exec() call could take minutes and froze the entire UI mid-stream (the "app hangs
  // while the reply arrives" bug). A sentence ends at a run of [.!?] followed by whitespace,
  // or at newline(s); anything after the last boundary stays in the remainder.
  const complete = [];
  let lastIndex = 0;
  let i = 0;
  while (i < splittable.length) {
    const ch = splittable[i];
    if (ch === "\n") {
      let end = i + 1;
      while (end < splittable.length && splittable[end] === "\n") end++;
      const sentence = splittable.slice(lastIndex, end).trim();
      if (sentence) complete.push(sentence);
      lastIndex = end;
      i = end;
    } else if (ch === "." || ch === "!" || ch === "?") {
      let end = i + 1;
      while (end < splittable.length && ".!?".includes(splittable[end])) end++;
      let ws = end;
      while (ws < splittable.length && splittable[ws] !== "\n" && /\s/.test(splittable[ws])) ws++;
      if (ws > end) {
        // Terminator run followed by whitespace = sentence boundary.
        const sentence = splittable.slice(lastIndex, ws).trim();
        if (sentence) complete.push(sentence);
        lastIndex = ws;
        i = ws;
      } else {
        // "1.5", "v2.0", or a terminator at the very end of the buffer (more may stream in).
        i = end;
      }
    } else {
      i++;
    }
  }
  return { complete, remainder: buffer.slice(lastIndex) };
}

const sendBtn = document.querySelector(".send-btn");
let isStreaming = false;
let chatAbortController = null;

function setStreaming(streaming) {
  isStreaming = streaming;
  sendBtn.textContent = streaming ? "Stop" : "Send";
  sendBtn.classList.toggle("stop", streaming);
}

async function sendMessage(text) {
  exitEmptyState();
  if (!getActiveChat()) createNewChat();
  const chat = getActiveChat();

  stopNarration();
  appendMessage("user", text, null, true);
  addMessageToChat(chat, "user", text);
  playSentSfx();

  const narrateEnabled = document.getElementById("cfg-narrate").checked;
  const assistantEl = appendMessage("assistant", "", null, true);
  let fullReply = "";
  let sentenceBuffer = "";

  awaitingReply = true;
  chatAbortController = new AbortController();
  setStreaming(true);
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chat.messages, include_screenshot: includeScreenshot }),
      signal: chatAbortController.signal,
    });

    if (!response.ok || !response.body) {
      const error = await response.json().catch(() => ({}));
      const errText = `⚠️ ${error.detail || "Chat request failed."}`;
      assistantEl.innerHTML = renderMessageMarkup(errText);
      // Persist the error bubble too - otherwise it vanishes on chat switch/reload and the
      // conversation shows a user message with no reply at all.
      addMessageToChat(chat, "assistant", errText);
      return;
    }

    awaitingReply = false;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (!rawEvent.startsWith("data: ")) continue;

        const payload = JSON.parse(rawEvent.slice(6));

        if (payload.error) {
          fullReply += `\n⚠️ ${payload.error}`;
          assistantEl.innerHTML = renderMessageMarkup(fullReply);
          continue;
        }
        if (payload.volume !== undefined) {
          // The agent adjusted its own volume mid-reply (set_narration_volume tool) - apply it
          // immediately, including to whatever's playing right now, not just future sentences.
          applyNarrationVolume(payload.volume);
          continue;
        }
        if (payload.stop_listening) {
          agentStopListening();
          continue;
        }
        if (payload.tool_sfx) {
          playToolSfx(payload.tool_sfx);
          continue;
        }
        if (payload.done) continue;

        fullReply += payload.delta;
        // Re-attach if a mid-stream re-render (chat switch, modal, etc.) detached the bubble.
        if (!assistantEl.isConnected && activeChatId === chat.id) {
          chatLog.appendChild(assistantEl.parentElement);
        }
        assistantEl.innerHTML = renderMessageMarkup(fullReply);
        chatLog.scrollTop = chatLog.scrollHeight;

        if (narrateEnabled) {
          sentenceBuffer += payload.delta;
          // Split the RAW buffer; markdown stripping happens per complete sentence
          // inside enqueueNarration, so patterns spanning multiple deltas stay intact.
          const { complete, remainder } = extractCompleteSentences(sentenceBuffer);
          sentenceBuffer = remainder;
          for (const sentence of complete) {
            enqueueNarration(sentence);
          }
        }
      }
    }

    if (narrateEnabled && sentenceBuffer.trim()) {
      enqueueNarration(sentenceBuffer.trim());
    }

    addMessageToChat(chat, "assistant", fullReply);
    if (!assistantEl.isConnected && activeChatId === chat.id) renderChatLog();
    maybeGenerateTitle(chat, text, fullReply);
  } catch (err) {
    if (err.name !== "AbortError") {
      const errText = fullReply
        ? `${fullReply}\n⚠️ Connection lost mid-reply.`
        : "⚠️ Chat request failed.";
      assistantEl.innerHTML = renderMessageMarkup(errText);
      addMessageToChat(chat, "assistant", errText);
    } else if (fullReply) {
      addMessageToChat(chat, "assistant", fullReply);
    }
  } finally {
    awaitingReply = false;
    chatAbortController = null;
    setStreaming(false);
  }
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (isStreaming) {
    chatAbortController?.abort();
    return;
  }
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  sendMessage(text);
});

screenshotToggle.addEventListener("click", () => {
  includeScreenshot = !includeScreenshot;
  screenshotToggle.classList.toggle("active", includeScreenshot);
  screenshotToggle.title = `Include screenshot: ${includeScreenshot ? "on" : "off"}`;
  screenshotIndicator.hidden = !includeScreenshot;
});

// --- Voice input: always sent directly to an audio-capable LLM. Local
// transcription was removed (unreliable, especially for Romanian) — both the
// push-to-talk mic button and hands-free Live Mic capture audio and send it
// straight to /api/chat/voice. ---

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  function writeString(offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }

  return new Blob([buffer], { type: "audio/wav" });
}

async function blobToWavBlob(blob) {
  const arrayBuffer = await blob.arrayBuffer();
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
  return encodeWav(audioBuffer.getChannelData(0), audioBuffer.sampleRate);
}

async function sendDirectVoice(wavBlob) {
  exitEmptyState();
  if (!getActiveChat()) createNewChat();
  const chat = getActiveChat();

  // In transcription mode the recording is thrown away after the LLM sees only the transcript
  // text, so there's no audio worth persisting/playing back - render as a plain text bubble.
  const transcriptionMode = transcriptionEnabledInput.checked;
  const audioId = transcriptionMode ? null : (crypto.randomUUID && crypto.randomUUID()) || `voice-${Date.now()}`;
  if (audioId) saveVoiceBlob(audioId, wavBlob);

  stopNarration();
  awaitingReply = true;
  try {
    // Snapshot the history BEFORE the new voice turn is added to it - the audio itself is what
    // carries this turn to the backend, so including a placeholder text message too would
    // duplicate the turn in the LLM's view.
    const historyJson = JSON.stringify(chat.messages);

    const userContentDiv = appendMessage(
      "user",
      transcriptionMode ? "🎤 Transcribing…" : "🎤 (voice message)",
      audioId,
      true,
      audioId ? wavBlob : null
    );
    // Persist the voice turn immediately - the error paths below used to return before it was
    // ever added to chat.messages, making the bubble vanish on the next chat switch or reload.
    addMessageToChat(chat, "user", "🎤 (voice message)", audioId);
    playSentSfx();
    const userMessage = chat.messages[chat.messages.length - 1];
    setVoiceStatus("Sending voice message...");

    const formData = new FormData();
    formData.append("audio", wavBlob, "voice.wav");
    formData.append("history", historyJson);
    formData.append("include_screenshot", String(includeScreenshot));

    const response = await fetch("/api/chat/voice/stream", { method: "POST", body: formData });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const errText = `⚠️ ${error.detail || "Voice chat failed."}`;
      appendMessage("assistant", errText, null, true);
      addMessageToChat(chat, "assistant", errText);
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
      return;
    }

    const assistantEl = appendMessage("assistant", "", null, true);
    let fullReply = "";
    let stopListening = false;
    let transcript = null;
    // Sentence-pipelined narration, same as the text path - narrating only after the full
    // reply arrived added several seconds of silence to every voice exchange.
    const narrateEnabled = document.getElementById("cfg-narrate").checked;
    let sentenceBuffer = "";

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (!rawEvent.startsWith("data: ")) continue;

        const payload = JSON.parse(rawEvent.slice(6));

        if (payload.error) {
          const errText = fullReply ? `${fullReply}\n⚠️ ${payload.error}` : `⚠️ ${payload.error}`;
          assistantEl.innerHTML = renderMessageMarkup(errText);
          addMessageToChat(chat, "assistant", errText);
          return;
        }
        if (payload.delta) {
          fullReply += payload.delta;
          // A re-render mid-stream (chat switch, modal, etc.) wipes chatLog and detaches the
          // live bubbles - the stream then types into limbo and the UI looks hung. Re-attach
          // as long as this chat is still the visible one.
          if (!assistantEl.isConnected && activeChatId === chat.id) {
            chatLog.appendChild(assistantEl.parentElement);
          }
          assistantEl.innerHTML = renderMessageMarkup(fullReply);
          chatLog.scrollTop = chatLog.scrollHeight;
          if (narrateEnabled) {
            sentenceBuffer += payload.delta;
            const { complete, remainder } = extractCompleteSentences(sentenceBuffer);
            sentenceBuffer = remainder;
            for (const sentence of complete) enqueueNarration(sentence);
          }
        }
        if (payload.volume !== undefined) applyNarrationVolume(payload.volume);
        if (payload.stop_listening) { stopListening = true; agentStopListening(); }
        if (payload.tool_sfx) playToolSfx(payload.tool_sfx);
        if (payload.done) {
          applyNarrationVolume(payload.narration_volume);
          transcript = payload.transcript || null;
        }
      }
    }

    if (transcript) {
      // Update the store first - it's the source of truth for re-renders and future history.
      userMessage.content = transcript;
      userContentDiv.hidden = false;
      userContentDiv.innerHTML = renderMessageMarkup(transcript);
    }
    addMessageToChat(chat, "assistant", fullReply);
    // If a re-render happened mid-stream, the live bubbles are detached; now that everything
    // is in chat.messages, one re-render restores the full exchange (incl. the transcript).
    if (!assistantEl.isConnected && activeChatId === chat.id) renderChatLog();
    maybeGenerateTitle(chat, transcript || "(voice message)", fullReply);

    awaitingReply = false;
    if (stopListening) {
      agentStopListening();
    } else {
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    }
    if (narrateEnabled && sentenceBuffer.trim()) {
      await enqueueNarration(sentenceBuffer.trim());
    }
  } catch (err) {
    setVoiceStatus("Voice chat failed", "error");
  } finally {
    awaitingReply = false;
  }
}

let mediaRecorder;
let audioChunks = [];

micBtn.addEventListener("click", async () => {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }

  stopNarration();

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    setVoiceStatus("Microphone access denied", "error");
    return;
  }

  mediaRecorder = new MediaRecorder(stream);
  audioChunks = [];

  mediaRecorder.ondataavailable = (event) => audioChunks.push(event.data);
  mediaRecorder.onstop = async () => {
    micBtn.classList.remove("recording");
    beep(440, 0.12);
    stream.getTracks().forEach((track) => track.stop());

    const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
    if (audioBlob.size === 0) {
      setVoiceStatus("");
      return;
    }

    setVoiceStatus("Converting audio...");
    try {
      const wavBlob = await blobToWavBlob(audioBlob);
      await sendDirectVoice(wavBlob);
    } catch (err) {
      setVoiceStatus("Voice chat failed", "error");
    }
  };

  mediaRecorder.start();
  micBtn.classList.add("recording");
  setVoiceStatus("Listening...", "recording");
  beep(880, 0.12);
});

// --- Live mic: hands-free, volume-gated capture for talking while gaming ---
// --- Lightweight speech/non-speech gate (Silero VAD via @ricky0123/vad-web) ---
// Runs once on each finalized live-mic utterance, locally, before sending - filters out coughs/
// claps/keyboard noise that pass the amplitude threshold but aren't actually speech, without
// adding round-trip latency (it's a local check on the already-captured buffer, not an extra
// network call). Fails open: if the library didn't load or errors out, utterances are sent as
// before rather than silently dropped, so this is a refinement, not a hard dependency.

let nonRealTimeVadPromise = null;

function getNonRealTimeVad() {
  if (!window.vad || !window.vad.NonRealTimeVAD) return null;
  if (!nonRealTimeVadPromise) {
    nonRealTimeVadPromise = window.vad.NonRealTimeVAD.new().catch(() => {
      nonRealTimeVadPromise = null;
      return null;
    });
  }
  return nonRealTimeVadPromise;
}

async function isLikelySpeech(samples, sampleRate) {
  try {
    const instance = await getNonRealTimeVad();
    if (!instance) return true; // VAD unavailable - fail open, don't block sending

    for await (const _segment of instance.run(samples, sampleRate)) {
      return true; // any detected speech segment is enough
    }
    return false;
  } catch (err) {
    return true; // fail open on any runtime error
  }
}

// Continuously records raw PCM into a rolling ring buffer (not just monitoring
// volume) so that when speech is detected, we can prepend ~600ms of audio from
// *before* the threshold was crossed. Without this pre-roll, the first word or
// two spoken (before volume ramps up enough to trigger) gets lost, because a
// fresh recorder starting only at detection time can't capture audio that
// already happened. Once volume drops back below threshold for vadSilenceMs,
// the utterance is finalized as a WAV and sent directly to the LLM.

// Padding kept around the detected speech window, user-configurable (0-2500ms), so the first/last
// word or breath doesn't get clipped. Set from server config in loadConfig().
let preRollMs = 1000;
let postRollMs = 500;
const MAX_UTTERANCE_MS = 60000;
// The forced cutoff at liveSpeechStartTime + MAX_UTTERANCE_MS bounds total recording length
// regardless of vadSilenceMs/postRollMs, so the worst case span extractFromRing ever needs is
// preRollMs (max 2500ms) + MAX_UTTERANCE_MS = 62.5s. 65s leaves a safety margin.
const RING_BUFFER_SECONDS = 65;

let liveMicEnabled = false;
let liveMicStream = null;
let liveSource = null;
let liveProcessor = null;
let liveSilentGain = null;
let ringBuffer = null;
let ringSampleRate = 0;
let absoluteSampleCount = 0;
let liveRecording = false;
let utteranceStartAbsolute = 0;
let liveSilenceStart = null;
let liveSpeechStartTime = null;

function computeAmplitude(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    sum += Math.abs(samples[i]);
  }
  return sum / samples.length;
}

function writeToRing(samples) {
  const ringLength = ringBuffer.length;
  for (let i = 0; i < samples.length; i++) {
    ringBuffer[(absoluteSampleCount + i) % ringLength] = samples[i];
  }
  absoluteSampleCount += samples.length;
}

function extractFromRing(startAbs, endAbs) {
  const length = Math.min(endAbs - startAbs, ringBuffer.length);
  const clampedStart = endAbs - length;
  const ringLength = ringBuffer.length;
  const result = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    result[i] = ringBuffer[((clampedStart + i) % ringLength + ringLength) % ringLength];
  }
  return result;
}

async function startLiveMic() {
  try {
    liveMicStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    setVoiceStatus("Microphone access denied", "error");
    liveMicEnabled = false;
    liveMicToggle.classList.remove("active");
    return;
  }

  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") {
    await audioCtx.resume();
  }

  ringSampleRate = audioCtx.sampleRate;
  ringBuffer = new Float32Array(Math.ceil(RING_BUFFER_SECONDS * ringSampleRate));
  absoluteSampleCount = 0;
  liveRecording = false;
  liveSilenceStart = null;

  liveSource = audioCtx.createMediaStreamSource(liveMicStream);
  liveProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
  liveSilentGain = audioCtx.createGain();
  liveSilentGain.gain.value = 0; // keep the processor alive without echoing mic audio to speakers

  liveProcessor.onaudioprocess = (event) => {
    const samples = new Float32Array(event.inputBuffer.getChannelData(0));
    writeToRing(samples);

    if (awaitingReply) {
      liveSilenceStart = null;
      return;
    }

    const loud = computeAmplitude(samples) > vadThreshold / 127;

    if (isNarrating && loud) {
      stopNarration();
    }

    if (!liveRecording) {
      if (loud) {
        liveRecording = true;
        liveSilenceStart = null;
        liveSpeechStartTime = Date.now();
        const preRollSamples = Math.round((preRollMs / 1000) * ringSampleRate);
        utteranceStartAbsolute = Math.max(0, absoluteSampleCount - samples.length - preRollSamples);
        micBtn.classList.add("recording");
        setVoiceStatus("Listening to your request...", "recording");
        beep(880, 0.1);
      }
      return;
    }

    if (loud) {
      liveSilenceStart = null;
    } else {
      if (liveSilenceStart === null) liveSilenceStart = Date.now();
      if (Date.now() - liveSilenceStart > vadSilenceMs + postRollMs) {
        finalizeLiveUtterance();
      }
    }

    if (Date.now() - liveSpeechStartTime > MAX_UTTERANCE_MS) {
      finalizeLiveUtterance();
    }
  };

  liveSource.connect(liveProcessor);
  liveProcessor.connect(liveSilentGain);
  liveSilentGain.connect(audioCtx.destination);

  setVoiceStatus("Listening...");
}

async function finalizeLiveUtterance() {
  if (!liveRecording) return;
  liveRecording = false;
  // Block new recordings immediately — without this, the gap between here and
  // sendDirectVoice setting awaitingReply (after the async isLikelySpeech call)
  // is wide enough that ambient noise starts a second utterance. WebView2 makes
  // this worse because WASM module caching is weaker than Chrome, so isLikelySpeech
  // takes longer on first call.
  awaitingReply = true;
  micBtn.classList.remove("recording");
  beep(440, 0.1);

  const samples = extractFromRing(utteranceStartAbsolute, absoluteSampleCount);
  const durationMs = (samples.length / ringSampleRate) * 1000;

  if (durationMs < vadMinSpeechMs) {
    awaitingReply = false;
    setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    return;
  }

  setVoiceStatus("Processing...");
  if (!(await isLikelySpeech(samples, ringSampleRate))) {
    awaitingReply = false;
    setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    return;
  }

  // awaitingReply stays true — sendDirectVoice owns it from here
  sendDirectVoice(encodeWav(samples, ringSampleRate));
}

function stopLiveMic() {
  if (liveProcessor) {
    liveProcessor.onaudioprocess = null;
    liveProcessor.disconnect();
    liveProcessor = null;
  }
  if (liveSilentGain) {
    liveSilentGain.disconnect();
    liveSilentGain = null;
  }
  if (liveSource) {
    liveSource.disconnect();
    liveSource = null;
  }
  if (liveMicStream) {
    liveMicStream.getTracks().forEach((track) => track.stop());
    liveMicStream = null;
  }
  liveRecording = false;
  ringBuffer = null;
  micBtn.classList.remove("recording");
  setVoiceStatus(wakeWordEnabled ? `Say "${wakeWordPhrase}" to resume` : "");
}

liveMicToggle.addEventListener("click", () => {
  liveMicEnabled = !liveMicEnabled;
  liveMicToggle.classList.toggle("active", liveMicEnabled);
  if (liveMicEnabled) {
    startLiveMic();
  } else {
    stopLiveMic();
  }
  updateWakeWordListenerState();
});

// --- Agent-driven stop_listening tool: lets the companion disable hands-free listening itself
// (e.g. user says "goodbye" or it's picking up unwanted audio). Always indefinite - resuming is
// the wake word's job now, not a guessed auto-resume timer. No-op if hands-free wasn't even on.

function agentStopListening() {
  if (!liveMicEnabled) return;
  liveMicEnabled = false;
  liveMicToggle.classList.remove("active");
  stopLiveMic();
  updateWakeWordListenerState();
}

// --- Wake word ---
// Re-enables hands-free listening by voice once it's been turned off (manually or by the
// agent), since the main use case is couch/controller play where touching the keyboard/mouse
// defeats the point. Uses the browser's built-in SpeechRecognition API (Chrome/Edge only) -
// only actually runs while hands-free is off, so it never competes with the live mic's own
// mic stream or double-submits the wake phrase itself as a voice message.

const wakeWordUnsupportedEl = document.getElementById("wake-word-unsupported");
const wakeWordControlsEl = document.getElementById("wake-word-controls");
const wakeWordEnabledInput = document.getElementById("cfg-wake-word-enabled");
const wakeWordPhraseInput = document.getElementById("cfg-wake-word-phrase");
const wakeWordDependentEl = document.getElementById("wake-word-dependent");
const wakeWordMaxFailuresInput = document.getElementById("cfg-wake-word-max-failures");
const wakeWordMaxFailuresValue = document.getElementById("cfg-wake-word-max-failures-value");
const wakeWordDebugEl = document.getElementById("wake-word-debug");
const wakeWordStatusEl = document.getElementById("wake-word-status");
const wakeWordTranscriptEl = document.getElementById("wake-word-transcript");

const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
const wakeWordSupported = Boolean(SpeechRecognitionCtor);

let wakeWordRecognition = null;
let wakeWordShouldRun = false;
let wakeWordConsecutiveFailures = 0;
let wakeWordLastError = null;
let wakeWordMaxFailures = 3;

// Errors that mean recognition is genuinely broken (e.g. a plain/open-source Chromium build
// without Google's proprietary speech API key - the API exists but every start() fails). Distinct
// from "no-speech", which fires routinely during normal continuous listening and isn't a failure.
const WAKE_WORD_HARD_ERRORS = new Set(["network", "service-not-allowed", "audio-capture", "not-allowed"]);
const WAKE_WORD_RESTART_DELAY_MS = 500;

wakeWordMaxFailuresInput.addEventListener("input", () => {
  wakeWordMaxFailures = Number(wakeWordMaxFailuresInput.value);
  wakeWordMaxFailuresValue.textContent = wakeWordMaxFailures;
});

function normalizeForWakeMatch(text) {
  // Unicode-aware: NFD + combining-mark strip folds diacritics (ș→s, ă→a) so a wake phrase in
  // e.g. Romanian still matches; the old [^a-z0-9] filter deleted every accented letter outright.
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^\p{L}\p{N}\s]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

function handleWakeWordDetected() {
  if (liveMicEnabled) return;
  liveMicEnabled = true;
  liveMicToggle.classList.add("active");
  startLiveMic();
  playWakeChime();
  if (!wakeWordDebugEl.hidden) {
    wakeWordStatusEl.textContent = "Detected ✓";
    setTimeout(() => {
      if (wakeWordStatusEl) wakeWordStatusEl.textContent = "Listening for wake phrase...";
    }, 1500);
  }
  updateWakeWordListenerState();
}

function startWakeWordRecognition() {
  if (!wakeWordSupported || wakeWordRecognition) return;
  wakeWordRecognition = new SpeechRecognitionCtor();
  wakeWordRecognition.continuous = true;
  wakeWordRecognition.interimResults = true;
  wakeWordRecognition.lang = "en-US";

  wakeWordRecognition.onresult = (event) => {
    wakeWordConsecutiveFailures = 0; // got a real result - recognition is genuinely working

    let transcript = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript + " ";
    }
    transcript = transcript.trim();
    wakeWordTranscriptEl.textContent = transcript;

    const normalizedPhrase = normalizeForWakeMatch(wakeWordPhrase);
    if (normalizedPhrase && normalizeForWakeMatch(transcript).includes(normalizedPhrase)) {
      handleWakeWordDetected();
    }
  };

  wakeWordRecognition.onerror = (event) => {
    wakeWordLastError = event.error;
    if (WAKE_WORD_HARD_ERRORS.has(event.error)) {
      wakeWordConsecutiveFailures++;
    }
  };

  wakeWordRecognition.onend = () => {
    wakeWordRecognition = null;
    if (!wakeWordShouldRun) return;

    if (wakeWordConsecutiveFailures >= wakeWordMaxFailures) {
      wakeWordStatusEl.textContent = `Speech recognition unavailable (${wakeWordLastError || "unknown error"}) - try Google Chrome.`;
      return; // give up instead of hot-looping (and flickering the mic indicator) forever
    }

    setTimeout(() => {
      if (wakeWordShouldRun) startWakeWordRecognition();
    }, WAKE_WORD_RESTART_DELAY_MS);
  };

  try {
    wakeWordRecognition.start();
  } catch (err) {
    wakeWordRecognition = null;
  }
}

function stopWakeWordRecognition() {
  if (wakeWordRecognition) {
    wakeWordRecognition.onend = null;
    wakeWordRecognition.stop();
    wakeWordRecognition = null;
  }
}

function updateWakeWordListenerState() {
  const wasRunning = wakeWordShouldRun;
  wakeWordShouldRun = wakeWordSupported && wakeWordEnabled && !liveMicEnabled;

  if (wakeWordShouldRun) {
    if (!wasRunning) wakeWordConsecutiveFailures = 0; // fresh start - give it a clean shot
    startWakeWordRecognition();
  } else {
    stopWakeWordRecognition();
  }

  wakeWordDependentEl.hidden = !wakeWordEnabled;
  wakeWordDebugEl.hidden = !(wakeWordEnabled && wakeWordSupported);
  if (wakeWordEnabled && wakeWordSupported) {
    wakeWordStatusEl.textContent = liveMicEnabled ? "Hands-free is already on." : "Listening for wake phrase...";
    if (liveMicEnabled) wakeWordTranscriptEl.textContent = "";
  }
}

if (!wakeWordSupported) {
  wakeWordUnsupportedEl.hidden = false;
  wakeWordControlsEl.hidden = true;
}

wakeWordEnabledInput.addEventListener("change", () => {
  wakeWordEnabled = wakeWordEnabledInput.checked;
  updateWakeWordListenerState();
});

wakeWordPhraseInput.addEventListener("change", () => {
  wakeWordPhrase = wakeWordPhraseInput.value.trim() || "Hey Buddy";
});

updateWakeWordListenerState();

// --- Settings modal ---

function openModal(modal) {
  modal.hidden = false;
  // Double rAF: first lets display:flex paint, second triggers the CSS transition
  requestAnimationFrame(() => requestAnimationFrame(() => modal.classList.add('modal-animate-in')));
}

function closeModal(modal) {
  modal.classList.remove('modal-animate-in');
  setTimeout(() => { modal.hidden = true; }, 240);
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(document.getElementById(btn.dataset.close)));
});

[settingsModal, personalDataModal, diagnosticsModal, debugDetailModal, document.getElementById("gaming-journal-modal")].forEach((modal) => {
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

settingsBtn.addEventListener("click", () => openModal(settingsModal));

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
  restartPendingApprovalPolling(cfg.game_state_poll_interval_seconds);

  document.getElementById("cfg-debug-mode-enabled").checked = cfg.debug_mode_enabled;

  wakeWordEnabled = cfg.wake_word_enabled;
  wakeWordPhrase = cfg.wake_word_phrase || "Hey Buddy";
  wakeWordMaxFailures = cfg.wake_word_max_failures ?? 3;
  wakeWordMaxFailuresInput.value = wakeWordMaxFailures;
  wakeWordMaxFailuresValue.textContent = wakeWordMaxFailures;
  if (wakeWordSupported) {
    wakeWordEnabledInput.checked = wakeWordEnabled;
    wakeWordPhraseInput.value = wakeWordPhrase;
    updateWakeWordListenerState();
  }

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

  updateSetupBanner(cfg);
}

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
    game_state_ocr_enabled: gameStateEnabledInput.checked,
    game_state_poll_interval_seconds: parseInt(gameStateIntervalInput.value, 10),
    game_state_capture_interval_seconds: parseInt(gameStateCaptureIntervalInput.value, 10),
    game_state_model: document.getElementById("cfg-game-state-model").value,
    game_state_training_enabled: document.getElementById("cfg-game-state-training-enabled").checked,
    proactive_messages_enabled: document.getElementById("cfg-proactive-enabled").checked,
    proactive_min_interval_minutes: parseInt(document.getElementById("cfg-proactive-interval").value, 10),
    memory_rag_limit: parseInt(document.getElementById("cfg-memory-rag-limit").value, 10),
    wake_word_enabled: wakeWordEnabledInput.checked,
    wake_word_phrase: wakeWordPhraseInput.value.trim() || "Hey Buddy",
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
    "cfg-igdb-client-secret", "cfg-steam-api-key",
  ];
  for (const id of keyInputIds) {
    const el = document.getElementById(id);
    el.value = "";
    delete el.dataset.cleared;
  }
  applyConfigToForm(updatedCfg);
  flashSaved(saveButton);
}

document.getElementById("cfg-save").addEventListener("click", (event) => saveSettings(event.currentTarget));
document.getElementById("ga-save").addEventListener("click", (event) => saveSettings(event.currentTarget));

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

const memoryList = document.getElementById("memory-list");
const memoryAddForm = document.getElementById("memory-add-form");
const memoryAddInput = document.getElementById("memory-add-input");

// Auto-grow the add-memory textarea between its CSS min/max-height as the user types, instead
// of a fixed single-line input that scrolled long facts sideways.
memoryAddInput.addEventListener("input", () => {
  memoryAddInput.style.height = "auto";
  memoryAddInput.style.height = `${memoryAddInput.scrollHeight}px`;
});

// Textareas don't submit their form on Enter like a single-line input did - restore that,
// keeping Shift+Enter free for an actual newline in a longer fact.
memoryAddInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    memoryAddForm.requestSubmit();
  }
});

let allMemories = [];

function renderMemoryList() {
  memoryList.innerHTML = "";

  const visible = allMemories.filter((m) => (m.scope || "user") === "user");

  if (visible.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No memories here yet.";
    memoryList.appendChild(hint);
    return;
  }

  for (const entry of visible) {
    memoryList.appendChild(buildMemoryItem(entry, () => {
      allMemories = allMemories.filter((m) => m.id !== entry.id);
      renderMemoryList();
    }));
  }
}

// Shared editable memory row (Personal Data + Gaming Journal): contentEditable text saved on
// blur via PUT (keeping the entry's existing placement), and a delete button.
function buildMemoryItem(entry, onDeleted) {
  const item = document.createElement("div");
  item.className = "memory-item";

  const text = document.createElement("div");
  text.className = "memory-item-text";
  text.contentEditable = "true";
  text.textContent = entry.content;

  text.addEventListener("blur", async () => {
    const content = text.textContent.trim();
    if (!content || content === entry.content) {
      text.textContent = entry.content;
      return;
    }
    const response = await fetch(`/api/memory/${entry.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, process: entry.process, session_id: entry.session_id }),
    });
    if (response.ok) {
      entry.content = content;
    } else {
      text.textContent = entry.content;
    }
  });
  text.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      text.blur();
    }
  });

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "memory-item-delete";
  deleteBtn.textContent = "×";
  deleteBtn.title = "Remove memory";
  deleteBtn.addEventListener("click", async () => {
    const response = await fetch(`/api/memory/${entry.id}`, { method: "DELETE" });
    if (response.ok) onDeleted();
  });

  item.appendChild(text);
  item.appendChild(deleteBtn);
  return item;
}

async function loadMemories() {
  const response = await fetch("/api/memory");
  allMemories = await response.json();
  renderMemoryList();
}

memoryAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = memoryAddInput.value.trim();
  if (!content) return;
  memoryAddInput.value = "";
  memoryAddInput.style.height = "auto";
  await fetch("/api/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, scope: "user" }),
  });
  await loadMemories();
});

// --- Gaming Journal modal ---
// Per-game view of everything the companion knows: game-scope memories (hold across all
// playthroughs), profiles (named sessions) with their playthrough-scope memories, and each
// profile's unconfirmed screen observations. Also hosts the Game Awareness settings tab,
// moved out of Settings.

const gamingJournalModal = document.getElementById("gaming-journal-modal");
const gamingJournalBtn = document.getElementById("gaming-journal-btn");
const gamingJournalList = document.getElementById("gaming-journal-list");

gamingJournalBtn.addEventListener("click", () => {
  openModal(gamingJournalModal);
  loadGamingJournal();
  loadGameStateProcessLists();
});

async function loadGamingJournal() {
  let games = [];
  try {
    const response = await fetch("/api/gaming-journal");
    if (response.ok) games = await response.json();
  } catch (err) {
    games = [];
  }
  renderGamingJournal(games);
}

function buildJournalAddForm(placeholder, buildBody) {
  const form = document.createElement("form");
  form.className = "memory-add-form";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  const btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "secondary-btn";
  btn.textContent = "Add";
  form.appendChild(input);
  form.appendChild(btn);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = input.value.trim();
    if (!content) return;
    input.value = "";
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildBody(content)),
    });
    await loadGamingJournal();
  });
  return form;
}

function buildSectionLabel(text) {
  const label = document.createElement("div");
  label.className = "journal-section-label";
  label.textContent = text;
  return label;
}

function renderGamingJournal(games) {
  gamingJournalList.innerHTML = "";

  if (games.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No games yet - approve a game for tracking (Game Awareness tab) or let the companion learn about one in conversation.";
    gamingJournalList.appendChild(hint);
    return;
  }

  for (const game of games) {
    const card = document.createElement("div");
    card.className = "journal-game";

    const header = document.createElement("div");
    header.className = "journal-game-header";
    const title = document.createElement("span");
    title.className = "journal-game-title";
    title.textContent = game.process;
    header.appendChild(title);
    if (game.tracked) {
      const badge = document.createElement("span");
      badge.className = "journal-badge";
      badge.textContent = "tracked";
      badge.title = "Approved for background OCR awareness";
      header.appendChild(badge);
    }
    card.appendChild(header);

    // Game-scope memories: hold across every playthrough of this game.
    card.appendChild(buildSectionLabel("Game memories (all playthroughs)"));
    const gameMemList = document.createElement("div");
    gameMemList.className = "memory-list";
    if (game.memories.length === 0) {
      const hint = document.createElement("div");
      hint.className = "memory-empty-hint";
      hint.textContent = "Nothing saved for this game yet.";
      gameMemList.appendChild(hint);
    }
    for (const entry of game.memories) {
      gameMemList.appendChild(buildMemoryItem(entry, loadGamingJournal));
    }
    card.appendChild(gameMemList);
    card.appendChild(buildJournalAddForm(
      "Add a game memory (true across playthroughs)…",
      (content) => ({ content, scope: "game", process: game.process })
    ));

    // Profiles (named sessions), each with its playthrough-scope memories + observations.
    for (const session of game.sessions) {
      const profile = document.createElement("div");
      profile.className = "journal-profile";

      const pHeader = document.createElement("div");
      pHeader.className = "journal-game-header";
      const pTitle = document.createElement("span");
      pTitle.className = "journal-profile-title";
      pTitle.textContent = `Profile: ${session.name}`;
      pHeader.appendChild(pTitle);
      if (session.active) {
        const badge = document.createElement("span");
        badge.className = "journal-badge journal-badge--active";
        badge.textContent = "active";
        badge.title = "The profile new playthrough facts currently go to";
        pHeader.appendChild(badge);
      }
      profile.appendChild(pHeader);

      const sessMemList = document.createElement("div");
      sessMemList.className = "memory-list";
      if (session.memories.length === 0) {
        const hint = document.createElement("div");
        hint.className = "memory-empty-hint";
        hint.textContent = "Nothing saved for this playthrough yet.";
        sessMemList.appendChild(hint);
      }
      for (const entry of session.memories) {
        sessMemList.appendChild(buildMemoryItem(entry, loadGamingJournal));
      }
      profile.appendChild(sessMemList);
      profile.appendChild(buildJournalAddForm(
        "Add a playthrough memory…",
        (content) => ({ content, scope: "session", process: game.process, session_id: session.session_id })
      ));

      if (session.observations.length > 0) {
        profile.appendChild(buildSectionLabel("Unconfirmed observations (auto-read from screen)"));
        const obsList = document.createElement("div");
        obsList.className = "memory-list";
        for (const obs of session.observations) {
          const item = document.createElement("div");
          item.className = "memory-item journal-observation";
          const text = document.createElement("div");
          text.className = "memory-item-text";
          text.textContent = obs.content;
          const deleteBtn = document.createElement("button");
          deleteBtn.className = "memory-item-delete";
          deleteBtn.textContent = "×";
          deleteBtn.title = "Discard observation";
          deleteBtn.addEventListener("click", async () => {
            const response = await fetch(`/api/observations/${obs.id}`, { method: "DELETE" });
            if (response.ok) loadGamingJournal();
          });
          item.appendChild(text);
          item.appendChild(deleteBtn);
          obsList.appendChild(item);
        }
        profile.appendChild(obsList);
      }

      card.appendChild(profile);
    }

    gamingJournalList.appendChild(card);
  }
}

// --- Reminders & Alarms modal ---
// Read-only management views: the agent creates/removes these itself via tool calls during
// conversation (add_reminder/remove_reminder, add_alarm/cancel_alarm) - this just lets the user
// audit and delete them directly, mirroring the Memory tab's list/delete pattern.

const reminderListEl = document.getElementById("reminder-list");
const alarmListEl = document.getElementById("alarm-list");

function renderScheduleList(container, entries, { subtitle, onDelete }) {
  container.innerHTML = "";
  if (entries.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "None set yet.";
    container.appendChild(hint);
    return;
  }
  for (const entry of entries) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("div");
    text.className = "memory-item-text";
    text.textContent = entry.message;

    const tag = document.createElement("div");
    tag.className = "memory-item-tag";
    tag.textContent = `${entry.process} — ${subtitle(entry)}`;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "memory-item-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Remove";
    deleteBtn.addEventListener("click", () => onDelete(entry));

    item.appendChild(text);
    item.appendChild(tag);
    item.appendChild(deleteBtn);
    container.appendChild(item);
  }
}

async function loadReminders() {
  const entries = await fetch("/api/reminders").then((r) => r.json());
  renderScheduleList(reminderListEl, entries, {
    subtitle: (e) => `every ${e.interval_minutes} min`,
    onDelete: async (entry) => {
      const response = await fetch(`/api/reminders/${entry.id}`, { method: "DELETE" });
      if (response.ok) await loadReminders();
    },
  });
}

async function loadAlarms() {
  const entries = await fetch("/api/reminders/alarms").then((r) => r.json());
  renderScheduleList(alarmListEl, entries, {
    subtitle: (e) => new Date(e.fire_at).toLocaleString(),
    onDelete: async (entry) => {
      const response = await fetch(`/api/reminders/alarms/${entry.id}`, { method: "DELETE" });
      if (response.ok) await loadAlarms();
    },
  });
}

// Fired reminders/alarms land in a small backend-side pending queue (generated by a background
// LLM pass, entirely outside the normal chat request/response cycle) - poll for them and, the
// moment one shows up, inject it into whichever chat is currently open as if the companion just
// spoke up unprompted, then ack it so it isn't shown twice.
async function checkPendingReminders() {
  let pending;
  try {
    pending = await fetch("/api/reminders/pending").then((r) => r.json());
  } catch (err) {
    return; // transient server hiccup - the next poll tick will catch up
  }
  if (pending.length === 0) return;

  // With lazy chat creation there's often no active chat (app idling on the empty state) -
  // a fired reminder must still be spoken and kept, not acked into the void, so open a chat
  // for it the same way sending a message would.
  if (!getActiveChat()) {
    exitEmptyState();
    createNewChat();
  }
  const chat = getActiveChat();
  const narrateEnabled = document.getElementById("cfg-narrate").checked;

  for (const item of pending) {
    appendMessage("assistant", item.text, null, true);
    addMessageToChat(chat, "assistant", item.text);
    if (narrateEnabled) enqueueNarration(item.text);
    await fetch(`/api/reminders/pending/${item.id}`, { method: "DELETE" }).catch(() => {});
  }
}

setInterval(checkPendingReminders, 20000);
checkPendingReminders();

// --- Game State floating panel ---
// Ephemeral, background-OCR-derived snapshot of what's happening in-game right now. Distinct
// from Memory: this never gets written to disk, it just reflects the current session. Shown
// automatically while a game is being tracked, hidden otherwise - no manual toggle button.

const GAME_STATE_POS_KEY = "lyko-game-state-panel-pos";
const GAME_STATE_CLOSED_KEY = "lyko-game-state-panel-closed";

let gameStatePanelClosed = localStorage.getItem(GAME_STATE_CLOSED_KEY) === "true";
let lastTrackedProcess = null;

// Session selector state - updated by updateGameStatePanel on every poll
let _sessionProcess = null;
let _sessionActiveId = null;
let _sessionListOpen = false;

function closeSessionList() {
  gameStateSessionList.hidden = true;
  gameStateSessionChevron.classList.remove("open");
  _sessionListOpen = false;
}

async function openSessionList(focusNewInput = false) {
  await _renderSessionList();
  gameStateSessionList.hidden = false;
  gameStateSessionChevron.classList.add("open");
  _sessionListOpen = true;
  if (focusNewInput) _showNewSessionInput();
}

async function _renderSessionList() {
  if (!_sessionProcess) return;
  const sessions = await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}`).then((r) => r.json());
  gameStateSessionList.innerHTML = "";
  for (const session of sessions) {
    gameStateSessionList.appendChild(_makeSessionItem(session));
  }
}

function _makeSessionItem(session) {
  const item = document.createElement("div");
  item.className = "gs-session-item" + (session.session_id === _sessionActiveId ? " active" : "");

  const nameSpan = document.createElement("span");
  nameSpan.className = "gs-session-item-name";
  nameSpan.textContent = session.name;
  item.appendChild(nameSpan);

  const renameBtn = document.createElement("button");
  renameBtn.type = "button";
  renameBtn.className = "gs-session-rename-btn";
  renameBtn.title = "Rename";
  renameBtn.innerHTML = `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M8.5 1.5l2 2L4 10H2v-2L8.5 1.5z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>`;
  item.appendChild(renameBtn);

  nameSpan.addEventListener("click", async () => {
    if (session.session_id === _sessionActiveId) { closeSessionList(); return; }
    await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}/${session.session_id}/active`, { method: "PUT" });
    const data = await fetchGameState();
    updateGameStatePanel(data);
    closeSessionList();
  });

  renameBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const input = document.createElement("input");
    input.type = "text";
    input.className = "gs-session-item-name-input";
    input.value = session.name;
    item.replaceChild(input, nameSpan);
    input.focus();
    input.select();

    const restore = () => { if (item.contains(input)) item.replaceChild(nameSpan, input); };
    const save = async () => {
      const newName = input.value.trim();
      if (newName && newName !== session.name) {
        const res = await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}/${session.session_id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName }),
        });
        if (res.ok) {
          session.name = newName;
          nameSpan.textContent = newName;
          if (session.session_id === _sessionActiveId) gameStateSessionNameEl.textContent = newName;
        }
      }
      restore();
    };
    input.addEventListener("blur", save);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.removeEventListener("blur", save); restore(); }
    });
  });

  return item;
}

function _showNewSessionInput() {
  if (gameStateSessionList.querySelector(".gs-session-new-row")) {
    gameStateSessionList.querySelector(".gs-session-new-input")?.focus();
    return;
  }
  const row = document.createElement("div");
  row.className = "gs-session-new-row";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "gs-session-new-input";
  input.placeholder = "Session name…";
  row.appendChild(input);

  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "gs-session-new-confirm";
  confirmBtn.textContent = "Create";
  row.appendChild(confirmBtn);

  const create = async () => {
    const name = input.value.trim();
    if (!name) return;
    const res = await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (res.ok) {
      const data = await fetchGameState();
      updateGameStatePanel(data);
      closeSessionList();
    }
  };
  confirmBtn.addEventListener("click", create);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); create(); }
    if (e.key === "Escape") row.remove();
  });

  gameStateSessionList.appendChild(row);
  input.focus();
}

gameStateSessionToggle.addEventListener("click", () => {
  if (_sessionListOpen) closeSessionList();
  else openSessionList();
});

gameStateNewSessionBtn.addEventListener("click", () => {
  if (_sessionListOpen) _showNewSessionInput();
  else openSessionList(true);
});

document.addEventListener("click", (e) => {
  if (_sessionListOpen && !gameStatePanel.contains(e.target)) closeSessionList();
});

function renderGameStateFields(data) {
  gameStateFields.innerHTML = "";
  const rows = [
    ["Process", data.process],
    ...data.trackers.map((t) => [t.label, t.value]),
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "game-state-row";

    const labelEl = document.createElement("span");
    labelEl.className = "game-state-row-label";
    labelEl.textContent = label;
    row.appendChild(labelEl);

    // Multi-part values (a "Known Stats"-style field gathering several distinct facts) read as
    // an unreadable wall of text on one line - split them into a small list, one part per line,
    // instead. A single short value (most fields, most of the time) stays as plain inline text.
    const parts = (value || "").split(/\s*;\s*/).filter(Boolean);
    if (parts.length > 1) {
      const list = document.createElement("ul");
      list.className = "game-state-row-value game-state-row-value-list";
      for (const part of parts) {
        const item = document.createElement("li");
        item.textContent = part;
        list.appendChild(item);
      }
      row.appendChild(list);
    } else {
      const valueEl = document.createElement("span");
      valueEl.className = "game-state-row-value";
      valueEl.textContent = value || "(not seen yet)";
      row.appendChild(valueEl);
    }

    gameStateFields.appendChild(row);
  }
}

function renderGameStateStats(data) {
  gameStateStats.innerHTML = "";
  const rows = [
    ["Extraction calls", data.extraction_call_count],
    ["Extraction cost", `$${data.extraction_cost_usd.toFixed(4)}`],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "game-state-stats-row";
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const valueEl = document.createElement("span");
    valueEl.textContent = value;
    row.appendChild(labelEl);
    row.appendChild(valueEl);
    gameStateStats.appendChild(row);
  }
}

function updateGameStatePanel(data) {
  gameStatePanelDot.classList.remove("active", "idle");

  if (data.tracking && data.process !== lastTrackedProcess) {
    // A new tracking session started (first game, or switched games) - re-show the panel even
    // if the user previously dismissed it for a prior session.
    gameStatePanelClosed = false;
    localStorage.setItem(GAME_STATE_CLOSED_KEY, "false");
  }
  lastTrackedProcess = data.tracking ? data.process : null;

  if (!data.tracking) {
    gameStatePanel.hidden = true;
    gameStateSessionBar.hidden = true;
    closeSessionList();
    return;
  }

  // Update session bar (don't re-render the open list to avoid disrupting in-progress renames)
  _sessionProcess = data.process;
  _sessionActiveId = data.session_id;
  gameStateSessionBar.hidden = false;
  gameStateSessionNameEl.textContent = data.session_name || "Default";

  gameStatePanelDot.classList.add("active");
  gameStatePanelDot.title = `Tracking: ${data.process}`;
  renderGameStateFields(data);
  renderGameStateStats(data);
  gameStatePanel.hidden = gameStatePanelClosed;
}

async function fetchGameState() {
  const response = await fetch("/api/game-state");
  return response.json();
}

gameStatePanelCloseBtn.addEventListener("click", () => {
  gameStatePanelClosed = true;
  localStorage.setItem(GAME_STATE_CLOSED_KEY, "true");
  gameStatePanel.hidden = true;
});

// Restore a dragged position, or fall back to the default top-right CSS anchor. A saved
// position is only applied when it's valid finite numbers, clamped into the current viewport -
// a corrupt entry (a click-without-drag used to persist {top: null, left: null}) or a window
// that shrank since the drag would otherwise strand the panel off-screen while "visible".
(() => {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(GAME_STATE_POS_KEY) || "null");
  } catch (err) { /* corrupt entry - fall through to the CSS anchor */ }
  if (saved && Number.isFinite(saved.top) && Number.isFinite(saved.left)) {
    const maxLeft = Math.max(0, window.innerWidth - 260);  // panel CSS width
    const maxTop = Math.max(0, window.innerHeight - 40);   // keep at least the header on-screen
    gameStatePanel.style.top = `${Math.min(Math.max(0, saved.top), maxTop)}px`;
    gameStatePanel.style.left = `${Math.min(Math.max(0, saved.left), maxLeft)}px`;
    gameStatePanel.style.right = "auto";
  } else if (saved !== null) {
    localStorage.removeItem(GAME_STATE_POS_KEY);
  }
})();

(() => {
  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;

  gameStatePanelHeader.addEventListener("mousedown", (event) => {
    if (event.target.closest(".floating-panel-close")) return;
    dragging = true;
    const rect = gameStatePanel.getBoundingClientRect();
    offsetX = event.clientX - rect.left;
    offsetY = event.clientY - rect.top;
    event.preventDefault();
  });

  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const maxLeft = window.innerWidth - gameStatePanel.offsetWidth;
    const maxTop = window.innerHeight - gameStatePanel.offsetHeight;
    const left = Math.min(Math.max(0, event.clientX - offsetX), maxLeft);
    const top = Math.min(Math.max(0, event.clientY - offsetY), maxTop);
    gameStatePanel.style.left = `${left}px`;
    gameStatePanel.style.top = `${top}px`;
    gameStatePanel.style.right = "auto";
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    const top = parseFloat(gameStatePanel.style.top);
    const left = parseFloat(gameStatePanel.style.left);
    // A click on the header without any movement never sets style.top/left - parseFloat("")
    // is NaN, which used to get persisted as {top: null, left: null} and strand the panel
    // off-screen on every subsequent load. Only persist an actual dragged position.
    if (Number.isFinite(top) && Number.isFinite(left)) {
      localStorage.setItem(GAME_STATE_POS_KEY, JSON.stringify({ top, left }));
    }
  });
})();

// Fast enough that the panel feels immediate when tracking starts/stops (the backend itself
// flips tracking via game_state.start_tracking() the moment it notices, via game_state_extraction.
// _capture_tick()) without hammering the endpoint - a slow fixed interval here would reintroduce
// the "state changed, but the panel doesn't show it yet" lag even though the backend is instant.
const GAME_STATE_PANEL_POLL_MS = 2000;

fetchGameState().then(updateGameStatePanel).catch(() => {});
setInterval(() => {
  fetchGameState().then(updateGameStatePanel).catch(() => {});
}, GAME_STATE_PANEL_POLL_MS);

// --- Game-state process blacklist / whitelist (Settings > General) ---

const gameStateBlacklistEl = document.getElementById("game-state-blacklist");
const gameStateBlacklistForm = document.getElementById("game-state-blacklist-form");
const gameStateBlacklistInput = document.getElementById("game-state-blacklist-input");
const gameStateWhitelistEl = document.getElementById("game-state-whitelist");

async function loadGameStateProcessLists() {
  const [blacklist, whitelist] = await Promise.all([
    fetch("/api/game-state/blacklist").then((r) => r.json()),
    fetch("/api/game-state/whitelist").then((r) => r.json()),
  ]);
  renderGameStateBlacklist(blacklist);
  renderGameStateWhitelist(whitelist);
  await populateTrackerProcessOptions(whitelist);
  await populateTrainingDataProcessOptions(whitelist);
}

function renderGameStateBlacklist(blacklist) {
  gameStateBlacklistEl.innerHTML = "";

  if (blacklist.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No blacklisted processes.";
    gameStateBlacklistEl.appendChild(hint);
    return;
  }

  for (const process of blacklist) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("div");
    text.className = "memory-item-text";
    text.textContent = process;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "memory-item-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Remove from blacklist";
    deleteBtn.addEventListener("click", async () => {
      await fetch(`/api/game-state/blacklist/${encodeURIComponent(process)}`, { method: "DELETE" });
      await loadGameStateProcessLists();
    });

    item.appendChild(text);
    item.appendChild(deleteBtn);
    gameStateBlacklistEl.appendChild(item);
  }
}

function renderGameStateWhitelist(whitelist) {
  gameStateWhitelistEl.innerHTML = "";

  if (whitelist.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No approved processes yet.";
    gameStateWhitelistEl.appendChild(hint);
    return;
  }

  for (const process of whitelist) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("div");
    text.className = "memory-item-text";
    text.textContent = process;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "memory-item-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Revoke approval";
    deleteBtn.addEventListener("click", async () => {
      await fetch(`/api/game-state/whitelist/${encodeURIComponent(process)}`, { method: "DELETE" });
      await loadGameStateProcessLists();
    });

    item.appendChild(text);
    item.appendChild(deleteBtn);
    gameStateWhitelistEl.appendChild(item);
  }
}

// --- Custom trackers (Settings > Game Awareness) ---
// Per-process, user-editable list of fields the Game-State Model fills in each poll window.
// "activity" (Current Activity) is locked server-side - always present, never editable/removable.

const gameStateTrackerProcessEl = document.getElementById("game-state-tracker-process");
const gameStateTrackersListEl = document.getElementById("game-state-trackers-list");
const gameStateTrackerAddForm = document.getElementById("game-state-tracker-add-form");
const gameStateTrackerAddLabel = document.getElementById("game-state-tracker-add-label");
const gameStateTrackerAddDesc = document.getElementById("game-state-tracker-add-desc");
const gameStateTrackerResetBtn = document.getElementById("game-state-tracker-reset");

let currentTrackers = [];

// The locked "activity" tracker's real description (sent to the model) is long and detailed by
// design - shown here instead since it's never editable anyway, so there's no risk of this
// display-only text drifting from what actually gets saved/sent.
const ACTIVITY_TRACKER_SHORT_DESC = "What's happening on screen right now - refreshed every check, never a sticky fact.";

async function populateTrackerProcessOptions(whitelist) {
  const previousValue = gameStateTrackerProcessEl.value;
  gameStateTrackerProcessEl.innerHTML = "";

  if (whitelist.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No approved processes yet";
    gameStateTrackerProcessEl.appendChild(option);
    gameStateTrackerProcessEl.disabled = true;
    currentTrackers = [];
    renderTrackersList();
    return;
  }

  gameStateTrackerProcessEl.disabled = false;
  for (const process of whitelist) {
    const option = document.createElement("option");
    option.value = process;
    option.textContent = process;
    gameStateTrackerProcessEl.appendChild(option);
  }
  if (whitelist.includes(previousValue)) {
    gameStateTrackerProcessEl.value = previousValue;
  }
  await loadTrackersForSelectedProcess();
}

async function loadTrackersForSelectedProcess() {
  const process = gameStateTrackerProcessEl.value;
  if (!process) {
    currentTrackers = [];
    renderTrackersList();
    return;
  }
  currentTrackers = await fetch(`/api/game-state/trackers/${encodeURIComponent(process)}`).then((r) => r.json());
  renderTrackersList();
}

async function saveTrackers() {
  const process = gameStateTrackerProcessEl.value;
  if (!process) return;
  const body = currentTrackers
    .filter((t) => !t.locked)
    .map((t) => ({ id: t.id, label: t.label, description: t.description }));
  currentTrackers = await fetch(`/api/game-state/trackers/${encodeURIComponent(process)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  renderTrackersList();
}

function renderTrackersList() {
  gameStateTrackersListEl.innerHTML = "";

  if (currentTrackers.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "Select an approved process to edit its trackers.";
    gameStateTrackersListEl.appendChild(hint);
    return;
  }

  for (const tracker of currentTrackers) {
    const item = document.createElement("div");
    item.className = "tracker-item";

    const header = document.createElement("div");
    header.className = "tracker-item-header";

    const label = document.createElement("div");
    label.className = "tracker-item-label";
    label.textContent = tracker.label;

    const desc = document.createElement("div");
    desc.className = "tracker-item-desc";
    desc.textContent = tracker.locked ? ACTIVITY_TRACKER_SHORT_DESC : tracker.description || "";

    if (tracker.locked) {
      label.title = "Always tracked - can't be edited or removed";
    } else {
      label.contentEditable = "true";
      desc.contentEditable = "true";
      desc.title = "What the model should look for";

      label.addEventListener("blur", () => {
        const text = label.textContent.trim();
        if (!text) {
          label.textContent = tracker.label;
          return;
        }
        if (text === tracker.label) return;
        tracker.label = text;
        saveTrackers();
      });

      desc.addEventListener("blur", () => {
        const text = desc.textContent.trim();
        if (text === tracker.description) return;
        tracker.description = text;
        saveTrackers();
      });

      for (const el of [label, desc]) {
        el.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            el.blur();
          }
        });
      }
    }

    header.appendChild(label);

    if (!tracker.locked) {
      const deleteBtn = document.createElement("button");
      deleteBtn.className = "memory-item-delete";
      deleteBtn.textContent = "×";
      deleteBtn.title = "Remove tracker";
      deleteBtn.addEventListener("click", () => {
        currentTrackers = currentTrackers.filter((t) => t.id !== tracker.id);
        saveTrackers();
      });
      header.appendChild(deleteBtn);
    }

    item.appendChild(header);
    item.appendChild(desc);

    gameStateTrackersListEl.appendChild(item);
  }
}

gameStateTrackerProcessEl.addEventListener("change", loadTrackersForSelectedProcess);

gameStateTrackerAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const label = gameStateTrackerAddLabel.value.trim();
  if (!label || !gameStateTrackerProcessEl.value) return;
  const description = gameStateTrackerAddDesc.value.trim();
  currentTrackers.push({ id: "", label, description, locked: false });
  gameStateTrackerAddLabel.value = "";
  gameStateTrackerAddDesc.value = "";
  await saveTrackers();
});

gameStateTrackerResetBtn.addEventListener("click", async () => {
  const process = gameStateTrackerProcessEl.value;
  if (!process) return;
  currentTrackers = await fetch(`/api/game-state/trackers/${encodeURIComponent(process)}/reset`, {
    method: "POST",
  }).then((r) => r.json());
  renderTrackersList();
});

// --- Training Data (Settings > Game Awareness > Training Data) ---
// A single living reference document per process, self-maintained by the game-state extraction
// pass (its training_data_update output) - editable directly, but not user-created here, since
// it's meant to reflect what the model actually learned about the game's UI.

const gameStateTrainingDataProcessEl = document.getElementById("game-state-training-data-process");
const gameStateTrainingDataContentEl = document.getElementById("game-state-training-data-content");

let currentTrainingDataContent = "";

async function populateTrainingDataProcessOptions(whitelist) {
  const previousValue = gameStateTrainingDataProcessEl.value;
  gameStateTrainingDataProcessEl.innerHTML = "";

  if (whitelist.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No approved processes yet";
    gameStateTrainingDataProcessEl.appendChild(option);
    gameStateTrainingDataProcessEl.disabled = true;
    gameStateTrainingDataContentEl.value = "";
    gameStateTrainingDataContentEl.disabled = true;
    return;
  }

  gameStateTrainingDataProcessEl.disabled = false;
  gameStateTrainingDataContentEl.disabled = false;
  for (const process of whitelist) {
    const option = document.createElement("option");
    option.value = process;
    option.textContent = process;
    gameStateTrainingDataProcessEl.appendChild(option);
  }
  if (whitelist.includes(previousValue)) {
    gameStateTrainingDataProcessEl.value = previousValue;
  }
  await loadTrainingDataForSelectedProcess();
}

async function loadTrainingDataForSelectedProcess() {
  const process = gameStateTrainingDataProcessEl.value;
  if (!process) {
    currentTrainingDataContent = "";
    gameStateTrainingDataContentEl.value = "";
    return;
  }
  const data = await fetch(`/api/game-state/training-data/${encodeURIComponent(process)}`).then((r) => r.json());
  currentTrainingDataContent = data.content;
  gameStateTrainingDataContentEl.value = currentTrainingDataContent;
}

gameStateTrainingDataProcessEl.addEventListener("change", loadTrainingDataForSelectedProcess);

gameStateTrainingDataContentEl.addEventListener("blur", async () => {
  const process = gameStateTrainingDataProcessEl.value;
  if (!process) return;
  const content = gameStateTrainingDataContentEl.value;
  if (content === currentTrainingDataContent) return;
  const data = await fetch(`/api/game-state/training-data/${encodeURIComponent(process)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  }).then((r) => r.json());
  currentTrainingDataContent = data.content;
});

gameStateBlacklistForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const process = gameStateBlacklistInput.value.trim();
  if (!process) return;
  gameStateBlacklistInput.value = "";
  await fetch("/api/game-state/blacklist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process }),
  });
  await loadGameStateProcessLists();
});

loadGameStateProcessLists();

// --- Pending process approvals ---
// Surfaced entirely via persistent toasts (bottom-right, stay until acted on, "+N more" when
// there are several) - see _toasts near the top of this file. No separate bell/badge/modal.

const _shownProcessToasts = new Set();

async function checkPendingApprovals() {
  let data;
  try {
    const response = await fetch("/api/game-state/pending");
    data = await response.json();
  } catch (err) {
    return; // transient server hiccup - the next poll tick will catch up
  }
  const processes = data.processes || [];

  for (const proc of processes) {
    if (_shownProcessToasts.has(proc)) continue;
    _shownProcessToasts.add(proc);
    showToast(`process-${proc}`, {
      title: "Process detected",
      body: `Allow <strong>${proc}</strong> to use game-state OCR?`,
      duration: 0,
      actions: [
        {
          label: "Allow",
          variant: "primary",
          onClick: async () => {
            await fetch("/api/game-state/whitelist", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ process: proc }),
            });
            _shownProcessToasts.delete(proc);
            await Promise.all([checkPendingApprovals(), loadGameStateProcessLists()]);
          },
        },
        {
          label: "Blacklist",
          variant: "danger",
          onClick: async () => {
            await fetch("/api/game-state/blacklist", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ process: proc }),
            });
            _shownProcessToasts.delete(proc);
            await Promise.all([checkPendingApprovals(), loadGameStateProcessLists()]);
          },
        },
      ],
    });
  }

  // Clean up resolved processes so their toast can reappear if they come back as pending
  for (const proc of _shownProcessToasts) {
    if (!processes.includes(proc)) _shownProcessToasts.delete(proc);
  }
}

let _pendingApprovalIntervalId = null;

function restartPendingApprovalPolling(intervalSeconds) {
  if (_pendingApprovalIntervalId) clearInterval(_pendingApprovalIntervalId);
  _pendingApprovalIntervalId = setInterval(checkPendingApprovals, intervalSeconds * 1000);
}

checkPendingApprovals();
// Interval started by loadConfig() on startup — see restartPendingApprovalPolling call there.

// --- Consumption modal ---
// Fetches the full per-call record list once per open/filter-change and aggregates client-side
// (totals + per-feature breakdown) for the selected time range - same "fetch whole list, filter
// in JS" pattern already used for the game-state blacklist/whitelist.

const USAGE_RANGES = [
  ["1h", "Last hour", 1 / 24],
  ["24h", "Last 24h", 1],
  ["7d", "Last 7d", 7],
  ["30d", "Last 30d", 30],
  ["all", "All time", null],
  ["custom", "Custom", null],
];

let usageRange = "all";
let usageRecordsCache = [];

const usageRangePillsEl = document.getElementById("usage-range-pills");
const usageCustomRangeEl = document.getElementById("usage-custom-range");
const usageRangeStartInput = document.getElementById("usage-range-start");
const usageRangeEndInput = document.getElementById("usage-range-end");
const usageBySourceEl = document.getElementById("usage-by-source");
const usageClearBtn = document.getElementById("usage-clear-btn");

function renderUsageRangePills() {
  usageRangePillsEl.innerHTML = "";
  for (const [value, label] of USAGE_RANGES) {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "memory-filter-pill" + (usageRange === value ? " active" : "");
    pill.textContent = label;
    pill.addEventListener("click", () => {
      usageRange = value;
      renderUsageRangePills();
      usageCustomRangeEl.hidden = usageRange !== "custom";
      renderUsageStats();
    });
    usageRangePillsEl.appendChild(pill);
  }
}

function filteredUsageRecords() {
  if (usageRange === "all") return usageRecordsCache;

  let startMs;
  let endMs = Infinity;
  if (usageRange === "custom") {
    startMs = usageRangeStartInput.value ? new Date(usageRangeStartInput.value).getTime() : -Infinity;
    endMs = usageRangeEndInput.value ? new Date(usageRangeEndInput.value).getTime() : Infinity;
  } else {
    const days = USAGE_RANGES.find(([value]) => value === usageRange)[2];
    startMs = Date.now() - days * 24 * 60 * 60 * 1000;
  }

  return usageRecordsCache.filter((r) => {
    const ts = new Date(r.timestamp).getTime();
    return ts >= startMs && ts <= endMs;
  });
}

function renderUsageStats() {
  const records = filteredUsageRecords();

  const totalPromptTokens = records.reduce((sum, r) => sum + r.prompt_tokens, 0);
  const totalCompletionTokens = records.reduce((sum, r) => sum + r.completion_tokens, 0);
  const totalCost = records.reduce((sum, r) => sum + r.cost_usd, 0);

  document.getElementById("usage-requests").textContent = records.length.toLocaleString();
  document.getElementById("usage-prompt-tokens").textContent = totalPromptTokens.toLocaleString();
  document.getElementById("usage-completion-tokens").textContent = totalCompletionTokens.toLocaleString();
  document.getElementById("usage-cost").textContent = `$${totalCost.toFixed(4)}`;

  const bySource = new Map();
  for (const r of records) {
    const entry = bySource.get(r.source) || { count: 0, promptTokens: 0, completionTokens: 0, cost: 0 };
    entry.count += 1;
    entry.promptTokens += r.prompt_tokens;
    entry.completionTokens += r.completion_tokens;
    entry.cost += r.cost_usd;
    bySource.set(r.source, entry);
  }

  usageBySourceEl.innerHTML = "";
  const sorted = [...bySource.entries()].sort((a, b) => b[1].cost - a[1].cost);
  if (sorted.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No requests in this range.";
    usageBySourceEl.appendChild(hint);
  }
  for (const [source, entry] of sorted) {
    const row = document.createElement("div");
    row.className = "usage-source-row";
    row.innerHTML = `
      <span class="usage-source-name">${source}</span>
      <span class="usage-source-detail">${entry.count} req</span>
      <span class="usage-source-detail">${(entry.promptTokens + entry.completionTokens).toLocaleString()} tok</span>
      <span class="usage-source-detail">$${entry.cost.toFixed(4)}</span>
    `;
    usageBySourceEl.appendChild(row);
  }
}

usageRangeStartInput.addEventListener("change", renderUsageStats);
usageRangeEndInput.addEventListener("change", renderUsageStats);

const usageBalanceListEl = document.getElementById("usage-balance-list");

const PROVIDER_LABELS = {
  openrouter: "OpenRouter",
  google_ai_studio: "Google AI Studio",
  custom: "Custom provider",
};

// Only OpenRouter exposes a real balance/credits API right now - Google AI Studio and custom
// endpoints are fetched too (in case that changes) but silently omitted whenever unavailable,
// same as today's single-provider behavior.
async function fetchAndRenderBalance() {
  usageBalanceListEl.innerHTML = "";
  const balances = await fetch("/api/usage/balance").then((r) => r.json());
  for (const data of balances) {
    const row = document.createElement("div");
    row.className = "usage-balance-row";

    const label = document.createElement("span");
    label.className = "usage-balance-label";
    label.textContent = `${PROVIDER_LABELS[data.provider] || data.provider} balance`;
    row.appendChild(label);

    const value = document.createElement("span");
    value.className = "usage-balance-value";
    if (data.limit_usd !== null && data.limit_usd !== undefined) {
      const remaining = data.remaining_usd ?? 0;
      value.textContent = `$${remaining.toFixed(4)} remaining of $${data.limit_usd.toFixed(2)} ($${data.spent_usd.toFixed(4)} spent)`;
    } else {
      const tierLabel = data.is_free_tier ? " (free tier)" : "";
      value.textContent = `$${data.spent_usd.toFixed(4)} spent${tierLabel} · no credit limit set`;
    }
    row.appendChild(value);

    usageBalanceListEl.appendChild(row);
  }
}

diagnosticsBtn.addEventListener("click", async () => {
  openModal(diagnosticsModal);
  renderUsageRangePills();
  usageCustomRangeEl.hidden = usageRange !== "custom";
  const [recordsRes] = await Promise.all([fetch("/api/usage/records"), fetchAndRenderBalance()]);
  usageRecordsCache = await recordsRes.json();
  renderUsageStats();
  const debugRes = await fetch("/api/debug/requests");
  renderDebugRequests(await debugRes.json());
});

let usageClearConfirming = false;
let usageClearConfirmTimeout = null;

usageClearBtn.addEventListener("click", async () => {
  if (!usageClearConfirming) {
    usageClearConfirming = true;
    usageClearBtn.textContent = "Click to confirm";
    usageClearBtn.classList.add("confirming");
    usageClearConfirmTimeout = setTimeout(() => {
      usageClearConfirming = false;
      usageClearBtn.textContent = "Clear Consumption Data";
      usageClearBtn.classList.remove("confirming");
    }, 4000);
    return;
  }

  clearTimeout(usageClearConfirmTimeout);
  usageClearConfirming = false;
  usageClearBtn.textContent = "Clear Consumption Data";
  usageClearBtn.classList.remove("confirming");

  await fetch("/api/usage", { method: "DELETE" });
  usageRecordsCache = [];
  renderUsageStats();
});

// --- Backup / restore (General > Backup tab) ---
// A plain <a download> for export (needs the token as a query param, since it isn't a fetch()
// call the auth patch above can intercept); a hidden file input + fetch for import.
const backupExportBtn = document.getElementById("backup-export-btn");
const backupImportBtn = document.getElementById("backup-import-btn");
const backupImportInput = document.getElementById("backup-import-input");

if (backupExportBtn) backupExportBtn.href = apiUrl("/api/backup/export");

backupImportBtn?.addEventListener("click", () => backupImportInput.click());

backupImportInput?.addEventListener("change", async () => {
  const file = backupImportInput.files[0];
  backupImportInput.value = "";
  if (!file) return;
  if (!confirm("Importing overwrites current chats, memories, and settings with the backup's contents. Continue?")) return;

  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/backup/import", { method: "POST", body: formData });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      alert(`Import failed: ${error.detail || "unknown error"}`);
      return;
    }
    alert("Backup imported. Restart Lykompanion for the restored settings to take effect.");
  } catch (err) {
    alert("Import failed: connection error.");
  }
});

// --- Debug tab (Usage & Debug modal) ---
// Last 50 individual LLM API calls and tool executions - persisted to data/debug_log.json when
// debug mode is enabled so history survives restarts. Lets the user inspect exactly what was
// sent/received for each request, including tool round-trips.

const debugRequestsListEl = document.getElementById("debug-requests-list");

function formatDebugMessage(message) {
  const block = document.createElement("div");
  block.className = "debug-message-block";

  const role = document.createElement("div");
  role.className = "debug-message-role";
  role.textContent = message.role + (message.tool_call_id ? ` (tool: ${message.tool_call_id})` : "");
  block.appendChild(role);

  const content = document.createElement("div");
  content.className = "debug-message-content";
  if (typeof message.content === "string") {
    content.textContent = message.content;
  } else if (Array.isArray(message.content)) {
    content.textContent = message.content
      .map((part) => (part.type === "text" ? part.text : `[${part.type}]`))
      .join("\n");
  } else if (message.tool_calls) {
    content.textContent = message.tool_calls
      .map((tc) => `→ ${tc.function?.name || tc.name}(${tc.function?.arguments || tc.arguments})`)
      .join("\n");
  } else {
    content.textContent = "(empty)";
  }
  block.appendChild(content);

  return block;
}

function openDebugDetail(entry) {
  debugDetailTitleEl.textContent = `${entry.source} · ${entry.model}`;
  debugDetailBodyEl.innerHTML = "";

  // ── Left pane: metadata + full prompt messages ───────────────────────
  const leftPane = document.createElement("div");
  leftPane.className = "debug-detail-pane";
  const leftLabel = document.createElement("div");
  leftLabel.className = "debug-detail-pane-label";
  leftLabel.textContent = "Prompt";
  leftPane.appendChild(leftLabel);

  const time = new Date(entry.timestamp).toLocaleString();
  const metaBlock = document.createElement("div");
  metaBlock.className = "debug-message-block";
  metaBlock.innerHTML = `<div class="debug-message-role">metadata</div><div class="debug-message-content"></div>`;
  metaBlock.querySelector(".debug-message-content").textContent =
    `${time}\n${entry.prompt_tokens}+${entry.completion_tokens} tok · $${entry.cost_usd.toFixed(4)} · ${entry.duration_ms ? Math.round(entry.duration_ms) + "ms" : "-"}`;
  leftPane.appendChild(metaBlock);

  for (const message of entry.messages) {
    leftPane.appendChild(formatDebugMessage(message));
  }

  // ── Right pane: tools available + LLM response ───────────────────────
  const rightPane = document.createElement("div");
  rightPane.className = "debug-detail-pane";
  const rightLabel = document.createElement("div");
  rightLabel.className = "debug-detail-pane-label";
  rightLabel.textContent = "Response";
  rightPane.appendChild(rightLabel);

  if (entry.tools && entry.tools.length > 0) {
    const toolsBlock = document.createElement("div");
    toolsBlock.className = "debug-message-block";
    toolsBlock.innerHTML = `<div class="debug-message-role">tools available</div><div class="debug-message-content"></div>`;
    toolsBlock.querySelector(".debug-message-content").textContent = entry.tools.join(", ");
    rightPane.appendChild(toolsBlock);
  }

  const replyBlock = document.createElement("div");
  replyBlock.className = "debug-message-block";
  const toolCallsText = entry.tool_calls
    ? entry.tool_calls.map((tc) => `→ ${tc.name}(${tc.arguments})`).join("\n")
    : "";
  replyBlock.innerHTML = `<div class="debug-message-role">result</div><div class="debug-message-content"></div>`;
  replyBlock.querySelector(".debug-message-content").textContent =
    [entry.reply, toolCallsText].filter(Boolean).join("\n") || "(empty)";
  rightPane.appendChild(replyBlock);

  debugDetailBodyEl.appendChild(leftPane);
  debugDetailBodyEl.appendChild(rightPane);

  openModal(debugDetailModal);
}

function renderDebugRequests(entries) {
  debugRequestsListEl.innerHTML = "";

  if (entries.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No requests recorded. Enable debug capture and make a request.";
    debugRequestsListEl.appendChild(hint);
    return;
  }

  for (const entry of entries) {
    const card = document.createElement("div");
    card.className = "debug-request-card";

    const summary = document.createElement("div");
    summary.className = "debug-request-summary";
    const time = new Date(entry.timestamp).toLocaleTimeString();
    summary.innerHTML = `
      <span class="debug-request-source">${entry.source}</span>
      <span class="debug-request-meta">${entry.model} · ${time} · ${entry.prompt_tokens}+${entry.completion_tokens} tok
      · $${entry.cost_usd.toFixed(4)} · ${entry.duration_ms ? Math.round(entry.duration_ms) + "ms" : "-"}</span>
      <span>▾</span>
    `;

    summary.addEventListener("click", () => openDebugDetail(entry));

    card.appendChild(summary);
    debugRequestsListEl.appendChild(card);
  }
}


// Release the Speech Recognition DLL before pywebview cleans up its temp profile folder,
// otherwise Windows locks the file and pywebview logs a WinError 5 access-denied warning.
window.addEventListener("beforeunload", () => {
  stopWakeWordRecognition();
});

// Block the browser's right-click context menu (hides Inspect, View Source, Save As, etc.).
// We use JS rather than AreDefaultContextMenusEnabled=False in Python because that flag also
// kills the <audio> player's 3-dot menu — contextmenu events are only right-click, not button clicks.
document.addEventListener("contextmenu", (e) => e.preventDefault());

// --- Setup banner + Quick Setup wizard ---
// The wizard walks through the minimum viable configuration (name → provider/key → model →
// voice), page by page. It auto-opens on first run on a machine (no "done" flag in this
// webview profile AND no API key configured for the active provider), and can always be
// re-launched from Settings → "Quick Setup…" or the chat banner.

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

async function init() {
  await refreshAvatarStatus();
  await loadChatsFromStorage();
  const firstWithMessages = chats.find((c) => c.messages.length > 0);
  if (firstWithMessages) {
    switchChat(firstWithMessages.id);
  } else {
    activeChatId = null;
    renderChatList();
    renderChatLog();
  }
  const cfg = await loadConfig();
  // First run on this machine with nothing configured - walk through setup automatically.
  if (cfg && !localStorage.getItem(QS_DONE_KEY) && providerKeyMissing(cfg)) {
    openQuickSetup();
  }
}

init();
