const YOUTUBE_PANEL_POS_KEY = "lyko-youtube-panel-pos";

const ytPanel = document.getElementById("youtube-player-panel");
const ytPanelHeader = document.getElementById("youtube-player-panel-header");
const ytPanelClose = document.getElementById("youtube-player-panel-close");
const ytPanelTitle = document.getElementById("youtube-player-title");
const ytFrameMount = document.getElementById("youtube-player-frame-mount");
const ytPrevBtn = document.getElementById("youtube-player-prev");
const ytPlayPauseBtn = document.getElementById("youtube-player-playpause");
const ytNextBtn = document.getElementById("youtube-player-next");

let ytPlayer = null;
let ytApiReady = false;
// Every play_on_youtube call this session, so next/previous have something to move through -
// there's no real YouTube playlist involved, just local history.
let ytQueue = [];
let ytQueueIndex = -1;

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

function ytPlayQueueEntry(entry) {
  if (!ytApiReady || !entry) return;
  if (ytPlayer && ytPlayer.loadVideoById) {
    ytPlayer.loadVideoById(entry.videoId);
    return;
  }
  ytPlayer = new YT.Player(ytFrameMount, {
    videoId: entry.videoId,
    playerVars: { autoplay: 1, rel: 0 },
  });
}

function showYoutubePanel(title) {
  ytPanelTitle.textContent = title || "YouTube";
  ytPanel.hidden = false;
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

// Called from chat-stream.js for control_youtube_player tool calls (SSE `youtube_control` event /
// non-streaming `youtube_control` response field) and by the panel's own buttons.
window.controlYoutubePlayer = function (action, volume) {
  const currentTitle = ytQueueIndex >= 0 ? ytQueue[ytQueueIndex].title : undefined;
  switch (action) {
    case "play":
      // "stop" hides the panel and fully unloads the player - resuming (which, per the YouTube
      // IFrame API, restarts a stopped video from 0:00 since stopVideo() doesn't just pause) must
      // re-show it, or the video plays audibly with no panel visible anywhere.
      if (ytPlayer) showYoutubePanel(currentTitle);
      if (ytPlayer && ytPlayer.playVideo) ytPlayer.playVideo();
      break;
    case "pause":
      if (ytPlayer && ytPlayer.pauseVideo) ytPlayer.pauseVideo();
      break;
    case "restart":
      if (ytPlayer) showYoutubePanel(currentTitle);
      if (ytPlayer && ytPlayer.seekTo) {
        ytPlayer.seekTo(0, true);
        ytPlayer.playVideo();
      }
      break;
    case "next":
      if (ytQueueIndex < ytQueue.length - 1) {
        ytQueueIndex += 1;
        showYoutubePanel(ytQueue[ytQueueIndex].title);
        ytPlayQueueEntry(ytQueue[ytQueueIndex]);
      }
      break;
    case "previous":
      if (ytQueueIndex > 0) {
        ytQueueIndex -= 1;
        showYoutubePanel(ytQueue[ytQueueIndex].title);
        ytPlayQueueEntry(ytQueue[ytQueueIndex]);
      }
      break;
    case "stop":
      if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();
      hideYoutubePanel();
      break;
    case "set_volume":
      if (ytPlayer && ytPlayer.setVolume && Number.isFinite(volume)) {
        ytPlayer.setVolume(Math.min(100, Math.max(0, volume)));
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

// Restore a dragged position, or fall back to the default bottom-right CSS anchor - mirrors
// game-state.js's floating panel exactly (same corrupt/off-screen guards).
(() => {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(YOUTUBE_PANEL_POS_KEY) || "null");
  } catch (err) { /* corrupt entry - fall through to the CSS anchor */ }
  if (saved && Number.isFinite(saved.top) && Number.isFinite(saved.left)) {
    const maxLeft = Math.max(0, window.innerWidth - 320);  // panel CSS width
    const maxTop = Math.max(0, window.innerHeight - 40);   // keep at least the header on-screen
    ytPanel.style.top = `${Math.min(Math.max(0, saved.top), maxTop)}px`;
    ytPanel.style.left = `${Math.min(Math.max(0, saved.left), maxLeft)}px`;
    ytPanel.style.right = "auto";
    ytPanel.style.bottom = "auto";
  } else if (saved !== null) {
    localStorage.removeItem(YOUTUBE_PANEL_POS_KEY);
  }
})();

(() => {
  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;

  ytPanelHeader.addEventListener("mousedown", (event) => {
    if (event.target.closest(".floating-panel-close")) return;
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
    const top = parseFloat(ytPanel.style.top);
    const left = parseFloat(ytPanel.style.left);
    // A click on the header without any movement never sets style.top/left - only persist an
    // actual dragged position (see game-state.js's identical guard for why).
    if (Number.isFinite(top) && Number.isFinite(left)) {
      localStorage.setItem(YOUTUBE_PANEL_POS_KEY, JSON.stringify({ top, left }));
    }
  });
})();
