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

const sleepWordControlsEl = document.getElementById("sleep-word-controls");
const sleepWordEnabledInput = document.getElementById("cfg-sleep-word-enabled");
const sleepWordPhraseInput = document.getElementById("cfg-sleep-word-phrase");
const sleepWordDependentEl = document.getElementById("sleep-word-dependent");

const overlayEditPhraseControlsEl = document.getElementById("overlay-edit-phrase-controls");
const overlayEditPhraseEnabledInput = document.getElementById("cfg-overlay-edit-phrase-enabled");
const overlayEditPhraseInput = document.getElementById("cfg-overlay-edit-phrase");
const overlayEditPhraseDependentEl = document.getElementById("overlay-edit-phrase-dependent");

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

// Opens the native overlay's edit mode directly — no chat request, no tool spec, no LLM
// involvement at all. Orthogonal to hands-free state; best-effort (no-ops if the overlay isn't
// running, which app/api/overlay.py's endpoint already guarantees).
async function handleOverlayEditPhraseDetected() {
  try {
    // window.fetch is patched (core.js) to attach the required auth token header.
    await fetch("/api/overlay/edit-mode", { method: "POST" });
  } catch (err) {
    // best-effort — overlay may not be running
  }
}

function handleSleepWordDetected() {
  if (!liveMicEnabled) return;
  // Suppress the live-mic utterance carrying this same phrase so it's never sent to the model
  // (the client's job); the LLM stop-intent backstop covers the rare case it already went out.
  if (typeof suppressLiveUtterance === "function") suppressLiveUtterance();
  liveMicEnabled = false;
  liveMicToggle.classList.remove("active");
  stopLiveMic();
  playSleepChime();
  if (!wakeWordDebugEl.hidden) {
    wakeWordStatusEl.textContent = "Sleeping 💤";
    setTimeout(() => {
      if (wakeWordStatusEl && !liveMicEnabled) wakeWordStatusEl.textContent = "Listening for wake phrase...";
    }, 1500);
  }
  updateWakeWordListenerState();  // hands-free now off → recognizer switches back to wake duty
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

    const normalizedTranscript = normalizeForWakeMatch(transcript);
    // One recognizer, two jobs depending on hands-free state: while OFF it listens for the wake
    // phrase (turn on); while ON it listens for the sleep phrase (turn off).
    if (!liveMicEnabled) {
      const normalizedWake = normalizeForWakeMatch(wakeWordPhrase);
      if (normalizedWake && normalizedTranscript.includes(normalizedWake)) {
        handleWakeWordDetected();
      }
    } else if (sleepWordEnabled) {
      const normalizedSleep = normalizeForWakeMatch(sleepWordPhrase);
      if (normalizedSleep && normalizedTranscript.includes(normalizedSleep)) {
        handleSleepWordDetected();
      }
    }

    // The "Edit overlay" phrase is checked unconditionally, independent of hands-free state
    // (unlike wake/sleep word above, which are mutually exclusive by mic state) — bypasses the
    // LLM entirely, see handleOverlayEditPhraseDetected().
    if (overlayEditPhraseEnabled) {
      const normalizedEdit = normalizeForWakeMatch(overlayEditPhrase);
      if (normalizedEdit && normalizedTranscript.includes(normalizedEdit)) {
        handleOverlayEditPhraseDetected();
      }
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
  // The recognizer runs to catch the wake phrase (while hands-free is off) OR the sleep phrase
  // (while it's on) — so it stays alive across the on/off transition instead of stopping.
  const wantWake = wakeWordSupported && wakeWordEnabled && !liveMicEnabled;
  const wantSleep = wakeWordSupported && sleepWordEnabled && liveMicEnabled;
  // Independent of liveMicEnabled — the edit-overlay phrase must be caught whether or not
  // hands-free is on, so the recognizer stays alive purely for it even if wake/sleep are both off.
  const wantEditPhrase = wakeWordSupported && overlayEditPhraseEnabled;
  wakeWordShouldRun = wantWake || wantSleep || wantEditPhrase;

  if (wakeWordShouldRun) {
    if (!wasRunning) wakeWordConsecutiveFailures = 0; // fresh start - give it a clean shot
    startWakeWordRecognition();
  } else {
    stopWakeWordRecognition();
  }

  wakeWordDependentEl.hidden = !wakeWordEnabled;
  sleepWordDependentEl.hidden = !sleepWordEnabled;
  overlayEditPhraseDependentEl.hidden = !overlayEditPhraseEnabled;
  wakeWordDebugEl.hidden = !(wakeWordSupported && (wakeWordEnabled || sleepWordEnabled));
  if (wakeWordSupported && (wakeWordEnabled || sleepWordEnabled)) {
    wakeWordStatusEl.textContent = liveMicEnabled
      ? (sleepWordEnabled ? "Listening for sleep phrase..." : "Hands-free is already on.")
      : (wakeWordEnabled ? "Listening for wake phrase..." : "");
    if (liveMicEnabled && !sleepWordEnabled) wakeWordTranscriptEl.textContent = "";
  }
}

if (!wakeWordSupported) {
  wakeWordUnsupportedEl.hidden = false;
  wakeWordControlsEl.hidden = true;
  sleepWordControlsEl.hidden = true;
  overlayEditPhraseControlsEl.hidden = true;
}

wakeWordEnabledInput.addEventListener("change", () => {
  wakeWordEnabled = wakeWordEnabledInput.checked;
  updateWakeWordListenerState();
  updateVoiceHints();
});

wakeWordPhraseInput.addEventListener("input", () => {
  wakeWordPhrase = wakeWordPhraseInput.value.trim() || "Hey Buddy";
  updateVoiceHints();
});

sleepWordEnabledInput.addEventListener("change", () => {
  sleepWordEnabled = sleepWordEnabledInput.checked;
  updateWakeWordListenerState();
  updateVoiceHints();
});

sleepWordPhraseInput.addEventListener("input", () => {
  sleepWordPhrase = sleepWordPhraseInput.value.trim() || "Go to sleep";
  updateVoiceHints();
});

overlayEditPhraseEnabledInput.addEventListener("change", () => {
  overlayEditPhraseEnabled = overlayEditPhraseEnabledInput.checked;
  updateWakeWordListenerState();
  updateVoiceHints();
});

overlayEditPhraseInput.addEventListener("input", () => {
  overlayEditPhrase = overlayEditPhraseInput.value.trim() || "Edit overlay";
  updateVoiceHints();
});

updateWakeWordListenerState();

// --- Settings modal ---

