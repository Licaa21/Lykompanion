const YOUTUBE_VOLUME_KEY = "lyko-youtube-volume";
const YOUTUBE_COLLAPSED_KEY = "lyko-youtube-collapsed";
// Remembers what was playing across app restarts - the panel itself stays closed (hidden by
// default in index.html) until the toolbar button or a play_on_youtube tool call opens it; this
// only decides what shows up once it IS opened, it never auto-opens or auto-plays on load.
const YOUTUBE_QUEUE_KEY = "lyko-youtube-queue";

const ytPanel = document.getElementById("youtube-player-panel");
const ytPanelClose = document.getElementById("youtube-player-panel-close");
const ytPanelToggle = document.getElementById("youtube-player-panel-toggle");
const ytPanelFullscreenBtn = document.getElementById("youtube-player-panel-fullscreen");
const ytFullscreenIconExpand = ytPanel.querySelector(".youtube-player-fullscreen-icon-expand");
const ytFullscreenIconCompress = ytPanel.querySelector(".youtube-player-fullscreen-icon-compress");
const ytPanelPopoutBtn = document.getElementById("youtube-player-panel-popout");
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
const ytPlayIcon = ytPlayPauseBtn.querySelector(".youtube-player-playpause-icon-play");
const ytPauseIcon = ytPlayPauseBtn.querySelector(".youtube-player-playpause-icon-pause");
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
let ytCollapsed = false;
// True while the panel has been moved into a separate documentPictureInPicture window (see
// ytPanelPopoutBtn below).
let ytPoppedOut = false;
let ytPipWindow = null;
// True from page load until the restored entry (see ytLoadPersistedQueue below) actually gets a
// live player - tells the first ytPlayQueueEntry call for it to cue paused at the remembered
// position instead of autoplaying from 0, since the user hasn't asked for playback yet, only to
// see what was last playing.
let ytRestoredNeedsCue = false;
let ytRestoredTime = 0;

function ytGetSavedVolume() {
  const raw = localStorage.getItem(YOUTUBE_VOLUME_KEY);
  if (raw === null) return 100;
  const saved = Number(raw);
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
// --fill drives the gradient-filled portion of the track (see style.css) - kept in sync with the
// value here because CSS alone can't read a range input's position.
function ytSyncSeekFill() {
  const max = Number(ytSeekBar.max) || 0;
  const pct = max > 0 ? (Number(ytSeekBar.value) / max) * 100 : 0;
  ytSeekBar.style.setProperty("--fill", `${pct}%`);
}
function ytSyncVolumeFill() {
  ytVolumeSlider.style.setProperty("--fill", `${Number(ytVolumeSlider.value)}%`);
}
ytSyncVolumeFill();

setInterval(() => {
  if (ytSeeking || !ytPlayer || !ytPlayer.getCurrentTime || ytPanel.hidden) return;
  const duration = ytPlayer.getDuration() || 0;
  const current = ytPlayer.getCurrentTime() || 0;
  ytSeekBar.max = duration;
  ytSeekBar.value = current;
  ytSyncSeekFill();
  ytTimeCurrent.textContent = ytFormatTime(current);
  ytTimeDuration.textContent = ytFormatTime(duration);
  if (ytPlayer.getPlayerState) {
    const YT_PLAYING = 1;
    const playing = ytPlayer.getPlayerState() === YT_PLAYING;
    // Plain style.display, not .hidden - SVGElement's [hidden] reflection is unreliable across
    // WebView2/Chromium versions (observed both icons rendering at once despite one being
    // "hidden"), whereas an inline style always wins.
    ytPlayIcon.style.display = playing ? "none" : "";
    ytPauseIcon.style.display = playing ? "" : "none";
    // Drives the header equalizer bars and the play-orb ripple ring.
    ytPanel.classList.toggle("youtube-player-panel--playing", playing);
  }
}, 500);

ytSeekBar.addEventListener("input", () => {
  ytSeeking = true;
  ytSyncSeekFill();
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

// Consumes ytRestoredNeedsCue (if set) into a one-shot opts object for the next ytPlayQueueEntry
// call, so a restored-from-storage video cues paused at its remembered position instead of
// autoplaying - the flag must only fire once, the very first time that entry gets a live player.
function ytConsumeRestoreOpts() {
  if (!ytRestoredNeedsCue) return undefined;
  ytRestoredNeedsCue = false;
  return { startSeconds: ytRestoredTime, paused: true };
}

// Called by the IFrame API script itself once it finishes loading - must be a global.
window.onYouTubeIframeAPIReady = function () {
  ytApiReady = true;
  if (ytQueueIndex >= 0) ytPlayQueueEntry(ytQueue[ytQueueIndex], ytConsumeRestoreOpts());
};

// --- Remembers what was last playing across app restarts (ytQueue/ytQueueIndex are otherwise
// pure in-memory and reset on every reload). The panel itself never auto-opens from this - only
// the toolbar button and play_on_youtube tool calls open it (see showYoutubePanel callers) - this
// purely decides what's ready to resume once the user (or the LLM) does. ---
function ytSaveQueueState() {
  if (!ytQueue.length || ytQueueIndex < 0) return;
  // While popped out to the native window, this window's own ytPlayer is stopped (0 makes no
  // sense as "the position") - the pop-out's own heartbeat keeps ytQueueIndex in sync already
  // (see ytPipPoll), so just skip touching the persisted time until playback is back here.
  if (ytNativePopout) return;
  localStorage.setItem(YOUTUBE_QUEUE_KEY, JSON.stringify({
    queue: ytQueue,
    index: ytQueueIndex,
    time: ytPlayer && ytPlayer.getCurrentTime ? ytPlayer.getCurrentTime() || 0 : 0,
  }));
}

function ytLoadPersistedQueue() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(YOUTUBE_QUEUE_KEY) || "null");
  } catch (err) { /* corrupt entry - nothing to restore */ }
  if (!saved || !Array.isArray(saved.queue) || !saved.queue.length || !Number.isFinite(saved.index)) return;
  ytQueue = saved.queue;
  ytQueueIndex = Math.min(Math.max(0, saved.index), ytQueue.length - 1);
  ytRestoredTime = Number.isFinite(saved.time) ? saved.time : 0;
  ytRestoredNeedsCue = true;
}
ytLoadPersistedQueue();

setInterval(ytSaveQueueState, 3000);
window.addEventListener("pagehide", ytSaveQueueState);

// Advances to the next queued video, if any - shared by the next button and auto-advance on end.
function ytAdvanceQueue() {
  if (ytQueueIndex < ytQueue.length - 1) {
    ytQueueIndex += 1;
    const entry = ytQueue[ytQueueIndex];
    showYoutubePanel(entry.title, entry.channel);
    ytPlayQueueEntry(entry);
    ytSaveQueueState();
  }
}

// opts.startSeconds resumes mid-video (used when playback returns from the pop-out window);
// opts.paused cues the video at that position without autoplaying (pop-out was closed paused).
function ytPlayQueueEntry(entry, opts) {
  if (!ytApiReady || !entry) return;
  const startSeconds = opts && Number.isFinite(opts.startSeconds) ? opts.startSeconds : 0;
  const paused = !!(opts && opts.paused);
  ytSeekBar.value = 0;
  ytSeekBar.style.setProperty("--fill", "0%");
  ytTimeCurrent.textContent = "0:00";
  ytTimeDuration.textContent = "0:00";
  if (ytPlayer && ytPlayer.loadVideoById) {
    if (paused && ytPlayer.cueVideoById) {
      ytPlayer.cueVideoById({ videoId: entry.videoId, startSeconds });
    } else {
      ytPlayer.loadVideoById({ videoId: entry.videoId, startSeconds });
    }
    return;
  }
  ytPlayer = new YT.Player(ytFrameMount, {
    videoId: entry.videoId,
    // controls: 0 hides YouTube's own chrome - its native volume slider pops out below the
    // icon and, in our small floating panel, the pop-out sits outside the iframe's own bounds,
    // so moving the mouse toward it crosses into our page and the iframe fires a mouseout that
    // closes the slider before it can be dragged. A custom slider (below) avoids this entirely.
    // disablekb/fs/iv_load_policy/cc_load_policy/modestbranding strip as much of YouTube's own
    // embed chrome as the IFrame API actually allows - it still shows a hover overlay (title
    // card, share/related-videos bar, YouTube logo) that NONE of these params suppress; that's
    // blocked separately by ytVideoGuard below, which sits over the iframe and eats the mouse
    // hover before YouTube's own player ever sees it.
    playerVars: {
      autoplay: 1, rel: 0, controls: 0,
      disablekb: 1, fs: 0, iv_load_policy: 3, cc_load_policy: 0, modestbranding: 1,
    },
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
      // A video that fails to play (embedding disabled, removed, region-locked) leaves the
      // player permanently in an error state - getCurrentTime/getDuration never populate (seek
      // bar stuck at 0:00) and playVideo/pauseVideo/seekTo are no-ops on it, which looked
      // identical to the whole player being broken. Surface the message AND skip to the next
      // queued video automatically (mirrors the ENDED handler below), instead of leaving the
      // user stuck on a dead entry. YouTube's own error codes:
      // https://developers.google.com/youtube/iframe_api_reference#onError
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
        ytAdvanceQueue();
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
  if (ytPipWindow) ytPipWindow.close();
}

// Called from chat-stream.js when a play_on_youtube tool call resolves (SSE `youtube_play` event
// / non-streaming `youtube_play` response field) - loads the video into the in-app player instead
// of opening a browser tab.
window.loadYoutubeVideo = function (videoId, title) {
  if (!videoId) return;
  ytForceClosePopout();
  // A fresh explicit play always wins over whatever paused-resume was pending from a restored
  // session - there's nothing to "resume" into anymore once a new video is requested.
  ytRestoredNeedsCue = false;
  ytQueue = ytQueue.slice(0, ytQueueIndex + 1);
  ytQueue.push({ videoId, title });
  ytQueueIndex = ytQueue.length - 1;
  showYoutubePanel(title);
  ytLoadApiOnce();
  ytPlayQueueEntry(ytQueue[ytQueueIndex]);
  ytSaveQueueState();
};

// Called from chat-stream.js when a play_youtube_playlist tool call resolves (SSE
// `youtube_playlist` event / non-streaming response field) - queues every video in the playlist
// (each { video_id, title, channel }) and starts playing the first one, so next/previous walk the
// real playlist instead of just this session's ad-hoc play history.
window.loadYoutubePlaylist = function (videos, title) {
  if (!videos || !videos.length) return;
  ytForceClosePopout();
  ytRestoredNeedsCue = false;
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
  ytSaveQueueState();
};

// Called from chat-stream.js for control_youtube_player tool calls (SSE `youtube_control` event /
// non-streaming `youtube_control` response field) and by the panel's own buttons.
window.controlYoutubePlayer = function (action, volume) {
  // Playback lives in the pop-out window right now - relay the command there instead of driving
  // the (stopped) in-app player. "stop" also ends the pop-out session: the pop-out closes itself
  // on consuming it, and we must not treat that close as a "resume playback here" handoff.
  if (ytNativePopout) {
    ytPipSendCommand(action, volume);
    if (action === "stop") {
      ytEndNativePopout(null, false);
      hideYoutubePanel();
    }
    return;
  }
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
        ytSaveQueueState();
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
        if (ytVolumeSlider) {
          ytVolumeSlider.value = clamped;
          ytSyncVolumeFill();
        }
        localStorage.setItem(YOUTUBE_VOLUME_KEY, String(clamped));
      }
      break;
  }
};

// Music ducking: called by narration.js/voice.js whenever the companion is speaking or the user
// is speaking hands-free, so the game/companion voice isn't fighting music for attention. Applies
// directly to ytPlayer (bypassing the slider/localStorage) so the duck never overwrites the user's
// real saved volume. Only affects the in-app player - a popped-out native window relays volume back
// through YT_PIP_STATE_KEY (ytPipPoll, below) which would persist the ducked value as "saved volume",
// so ducking is skipped entirely while popped out (documented gap, not a bug: see CLAUDE.md).
let ytDucked = false;
let ytPreDuckVolume = null;
window.applyMusicDucking = function (active) {
  if (ytNativePopout || !ytPlayer || !ytPlayer.setVolume) return;
  if (active === ytDucked) return;
  if (active) {
    ytPreDuckVolume = ytPlayer.getVolume ? ytPlayer.getVolume() : Number(ytVolumeSlider.value);
    ytDucked = true;
    ytPlayer.setVolume(Math.round(ytPreDuckVolume * 0.1));
  } else {
    ytDucked = false;
    if (ytPreDuckVolume !== null) ytPlayer.setVolume(ytPreDuckVolume);
    ytPreDuckVolume = null;
  }
};

ytPanelClose.addEventListener("click", () => window.controlYoutubePlayer("stop"));
ytPrevBtn.addEventListener("click", () => window.controlYoutubePlayer("previous"));
ytNextBtn.addEventListener("click", () => window.controlYoutubePlayer("next"));
function ytTogglePlayPause() {
  if (!ytPlayer || !ytPlayer.getPlayerState) return;
  const PLAYING = 1;
  window.controlYoutubePlayer(ytPlayer.getPlayerState() === PLAYING ? "pause" : "play");
}
ytPlayPauseBtn.addEventListener("click", ytTogglePlayPause);
ytVolumeSlider.addEventListener("input", () => {
  const value = Number(ytVolumeSlider.value);
  if (ytPlayer && ytPlayer.setVolume) ytPlayer.setVolume(value);
  localStorage.setItem(YOUTUBE_VOLUME_KEY, String(value));
  ytSyncVolumeFill();
});

// Collapsed mode hides the video frame only, keeping the seek bar/transport/volume controls -
// the iframe keeps playing audio while hidden via CSS, so this is a real "audio only" mode, not
// a pause.
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

// Defaults to expanded unless the user has explicitly chosen otherwise.
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
  if (ytNativePopout) return; // playing in the pop-out window - nothing to show in-app
  const entry = ytQueueIndex >= 0 ? ytQueue[ytQueueIndex] : null;
  if (!entry) {
    showYoutubePanel("YouTube");
    ytOpenBrowsePanel();
    return;
  }
  showYoutubePanel(entry.title, entry.channel);
  // First time this session anyone's asked to see this entry (e.g. it was only just restored
  // from a previous session - see ytLoadPersistedQueue) - there's no live player for it yet, so
  // cue it (paused if it's a resumed one, per ytConsumeRestoreOpts) instead of showing an empty
  // frame. If a player already exists (already playing/paused from this session), leave it alone.
  if (!ytPlayer) {
    ytLoadApiOnce();
    if (ytApiReady) ytPlayQueueEntry(entry, ytConsumeRestoreOpts());
    // else: onYouTubeIframeAPIReady (above) creates it once the script tag finishes loading.
  }
});

// --- Fullscreen - the video fills the entire SCREEN, not just the app viewport. The CSS class
// makes the panel cover the page with overlay controls (header/seek/transport on scrims that
// auto-hide on idle); on top of that:
//   - Desktop app (pywebview): the frameless app window itself is resized to cover the whole
//     monitor (taskbar included) through the already-exposed window_set_bounds bridge - element
//     requestFullscreen inside WebView2 only fills the webview control, never the OS window,
//     which is why the first attempt still showed the taskbar. Previous bounds are restored on
//     exit and deliberately never persisted via window_save_bounds.
//   - Browser (dev): documentElement.requestFullscreen hides the browser chrome natively.
function ytIsFullscreen() {
  return ytPanel.classList.contains("youtube-player-panel--fullscreen");
}

let ytPrevWinBounds = null; // logical px, desktop-app path only
let ytIdleTimer = null;

function ytSetFullscreenState(active) {
  ytPanel.classList.toggle("youtube-player-panel--fullscreen", active);
  ytPanelFullscreenBtn.classList.toggle("active", active);
  ytPanelFullscreenBtn.title = active ? "Exit fullscreen" : "Fullscreen";
  // Plain style.display, not .hidden - see the matching comment in the poll loop above.
  ytFullscreenIconExpand.style.display = active ? "none" : "";
  ytFullscreenIconCompress.style.display = active ? "" : "none";
}

// Auto-hide the overlay chrome after a short idle - any mouse movement brings it back.
function ytPokeIdle() {
  if (!ytIsFullscreen()) return;
  ytPanel.classList.remove("youtube-player-panel--idle");
  clearTimeout(ytIdleTimer);
  ytIdleTimer = setTimeout(() => ytPanel.classList.add("youtube-player-panel--idle"), 2600);
}
document.addEventListener("mousemove", ytPokeIdle);

function ytEnterFullscreen() {
  ytSetFullscreenState(true);
  // .app-shell's overflow:hidden otherwise clips this position:fixed panel to app-shell's own
  // (shorter than viewport, thanks to the titlebar above it) box - see the CSS comment beside
  // body.yt-fullscreen-active for the full explanation of why fixed doesn't escape this on its own.
  document.body.classList.add("yt-fullscreen-active");
  ytPokeIdle();
  const bridge = window.pywebview && window.pywebview.api;
  if (bridge && bridge.window_set_bounds) {
    ytPrevWinBounds = { x: window.screenX, y: window.screenY, w: window.innerWidth, h: window.innerHeight };
    const r = window.devicePixelRatio || 1;
    // screen.availLeft/Top locate the CURRENT monitor's origin (so fullscreen lands on the
    // monitor the app is on, not always the primary); screen.width/height are its full size
    // including the taskbar area, which availWidth/Height would exclude.
    const ox = Number.isFinite(screen.availLeft) ? screen.availLeft : 0;
    const oy = Number.isFinite(screen.availTop) ? screen.availTop : 0;
    bridge.window_set_bounds(Math.round(ox * r), Math.round(oy * r), Math.round(screen.width * r), Math.round(screen.height * r));
  } else if (document.fullscreenEnabled && document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(() => {});
  }
}

function ytExitFullscreen() {
  ytSetFullscreenState(false);
  document.body.classList.remove("yt-fullscreen-active");
  ytPanel.classList.remove("youtube-player-panel--idle");
  clearTimeout(ytIdleTimer);
  const bridge = window.pywebview && window.pywebview.api;
  if (ytPrevWinBounds && bridge && bridge.window_set_bounds) {
    const b = ytPrevWinBounds;
    ytPrevWinBounds = null;
    const r = window.devicePixelRatio || 1;
    bridge.window_set_bounds(Math.round(b.x * r), Math.round(b.y * r), Math.round(b.w * r), Math.round(b.h * r));
  }
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

ytPanelFullscreenBtn.addEventListener("click", () => {
  if (ytIsFullscreen()) ytExitFullscreen();
  else ytEnterFullscreen();
});

// See the .youtube-player-video-guard CSS comment - this is what actually receives the mouse
// events that a cross-origin YouTube iframe would otherwise swallow entirely.
const ytVideoGuard = document.getElementById("youtube-player-video-guard");
ytVideoGuard.addEventListener("mousemove", ytPokeIdle);
// Stealing focus onto our own element on every press means the iframe never ends up holding
// keyboard focus in the first place, so Escape/other shortcuts always reach our own listeners.
ytVideoGuard.addEventListener("mousedown", () => ytVideoGuard.focus());
ytVideoGuard.addEventListener("click", ytTogglePlayPause);
ytVideoGuard.addEventListener("dblclick", () => {
  if (ytIsFullscreen()) ytExitFullscreen();
  else ytEnterFullscreen();
});

// Browser path: the user exiting native fullscreen themselves (Esc, F11) must also drop our
// overlay class, or the panel stays stretched over the page.
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && ytIsFullscreen() && !ytPrevWinBounds) ytExitFullscreen();
});

// Desktop path has no native fullscreen to intercept Esc for us.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && ytIsFullscreen()) ytExitFullscreen();
});

// --- Pop out to a real, separate OS window that can be dragged to another monitor. Two paths:
//
// Desktop app (window.pywebview): WebView2 has no documentPictureInPicture, so run_app.py exposes
// open_player_window() which spawns a second, normally-framed pywebview window loading
// web/player.html. Playback HANDS OFF to that window (this one stops); state travels through
// localStorage, which both windows share (same WebView2 profile): "boot" carries the queue +
// position out, a 1s heartbeat carries live position/index back, "cmd" relays LLM/tool control
// commands out, and a closed flag on the heartbeat ends the pop-out session - its "resume" flag
// decides whether playback picks back up in this panel (its "Return to app" dock button) or just
// stops there entirely (its X button).
//
// Browser (dev): Document Picture-in-Picture moves the panel's DOM node into an always-on-top
// PiP window without reloading the iframe. ---
const YT_PIP_BOOT_KEY = "lyko-yt-pip-boot";
const YT_PIP_STATE_KEY = "lyko-yt-pip-state";
const YT_PIP_CMD_KEY = "lyko-yt-pip-cmd";

let ytNativePopout = false;
let ytPipPollTimer = null;
let ytPipCmdSeq = 0;

const ytPanelHome = ytPanel.parentElement;
const ytPanelHomeNext = ytPanel.nextSibling;

function ytPipSendCommand(action, volume) {
  ytPipCmdSeq += 1;
  localStorage.setItem(YT_PIP_CMD_KEY, JSON.stringify({ seq: ytPipCmdSeq, action, volume }));
}

// Ends the native pop-out session. When resume=true (pop-out window was closed), playback picks
// back up in this panel at the position/index the heartbeat last reported.
function ytEndNativePopout(state, resume) {
  if (ytPipPollTimer) { clearInterval(ytPipPollTimer); ytPipPollTimer = null; }
  ytNativePopout = false;
  localStorage.removeItem(YT_PIP_STATE_KEY);
  localStorage.removeItem(YT_PIP_BOOT_KEY);
  if (!resume) return;
  const entry = ytQueueIndex >= 0 ? ytQueue[ytQueueIndex] : null;
  if (!entry) return;
  showYoutubePanel(entry.title, entry.channel);
  ytPlayQueueEntry(entry, {
    startSeconds: state && Number.isFinite(state.time) ? state.time : 0,
    paused: state ? !state.playing : false,
  });
  ytSaveQueueState();
}

function ytPipPoll() {
  let state = null;
  try {
    state = JSON.parse(localStorage.getItem(YT_PIP_STATE_KEY) || "null");
  } catch (err) { /* not written yet / corrupt - wait for the next heartbeat */ }
  if (!state) return;
  // Track the pop-out's queue/index/volume live, so a later resume (and the LLM's view of "what's
  // playing") stays correct without waiting for the close handoff. The queue itself is included,
  // not just an index into this window's own copy - the pop-out's playlists/search buttons can
  // extend ITS queue independently (a fresh pick, not just walking next/previous), which would
  // otherwise leave this side with a stale/shorter queue the new index doesn't line up with.
  if (Array.isArray(state.queue) && state.queue.length) {
    ytQueue = state.queue;
  }
  if (Number.isFinite(state.index) && ytQueue.length) {
    ytQueueIndex = Math.min(Math.max(0, state.index), ytQueue.length - 1);
  }
  if (Number.isFinite(state.volume)) {
    ytVolumeSlider.value = state.volume;
    ytSyncVolumeFill();
    localStorage.setItem(YOUTUBE_VOLUME_KEY, String(state.volume));
  }
  if (state.closed) {
    const resume = state.resume !== false;
    ytEndNativePopout(state, resume);
    if (!resume) hideYoutubePanel();
  }
}

async function ytOpenNativePopout() {
  if (ytQueueIndex < 0) return;
  if (ytIsFullscreen()) ytExitFullscreen();
  const time = ytPlayer && ytPlayer.getCurrentTime ? ytPlayer.getCurrentTime() || 0 : 0;
  const playing = ytPlayer && ytPlayer.getPlayerState ? ytPlayer.getPlayerState() === 1 : true;
  localStorage.removeItem(YT_PIP_STATE_KEY);
  localStorage.removeItem(YT_PIP_CMD_KEY);
  localStorage.setItem(YT_PIP_BOOT_KEY, JSON.stringify({
    queue: ytQueue,
    index: ytQueueIndex,
    time,
    playing,
    volume: Number(ytVolumeSlider.value),
  }));
  try {
    await window.pywebview.api.open_player_window();
  } catch (err) {
    localStorage.removeItem(YT_PIP_BOOT_KEY);
    return;
  }
  if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();
  ytNativePopout = true;
  ytPanel.hidden = true;
  ytPipPollTimer = setInterval(ytPipPoll, 700);
}

// A tool call starting NEW playback while popped out closes the pop-out and plays in-app -
// two windows fighting over who's playing is never what anyone wants.
function ytForceClosePopout() {
  if (!ytNativePopout) return;
  ytPipSendCommand("stop");
  ytEndNativePopout(null, false);
}

async function ytOpenDocPipPopout() {
  if (ytPipWindow) {
    ytPipWindow.close();
    return;
  }
  if (ytIsFullscreen()) ytExitFullscreen();
  let pip;
  try {
    pip = await documentPictureInPicture.requestWindow({
      width: Math.round(ytPanel.offsetWidth),
      height: Math.round(ytPanel.offsetHeight),
    });
  } catch (err) {
    return;
  }
  ytPipWindow = pip;
  // Clone every stylesheet into the pip window's (otherwise blank) document so the moved panel
  // keeps its styling - link stylesheets copy as a matching <link>, inline/constructed ones copy
  // their parsed rules as a fresh <style> (a <link> would need a re-fetch and may 404 for a
  // dynamically-created sheet with no href).
  for (const sheet of document.styleSheets) {
    if (sheet.href) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = sheet.href;
      pip.document.head.appendChild(link);
    } else {
      try {
        const style = document.createElement("style");
        style.textContent = [...sheet.cssRules].map((rule) => rule.cssText).join("\n");
        pip.document.head.appendChild(style);
      } catch (err) { /* inaccessible sheet (e.g. cross-origin) - skip it */ }
    }
  }
  pip.document.body.style.margin = "0";
  pip.document.body.style.background = "#0a0a12";
  pip.document.body.appendChild(ytPanel);
  ytPanel.classList.add("youtube-player-panel--popped-out");
  ytPoppedOut = true;
  ytPanelPopoutBtn.title = "Return to app";

  pip.addEventListener("pagehide", () => {
    ytPanel.classList.remove("youtube-player-panel--popped-out");
    ytPoppedOut = false;
    ytPipWindow = null;
    ytPanelPopoutBtn.title = "Pop out to a floating window";
    if (ytPanelHomeNext && ytPanelHomeNext.parentElement === ytPanelHome) {
      ytPanelHome.insertBefore(ytPanel, ytPanelHomeNext);
    } else {
      ytPanelHome.appendChild(ytPanel);
    }
  });
}

ytPanelPopoutBtn.addEventListener("click", () => {
  if (window.pywebview) {
    if (!ytNativePopout) ytOpenNativePopout();
  } else if ("documentPictureInPicture" in window) {
    ytOpenDocPipPopout();
  }
});

// window.pywebview may not be injected yet at script-eval time - decide the button's visibility
// once the page has fully loaded (same timing init.js relies on for body.desktop-app).
window.addEventListener("load", () => {
  if (!window.pywebview && !("documentPictureInPicture" in window)) {
    ytPanelPopoutBtn.hidden = true;
  }
});

