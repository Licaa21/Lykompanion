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

