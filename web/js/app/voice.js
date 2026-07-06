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
  ensureAudioCtx();
  const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
  return encodeWav(audioBuffer.getChannelData(0), audioBuffer.sampleRate);
}

let voiceAbortController = null;

// Aborts whatever chat request (text or voice) is currently in flight. Called the instant the
// sleep word or overlay-edit-phrase is detected, so the user isn't stuck waiting out a reply to
// a question they've already moved on from. chatAbortController lives in chat-stream.js — safe to
// reference here since classic <script> tags share one global scope (see CLAUDE.md).
function cancelInFlightRequest() {
  chatAbortController?.abort();
  voiceAbortController?.abort();
  stopNarration();
}

async function sendDirectVoice(wavBlob) {
  // Sleep word landed during finalize (after its own suppression check) — don't send. Reset
  // awaitingReply (finalize set it true) so a later hands-free session isn't left blocked.
  if (Date.now() < suppressUtteranceUntil) {
    awaitingReply = false;
    return;
  }
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
  let assistantEl = null;
  let fullReply = "";
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
    if (attachedImageDataUrl) formData.append("image", attachedImageDataUrl);
    // When narration is on, the frontend drives overlay reply toasts itself (timed to narration);
    // tell the backend to skip its fixed-timer push so they don't double up.
    formData.append("client_overlay_toasts", String(document.getElementById("cfg-narrate").checked));
    clearAttachedImage();

    voiceAbortController = new AbortController();
    const response = await fetch("/api/chat/voice/stream", {
      method: "POST",
      body: formData,
      signal: voiceAbortController.signal,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const errText = `⚠️ ${error.detail || "Voice chat failed."}`;
      appendMessage("assistant", errText, null, true);
      addMessageToChat(chat, "assistant", errText);
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
      return;
    }

    assistantEl = appendMessage("assistant", "", null, true);
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
        if (payload.youtube_play) window.loadYoutubeVideo(payload.youtube_play.video_id, payload.youtube_play.title);
        if (payload.youtube_control) window.controlYoutubePlayer(payload.youtube_control.action, payload.youtube_control.volume);
        if (payload.youtube_playlist) window.loadYoutubePlaylist(payload.youtube_playlist.videos, payload.youtube_playlist.title);
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
    if (err.name === "AbortError") {
      // Cancelled from cancelInFlightRequest() (sleep word / edit-overlay phrase) - persist
      // whatever reply text had already streamed in, or drop the empty placeholder bubble.
      if (fullReply) {
        addMessageToChat(chat, "assistant", fullReply);
      } else if (assistantEl) {
        assistantEl.closest(".message")?.remove();
      }
      setVoiceStatus(liveMicEnabled ? "Listening..." : "");
    } else {
      setVoiceStatus("Voice chat failed", "error");
    }
  } finally {
    awaitingReply = false;
    voiceAbortController = null;
  }
}

let mediaRecorder;
let audioChunks = [];
let manualRecording = false;

micBtn.addEventListener("click", async () => {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }

  stopNarration();

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: micAudioConstraints() });
  } catch (err) {
    setVoiceStatus("Microphone access denied", "error");
    return;
  }

  mediaRecorder = new MediaRecorder(stream);
  audioChunks = [];

  mediaRecorder.ondataavailable = (event) => audioChunks.push(event.data);
  mediaRecorder.onstop = async () => {
    manualRecording = false;
    updateMusicDucking();
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
  manualRecording = true;
  updateMusicDucking();
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

// When the sleep word is detected (wake-word.js), the same utterance is being captured by the VAD;
// this short window makes finalizeLiveUtterance drop it instead of sending the sleep phrase to the
// model. Covers the VAD's silence-finalize delay plus a little processing slack.
let suppressUtteranceUntil = 0;
function suppressLiveUtterance() {
  suppressUtteranceUntil = Date.now() + 3000;
}

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

// Loaded once per shared AudioContext (see sfx.js's ensureAudioCtx - the context persists for the
// whole session, so re-adding the module on every startLiveMic() call would be wasted work).
let micWorkletLoadedFor = null;

async function startLiveMic() {
  try {
    liveMicStream = await navigator.mediaDevices.getUserMedia({ audio: micAudioConstraints() });
  } catch (err) {
    setVoiceStatus("Microphone access denied", "error");
    liveMicEnabled = false;
    liveMicToggle.classList.remove("active");
    return;
  }

  ensureAudioCtx();
  if (audioCtx.state === "suspended") {
    await audioCtx.resume();
  }

  if (micWorkletLoadedFor !== audioCtx) {
    await audioCtx.audioWorklet.addModule("js/app/mic-worklet-processor.js");
    micWorkletLoadedFor = audioCtx;
  }

  ringSampleRate = audioCtx.sampleRate;
  ringBuffer = new Float32Array(Math.ceil(RING_BUFFER_SECONDS * ringSampleRate));
  absoluteSampleCount = 0;
  liveRecording = false;
  liveSilenceStart = null;

  liveSource = audioCtx.createMediaStreamSource(liveMicStream);
  // AudioWorkletNode's process() runs on the dedicated audio rendering thread, so unlike the
  // ScriptProcessorNode this replaces, main-thread jank can't delay or drop capture buffers -
  // that was silently losing chunks of audio mid-utterance during hands-free listening.
  liveProcessor = new AudioWorkletNode(audioCtx, "mic-capture-processor", {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    channelCount: 1,
    // "explicit" forces the Web Audio API to actually downmix a stereo (or wider) input to mono
    // before process() sees it - the default "max" mode does NOT downmix, so a stereo capture
    // device would leave channel 1 silently discarded (mic-worklet-processor.js only reads
    // inputs[0][0]) instead of merged in. The ScriptProcessorNode this replaced forced true mono
    // via its (4096, 1, 1) constructor args; this restores that behavior for AudioWorkletNode.
    channelCountMode: "explicit",
  });
  liveSilentGain = audioCtx.createGain();
  liveSilentGain.gain.value = 0; // keep the processor alive without echoing mic audio to speakers

  liveProcessor.port.onmessage = (event) => {
    const samples = event.data;
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
        if (typeof disarmWakeWord === "function") disarmWakeWord();
        else updateMusicDucking();
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
  setOverlayHandsFree(true);
  updateVoiceHints();
}

// Tell the native overlay whether hands-free (live-mic) listening is active so it
// can show/hide its persistent mic indicator. Best-effort; ignored if no overlay.
function setOverlayHandsFree(active) {
  fetch("/api/overlay/handsfree", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  }).catch(() => {});
}

async function finalizeLiveUtterance() {
  if (!liveRecording) return;
  liveRecording = false;
  updateMusicDucking();
  // The sleep word was just detected — this utterance is the sleep phrase itself; drop it silently
  // instead of sending it to the model.
  if (Date.now() < suppressUtteranceUntil) {
    return;
  }
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
    liveProcessor.port.onmessage = null;
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
  setOverlayHandsFree(false);
  updateVoiceHints(true);
}

liveMicToggle.addEventListener("click", () => {
  liveMicEnabled = !liveMicEnabled;
  liveMicToggle.classList.toggle("active", liveMicEnabled);
  if (liveMicEnabled) {
    startLiveMic();
  } else {
    stopLiveMic();
    // Turning hands-free off by hand (not via the sleep word) skipped disarming a still-pending
    // wake-word duck-arm - it self-healed after WAKE_ARMED_MAX_MS, but music stayed ducked until
    // then instead of restoring immediately.
    if (typeof disarmWakeWord === "function") disarmWakeWord();
  }
  updateWakeWordListenerState();
});

// Re-acquire the hands-free stream on a newly-picked microphone (from Settings) so the change
// takes effect immediately. Push-to-talk isn't a persistent stream, so it just uses the new
// device on its next recording. No-op if hands-free isn't currently on.
function restartLiveMicForDeviceChange() {
  if (!liveMicEnabled) return;
  stopLiveMic();
  startLiveMic();
}

// --- Agent-driven stop_listening tool: lets the companion disable hands-free listening itself
// (e.g. user says "goodbye" or it's picking up unwanted audio). Always indefinite - resuming is
// the wake word's job now, not a guessed auto-resume timer. No-op if hands-free wasn't even on.

function agentStopListening() {
  if (!liveMicEnabled) return;
  liveMicEnabled = false;
  liveMicToggle.classList.remove("active");
  stopLiveMic();
  if (typeof disarmWakeWord === "function") disarmWakeWord();
  updateWakeWordListenerState();
}

// --- Wake word ---
// Re-enables hands-free listening by voice once it's been turned off (manually or by the
// agent), since the main use case is couch/controller play where touching the keyboard/mouse
// defeats the point. Uses the browser's built-in SpeechRecognition API (Chrome/Edge only) -
// only actually runs while hands-free is off, so it never competes with the live mic's own
// mic stream or double-submits the wake phrase itself as a voice message.

