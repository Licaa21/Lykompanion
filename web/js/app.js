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

let narrationSpeed = 1.0;
let narrationVolume = 1.0;
let includeScreenshot = false;

// Live-mic voice activity detection tuning, persisted in localStorage (client-side only).
let vadThreshold = Number(localStorage.getItem("vadThreshold")) || 8;
let vadSilenceMs = Number(localStorage.getItem("vadSilenceMs")) || 1200;
let vadMinSpeechMs = Number(localStorage.getItem("vadMinSpeechMs")) || 300;

// Wake word - re-enables hands-free listening by voice after it's been turned off, since
// touching the keyboard/mouse defeats the point of hands-free. Persisted client-side only.
let wakeWordEnabled = localStorage.getItem("wakeWordEnabled") === "true";
let wakeWordPhrase = localStorage.getItem("wakeWordPhrase") || "Hey Buddy";

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
// Multiple conversations, persisted client-side in localStorage. Each chat
// holds its own messages array; "active" chat functions capture a direct
// reference to that array at call time (not a shared global) so that
// switching chats mid-request can't make an in-flight reply get appended to
// the wrong conversation.

const CHATS_STORAGE_KEY = "lykompanion_chats";

let chats = [];
let activeChatId = null;

function loadChatsFromStorage() {
  try {
    const raw = localStorage.getItem(CHATS_STORAGE_KEY);
    chats = raw ? JSON.parse(raw) : [];
  } catch (err) {
    chats = [];
  }
}

function saveChatsToStorage() {
  localStorage.setItem(CHATS_STORAGE_KEY, JSON.stringify(chats));
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

// --- Voice message audio storage (IndexedDB) ---
// Voice messages have no text transcript (audio goes straight to an audio-capable LLM, skipping
// local STT), so the actual recording is what's worth keeping for replay/debugging. Stored in
// IndexedDB rather than localStorage, which can't hold binary Blobs efficiently and has a much
// smaller size cap. Keyed by a random id referenced from the chat message itself.

const VOICE_DB_NAME = "lykompanion-voice";
const VOICE_STORE_NAME = "messages";

function openVoiceDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(VOICE_DB_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(VOICE_STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function saveVoiceBlob(id, blob) {
  try {
    const db = await openVoiceDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(VOICE_STORE_NAME, "readwrite");
      tx.objectStore(VOICE_STORE_NAME).put(blob, id);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } catch (err) {
    // Replay is a nice-to-have - failing to persist the blob shouldn't break sending the message.
  }
}

async function getVoiceBlob(id) {
  try {
    const db = await openVoiceDb();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(VOICE_STORE_NAME, "readonly");
      const request = tx.objectStore(VOICE_STORE_NAME).get(id);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  } catch (err) {
    return null;
  }
}

async function deleteVoiceBlob(id) {
  try {
    const db = await openVoiceDb();
    db.transaction(VOICE_STORE_NAME, "readwrite").objectStore(VOICE_STORE_NAME).delete(id);
  } catch (err) {
    // best-effort cleanup
  }
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

function enqueueNarration(text) {
  if (!text || !text.trim()) return Promise.resolve();
  const promise = synthesizeSentence(text);
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

function appendMessage(role, content, audioId) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = content;
  if (audioId) {
    const audioEl = document.createElement("audio");
    audioEl.controls = true;
    audioEl.className = "voice-message-audio";
    el.appendChild(audioEl);
    getVoiceBlob(audioId).then((blob) => {
      if (blob) audioEl.src = URL.createObjectURL(blob);
    });
  }
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  return el;
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
  appendMessage("user", text);
  addMessageToChat(chat, "user", text);

  const narrateEnabled = document.getElementById("cfg-narrate").checked;
  const assistantEl = appendMessage("assistant", "");
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
      assistantEl.textContent = `⚠️ ${error.detail || "Chat request failed."}`;
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
          assistantEl.textContent = fullReply;
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
        assistantEl.textContent = fullReply;
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
      assistantEl.textContent = fullReply || "⚠️ Chat request failed.";
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
    appendMessage("user", "🎤 (voice message)", audioId);
    setVoiceStatus("Sending voice message...");

    const formData = new FormData();
    formData.append("audio", wavBlob, "voice.wav");
    formData.append("history", JSON.stringify(chat.messages));
    formData.append("include_screenshot", String(includeScreenshot));

    const response = await fetch("/api/chat/voice", { method: "POST", body: formData });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      appendMessage("assistant", `⚠️ ${error.detail || "Voice chat failed."}`);
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
      return;
    }

    const data = await response.json();
    appendMessage("assistant", data.reply);
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
      audio: { echoCancellation: true, noiseSuppression: true },
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

const PRE_ROLL_MS = 600;
const MAX_UTTERANCE_MS = 15000;
const RING_BUFFER_SECONDS = 18;

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
      audio: { echoCancellation: true, noiseSuppression: true },
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
        const preRollSamples = Math.round((PRE_ROLL_MS / 1000) * ringSampleRate);
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
      if (Date.now() - liveSilenceStart > vadSilenceMs) {
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
  micBtn.classList.remove("recording");
  beep(440, 0.1);

  const samples = extractFromRing(utteranceStartAbsolute, absoluteSampleCount);
  const durationMs = (samples.length / ringSampleRate) * 1000;

  if (durationMs < vadMinSpeechMs) {
    setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    return;
  }

  if (!(await isLikelySpeech(samples, ringSampleRate))) {
    setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    return;
  }

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
const wakeWordDebugEl = document.getElementById("wake-word-debug");
const wakeWordStatusEl = document.getElementById("wake-word-status");
const wakeWordTranscriptEl = document.getElementById("wake-word-transcript");

const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
const wakeWordSupported = Boolean(SpeechRecognitionCtor);

let wakeWordRecognition = null;
let wakeWordShouldRun = false;
let wakeWordConsecutiveFailures = 0;
let wakeWordLastError = null;

// Errors that mean recognition is genuinely broken (e.g. a plain/open-source Chromium build
// without Google's proprietary speech API key - the API exists but every start() fails). Distinct
// from "no-speech", which fires routinely during normal continuous listening and isn't a failure.
const WAKE_WORD_HARD_ERRORS = new Set(["network", "service-not-allowed", "audio-capture", "not-allowed"]);
const WAKE_WORD_MAX_CONSECUTIVE_FAILURES = 3;
const WAKE_WORD_RESTART_DELAY_MS = 500;

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

    if (wakeWordConsecutiveFailures >= WAKE_WORD_MAX_CONSECUTIVE_FAILURES) {
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

  wakeWordDebugEl.hidden = !(wakeWordEnabled && wakeWordSupported);
  if (wakeWordEnabled && wakeWordSupported) {
    wakeWordStatusEl.textContent = liveMicEnabled ? "Hands-free is already on." : "Listening for wake phrase...";
    if (liveMicEnabled) wakeWordTranscriptEl.textContent = "";
  }
}

if (!wakeWordSupported) {
  wakeWordUnsupportedEl.hidden = false;
  wakeWordControlsEl.hidden = true;
} else {
  wakeWordEnabledInput.checked = wakeWordEnabled;
  wakeWordPhraseInput.value = wakeWordPhrase;
}

wakeWordEnabledInput.addEventListener("change", () => {
  wakeWordEnabled = wakeWordEnabledInput.checked;
  localStorage.setItem("wakeWordEnabled", wakeWordEnabled);
  updateWakeWordListenerState();
});

wakeWordPhraseInput.addEventListener("change", () => {
  wakeWordPhrase = wakeWordPhraseInput.value.trim() || "Hey Buddy";
  localStorage.setItem("wakeWordPhrase", wakeWordPhrase);
});

updateWakeWordListenerState();

// --- Settings modal ---

function openModal(modal) {
  modal.hidden = false;
}

function closeModal(modal) {
  modal.hidden = true;
}

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(document.getElementById(btn.dataset.close)));
});

[settingsModal, instructionsModal, memoryModal, usageModal, gameStateModal].forEach((modal) => {
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

const gameStateIntervalInput = document.getElementById("cfg-game-state-interval");
const gameStateIntervalValue = document.getElementById("cfg-game-state-interval-value");

gameStateIntervalInput.addEventListener("input", () => {
  gameStateIntervalValue.textContent = gameStateIntervalInput.value;
});

const vadThresholdInput = document.getElementById("cfg-vad-threshold");
const vadThresholdValue = document.getElementById("cfg-vad-threshold-value");
const vadSilenceInput = document.getElementById("cfg-vad-silence");
const vadSilenceValue = document.getElementById("cfg-vad-silence-value");
const vadMinSpeechInput = document.getElementById("cfg-vad-min-speech");
const vadMinSpeechValue = document.getElementById("cfg-vad-min-speech-value");

vadThresholdInput.value = vadThreshold;
vadThresholdValue.textContent = vadThreshold;
vadSilenceInput.value = vadSilenceMs;
vadSilenceValue.textContent = vadSilenceMs;
vadMinSpeechInput.value = vadMinSpeechMs;
vadMinSpeechValue.textContent = vadMinSpeechMs;

vadThresholdInput.addEventListener("input", () => {
  vadThreshold = Number(vadThresholdInput.value);
  vadThresholdValue.textContent = vadThreshold;
  localStorage.setItem("vadThreshold", vadThreshold);
});

vadSilenceInput.addEventListener("input", () => {
  vadSilenceMs = Number(vadSilenceInput.value);
  vadSilenceValue.textContent = vadSilenceMs;
  localStorage.setItem("vadSilenceMs", vadSilenceMs);
});

vadMinSpeechInput.addEventListener("input", () => {
  vadMinSpeechMs = Number(vadMinSpeechInput.value);
  vadMinSpeechValue.textContent = vadMinSpeechMs;
  localStorage.setItem("vadMinSpeechMs", vadMinSpeechMs);
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
  document.getElementById("cfg-tts-provider").value = cfg.tts_provider;
  updateTtsProviderVisibility();
  document.getElementById("cfg-api-key").placeholder = cfg.openrouter_api_key_set
    ? "•••••••• (set)"
    : "Not set";

  narrationSpeed = cfg.narration_speed;
  narrationSpeedInput.value = cfg.narration_speed;
  narrationSpeedValue.textContent = cfg.narration_speed;

  applyNarrationVolume(cfg.narration_volume);

  contextWindowInput.value = cfg.context_window_messages;
  contextWindowValue.textContent = cfg.context_window_messages === 0 ? "all" : cfg.context_window_messages;

  document.getElementById("cfg-game-state-enabled").checked = cfg.game_state_ocr_enabled;
  gameStateIntervalInput.value = cfg.game_state_poll_interval_seconds;
  gameStateIntervalValue.textContent = cfg.game_state_poll_interval_seconds;

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
    memory_extraction_model: document.getElementById("cfg-memory-model").value,
    tts_provider: document.getElementById("cfg-tts-provider").value,
    google_tts_api_key: document.getElementById("cfg-google-tts-api-key").value || null,
    google_tts_voice: document.getElementById("cfg-chirp3-voice").value,
    kokoro_voice: document.getElementById("cfg-kokoro-voice").value,
    openrouter_tts_model: document.getElementById("cfg-openrouter-tts-model").value,
    openrouter_voice: document.getElementById("cfg-openrouter-voice").value,
    openrouter_api_key: apiKeyInput.value || null,
    narration_speed: parseFloat(narrationSpeedInput.value),
    narration_volume: parseInt(narrationVolumeInput.value, 10) / 100,
    openrouter_voice_model: document.getElementById("cfg-openrouter-voice-model").value,
    context_window_messages: parseInt(contextWindowInput.value, 10),
    igdb_client_id: document.getElementById("cfg-igdb-client-id").value,
    igdb_client_secret: igdbSecretInput.value || null,
    steam_api_key: steamApiKeyInput.value || null,
    steam_id: document.getElementById("cfg-steam-id").value,
    game_state_ocr_enabled: document.getElementById("cfg-game-state-enabled").checked,
    game_state_poll_interval_seconds: parseInt(gameStateIntervalInput.value, 10),
    game_state_model: document.getElementById("cfg-game-state-model").value,
  };
  await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  apiKeyInput.value = "";
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

// --- Game-state approval chip ---
// Unfamiliar foreground processes aren't OCR'd automatically - they show up here so the user can
// explicitly allow or blacklist them, staying visible until acted upon (no auto-dismiss).

const approvalChip = document.getElementById("game-state-approval-chip");
const approvalChipProcess = document.getElementById("approval-chip-process");
let approvalChipShownFor = null;

async function checkPendingApproval() {
  const response = await fetch("/api/game-state/pending");
  const data = await response.json();

  if (!data.process) {
    approvalChip.hidden = true;
    approvalChipShownFor = null;
    return;
  }

  if (data.process === approvalChipShownFor) return;

  approvalChipShownFor = data.process;
  approvalChipProcess.textContent = data.process;
  approvalChip.hidden = false;
}

document.getElementById("approval-chip-allow").addEventListener("click", async () => {
  if (!approvalChipShownFor) return;
  await fetch("/api/game-state/whitelist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process: approvalChipShownFor }),
  });
  approvalChip.hidden = true;
  approvalChipShownFor = null;
  await loadGameStateProcessLists();
});

document.getElementById("approval-chip-blacklist").addEventListener("click", async () => {
  if (!approvalChipShownFor) return;
  await fetch("/api/game-state/blacklist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process: approvalChipShownFor }),
  });
  approvalChip.hidden = true;
  approvalChipShownFor = null;
  await loadGameStateProcessLists();
});

checkPendingApproval();
setInterval(checkPendingApproval, 20000);

usageBtn.addEventListener("click", async () => {
  openModal(usageModal);
  const response = await fetch("/api/usage");
  const usage = await response.json();
  document.getElementById("usage-requests").textContent = usage.request_count.toLocaleString();
  document.getElementById("usage-prompt-tokens").textContent = usage.total_prompt_tokens.toLocaleString();
  document.getElementById("usage-completion-tokens").textContent = usage.total_completion_tokens.toLocaleString();
  document.getElementById("usage-cost").textContent = `$${usage.total_cost_usd.toFixed(4)}`;
});

// --- Init ---

loadChatsFromStorage();
if (chats.length === 0) {
  createNewChat();
} else {
  switchChat(chats[0].id);
}
loadConfig();
