const YOUTUBE_PANEL_POS_KEY = "lyko-youtube-panel-pos";
const YOUTUBE_VOLUME_KEY = "lyko-youtube-volume";
const YOUTUBE_DOCKED_KEY = "lyko-youtube-docked";
const YOUTUBE_COLLAPSED_KEY = "lyko-youtube-collapsed";

const ytPanel = document.getElementById("youtube-player-panel");
const ytPanelHeader = document.getElementById("youtube-player-panel-header");
const ytPanelClose = document.getElementById("youtube-player-panel-close");
const ytPanelPin = document.getElementById("youtube-player-panel-pin");
const ytPanelToggle = document.getElementById("youtube-player-panel-toggle");
const ytPanelPlaylistsBtn = document.getElementById("youtube-player-panel-playlists");
const ytPanelSearchBtn = document.getElementById("youtube-player-panel-search");
const ytPlaylistsPanel = document.getElementById("youtube-player-playlists");
const ytPlaylistsList = document.getElementById("youtube-player-playlists-list");
const ytBrowsePanel = document.getElementById("youtube-player-browse");
const ytBrowseForm = document.getElementById("youtube-player-browse-form");
const ytBrowseInput = document.getElementById("youtube-player-browse-input");
const ytBrowseResults = document.getElementById("youtube-player-browse-results");
const ytPanelTitle = document.getElementById("youtube-player-title");
const ytFrameMount = document.getElementById("youtube-player-frame-mount");
const ytPrevBtn = document.getElementById("youtube-player-prev");
const ytPlayPauseBtn = document.getElementById("youtube-player-playpause");
const ytNextBtn = document.getElementById("youtube-player-next");
const ytPrevLabel = document.getElementById("youtube-player-prev-label");
const ytNextLabel = document.getElementById("youtube-player-next-label");
const ytVolumeSlider = document.getElementById("youtube-player-volume");
const ytSeekBar = document.getElementById("youtube-player-seekbar");
const ytTimeCurrent = document.getElementById("youtube-player-time-current");
const ytTimeDuration = document.getElementById("youtube-player-time-duration");

let ytPlayer = null;
let ytApiReady = false;
// Every play_on_youtube call this session, so next/previous have something to move through -
// there's no real YouTube playlist involved, just local history.
let ytQueue = [];
let ytQueueIndex = -1;
// True while the user is dragging the seek bar - suspends the poll below from fighting the drag.
let ytSeeking = false;
let ytDocked = false;
let ytCollapsed = false;

function ytGetSavedVolume() {
  const saved = Number(localStorage.getItem(YOUTUBE_VOLUME_KEY));
  return Number.isFinite(saved) && saved >= 0 && saved <= 100 ? saved : 100;
}
ytVolumeSlider.value = ytGetSavedVolume();

function ytFormatTime(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

// Polls current time/duration instead of relying on an IFrame API event - the API has no
// "timeupdate" event of its own, unlike an HTML5 <video> element.
setInterval(() => {
  if (ytSeeking || !ytPlayer || !ytPlayer.getCurrentTime || ytPanel.hidden) return;
  const duration = ytPlayer.getDuration() || 0;
  const current = ytPlayer.getCurrentTime() || 0;
  ytSeekBar.max = duration;
  ytSeekBar.value = current;
  ytTimeCurrent.textContent = ytFormatTime(current);
  ytTimeDuration.textContent = ytFormatTime(duration);
}, 500);

ytSeekBar.addEventListener("input", () => {
  ytSeeking = true;
  ytTimeCurrent.textContent = ytFormatTime(Number(ytSeekBar.value));
});
ytSeekBar.addEventListener("change", () => {
  if (ytPlayer && ytPlayer.seekTo) ytPlayer.seekTo(Number(ytSeekBar.value), true);
  ytSeeking = false;
});

function ytLoadApiOnce() {
  if (window.YT && window.YT.Player) {
    ytApiReady = true;
    return;
  }
  if (document.getElementById("youtube-iframe-api-script")) return;
  const tag = document.createElement("script");
  tag.id = "youtube-iframe-api-script";
  tag.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(tag);
}

// Called by the IFrame API script itself once it finishes loading - must be a global.
window.onYouTubeIframeAPIReady = function () {
  ytApiReady = true;
  if (ytQueueIndex >= 0) ytPlayQueueEntry(ytQueue[ytQueueIndex]);
};

// Advances to the next queued video, if any - shared by the next button and auto-advance on end.
function ytAdvanceQueue() {
  if (ytQueueIndex < ytQueue.length - 1) {
    ytQueueIndex += 1;
    const entry = ytQueue[ytQueueIndex];
    showYoutubePanel(entry.title, entry.channel);
    ytPlayQueueEntry(entry);
  }
}

function ytPlayQueueEntry(entry) {
  if (!ytApiReady || !entry) return;
  ytSeekBar.value = 0;
  ytTimeCurrent.textContent = "0:00";
  ytTimeDuration.textContent = "0:00";
  if (ytPlayer && ytPlayer.loadVideoById) {
    ytPlayer.loadVideoById(entry.videoId);
    return;
  }
  ytPlayer = new YT.Player(ytFrameMount, {
    videoId: entry.videoId,
    // controls: 0 hides YouTube's own chrome - its native volume slider pops out below the
    // icon and, in our small floating panel, the pop-out sits outside the iframe's own bounds,
    // so moving the mouse toward it crosses into our page and the iframe fires a mouseout that
    // closes the slider before it can be dragged. A custom slider (below) avoids this entirely.
    playerVars: { autoplay: 1, rel: 0, controls: 0 },
    events: {
      onReady: (event) => {
        event.target.setVolume(ytGetSavedVolume());
      },
      // The IFrame API has no native "autoplay next in playlist" for an ad-hoc video-by-id queue
      // (that's only automatic for a real YouTube playlist load) - ENDED must be handled manually
      // or playback just stops after one video despite a queued Mix.
      onStateChange: (event) => {
        if (event.data === YT.PlayerState.ENDED) ytAdvanceQueue();
      },
      // Without this, a video that fails to play (embedding disabled, removed, region-locked)
      // just sits there silently - no error ever surfaced anywhere, so it looked identical to the
      // app doing nothing. YouTube's own error codes: https://developers.google.com/youtube/iframe_api_reference#onError
      onError: (event) => {
        const messages = {
          2: "Invalid video.",
          5: "This video can't be played in this player.",
          100: "Video not found - it may have been removed or made private.",
          101: "This video's owner has disabled playback in embedded players.",
          150: "This video's owner has disabled playback in embedded players.",
        };
        const message = messages[event.data] || "Playback error.";
        console.error("YouTube player error", event.data, entry.videoId);
        ytPanelTitle.textContent = message;
      },
    },
  });
}

// Truncation is CSS (max-width + ellipsis) - this just decides whether there's a neighbor at all,
// so the label can be hidden entirely (no stray "Previous:"/"Up next:" with nothing after it) at
// the start/end of the queue.
// Plain-text "Title — Channel" used by the small prev/next preview labels (no color styling
// there, just showYoutubePanel's main title gets the accent-colored channel span below).
function ytCombinedTitle(entry) {
  return entry.channel ? `${entry.title} — ${entry.channel}` : entry.title;
}

function ytUpdateQueueLabels() {
  const prev = ytQueueIndex > 0 ? ytQueue[ytQueueIndex - 1] : null;
  const next = ytQueueIndex >= 0 && ytQueueIndex < ytQueue.length - 1 ? ytQueue[ytQueueIndex + 1] : null;
  ytPrevLabel.textContent = prev ? `Previous: ${ytCombinedTitle(prev)}` : "";
  ytNextLabel.textContent = next ? `Up next: ${ytCombinedTitle(next)}` : "";
}

// channel (optional) renders in the accent color, separate from the plain-colored title - callers
// that don't have a channel (single-video plays, error messages) just omit it.
function showYoutubePanel(title, channel) {
  ytPanelTitle.textContent = "";
  if (!title) {
    ytPanelTitle.textContent = "YouTube";
  } else if (channel) {
    ytPanelTitle.appendChild(document.createTextNode(`${title} — `));
    const channelSpan = document.createElement("span");
    channelSpan.className = "youtube-player-title-channel";
    channelSpan.textContent = channel;
    ytPanelTitle.appendChild(channelSpan);
  } else {
    ytPanelTitle.textContent = title;
  }
  ytPanel.hidden = false;
  ytRefreshPlaylistsButtonVisibility();
  ytUpdateQueueLabels();
}

function hideYoutubePanel() {
  ytPanel.hidden = true;
}

// Called from chat-stream.js when a play_on_youtube tool call resolves (SSE `youtube_play` event
// / non-streaming `youtube_play` response field) - loads the video into the in-app player instead
// of opening a browser tab.
window.loadYoutubeVideo = function (videoId, title) {
  if (!videoId) return;
  ytQueue = ytQueue.slice(0, ytQueueIndex + 1);
  ytQueue.push({ videoId, title });
  ytQueueIndex = ytQueue.length - 1;
  showYoutubePanel(title);
  ytLoadApiOnce();
  ytPlayQueueEntry(ytQueue[ytQueueIndex]);
};

// Called from chat-stream.js when a play_youtube_playlist tool call resolves (SSE
// `youtube_playlist` event / non-streaming response field) - queues every video in the playlist
// (each { video_id, title, channel }) and starts playing the first one, so next/previous walk the
// real playlist instead of just this session's ad-hoc play history.
window.loadYoutubePlaylist = function (videos, title) {
  if (!videos || !videos.length) return;
  ytQueue = ytQueue.slice(0, ytQueueIndex + 1);
  for (const video of videos) {
    ytQueue.push({ videoId: video.video_id, title: video.title, channel: video.channel });
  }
  ytQueueIndex = ytQueue.length - videos.length;
  // Show the first video's own title (falling back to the playlist name only if it's somehow
  // empty) instead of the playlist name - next/previous already show each video's own title, so
  // starting playback showed something different from every subsequent track.
  const first = ytQueue[ytQueueIndex];
  showYoutubePanel(first.title || title, first.channel);
  ytLoadApiOnce();
  ytPlayQueueEntry(ytQueue[ytQueueIndex]);
};

// Called from chat-stream.js for control_youtube_player tool calls (SSE `youtube_control` event /
// non-streaming `youtube_control` response field) and by the panel's own buttons.
window.controlYoutubePlayer = function (action, volume) {
  const currentEntry = ytQueueIndex >= 0 ? ytQueue[ytQueueIndex] : null;
  switch (action) {
    case "play":
      // "stop" hides the panel and fully unloads the player - resuming (which, per the YouTube
      // IFrame API, restarts a stopped video from 0:00 since stopVideo() doesn't just pause) must
      // re-show it, or the video plays audibly with no panel visible anywhere.
      if (ytPlayer && currentEntry) showYoutubePanel(currentEntry.title, currentEntry.channel);
      if (ytPlayer && ytPlayer.playVideo) ytPlayer.playVideo();
      break;
    case "pause":
      if (ytPlayer && ytPlayer.pauseVideo) ytPlayer.pauseVideo();
      break;
    case "restart":
      if (ytPlayer && currentEntry) showYoutubePanel(currentEntry.title, currentEntry.channel);
      if (ytPlayer && ytPlayer.seekTo) {
        ytPlayer.seekTo(0, true);
        ytPlayer.playVideo();
      }
      break;
    case "next":
      ytAdvanceQueue();
      break;
    case "previous":
      if (ytQueueIndex > 0) {
        ytQueueIndex -= 1;
        const entry = ytQueue[ytQueueIndex];
        showYoutubePanel(entry.title, entry.channel);
        ytPlayQueueEntry(entry);
      }
      break;
    case "stop":
      if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();
      hideYoutubePanel();
      break;
    case "set_volume":
      if (ytPlayer && ytPlayer.setVolume && Number.isFinite(volume)) {
        const clamped = Math.min(100, Math.max(0, volume));
        ytPlayer.setVolume(clamped);
        if (ytVolumeSlider) ytVolumeSlider.value = clamped;
        localStorage.setItem(YOUTUBE_VOLUME_KEY, String(clamped));
      }
      break;
  }
};

ytPanelClose.addEventListener("click", () => window.controlYoutubePlayer("stop"));
ytPrevBtn.addEventListener("click", () => window.controlYoutubePlayer("previous"));
ytNextBtn.addEventListener("click", () => window.controlYoutubePlayer("next"));
ytPlayPauseBtn.addEventListener("click", () => {
  if (!ytPlayer || !ytPlayer.getPlayerState) return;
  const PLAYING = 1;
  window.controlYoutubePlayer(ytPlayer.getPlayerState() === PLAYING ? "pause" : "play");
});
ytVolumeSlider.addEventListener("input", () => {
  const value = Number(ytVolumeSlider.value);
  if (ytPlayer && ytPlayer.setVolume) ytPlayer.setVolume(value);
  localStorage.setItem(YOUTUBE_VOLUME_KEY, String(value));
});

const ytResizeGrip = document.getElementById("youtube-player-resize-grip");
const YT_PANEL_MIN_WIDTH = 240;
const YT_PANEL_MAX_WIDTH = 640;
const YT_PANEL_DEFAULT_WIDTH = 320;

function ytSavePanelState() {
  // Read the actual rendered position (not style.top/left, which stay unset while the panel is
  // still sitting at its default bottom-right CSS anchor) so a resize-only interaction - no drag
  // ever happened - still has something concrete to persist alongside the new width.
  const rect = ytPanel.getBoundingClientRect();
  localStorage.setItem(
    YOUTUBE_PANEL_POS_KEY,
    JSON.stringify({ top: rect.top, left: rect.left, width: ytPanel.offsetWidth })
  );
}

// Restore a dragged position + resized width, or fall back to the default bottom-right CSS
// anchor - mirrors game-state.js's floating panel exactly (same corrupt/off-screen guards).
// Reused whenever the panel goes back to floating mode after being docked, since docking clears
// these inline styles (they'd otherwise outrank the docked CSS class, which is only a class
// selector and inline styles always win regardless of specificity).
function ytApplyFloatingPosition() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(YOUTUBE_PANEL_POS_KEY) || "null");
  } catch (err) { /* corrupt entry - fall through to the CSS anchor */ }
  if (saved && Number.isFinite(saved.top) && Number.isFinite(saved.left)) {
    const width = Number.isFinite(saved.width)
      ? Math.min(Math.max(YT_PANEL_MIN_WIDTH, saved.width), YT_PANEL_MAX_WIDTH)
      : YT_PANEL_DEFAULT_WIDTH;
    const maxLeft = Math.max(0, window.innerWidth - width);
    const maxTop = Math.max(0, window.innerHeight - 40);   // keep at least the header on-screen
    ytPanel.style.width = `${width}px`;
    ytPanel.style.top = `${Math.min(Math.max(0, saved.top), maxTop)}px`;
    ytPanel.style.left = `${Math.min(Math.max(0, saved.left), maxLeft)}px`;
    ytPanel.style.right = "auto";
    ytPanel.style.bottom = "auto";
  } else if (saved !== null) {
    localStorage.removeItem(YOUTUBE_PANEL_POS_KEY);
  } else {
    ytPanel.style.width = "";
    ytPanel.style.top = "";
    ytPanel.style.left = "";
    ytPanel.style.right = "";
    ytPanel.style.bottom = "";
  }
}
ytApplyFloatingPosition();

// Docked mode switches the panel from position:fixed to position:static so it sits in normal
// document flow right above #chat-log (chat-panel's flex column then shrinks the log to make
// room) instead of floating over the page. This is a CSS-only toggle - the panel element is
// permanently mounted in the same DOM spot (see index.html) and is never moved/reparented, since
// reparenting any ancestor of an <iframe> forces the browser to discard and reload it, which
// would restart the video on every dock/undock.
function ytSetDocked(docked) {
  ytDocked = docked;
  localStorage.setItem(YOUTUBE_DOCKED_KEY, docked ? "1" : "0");
  ytPanel.classList.toggle("youtube-player-panel--docked", docked);
  ytPanelPin.classList.toggle("active", docked);
  ytPanelPin.title = docked ? "Undock (float over page)" : "Dock above chat";
  if (docked) {
    // Inline styles left over from a drag/resize outrank the docked CSS class no matter its
    // specificity - clear them so the class's position/width actually take effect.
    ytPanel.style.top = "";
    ytPanel.style.left = "";
    ytPanel.style.right = "";
    ytPanel.style.bottom = "";
    ytPanel.style.width = "";
  } else {
    ytApplyFloatingPosition();
  }
}
ytPanelPin.addEventListener("click", () => ytSetDocked(!ytDocked));

// Collapsed mode hides the video frame only, keeping the seek bar/transport/volume controls -
// the iframe keeps playing audio while hidden via CSS, so this is a real "audio only" mode, not
// a pause. Works identically in docked or floating layout.
function ytSetCollapsed(collapsed) {
  ytCollapsed = collapsed;
  localStorage.setItem(YOUTUBE_COLLAPSED_KEY, collapsed ? "1" : "0");
  ytPanel.classList.toggle("youtube-player-panel--collapsed", collapsed);
  ytPanelToggle.title = collapsed ? "Expand video" : "Collapse video";
}
ytPanelToggle.addEventListener("click", () => {
  if (ytCollapsed) {
    // Expanding back to video - hide any open list panel (playlists/search) instead of letting
    // it fight the video for space, and drop the auto-collapse memory since we're now expanded
    // by explicit user action, not something ytCloseListPanels should later reverse.
    ytPlaylistsPanel.hidden = true;
    ytBrowsePanel.hidden = true;
    ytListAutoCollapsed = false;
  }
  ytSetCollapsed(!ytCollapsed);
});

// Defaults to docked+expanded unless the user has explicitly chosen otherwise.
ytSetDocked(localStorage.getItem(YOUTUBE_DOCKED_KEY) !== "0");
ytSetCollapsed(localStorage.getItem(YOUTUBE_COLLAPSED_KEY) === "1");

// Renders a list of {title, ...} items, each row itself clickable to play - shared by the
// playlists list and the search/browse results list below (same row shape, different data
// source/action).
function ytRenderResultsList(container, items, emptyText, onPlay) {
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

// Collapsing an expanded video to make room for a list panel (playlists or search/browse) is
// only undone on close if opening the list is what caused it - a collapse the user already chose
// beforehand is left alone.
let ytListAutoCollapsed = false;

function ytCloseListPanels() {
  ytPlaylistsPanel.hidden = true;
  ytBrowsePanel.hidden = true;
  if (ytListAutoCollapsed) ytSetCollapsed(false);
  ytListAutoCollapsed = false;
}

// --- Playlists button - only shown once the user has connected their YouTube account (Settings
// → API Keys → YouTube). Re-checked every time a video starts playing so connecting mid-session
// doesn't require a reload to reveal the button. ---
async function ytRefreshPlaylistsButtonVisibility() {
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    ytPanelPlaylistsBtn.hidden = !cfg.youtube_connected;
  } catch (err) {
    ytPanelPlaylistsBtn.hidden = true;
  }
}
ytRefreshPlaylistsButtonVisibility();

async function ytOpenPlaylistsPanel() {
  ytBrowsePanel.hidden = true;
  ytListAutoCollapsed = !ytCollapsed;
  if (!ytCollapsed) ytSetCollapsed(true);
  ytPlaylistsPanel.hidden = false;
  ytPlaylistsList.innerHTML = '<div class="youtube-player-list-empty">Loading…</div>';

  let playlists = [];
  try {
    const data = await fetch("/api/youtube/oauth/playlists").then((r) => r.json());
    playlists = data.playlists || [];
  } catch (err) {
    ytPlaylistsList.innerHTML = '<div class="youtube-player-list-empty">Couldn\'t load playlists.</div>';
    return;
  }

  ytRenderResultsList(ytPlaylistsList, playlists, "No playlists found.", async (playlist) => {
    let videos = [];
    try {
      const data = await fetch(`/api/youtube/oauth/playlists/${encodeURIComponent(playlist.id)}/videos`).then((r) => r.json());
      videos = data.videos || [];
    } catch (err) {
      return;
    }
    if (!videos.length) return;
    window.loadYoutubePlaylist(videos, playlist.title);
    ytCloseListPanels();
  });
}

ytPanelPlaylistsBtn.addEventListener("click", () => {
  if (ytPlaylistsPanel.hidden) ytOpenPlaylistsPanel();
  else ytCloseListPanels();
});

// --- Search/browse - lets the user find and play a video directly from the panel, without going
// through the LLM/play_on_youtube tool call. Opened either via its own toggle or automatically by
// the chat toolbar button when nothing has played yet this session. ---
function ytOpenBrowsePanel() {
  ytPlaylistsPanel.hidden = true;
  ytListAutoCollapsed = !ytCollapsed;
  if (!ytCollapsed) ytSetCollapsed(true);
  ytBrowsePanel.hidden = false;
  ytBrowseInput.focus();
}

ytPanelSearchBtn.addEventListener("click", () => {
  if (ytBrowsePanel.hidden) ytOpenBrowsePanel();
  else ytCloseListPanels();
});

ytBrowseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = ytBrowseInput.value.trim();
  if (!query) return;
  ytBrowseResults.innerHTML = '<div class="youtube-player-list-empty">Searching…</div>';

  let results = [];
  try {
    const data = await fetch(`/api/youtube/search?q=${encodeURIComponent(query)}`).then((r) => r.json());
    results = data.results || [];
  } catch (err) {
    ytBrowseResults.innerHTML = '<div class="youtube-player-list-empty">Search failed.</div>';
    return;
  }

  ytRenderResultsList(ytBrowseResults, results, "No results found.", ytPlaySearchResult);
});

// Same best-effort Mix lookup play_on_youtube already does server-side (media_tool.py's
// _build_player_payload), so picking a video here also auto-queues similar videos instead of
// stopping after just the one picked. loadYoutubeVideo/loadYoutubePlaylist each already call
// showYoutubePanel themselves.
async function ytPlaySearchResult(result) {
  ytCloseListPanels();
  let payload = { video_id: result.video_id, title: result.title };
  try {
    const url = `/api/youtube/mix?video_id=${encodeURIComponent(result.video_id)}&title=${encodeURIComponent(result.title)}`;
    payload = await fetch(url).then((r) => r.json());
  } catch (err) { /* fall back to just the picked video below */ }

  if (payload.videos) {
    window.loadYoutubePlaylist(payload.videos, payload.title);
  } else {
    window.loadYoutubeVideo(payload.video_id || result.video_id, payload.title || result.title);
  }
}

// Chat toolbar's YouTube button - opens the player even if nothing has played yet this session,
// dropping straight into search/browse in that case instead of showing an empty video frame.
document.getElementById("chat-toolbar-youtube").addEventListener("click", () => {
  const entry = ytQueueIndex >= 0 ? ytQueue[ytQueueIndex] : null;
  showYoutubePanel(entry ? entry.title : "YouTube", entry ? entry.channel : undefined);
  if (!entry) ytOpenBrowsePanel();
});

(() => {
  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;

  ytPanelHeader.addEventListener("mousedown", (event) => {
    if (ytDocked || event.target.closest("button")) return;
    dragging = true;
    const rect = ytPanel.getBoundingClientRect();
    offsetX = event.clientX - rect.left;
    offsetY = event.clientY - rect.top;
    event.preventDefault();
  });

  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const maxLeft = window.innerWidth - ytPanel.offsetWidth;
    const maxTop = window.innerHeight - ytPanel.offsetHeight;
    const left = Math.min(Math.max(0, event.clientX - offsetX), maxLeft);
    const top = Math.min(Math.max(0, event.clientY - offsetY), maxTop);
    ytPanel.style.left = `${left}px`;
    ytPanel.style.top = `${top}px`;
    ytPanel.style.right = "auto";
    ytPanel.style.bottom = "auto";
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    ytSavePanelState();
  });
})();

// Bottom-right corner grip resizes the panel width - the video frame's aspect-ratio CSS keeps
// height in lockstep, so only width needs to be tracked/persisted.
(() => {
  let resizing = false;
  let startWidth = 0;
  let startX = 0;

  ytResizeGrip.addEventListener("mousedown", (event) => {
    if (ytDocked) return;
    resizing = true;
    startWidth = ytPanel.offsetWidth;
    startX = event.clientX;
    event.preventDefault();
    event.stopPropagation();
  });

  window.addEventListener("mousemove", (event) => {
    if (!resizing) return;
    // Clamped so the panel can't grow past the right edge of the screen from wherever it
    // currently sits - Math.max keeps this from collapsing below the min width when there's
    // little room (e.g. panel already dragged close to the right edge).
    const maxWidth = Math.max(YT_PANEL_MIN_WIDTH, Math.min(YT_PANEL_MAX_WIDTH, window.innerWidth - ytPanel.offsetLeft));
    const width = Math.min(Math.max(YT_PANEL_MIN_WIDTH, startWidth + (event.clientX - startX)), maxWidth);
    ytPanel.style.width = `${width}px`;
  });

  window.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    ytSavePanelState();
  });
})();
