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
    deleteBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!(await showConfirm(`Delete "${chat.title}"? This can't be undone.`, { title: "Delete chat", danger: true, confirmText: "Delete" }))) return;
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

