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

