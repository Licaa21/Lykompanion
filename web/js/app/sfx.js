let audioCtx;

// Single shared Web Audio context for every synthesized cue (mic beeps, wake chime, decorative
// SFX) and the live-mic capture graph in voice.js. Created lazily and routed to the user's chosen
// audio-output device (AudioContext.setSinkId, Chromium 110+) so beeps/SFX follow the same output
// as narration; falls back to the system default where setSinkId isn't supported.
function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    applyOutputToAudioContext();
  }
  return audioCtx;
}

// Re-route the shared context to the currently selected output device. Called on creation and when
// the user changes the output in Settings. No-op before the context exists or without setSinkId.
async function applyOutputToAudioContext() {
  if (!audioCtx || typeof audioCtx.setSinkId !== "function") return;
  try {
    await audioCtx.setSinkId(getSelectedOutputId() || "");
  } catch (err) {
    /* device gone / unsupported — stays on default */
  }
}

function beep(frequency, duration) {
  ensureAudioCtx();
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
  ensureAudioCtx();
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
  ensureAudioCtx();
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
  ensureAudioCtx();
  (TOOL_SFX[toolName] || playGenericToolSfx)();
}

// --- Chat sessions (sidebar) ---
// Multiple conversations, persisted server-side via /api/chats (data/chats.json). Each chat
// holds its own messages array; "active" chat functions capture a direct
// reference to that array at call time (not a shared global) so that
// switching chats mid-request can't make an in-flight reply get appended to
// the wrong conversation.


