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

// Ducks in-app music (currently YouTube only) while the companion is narrating, the user is
// speaking (hands-free or push-to-talk), or a wake word just fired and an utterance is expected
// imminently - see applyMusicDucking in youtube-player.js. liveRecording/manualRecording/wakeArmed
// live in voice.js/wake-word.js, which load after this file; referencing them here is safe since
// this only runs at runtime, once every script has finished loading.
function updateMusicDucking() {
  if (typeof applyMusicDucking !== "function") return;
  applyMusicDucking(
    isNarrating ||
    (typeof liveRecording !== "undefined" && liveRecording) ||
    (typeof manualRecording !== "undefined" && manualRecording) ||
    (typeof wakeArmed !== "undefined" && wakeArmed)
  );
}

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

// The Kokoro TTS backend's phonemizer (misaki) reads some ALL-CAPS two/three-letter tokens as
// chemical formulas instead of acronyms - e.g. "HP" becomes "Hydrogen Phosphorus" because H and P
// are both valid single-letter element symbols. Dotting the letters ("H.P.") keeps them adjacent
// enough to still read as an acronym while breaking the formula match. Covers common RPG/gaming
// stat shorthand that collides with element symbols (H, B, C, N, O, F, P, S, K, V, Y, I, W, U).
const GAMING_ACRONYM_FIXES = {
  HP: "H.P.",
  MP: "M.P.",
  SP: "S.P.",
  XP: "X.P.",
  AP: "A.P.",
  OP: "O.P.",
  NP: "N.P.",
  CP: "C.P.",
  FP: "F.P.",
  BP: "B.P.",
  DPS: "D.P.S.",
};
const GAMING_ACRONYM_REGEX = new RegExp(`\\b(${Object.keys(GAMING_ACRONYM_FIXES).join("|")})\\b`, "g");

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
    .replace(GAMING_ACRONYM_REGEX, (m) => GAMING_ACRONYM_FIXES[m])
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function enqueueNarration(text) {
  const cleaned = text ? stripMarkdownForNarration(text) : "";
  if (!cleaned) return Promise.resolve();
  const promise = synthesizeSentence(cleaned);
  return new Promise((resolveItem) => {
    ttsQueue.push({ promise, resolveItem, text: cleaned });
    processTtsQueue();
  });
}

// Push a reply sentence to the native overlay as it starts being narrated, timed to how long it
// takes to speak at the current TTS speed, so the toast stays up for exactly the spoken sentence.
// (The backend skips its own fixed-timer reply toasts when narration is on — see client_overlay_toasts.)
function pushOverlayNarrationToast(text) {
  if (!text) return;
  const rate = narrationAudio.playbackRate || 1;
  const seconds = Number.isFinite(narrationAudio.duration) ? narrationAudio.duration / rate : 0;
  // A little padding so it lingers a touch past the last word instead of vanishing on the syllable.
  const durationMs = seconds > 0 ? Math.round(seconds * 1000) + 500 : 0;
  fetch("/api/overlay/toast", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, kind: "reply", duration_ms: durationMs }),
  }).catch(() => {});
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

// Peak audio level (0-1) below which the foreground game counts as "quiet" for the purposes of
// starting narration, and how long it must stay quiet before a queued sentence is released - a
// short debounce so narration doesn't sneak into a brief silent beat mid-dialogue-line. Capped by
// MAX_WAIT_MS so a game that never goes quiet (e.g. constant music/ambience) can't stall
// narration forever - it starts anyway once the cap is hit.
const GAME_QUIET_PEAK_THRESHOLD = 0.02;
const GAME_QUIET_HOLD_MS = 300;
const GAME_QUIET_POLL_MS = 150;
const GAME_QUIET_MAX_WAIT_MS = 6000;

// Holds a queued sentence until the foreground game's live audio peak (not the OCR-derived,
// ~15s-stale "activity" label) has been quiet for GAME_QUIET_HOLD_MS, so narration starts in an
// actual gap between game dialogue lines instead of talking over them. A null peak (non-Windows,
// no foreground game, or no audio session for it) means there's nothing to gate against, so it
// returns immediately rather than adding a pointless delay to every sentence.
async function waitForGameQuiet() {
  const deadline = Date.now() + GAME_QUIET_MAX_WAIT_MS;
  let quietSince = null;
  while (Date.now() < deadline) {
    let peak = null;
    try {
      const res = await fetch("/api/system/audio-peak");
      if (res.ok) peak = (await res.json()).peak;
    } catch (err) {
      peak = null;
    }
    if (peak === null) return;
    if (peak < GAME_QUIET_PEAK_THRESHOLD) {
      if (quietSince === null) quietSince = Date.now();
      if (Date.now() - quietSince >= GAME_QUIET_HOLD_MS) return;
    } else {
      quietSince = null;
    }
    await new Promise((resolve) => setTimeout(resolve, GAME_QUIET_POLL_MS));
  }
}

async function processTtsQueue() {
  if (ttsPlaying || ttsQueue.length === 0) return;
  ttsPlaying = true;
  isNarrating = true;
  updateMusicDucking();
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
      // Hold until the foreground game's audio has a real gap (see waitForGameQuiet) BEFORE
      // pushing the overlay toast or starting playback - otherwise the toast (and any duration
      // math built on top of it) would fire while we're still waiting, well ahead of the voice.
      await waitForGameQuiet();
      // Re-check: stopNarration() may have fired while we were waiting on the game to go quiet
      // (it can't cancel that wait directly, since playback hasn't started yet to hook into).
      if (!ttsPlaying) return;
      // Push the overlay toast for this sentence as early as we can — once metadata (duration) is
      // known, and BEFORE the setSinkId await + play() startup — so it lands at or just before the
      // first spoken syllable (accounting for the fetch→pipe→render hop), never seconds early (text
      // done) nor after the voice. Duration needs metadata; loadedmetadata may have already fired.
      const pushToast = () => pushOverlayNarrationToast(item.text);
      if (Number.isFinite(narrationAudio.duration) && narrationAudio.duration > 0) {
        pushToast();
      } else {
        narrationAudio.addEventListener("loadedmetadata", pushToast, { once: true });
      }
      await applyOutputDevice(narrationAudio);
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
    // Only release ducking once the queue is actually drained, not on every individual sentence -
    // processTtsQueue() below immediately re-enters and flips isNarrating back to true within the
    // same tick whenever more sentences are already queued (multi-sentence replies enqueue several
    // at once), so calling updateMusicDucking() unconditionally here caused a real bug: the
    // un-duck→re-duck round trip re-captured "current volume" via ytPlayer.getVolume() mid-fade
    // (before the un-duck ramp had time to finish), corrupting the saved pre-duck baseline lower
    // and lower with each sentence gap - so the final restore, at the true end of narration,
    // brought the volume back to that corrupted low value instead of the real original.
    if (ttsQueue.length === 0) {
      stopNarrationBtn.hidden = true;
      updateMusicDucking();
    }
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
  updateMusicDucking();
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
  // A raw https URL the model embedded directly (not via show_image's proxied output) still
  // needs to go through the proxy - a direct browser <img src> fetch sends this app's own origin
  // as Referer, which trips hotlink protection on many hosts even for a perfectly real image URL.
  // show_image's own fetch is server-to-server (no such Referer), so a URL that validated fine
  // there rendered as "[image unavailable]" here purely from this second, unproxied path.
  html = html.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, (_m, alt, url) => {
    const proxied = `/api/proxy/image?url=${encodeURIComponent(url)}`;
    return `<img src="${apiUrl(proxied)}" alt="${alt}" class="chat-inline-image" loading="lazy" />`;
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

