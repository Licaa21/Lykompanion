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
const instructionsBtn = document.getElementById("instructions-btn");
const memoryBtn = document.getElementById("memory-btn");
const usageBtn = document.getElementById("usage-btn");
const settingsBtn = document.getElementById("settings-btn");
const gameStateBtn = document.getElementById("game-state-btn");
const gameStateDot = document.getElementById("game-state-dot");
const gameStateFields = document.getElementById("game-state-fields");
const settingsModal = document.getElementById("settings-modal");
const instructionsModal = document.getElementById("instructions-modal");
const memoryModal = document.getElementById("memory-modal");
const usageModal = document.getElementById("usage-modal");
const gameStateModal = document.getElementById("game-state-modal");
const debugModal = document.getElementById("debug-modal");
const notifModal = document.getElementById("notif-modal");

let narrationSpeed = 1.0;
let narrationVolume = 1.0;
let includeScreenshot = false;

// User's uploaded profile picture (shown in chat in place of the "Y" initial). Cache-busted
// with a version stamp each time it's changed, since the URL itself never changes.
let hasUserAvatar = false;
let avatarVersion = Date.now();

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

function saveChatsToStorage() {
  fetch("/api/chats", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chats }),
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
  saveChatsToStorage();
  renderChatList();
  renderChatLog();
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
  saveChatsToStorage();
  if (activeChatId === id) {
    if (chats.length > 0) {
      switchChat(chats[0].id);
    } else {
      createNewChat();
    }
  } else {
    renderChatList();
  }
}

function addMessageToChat(chat, role, content, audioId) {
  chat.messages.push({ role, content, audioId: audioId || undefined });
  if (chat.title === "New Chat" && role === "user") {
    chat.title = content.slice(0, 40) || "New Chat";
    renderChatList();
  }
  saveChatsToStorage();
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
      saveChatsToStorage();
      renderChatList();
    }
  } catch (err) {
    // network/LLM failure: keep the fallback title, nothing to recover here
  }
}

function renderChatList() {
  chatListEl.innerHTML = "";
  for (const chat of chats) {
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

function renderChatLog() {
  chatLog.innerHTML = "";
  const chat = getActiveChat();
  if (!chat) return;
  for (const message of chat.messages) {
    appendMessage(message.role, message.content, message.audioId);
  }
}

newChatBtn.addEventListener("click", createNewChat);

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

// The companion may embed markdown images/links (see renderMessageMarkup) - narration should
// speak the alt/link text, not read raw "bracket bracket parenthesis http" syntax aloud.
function stripMarkdownForNarration(text) {
  return text
    .replace(/!\[([^\]]*)\]\(https?:\/\/[^\s)]+\)/g, (_m, alt) => alt || "an image")
    .replace(/\[([^\]]+)\]\(https?:\/\/[^\s)]+\)/g, (_m, label) => label)
    .replace(/https?:\/\/[^\s<>"']+/g, "");
}

function enqueueNarration(text) {
  if (!text || !text.trim()) return Promise.resolve();
  const promise = synthesizeSentence(stripMarkdownForNarration(text));
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
  try {
    const blob = await item.promise;
    if (ttsPlaying) {
      narrationAudio.src = URL.createObjectURL(blob);
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

function renderMessageMarkup(text) {
  let html = escapeHtml(text);

  // Images: ![alt](https://...)
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

function appendMessage(role, content, audioId, isNew = false) {
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
  avatar.innerHTML =
    role === "assistant"
      ? `<img src="img/logo.png" alt="" />`
      : hasUserAvatar
      ? `<img src="/api/profile/avatar?v=${avatarVersion}" alt="" />`
      : "Y";
  meta.appendChild(avatar);

  const roleName = document.createElement("span");
  roleName.className = "msg-role-name";
  roleName.textContent = role === "user" ? "You" : "Lykompanion";
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
      memBtn.addEventListener("click", () => {
        document.getElementById("memory-add-input").value = contentDiv.textContent.trim().slice(0, 300);
        openModal(document.getElementById("memory-modal"));
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
  // For voice messages the player IS the bubble; hide the text placeholder
  if (audioId) contentDiv.hidden = true;
  el.appendChild(contentDiv);

  // ── Voice player — styled as the message bubble ───────────
  if (audioId) {
    const audio = new Audio(`/api/voice/${audioId}`);

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
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  return contentDiv;
}

// Splits a growing text buffer into complete sentences plus a leftover
// remainder (incomplete sentence still being streamed in).
function extractCompleteSentences(buffer) {
  const complete = [];
  const regex = /[^.!?\n]*[.!?\n]+/g;
  let match;
  let lastIndex = 0;
  while ((match = regex.exec(buffer)) !== null) {
    const sentence = match[0].trim();
    if (sentence) complete.push(sentence);
    lastIndex = regex.lastIndex;
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
  const chat = getActiveChat();
  if (!chat) return;

  stopNarration();
  appendMessage("user", text, null, true);
  addMessageToChat(chat, "user", text);

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
      assistantEl.innerHTML = renderMessageMarkup(`⚠️ ${error.detail || "Chat request failed."}`);
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
        if (payload.done) continue;

        fullReply += payload.delta;
        assistantEl.innerHTML = renderMessageMarkup(fullReply);
        chatLog.scrollTop = chatLog.scrollHeight;

        if (narrateEnabled) {
          sentenceBuffer += payload.delta;
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
    maybeGenerateTitle(chat, text, fullReply);
  } catch (err) {
    if (err.name !== "AbortError") {
      assistantEl.innerHTML = renderMessageMarkup(fullReply || "⚠️ Chat request failed.");
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
  const chat = getActiveChat();
  if (!chat) return;

  const audioId = (crypto.randomUUID && crypto.randomUUID()) || `voice-${Date.now()}`;
  saveVoiceBlob(audioId, wavBlob);

  stopNarration();
  awaitingReply = true;
  try {
    appendMessage("user", "🎤 (voice message)", audioId, true);
    setVoiceStatus("Sending voice message...");

    const formData = new FormData();
    formData.append("audio", wavBlob, "voice.wav");
    formData.append("history", JSON.stringify(chat.messages));
    formData.append("include_screenshot", String(includeScreenshot));

    const response = await fetch("/api/chat/voice", { method: "POST", body: formData });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      appendMessage("assistant", `⚠️ ${error.detail || "Voice chat failed."}`, null, true);
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
      return;
    }

    const data = await response.json();
    appendMessage("assistant", data.reply, null, true);
    addMessageToChat(chat, "user", "🎤 (voice message)", audioId);
    addMessageToChat(chat, "assistant", data.reply);
    maybeGenerateTitle(chat, "(voice message)", data.reply);
    applyNarrationVolume(data.narration_volume);

    awaitingReply = false;
    if (data.stop_listening) {
      agentStopListening();
    } else {
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    }
    if (document.getElementById("cfg-narrate").checked) {
      await narrate(data.reply);
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
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, "")
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

[settingsModal, instructionsModal, memoryModal, usageModal, gameStateModal, debugModal, notifModal].forEach((modal) => {
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal(modal);
  });
});

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    document.querySelectorAll(".tab-panel").forEach((panel) => {
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
  kokoroVoice: [],
  openrouterTts: [],
  voiceInput: [],
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
setupModelSearch("cfg-memory-model-search", "cfg-memory-model", "llm", {
  value: "",
  label: "(use main chat model)",
});
setupModelSearch("cfg-game-state-model-search", "cfg-game-state-model", "llm", {
  value: "",
  label: "(use main chat model)",
});
setupModelSearch("cfg-kokoro-voice-search", "cfg-kokoro-voice", "kokoroVoice");
setupModelSearch("cfg-chirp3-voice-search", "cfg-chirp3-voice", "chirp3Voice");
setupModelSearch("cfg-openrouter-tts-model-search", "cfg-openrouter-tts-model", "openrouterTts");
setupModelSearch("cfg-openrouter-voice-model-search", "cfg-openrouter-voice-model", "voiceInput", {
  value: "",
  label: "(use main chat model)",
});

async function loadModels(
  selectedLlm,
  selectedKokoroVoice,
  selectedOpenrouterTts,
  selectedVoiceInputModel,
  selectedOpenrouterVoice,
  selectedMemoryModel,
  selectedChirp3Voice,
  selectedGameStateModel
) {
  const [llmModels, ttsModels, voiceInputModels] = await Promise.all([
    fetch("/api/models/llm").then((r) => r.json()),
    fetch("/api/models/tts").then((r) => r.json()),
    fetch("/api/models/voice-input").then((r) => r.json()),
  ]);

  const speechModels = ttsModels.openrouter_speech_models || [];
  openrouterSpeechModelsById = {};
  for (const m of speechModels) {
    openrouterSpeechModelsById[m.id] = m;
  }

  modelOptionsCache.llm = sortByLabel(llmModels.map((m) => ({ value: m.id, label: m.name })));
  modelOptionsCache.kokoroVoice = sortByLabel(
    (ttsModels.kokoro_voices || []).map((v) => ({ value: v.id, label: v.name }))
  );
  modelOptionsCache.openrouterTts = sortByLabel(speechModels.map((m) => ({ value: m.id, label: m.name })));
  modelOptionsCache.voiceInput = sortByLabel(voiceInputModels.map((m) => ({ value: m.id, label: m.name })));
  modelOptionsCache.chirp3Voice = sortByLabel((ttsModels.chirp3_voices || []).map((v) => ({ value: v.id, label: v.name })));

  populateSelect(document.getElementById("cfg-model"), modelOptionsCache.llm, selectedLlm);
  populateSelect(
    document.getElementById("cfg-memory-model"),
    [{ value: "", label: "(use main chat model)" }, ...modelOptionsCache.llm],
    selectedMemoryModel
  );
  populateSelect(
    document.getElementById("cfg-game-state-model"),
    [{ value: "", label: "(use main chat model)" }, ...modelOptionsCache.llm],
    selectedGameStateModel
  );
  populateSelect(document.getElementById("cfg-kokoro-voice"), modelOptionsCache.kokoroVoice, selectedKokoroVoice);
  populateSelect(document.getElementById("cfg-chirp3-voice"), modelOptionsCache.chirp3Voice, selectedChirp3Voice);
  populateSelect(
    document.getElementById("cfg-openrouter-tts-model"),
    modelOptionsCache.openrouterTts,
    selectedOpenrouterTts
  );
  populateSelect(
    document.getElementById("cfg-openrouter-voice-model"),
    [{ value: "", label: "(use main chat model)" }, ...modelOptionsCache.voiceInput],
    selectedVoiceInputModel
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

const avatarPreviewEl = document.getElementById("cfg-avatar-preview");
const avatarInputEl = document.getElementById("cfg-avatar-input");
const avatarUploadBtn = document.getElementById("cfg-avatar-upload-btn");
const avatarRemoveBtn = document.getElementById("cfg-avatar-remove-btn");

function renderAvatarPreview() {
  avatarPreviewEl.innerHTML = hasUserAvatar ? `<img src="/api/profile/avatar?v=${avatarVersion}" alt="" />` : "Y";
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
});

avatarRemoveBtn.addEventListener("click", async () => {
  await fetch("/api/profile/avatar", { method: "DELETE" });
  hasUserAvatar = false;
  renderAvatarPreview();
});

const gameStateEnabledInput = document.getElementById("cfg-game-state-enabled");
const gameStateDependentEl = document.getElementById("game-state-dependent");

function updateGameStateDependentVisibility() {
  gameStateDependentEl.hidden = !gameStateEnabledInput.checked;
}

gameStateEnabledInput.addEventListener("change", updateGameStateDependentVisibility);

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

async function loadConfig() {
  const response = await fetch("/api/config");
  const cfg = await response.json();
  renderAvatarPreview();
  document.getElementById("cfg-tts-provider").value = cfg.tts_provider;
  updateTtsProviderVisibility();
  document.getElementById("cfg-api-key").placeholder = cfg.openrouter_api_key_set
    ? "•••••••• (set)"
    : "Not set";
  document.getElementById("cfg-management-key").placeholder = cfg.openrouter_management_key_set
    ? "•••••••• (set)"
    : "Not set";

  narrationSpeed = cfg.narration_speed;
  narrationSpeedInput.value = cfg.narration_speed;
  narrationSpeedValue.textContent = cfg.narration_speed;

  applyNarrationVolume(cfg.narration_volume);

  contextWindowInput.value = cfg.context_window_messages;
  contextWindowValue.textContent = cfg.context_window_messages === 0 ? "all" : cfg.context_window_messages;

  screenshotWidthInput.value = cfg.screenshot_max_width;
  screenshotWidthValue.textContent = cfg.screenshot_max_width;
  screenshotQualityInput.value = cfg.screenshot_jpeg_quality;
  screenshotQualityValue.textContent = cfg.screenshot_jpeg_quality;

  gameStateEnabledInput.checked = cfg.game_state_ocr_enabled;
  updateGameStateDependentVisibility();
  document.getElementById("cfg-tesseract-cmd").value = cfg.tesseract_cmd || "";
  document.getElementById("cfg-openrouter-base-url").value = cfg.openrouter_base_url || "";
  document.getElementById("cfg-kokoro-base-url").value = cfg.kokoro_base_url || "";
  gameStateIntervalInput.value = cfg.game_state_poll_interval_seconds;
  gameStateIntervalValue.textContent = cfg.game_state_poll_interval_seconds;
  restartPendingApprovalPolling(cfg.game_state_poll_interval_seconds);

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

  await loadModels(
    cfg.openrouter_model,
    cfg.kokoro_voice,
    cfg.openrouter_tts_model,
    cfg.openrouter_voice_model,
    cfg.openrouter_voice,
    cfg.memory_extraction_model,
    cfg.google_tts_voice,
    cfg.game_state_model
  );
}

document.getElementById("cfg-refresh-models").addEventListener("click", () => {
  loadModels(
    document.getElementById("cfg-model").value,
    document.getElementById("cfg-kokoro-voice").value,
    document.getElementById("cfg-openrouter-tts-model").value,
    document.getElementById("cfg-openrouter-voice-model").value,
    document.getElementById("cfg-openrouter-voice").value,
    document.getElementById("cfg-memory-model").value,
    document.getElementById("cfg-chirp3-voice").value,
    document.getElementById("cfg-game-state-model").value
  );
});

document.getElementById("cfg-save").addEventListener("click", async () => {
  const apiKeyInput = document.getElementById("cfg-api-key");
  const igdbSecretInput = document.getElementById("cfg-igdb-client-secret");
  const steamApiKeyInput = document.getElementById("cfg-steam-api-key");
  const body = {
    openrouter_model: document.getElementById("cfg-model").value,
    openrouter_base_url: document.getElementById("cfg-openrouter-base-url").value.trim() || "https://openrouter.ai/api/v1",
    memory_extraction_model: document.getElementById("cfg-memory-model").value,
    tts_provider: document.getElementById("cfg-tts-provider").value,
    google_tts_api_key: document.getElementById("cfg-google-tts-api-key").value || null,
    google_tts_voice: document.getElementById("cfg-chirp3-voice").value,
    kokoro_base_url: document.getElementById("cfg-kokoro-base-url").value.trim() || "http://localhost:8880/v1",
    kokoro_voice: document.getElementById("cfg-kokoro-voice").value,
    openrouter_tts_model: document.getElementById("cfg-openrouter-tts-model").value,
    openrouter_voice: document.getElementById("cfg-openrouter-voice").value,
    openrouter_api_key: apiKeyInput.value || null,
    openrouter_management_key: document.getElementById("cfg-management-key").value || null,
    narration_speed: parseFloat(narrationSpeedInput.value),
    narration_volume: parseInt(narrationVolumeInput.value, 10) / 100,
    openrouter_voice_model: document.getElementById("cfg-openrouter-voice-model").value,
    context_window_messages: parseInt(contextWindowInput.value, 10),
    screenshot_max_width: parseInt(screenshotWidthInput.value, 10),
    screenshot_jpeg_quality: parseInt(screenshotQualityInput.value, 10),
    igdb_client_id: document.getElementById("cfg-igdb-client-id").value,
    igdb_client_secret: igdbSecretInput.value || null,
    steam_api_key: steamApiKeyInput.value || null,
    steam_id: document.getElementById("cfg-steam-id").value,
    game_state_ocr_enabled: gameStateEnabledInput.checked,
    game_state_poll_interval_seconds: parseInt(gameStateIntervalInput.value, 10),
    game_state_model: document.getElementById("cfg-game-state-model").value,
    tesseract_cmd: document.getElementById("cfg-tesseract-cmd").value,
    wake_word_enabled: wakeWordEnabledInput.checked,
    wake_word_phrase: wakeWordPhraseInput.value.trim() || "Hey Buddy",
    wake_word_max_failures: wakeWordMaxFailures,
    vad_threshold: vadThreshold,
    vad_silence_ms: vadSilenceMs,
    vad_min_speech_ms: vadMinSpeechMs,
    pre_roll_ms: preRollMs,
    post_roll_ms: postRollMs,
  };
  await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  apiKeyInput.value = "";
  document.getElementById("cfg-management-key").value = "";
  igdbSecretInput.value = "";
  steamApiKeyInput.value = "";
  await loadConfig();
});

// --- Personal Instructions modal ---

instructionsBtn.addEventListener("click", async () => {
  const response = await fetch("/api/instructions");
  const data = await response.json();
  document.getElementById("instructions-text").value = data.instructions;
  openModal(instructionsModal);
});

document.getElementById("instructions-save").addEventListener("click", async () => {
  const text = document.getElementById("instructions-text").value;
  await fetch("/api/instructions", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instructions: text }),
  });
  closeModal(instructionsModal);
});

// --- Memory modal ---
// The agent can save/remove facts itself via tool calls during conversation;
// this view lets the user audit and correct that memory directly. Facts can optionally be
// tagged to a process (e.g. "bg3.exe") so they only get shown to the companion while that
// game is active - general facts (no tag) always show.

const memoryList = document.getElementById("memory-list");
const memoryFiltersEl = document.getElementById("memory-filters");
const memoryAddForm = document.getElementById("memory-add-form");
const memoryAddInput = document.getElementById("memory-add-input");
const memoryAddProcess = document.getElementById("memory-add-process");
const memoryAddProcessCustom = document.getElementById("memory-add-process-custom");

let allMemories = [];
let memoryFilter = "all"; // "all" | "general" | a specific process name

memoryAddProcess.addEventListener("change", () => {
  memoryAddProcessCustom.hidden = memoryAddProcess.value !== "__other__";
});

async function populateMemoryProcessOptions() {
  const whitelist = await fetch("/api/game-state/whitelist").then((r) => r.json());
  const previousValue = memoryAddProcess.value;
  memoryAddProcess.innerHTML = '<option value="">General</option>';
  for (const process of whitelist) {
    const option = document.createElement("option");
    option.value = process;
    option.textContent = process;
    memoryAddProcess.appendChild(option);
  }
  const otherOption = document.createElement("option");
  otherOption.value = "__other__";
  otherOption.textContent = "Other...";
  memoryAddProcess.appendChild(otherOption);
  if ([...memoryAddProcess.options].some((o) => o.value === previousValue)) {
    memoryAddProcess.value = previousValue;
  }
}

function renderMemoryFilters() {
  memoryFiltersEl.innerHTML = "";

  const processes = [...new Set(allMemories.map((m) => m.process).filter(Boolean))].sort();
  const filters = [
    ["all", "All"],
    ["general", "General"],
    ...processes.map((p) => [p, p]),
  ];

  for (const [value, label] of filters) {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "memory-filter-pill" + (memoryFilter === value ? " active" : "");
    pill.textContent = label;
    pill.addEventListener("click", () => {
      memoryFilter = value;
      renderMemoryFilters();
      renderMemoryList();
    });
    memoryFiltersEl.appendChild(pill);
  }
}

function renderMemoryList() {
  memoryList.innerHTML = "";

  const visible = allMemories.filter((m) => {
    if (memoryFilter === "all") return true;
    if (memoryFilter === "general") return !m.process;
    return m.process === memoryFilter;
  });

  if (visible.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No memories here yet.";
    memoryList.appendChild(hint);
    return;
  }

  for (const entry of visible) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("div");
    text.className = "memory-item-text";
    text.contentEditable = "true";
    text.textContent = entry.content;

    const tag = document.createElement("div");
    tag.className = "memory-item-tag";
    tag.contentEditable = "true";
    tag.textContent = entry.process || "General";
    tag.title = "Edit to tag this fact to a process, or clear/type General for an always-shown fact";

    const saveEdits = async () => {
      const content = text.textContent.trim();
      const tagText = tag.textContent.trim();
      const process = !tagText || tagText.toLowerCase() === "general" ? null : tagText;
      if (!content) {
        text.textContent = entry.content;
        return;
      }
      if (content === entry.content && process === entry.process) {
        tag.textContent = entry.process || "General";
        return;
      }
      const response = await fetch(`/api/memory/${entry.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, process }),
      });
      if (response.ok) {
        entry.content = content;
        entry.process = process;
        renderMemoryFilters();
      } else {
        text.textContent = entry.content;
        tag.textContent = entry.process || "General";
      }
    };

    text.addEventListener("blur", saveEdits);
    tag.addEventListener("blur", saveEdits);
    for (const el of [text, tag]) {
      el.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          el.blur();
        }
      });
    }

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "memory-item-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Remove memory";
    deleteBtn.addEventListener("click", async () => {
      const response = await fetch(`/api/memory/${entry.id}`, { method: "DELETE" });
      if (response.ok) {
        allMemories = allMemories.filter((m) => m.id !== entry.id);
        renderMemoryFilters();
        renderMemoryList();
      }
    });

    item.appendChild(text);
    item.appendChild(tag);
    item.appendChild(deleteBtn);
    memoryList.appendChild(item);
  }
}

async function loadMemories() {
  const response = await fetch("/api/memory");
  allMemories = await response.json();
  renderMemoryFilters();
  renderMemoryList();
}

memoryBtn.addEventListener("click", async () => {
  openModal(memoryModal);
  await populateMemoryProcessOptions();
  await loadMemories();
});

memoryAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = memoryAddInput.value.trim();
  if (!content) return;
  let process = memoryAddProcess.value;
  if (process === "__other__") {
    process = memoryAddProcessCustom.value.trim();
  }
  memoryAddInput.value = "";
  memoryAddProcessCustom.value = "";
  await fetch("/api/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, process: process || null }),
  });
  await loadMemories();
});

// --- Consumption modal ---

// --- Game State modal ---
// Ephemeral, background-OCR-derived snapshot of what's happening in-game right now. Distinct
// from Memory: this never gets written to disk, it just reflects the current session.

function updateGameStateDot(data) {
  gameStateDot.classList.remove("active", "idle");
  if (!data.enabled) {
    gameStateDot.title = "Game-state awareness disabled (enable in Settings > Behavior)";
  } else if (data.tracking) {
    gameStateDot.classList.add("active");
    gameStateDot.title = `Tracking: ${data.process}`;
  } else {
    gameStateDot.classList.add("idle");
    gameStateDot.title = "Enabled, not currently tracking a game";
  }
}

function renderGameStateFields(data) {
  gameStateFields.innerHTML = "";

  if (!data.enabled) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "OCR awareness is disabled. Enable it in Settings > Behavior to turn this on.";
    gameStateFields.appendChild(hint);
    return;
  }

  if (!data.tracking) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "Enabled, but not currently tracking anything — focus a game window and wait for the next poll.";
    gameStateFields.appendChild(hint);
    return;
  }

  const rows = [
    ["Process", data.process],
    ["Currently", data.activity],
    ["Location", data.location],
    ["Quest", data.quest],
    ["Character", data.character],
    ["Recent choice", data.notable_choice],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "game-state-row";
    const labelEl = document.createElement("span");
    labelEl.className = "game-state-row-label";
    labelEl.textContent = label;
    const valueEl = document.createElement("span");
    valueEl.className = "game-state-row-value";
    valueEl.textContent = value || "(not seen yet)";
    row.appendChild(labelEl);
    row.appendChild(valueEl);
    gameStateFields.appendChild(row);
  }
}

async function fetchGameState() {
  const response = await fetch("/api/game-state");
  return response.json();
}

gameStateBtn.addEventListener("click", async () => {
  openModal(gameStateModal);
  const data = await fetchGameState();
  updateGameStateDot(data);
  renderGameStateFields(data);
});

fetchGameState().then(updateGameStateDot);
setInterval(() => {
  fetchGameState().then(updateGameStateDot);
}, 20000);

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

settingsBtn.addEventListener("click", loadGameStateProcessLists);
loadGameStateProcessLists();

// --- Notification bell ---
// Extensible notification tray in the sidebar. Currently used for game-state process approvals.

const notifBellBtn = document.getElementById("notif-bell-btn");
const notifBadge = document.getElementById("notif-badge");
const notifList = document.getElementById("notif-list");
const notifEmpty = document.getElementById("notif-empty");

notifBellBtn.addEventListener("click", () => openModal(notifModal));

function renderNotifications(pendingProcesses) {
  notifList.innerHTML = "";
  const count = pendingProcesses.length;

  notifBadge.hidden = count === 0;
  notifBadge.textContent = count;

  if (count === 0) {
    notifEmpty.hidden = false;
    return;
  }
  notifEmpty.hidden = true;

  for (const process of pendingProcesses) {
    const item = document.createElement("div");
    item.className = "notif-item";

    const text = document.createElement("div");
    text.className = "notif-item-text";
    text.innerHTML = `<strong>${process}</strong><span class="notif-item-sub">Allow game-state OCR tracking?</span>`;

    const actions = document.createElement("div");
    actions.className = "notif-item-actions";

    const allowBtn = document.createElement("button");
    allowBtn.className = "secondary-btn notif-action-btn";
    allowBtn.textContent = "Allow";
    allowBtn.addEventListener("click", async () => {
      await fetch("/api/game-state/whitelist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ process }),
      });
      await Promise.all([checkPendingApprovals(), loadGameStateProcessLists()]);
    });

    const blacklistBtn = document.createElement("button");
    blacklistBtn.className = "secondary-btn notif-action-btn";
    blacklistBtn.textContent = "Blacklist";
    blacklistBtn.addEventListener("click", async () => {
      await fetch("/api/game-state/blacklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ process }),
      });
      await Promise.all([checkPendingApprovals(), loadGameStateProcessLists()]);
    });

    actions.appendChild(allowBtn);
    actions.appendChild(blacklistBtn);
    item.appendChild(text);
    item.appendChild(actions);
    notifList.appendChild(item);
  }
}

const _shownProcessToasts = new Set();

async function checkPendingApprovals() {
  const response = await fetch("/api/game-state/pending");
  const data = await response.json();
  const processes = data.processes || [];
  renderNotifications(processes);

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

const usageBalanceRow = document.getElementById("usage-balance-row");
const usageBalanceValue = document.getElementById("usage-balance-value");

async function fetchAndRenderBalance() {
  usageBalanceRow.hidden = true;
  const data = await fetch("/api/usage/balance").then((r) => r.json());
  if (!data.available) return;
  usageBalanceRow.hidden = false;
  if (data.limit_usd !== null && data.limit_usd !== undefined) {
    const remaining = data.remaining_usd ?? 0;
    usageBalanceValue.textContent = `$${remaining.toFixed(4)} remaining of $${data.limit_usd.toFixed(2)} ($${data.spent_usd.toFixed(4)} spent)`;
  } else {
    const label = data.is_free_tier ? " (free tier)" : "";
    usageBalanceValue.textContent = `$${data.spent_usd.toFixed(4)} spent${label} · no credit limit set`;
  }
}

usageBtn.addEventListener("click", async () => {
  openModal(usageModal);
  renderUsageRangePills();
  usageCustomRangeEl.hidden = usageRange !== "custom";
  const [recordsRes] = await Promise.all([fetch("/api/usage/records"), fetchAndRenderBalance()]);
  usageRecordsCache = await recordsRes.json();
  renderUsageStats();
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

// --- Debug modal ---
// Last 10 individual LLM API calls (not persisted, resets on server restart) - lets the user
// inspect exactly what was sent/received for each request, including tool round-trips.

const debugBtn = document.getElementById("debug-btn");
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

function renderDebugRequests(entries) {
  debugRequestsListEl.innerHTML = "";

  if (entries.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No requests recorded yet this session.";
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

    const detail = document.createElement("div");
    detail.className = "debug-request-detail";
    detail.hidden = true;

    summary.addEventListener("click", () => {
      detail.hidden = !detail.hidden;
    });

    card.appendChild(summary);
    card.appendChild(detail);
    debugRequestsListEl.appendChild(card);

    // Lazily build the (potentially large) detail body only when first expanded.
    let built = false;
    summary.addEventListener("click", () => {
      if (built || detail.hidden) return;
      built = true;
      for (const message of entry.messages) {
        detail.appendChild(formatDebugMessage(message));
      }
      if (entry.tools && entry.tools.length > 0) {
        const toolsBlock = document.createElement("div");
        toolsBlock.className = "debug-message-block";
        toolsBlock.innerHTML = `<div class="debug-message-role">tools available</div><div class="debug-message-content">${entry.tools.join(", ")}</div>`;
        detail.appendChild(toolsBlock);
      }
      if (entry.reply || entry.tool_calls) {
        const replyBlock = document.createElement("div");
        replyBlock.className = "debug-message-block";
        const toolCallsText = entry.tool_calls
          ? entry.tool_calls.map((tc) => `→ ${tc.name}(${tc.arguments})`).join("\n")
          : "";
        replyBlock.innerHTML = `<div class="debug-message-role">result</div><div class="debug-message-content"></div>`;
        replyBlock.querySelector(".debug-message-content").textContent =
          [entry.reply, toolCallsText].filter(Boolean).join("\n") || "(empty)";
        detail.appendChild(replyBlock);
      }
    });
  }
}

debugBtn.addEventListener("click", async () => {
  openModal(debugModal);
  const response = await fetch("/api/debug/requests");
  const entries = await response.json();
  renderDebugRequests(entries);
});

// Release the Speech Recognition DLL before pywebview cleans up its temp profile folder,
// otherwise Windows locks the file and pywebview logs a WinError 5 access-denied warning.
window.addEventListener("beforeunload", () => {
  stopWakeWordRecognition();
});

// Block the browser's right-click context menu (hides Inspect, View Source, Save As, etc.).
// We use JS rather than AreDefaultContextMenusEnabled=False in Python because that flag also
// kills the <audio> player's 3-dot menu — contextmenu events are only right-click, not button clicks.
document.addEventListener("contextmenu", (e) => e.preventDefault());

// --- Init ---

async function init() {
  await refreshAvatarStatus();
  await loadChatsFromStorage();
  if (chats.length === 0) {
    createNewChat();
  } else {
    switchChat(chats[0].id);
  }
  loadConfig();
}

init();
