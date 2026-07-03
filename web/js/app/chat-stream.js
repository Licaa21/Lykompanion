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

