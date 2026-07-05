// Pop-out player window logic (web/player.html, desktop app only). This window OWNS playback
// while it exists - the main window's player is stopped for the duration. Coordination happens
// entirely through localStorage, shared with the main window via the common WebView2 profile:
//
//   lyko-yt-pip-boot   main -> here, once: { queue, index, time, playing, volume }
//   lyko-yt-pip-state  here -> main, ~1s heartbeat: { ts, index, time, playing, volume, closed }
//                      (closed:true is the handoff - main resumes playback at `time`)
//   lyko-yt-pip-cmd    main -> here: { seq, action, volume? } - relayed LLM/tool commands
//                      (play/pause/restart/next/previous/set_volume/stop)
//
// Closing this window (its own close button, relayed "stop", Alt+F4, or app quit) hands playback
// back to the main window at the exact position reached here. That handoff is written from TWO
// places for redundancy: closeSelf() below (the normal path - button click / relayed "stop"), and
// window.__lykoPlayerHandoff, called from Python's window.events.closing (run_app.py) as a
// fallback for close paths that never run this page's own JS at all (observed: closing via the
// OS window chrome didn't reliably fire `pagehide` in WebView2, leaving the main window's
// "popped out" flag stuck forever). writeState()'s closedWritten guard makes calling it from both
// harmless.

const BOOT_KEY = "lyko-yt-pip-boot";
const STATE_KEY = "lyko-yt-pip-state";
const CMD_KEY = "lyko-yt-pip-cmd";

const titleEl = document.getElementById("youtube-player-title");
const panelEl = document.getElementById("youtube-player-panel");
const seekBar = document.getElementById("youtube-player-seekbar");
const timeCurrent = document.getElementById("youtube-player-time-current");
const timeDuration = document.getElementById("youtube-player-time-duration");
const prevBtn = document.getElementById("youtube-player-prev");
const nextBtn = document.getElementById("youtube-player-next");
const playPauseBtn = document.getElementById("youtube-player-playpause");
const playIcon = playPauseBtn.querySelector(".youtube-player-playpause-icon-play");
const pauseIcon = playPauseBtn.querySelector(".youtube-player-playpause-icon-pause");
const volumeSlider = document.getElementById("youtube-player-volume");
const prevLabel = document.getElementById("youtube-player-prev-label");
const nextLabel = document.getElementById("youtube-player-next-label");
const videoGuard = document.getElementById("youtube-player-video-guard");
const dragHeader = document.getElementById("player-drag-header");
const fullscreenBtn = document.getElementById("player-fullscreen-btn");
const fullscreenIconExpand = fullscreenBtn.querySelector(".youtube-player-fullscreen-icon-expand");
const fullscreenIconCompress = fullscreenBtn.querySelector(".youtube-player-fullscreen-icon-compress");
const closeBtn = document.getElementById("player-close-btn");
const qualitySelect = document.getElementById("youtube-player-quality-select");

let boot = null;
try {
  boot = JSON.parse(localStorage.getItem(BOOT_KEY) || "null");
} catch (err) { /* handled below */ }

let queue = (boot && Array.isArray(boot.queue)) ? boot.queue : [];
let index = boot && Number.isFinite(boot.index) ? boot.index : 0;
let player = null;
let seeking = false;
let lastCmdSeq = 0;
let closedWritten = false;

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Same best-effort approach as the in-app player's ytPopulateQualityOptions - see its comment in
// youtube-player.js for why this can't reliably force a resolution, only list real ones.
const QUALITY_LABELS = {
  highres: "4K+", hd2160: "2160p", hd1440: "1440p", hd1080: "1080p", hd720: "720p",
  large: "480p", medium: "360p", small: "240p", tiny: "144p", auto: "Auto",
};
function populateQualityOptions() {
  if (!player || !player.getAvailableQualityLevels) return;
  let levels = [];
  try { levels = player.getAvailableQualityLevels() || []; } catch (err) { return; }
  if (!levels.length) levels = ["auto"];
  else if (!levels.includes("auto")) levels = [...levels, "auto"];
  let current = "auto";
  try { current = (player.getPlaybackQuality && player.getPlaybackQuality()) || "auto"; } catch (err) { /* default to auto */ }
  qualitySelect.innerHTML = "";
  for (const level of levels) {
    const opt = document.createElement("option");
    opt.value = level;
    opt.textContent = QUALITY_LABELS[level] || level;
    if (level === current) opt.selected = true;
    qualitySelect.appendChild(opt);
  }
}
qualitySelect.addEventListener("change", () => {
  if (player && player.setPlaybackQuality) player.setPlaybackQuality(qualitySelect.value);
});

function combinedTitle(entry) {
  return entry.channel ? `${entry.title} — ${entry.channel}` : entry.title;
}

function updateChrome() {
  const entry = queue[index];
  titleEl.textContent = "";
  if (entry && entry.channel) {
    titleEl.appendChild(document.createTextNode(`${entry.title} — `));
    const span = document.createElement("span");
    span.className = "youtube-player-title-channel";
    span.textContent = entry.channel;
    titleEl.appendChild(span);
  } else {
    titleEl.textContent = entry ? entry.title : "YouTube";
  }
  const prev = index > 0 ? queue[index - 1] : null;
  const next = index < queue.length - 1 ? queue[index + 1] : null;
  prevLabel.textContent = prev ? `Previous: ${combinedTitle(prev)}` : "";
  nextLabel.textContent = next ? `Up next: ${combinedTitle(next)}` : "";
}

function syncSeekFill() {
  const max = Number(seekBar.max) || 0;
  seekBar.style.setProperty("--fill", `${max > 0 ? (Number(seekBar.value) / max) * 100 : 0}%`);
}
function syncVolumeFill() {
  volumeSlider.style.setProperty("--fill", `${Number(volumeSlider.value)}%`);
}

function writeState(closed) {
  if (closedWritten) return;
  if (closed) closedWritten = true;
  const playing = player && player.getPlayerState ? player.getPlayerState() === 1 : false;
  localStorage.setItem(STATE_KEY, JSON.stringify({
    ts: Date.now(),
    index,
    time: player && player.getCurrentTime ? player.getCurrentTime() || 0 : 0,
    playing,
    volume: Number(volumeSlider.value),
    closed: !!closed,
  }));
}

function closeSelf() {
  writeState(true);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.player_close) {
    window.pywebview.api.player_close();
  } else {
    window.close();
  }
}
// Called from Python (run_app.py's window.events.closing) as a fallback handoff - see the
// top-of-file comment for why this exists alongside closeSelf()/pagehide.
window.__lykoPlayerHandoff = () => writeState(true);

closeBtn.addEventListener("click", closeSelf);

let fullscreenActive = false;
fullscreenBtn.addEventListener("click", () => {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.player_toggle_fullscreen) {
    window.pywebview.api.player_toggle_fullscreen();
  }
  // pywebview's native toggle_fullscreen has no JS-side event to observe - we're the only thing
  // that ever calls it, so a locally-tracked flag stays accurate.
  fullscreenActive = !fullscreenActive;
  fullscreenBtn.classList.toggle("active", fullscreenActive);
  fullscreenBtn.title = fullscreenActive ? "Exit fullscreen" : "Fullscreen";
  fullscreenIconExpand.hidden = fullscreenActive;
  fullscreenIconCompress.hidden = !fullscreenActive;
});

// Frameless drag - mirrors the main window's setupDesktopTitlebar (init.js) but simplified (no
// edge-snap, no double-click-maximize): this is a small utility window, not the main shell.
// Clamped to the work area (screen.avail*, which excludes the taskbar) exactly like the main
// window's own titlebar drag - without this, a frameless window has no OS-imposed boundary at
// all and can be dragged fully behind the taskbar and become unreachable.
function workArea() {
  return { x: screen.availLeft || 0, y: screen.availTop || 0, w: screen.availWidth, h: screen.availHeight };
}
(() => {
  const HEADER = 40, EDGE_KEEP = 80;
  dragHeader.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button")) return;
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.player_set_bounds) return;
    const api = window.pywebview.api;
    const r = window.devicePixelRatio || 1;
    const sx = event.screenX, sy = event.screenY;
    const start = { x: window.screenX, y: window.screenY, w: window.innerWidth, h: window.innerHeight };
    dragHeader.setPointerCapture(event.pointerId);
    let raf = 0, pending = null;
    const flush = () => {
      raf = 0;
      if (pending) api.player_set_bounds(Math.round(pending.x * r), Math.round(pending.y * r), Math.round(start.w * r), Math.round(start.h * r));
    };
    const onMove = (ev) => {
      const a = workArea();
      let x = start.x + (ev.screenX - sx), y = start.y + (ev.screenY - sy);
      x = Math.min(Math.max(x, a.x - (start.w - EDGE_KEEP)), a.x + a.w - EDGE_KEEP);
      y = Math.min(Math.max(y, a.y), a.y + a.h - HEADER);
      pending = { x, y };
      if (!raf) raf = requestAnimationFrame(flush);
    };
    const onUp = () => {
      try { dragHeader.releasePointerCapture(event.pointerId); } catch (err) { /* already released */ }
      dragHeader.removeEventListener("pointermove", onMove);
      dragHeader.removeEventListener("pointerup", onUp);
    };
    dragHeader.addEventListener("pointermove", onMove);
    dragHeader.addEventListener("pointerup", onUp);
  });
})();

// Edge/corner resize grips - same coalesce-to-one-bridge-call-per-frame approach as init.js.
(() => {
  const MIN_W = 380, MIN_H = 300;
  document.querySelectorAll(".resize-grip").forEach((grip) => {
    grip.addEventListener("pointerdown", (event) => {
      if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.player_set_bounds) return;
      const api = window.pywebview.api;
      const r = window.devicePixelRatio || 1;
      event.preventDefault();
      const dir = grip.dataset.dir;
      const sx = event.screenX, sy = event.screenY;
      const start = { x: window.screenX, y: window.screenY, w: window.innerWidth, h: window.innerHeight };
      grip.setPointerCapture(event.pointerId);
      let raf = 0, pending = null;
      const flush = () => {
        raf = 0;
        if (pending) api.player_set_bounds(Math.round(pending.x * r), Math.round(pending.y * r), Math.round(pending.w * r), Math.round(pending.h * r));
      };
      const onMove = (ev) => {
        const dx = ev.screenX - sx, dy = ev.screenY - sy;
        let x = start.x, y = start.y, w = start.w, h = start.h;
        if (dir.includes("e")) w = start.w + dx;
        if (dir.includes("s")) h = start.h + dy;
        if (dir.includes("w")) { w = start.w - dx; x = start.x + dx; }
        if (dir.includes("n")) { h = start.h - dy; y = start.y + dy; }
        if (w < MIN_W) { if (dir.includes("w")) x -= (MIN_W - w); w = MIN_W; }
        if (h < MIN_H) { if (dir.includes("n")) y -= (MIN_H - h); h = MIN_H; }
        pending = { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
        if (!raf) raf = requestAnimationFrame(flush);
      };
      const onUp = () => {
        try { grip.releasePointerCapture(event.pointerId); } catch (err) { /* already released */ }
        grip.removeEventListener("pointermove", onMove);
        grip.removeEventListener("pointerup", onUp);
      };
      grip.addEventListener("pointermove", onMove);
      grip.addEventListener("pointerup", onUp);
    });
  });
})();

function playIndex(newIndex, startSeconds, paused) {
  if (newIndex < 0 || newIndex >= queue.length || !player) return;
  index = newIndex;
  updateChrome();
  const args = { videoId: queue[index].videoId, startSeconds: startSeconds || 0 };
  if (paused && player.cueVideoById) player.cueVideoById(args);
  else player.loadVideoById(args);
}

function handleCommand(cmd) {
  switch (cmd.action) {
    case "play": if (player && player.playVideo) player.playVideo(); break;
    case "pause": if (player && player.pauseVideo) player.pauseVideo(); break;
    case "restart":
      if (player && player.seekTo) { player.seekTo(0, true); player.playVideo(); }
      break;
    case "next": playIndex(index + 1); break;
    case "previous": playIndex(index - 1); break;
    case "set_volume":
      if (player && player.setVolume && Number.isFinite(cmd.volume)) {
        const clamped = Math.min(100, Math.max(0, cmd.volume));
        player.setVolume(clamped);
        volumeSlider.value = clamped;
        syncVolumeFill();
      }
      break;
    case "stop": closeSelf(); break;
  }
}

// Command relay + heartbeat. The storage event is unreliable across separate WebView2 windows,
// so both directions poll - cheap at these sizes/rates.
setInterval(() => {
  let cmd = null;
  try {
    cmd = JSON.parse(localStorage.getItem(CMD_KEY) || "null");
  } catch (err) { /* corrupt/absent */ }
  if (cmd && Number.isFinite(cmd.seq) && cmd.seq > lastCmdSeq) {
    lastCmdSeq = cmd.seq;
    handleCommand(cmd);
  }
}, 400);

setInterval(() => writeState(false), 1000);

// UI poll - same 500ms cadence as the in-app player.
setInterval(() => {
  if (seeking || !player || !player.getCurrentTime) return;
  const duration = player.getDuration() || 0;
  const current = player.getCurrentTime() || 0;
  seekBar.max = duration;
  seekBar.value = current;
  syncSeekFill();
  timeCurrent.textContent = formatTime(current);
  timeDuration.textContent = formatTime(duration);
  const playing = player.getPlayerState ? player.getPlayerState() === 1 : false;
  playIcon.hidden = playing;
  pauseIcon.hidden = !playing;
  panelEl.classList.toggle("youtube-player-panel--playing", playing);
}, 500);

seekBar.addEventListener("input", () => {
  seeking = true;
  syncSeekFill();
  timeCurrent.textContent = formatTime(Number(seekBar.value));
});
seekBar.addEventListener("change", () => {
  if (player && player.seekTo) player.seekTo(Number(seekBar.value), true);
  seeking = false;
});
prevBtn.addEventListener("click", () => playIndex(index - 1));
nextBtn.addEventListener("click", () => playIndex(index + 1));
function togglePlayPause() {
  if (!player || !player.getPlayerState) return;
  if (player.getPlayerState() === 1) player.pauseVideo();
  else player.playVideo();
}
playPauseBtn.addEventListener("click", togglePlayPause);
volumeSlider.addEventListener("input", () => {
  if (player && player.setVolume) player.setVolume(Number(volumeSlider.value));
  syncVolumeFill();
});

// Same reasoning as the in-app player's .youtube-player-video-guard (see its CSS comment in
// style.css): sits over the cross-origin iframe so this window's own keyboard/mouse handling
// isn't silently swallowed by it, and doubles as a click-to-toggle-play/pause surface.
videoGuard.addEventListener("mousedown", () => videoGuard.focus());
videoGuard.addEventListener("click", togglePlayPause);

// Native X / relayed stop / app quit - hand the final position back to the main window.
window.addEventListener("pagehide", () => writeState(true));

window.onYouTubeIframeAPIReady = function () {
  if (!queue.length) {
    titleEl.textContent = "Nothing to play.";
    return;
  }
  const startVolume = boot && Number.isFinite(boot.volume) ? boot.volume : 100;
  volumeSlider.value = startVolume;
  syncVolumeFill();
  updateChrome();
  player = new YT.Player(document.getElementById("youtube-player-frame-mount"), {
    videoId: queue[index].videoId,
    playerVars: {
      autoplay: boot && boot.playing === false ? 0 : 1,
      rel: 0,
      controls: 0,
      disablekb: 1, fs: 0, iv_load_policy: 3, cc_load_policy: 0, modestbranding: 1,
      start: boot && Number.isFinite(boot.time) ? Math.floor(boot.time) : 0,
    },
    events: {
      onReady: (event) => {
        event.target.setVolume(startVolume);
      },
      onStateChange: (event) => {
        if (event.data === YT.PlayerState.ENDED) playIndex(index + 1);
        else if (event.data === YT.PlayerState.PLAYING) populateQualityOptions();
      },
      onError: () => {
        if (index < queue.length - 1) playIndex(index + 1);
        else titleEl.textContent = "Playback error.";
      },
    },
  });
};

const tag = document.createElement("script");
tag.src = "https://www.youtube.com/iframe_api";
document.head.appendChild(tag);
