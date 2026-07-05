// Pop-out player window logic (web/player.html, desktop app only). This window OWNS playback
// while it exists - the main window's player is stopped for the duration. Coordination happens
// entirely through localStorage, shared with the main window via the common WebView2 profile:
//
//   lyko-yt-pip-boot   main -> here, once: { queue, index, time, playing, volume }
//   lyko-yt-pip-state  here -> main, ~1s heartbeat: { ts, index, time, playing, volume, closed, resume }
//                      (closed:true ends the session; resume:true additionally hands playback
//                      back to the main window at `time` - resume:false just stops it there)
//   lyko-yt-pip-cmd    main -> here: { seq, action, volume? } - relayed LLM/tool commands
//                      (play/pause/restart/next/previous/set_volume/stop)
//
// Two distinct ways to end this window, both funnelled through closeSelf(resume):
//   - The dock button ("Return to app") hands playback back to the main window's in-app player at
//     the exact position reached here (resume:true).
//   - The X button stops playback outright - no resume, the main window's player stays closed
//     (resume:false).
// Either way the window itself closes. That's also written from window.__lykoPlayerHandoff,
// called from Python's window.events.closing (run_app.py) as a fallback for close paths that
// never run this page's own JS at all (observed: closing via the OS window chrome didn't reliably
// fire `pagehide` in WebView2, leaving the main window's "popped out" flag stuck forever) - it
// defaults to resume:true, the safer of the two for an close path the user didn't explicitly pick.
// writeState()'s closedWritten guard makes calling it from multiple paths harmless.

// This window is opened fresh (never reloaded) with ?token=... on its URL (run_app.py's
// open_player_window) - unlike core.js's version of this same patch, there's no need to persist
// the token across reloads, just read it once and patch fetch for the playlists/search buttons'
// authenticated /api/* calls.
const API_TOKEN = new URLSearchParams(location.search).get("token") || "";
if (API_TOKEN) {
  history.replaceState(null, "", location.pathname);
  const _origFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const headers = new Headers(init.headers || {});
    headers.set("X-Lyko-Token", API_TOKEN);
    return _origFetch(input, { ...init, headers });
  };
}

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
const dockBtn = document.getElementById("player-dock-btn");
const playlistsBtn = document.getElementById("player-playlists-btn");
const searchBtn = document.getElementById("player-search-btn");
const playlistsPanel = document.getElementById("player-playlists");
const playlistsList = document.getElementById("player-playlists-list");
const browsePanel = document.getElementById("player-browse");
const browseForm = document.getElementById("player-browse-form");
const browseInput = document.getElementById("player-browse-input");
const browseResults = document.getElementById("player-browse-results");

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

function writeState(closed, resume) {
  if (closedWritten) return;
  if (closed) closedWritten = true;
  const playing = player && player.getPlayerState ? player.getPlayerState() === 1 : false;
  localStorage.setItem(STATE_KEY, JSON.stringify({
    ts: Date.now(),
    // The playlists/search buttons can extend this window's own queue past what the main window
    // booted it with (picking a new video/playlist here isn't just walking next/previous) - send
    // the queue itself back too, not just an index into the main window's now-stale copy of it.
    queue,
    index,
    time: player && player.getCurrentTime ? player.getCurrentTime() || 0 : 0,
    playing,
    volume: Number(volumeSlider.value),
    closed: !!closed,
    resume: resume !== false,
  }));
}

function closeSelf(resume) {
  writeState(true, resume);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.player_close) {
    window.pywebview.api.player_close();
  } else {
    window.close();
  }
}
// Called from Python (run_app.py's window.events.closing) as a fallback handoff - see the
// top-of-file comment for why this exists alongside closeSelf()/pagehide.
window.__lykoPlayerHandoff = () => writeState(true, true);

dockBtn.addEventListener("click", () => closeSelf(true));
closeBtn.addEventListener("click", () => closeSelf(false));

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
  // Plain style.display, not .hidden - see youtube-player.js's matching comment.
  fullscreenIconExpand.style.display = fullscreenActive ? "none" : "";
  fullscreenIconCompress.style.display = fullscreenActive ? "" : "none";
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

// Playlists/search picks a video directly in THIS window's own queue - there's no main-window
// loadYoutubeVideo/loadYoutubePlaylist to call from here, this window owns playback on its own.
function loadVideo(videoId, title, channel) {
  queue = queue.slice(0, index + 1);
  queue.push({ videoId, title, channel });
  index = queue.length - 1;
  updateChrome();
  if (player && player.loadVideoById) player.loadVideoById({ videoId, startSeconds: 0 });
}
function loadPlaylist(videos, title) {
  if (!videos || !videos.length) return;
  queue = queue.slice(0, index + 1);
  for (const video of videos) {
    queue.push({ videoId: video.video_id, title: video.title, channel: video.channel });
  }
  index = queue.length - videos.length;
  updateChrome();
  const first = queue[index];
  if (player && player.loadVideoById) player.loadVideoById({ videoId: first.videoId, startSeconds: 0 });
}

// Renders a list of {title, ...} items, each row itself clickable to play - mirrors
// youtube-player.js's ytRenderResultsList (same row shape, different data source).
function renderResultsList(container, items, emptyText, onPlay) {
  if (!items.length) {
    container.innerHTML = `<div class="youtube-player-list-empty">${emptyText}</div>`;
    return;
  }
  container.innerHTML = "";
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "youtube-player-list-item";
    row.title = `Play '${item.title}'`;
    row.addEventListener("click", () => onPlay(item));
    const name = document.createElement("span");
    name.className = "youtube-player-list-name";
    name.textContent = item.title;
    row.appendChild(name);
    container.appendChild(row);
  }
}

function closeListPanels() {
  playlistsPanel.hidden = true;
  browsePanel.hidden = true;
}

// Only shown once the user has connected their YouTube account (Settings -> API Keys -> YouTube),
// mirrors youtube-player.js's ytRefreshPlaylistsButtonVisibility.
async function refreshPlaylistsButtonVisibility() {
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    playlistsBtn.hidden = !cfg.youtube_connected;
  } catch (err) {
    playlistsBtn.hidden = true;
  }
}
refreshPlaylistsButtonVisibility();

async function openPlaylistsPanel() {
  browsePanel.hidden = true;
  playlistsPanel.hidden = false;
  playlistsList.innerHTML = '<div class="youtube-player-list-empty">Loading…</div>';
  let playlists = [];
  try {
    const data = await fetch("/api/youtube/oauth/playlists").then((r) => r.json());
    playlists = data.playlists || [];
  } catch (err) {
    playlistsList.innerHTML = '<div class="youtube-player-list-empty">Couldn\'t load playlists.</div>';
    return;
  }
  renderResultsList(playlistsList, playlists, "No playlists found.", async (playlist) => {
    let videos = [];
    try {
      const data = await fetch(`/api/youtube/oauth/playlists/${encodeURIComponent(playlist.id)}/videos`).then((r) => r.json());
      videos = data.videos || [];
    } catch (err) {
      return;
    }
    if (!videos.length) return;
    loadPlaylist(videos, playlist.title);
    closeListPanels();
  });
}
playlistsBtn.addEventListener("click", () => {
  if (playlistsPanel.hidden) openPlaylistsPanel();
  else closeListPanels();
});

function openBrowsePanel() {
  playlistsPanel.hidden = true;
  browsePanel.hidden = false;
  browseInput.focus();
}
searchBtn.addEventListener("click", () => {
  if (browsePanel.hidden) openBrowsePanel();
  else closeListPanels();
});

browseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = browseInput.value.trim();
  if (!query) return;
  browseResults.innerHTML = '<div class="youtube-player-list-empty">Searching…</div>';
  let results = [];
  try {
    const data = await fetch(`/api/youtube/search?q=${encodeURIComponent(query)}`).then((r) => r.json());
    results = data.results || [];
  } catch (err) {
    browseResults.innerHTML = '<div class="youtube-player-list-empty">Search failed.</div>';
    return;
  }
  renderResultsList(browseResults, results, "No results found.", async (result) => {
    closeListPanels();
    let payload = { video_id: result.video_id, title: result.title };
    try {
      const url = `/api/youtube/mix?video_id=${encodeURIComponent(result.video_id)}&title=${encodeURIComponent(result.title)}`;
      payload = await fetch(url).then((r) => r.json());
    } catch (err) { /* fall back to just the picked video below */ }
    if (payload.videos) loadPlaylist(payload.videos, payload.title);
    else loadVideo(payload.video_id || result.video_id, payload.title || result.title);
  });
});

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
  // Plain style.display, not .hidden - see youtube-player.js's matching comment.
  playIcon.style.display = playing ? "none" : "";
  pauseIcon.style.display = playing ? "" : "none";
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
