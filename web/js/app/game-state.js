const GAME_STATE_POS_KEY = "lyko-game-state-panel-pos";
const GAME_STATE_CLOSED_KEY = "lyko-game-state-panel-closed";

let gameStatePanelClosed = localStorage.getItem(GAME_STATE_CLOSED_KEY) === "true";
let lastTrackedProcess = null;

// Session selector state - updated by updateGameStatePanel on every poll
let _sessionProcess = null;
let _sessionActiveId = null;
let _sessionListOpen = false;

function closeSessionList() {
  gameStateSessionList.hidden = true;
  gameStateSessionChevron.classList.remove("open");
  _sessionListOpen = false;
}

async function openSessionList(focusNewInput = false) {
  await _renderSessionList();
  gameStateSessionList.hidden = false;
  gameStateSessionChevron.classList.add("open");
  _sessionListOpen = true;
  if (focusNewInput) _showNewSessionInput();
}

async function _renderSessionList() {
  if (!_sessionProcess) return;
  const sessions = await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}`).then((r) => r.json());
  gameStateSessionList.innerHTML = "";
  for (const session of sessions) {
    gameStateSessionList.appendChild(_makeSessionItem(session));
  }
}

function _makeSessionItem(session) {
  const item = document.createElement("div");
  item.className = "gs-session-item" + (session.session_id === _sessionActiveId ? " active" : "");

  const nameSpan = document.createElement("span");
  nameSpan.className = "gs-session-item-name";
  nameSpan.textContent = session.name;
  item.appendChild(nameSpan);

  const renameBtn = document.createElement("button");
  renameBtn.type = "button";
  renameBtn.className = "gs-session-rename-btn";
  renameBtn.title = "Rename";
  renameBtn.innerHTML = `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M8.5 1.5l2 2L4 10H2v-2L8.5 1.5z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>`;
  item.appendChild(renameBtn);

  nameSpan.addEventListener("click", async () => {
    if (session.session_id === _sessionActiveId) { closeSessionList(); return; }
    await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}/${session.session_id}/active`, { method: "PUT" });
    const data = await fetchGameState();
    updateGameStatePanel(data);
    closeSessionList();
  });

  renameBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const input = document.createElement("input");
    input.type = "text";
    input.className = "gs-session-item-name-input";
    input.value = session.name;
    item.replaceChild(input, nameSpan);
    input.focus();
    input.select();

    const restore = () => { if (item.contains(input)) item.replaceChild(nameSpan, input); };
    const save = async () => {
      const newName = input.value.trim();
      if (newName && newName !== session.name) {
        const res = await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}/${session.session_id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName }),
        });
        if (res.ok) {
          session.name = newName;
          nameSpan.textContent = newName;
          if (session.session_id === _sessionActiveId) gameStateSessionNameEl.textContent = newName;
        }
      }
      restore();
    };
    input.addEventListener("blur", save);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.removeEventListener("blur", save); restore(); }
    });
  });

  return item;
}

function _showNewSessionInput() {
  if (gameStateSessionList.querySelector(".gs-session-new-row")) {
    gameStateSessionList.querySelector(".gs-session-new-input")?.focus();
    return;
  }
  const row = document.createElement("div");
  row.className = "gs-session-new-row";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "gs-session-new-input";
  input.placeholder = "Session name…";
  row.appendChild(input);

  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "gs-session-new-confirm";
  confirmBtn.textContent = "Create";
  row.appendChild(confirmBtn);

  const create = async () => {
    const name = input.value.trim();
    if (!name) return;
    const res = await fetch(`/api/game-state/sessions/${encodeURIComponent(_sessionProcess)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (res.ok) {
      const data = await fetchGameState();
      updateGameStatePanel(data);
      closeSessionList();
    }
  };
  confirmBtn.addEventListener("click", create);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); create(); }
    if (e.key === "Escape") row.remove();
  });

  gameStateSessionList.appendChild(row);
  input.focus();
}

gameStateSessionToggle.addEventListener("click", () => {
  if (_sessionListOpen) closeSessionList();
  else openSessionList();
});

gameStateNewSessionBtn.addEventListener("click", () => {
  if (_sessionListOpen) _showNewSessionInput();
  else openSessionList(true);
});

document.addEventListener("click", (e) => {
  if (_sessionListOpen && !gameStatePanel.contains(e.target)) closeSessionList();
});

function renderGameStateFields(data) {
  gameStateFields.innerHTML = "";
  const rows = [
    ["Process", data.process],
    ...data.trackers.map((t) => [t.label, t.value]),
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "game-state-row";

    const labelEl = document.createElement("span");
    labelEl.className = "game-state-row-label";
    labelEl.textContent = label;
    row.appendChild(labelEl);

    // Multi-part values (a "Known Stats"-style field gathering several distinct facts) read as
    // an unreadable wall of text on one line - split them into a small list, one part per line,
    // instead. A single short value (most fields, most of the time) stays as plain inline text.
    const parts = (value || "").split(/\s*;\s*/).filter(Boolean);
    if (parts.length > 1) {
      const list = document.createElement("ul");
      list.className = "game-state-row-value game-state-row-value-list";
      for (const part of parts) {
        const item = document.createElement("li");
        item.textContent = part;
        list.appendChild(item);
      }
      row.appendChild(list);
    } else {
      const valueEl = document.createElement("span");
      valueEl.className = "game-state-row-value";
      valueEl.textContent = value || "(not seen yet)";
      row.appendChild(valueEl);
    }

    gameStateFields.appendChild(row);
  }
}

function renderGameStateStats(data) {
  gameStateStats.innerHTML = "";
  const rows = [
    ["Extraction calls", data.extraction_call_count],
    ["Extraction cost", `$${data.extraction_cost_usd.toFixed(4)}`],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "game-state-stats-row";
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const valueEl = document.createElement("span");
    valueEl.textContent = value;
    row.appendChild(labelEl);
    row.appendChild(valueEl);
    gameStateStats.appendChild(row);
  }
}

function updateGameStatePanel(data) {
  gameStatePanelDot.classList.remove("active", "idle");

  if (data.tracking && data.process !== lastTrackedProcess) {
    // A new tracking session started (first game, or switched games) - re-show the panel even
    // if the user previously dismissed it for a prior session.
    gameStatePanelClosed = false;
    localStorage.setItem(GAME_STATE_CLOSED_KEY, "false");
  }
  lastTrackedProcess = data.tracking ? data.process : null;

  if (!data.tracking) {
    gameStatePanel.hidden = true;
    gameStateSessionBar.hidden = true;
    closeSessionList();
    return;
  }

  // Update session bar (don't re-render the open list to avoid disrupting in-progress renames)
  _sessionProcess = data.process;
  _sessionActiveId = data.session_id;
  gameStateSessionBar.hidden = false;
  gameStateSessionNameEl.textContent = data.session_name || "Default";

  gameStatePanelDot.classList.add("active");
  gameStatePanelDot.title = `Tracking: ${data.process}`;
  renderGameStateFields(data);
  renderGameStateStats(data);
  gameStatePanel.hidden = gameStatePanelClosed;
}

async function fetchGameState() {
  const response = await fetch("/api/game-state");
  return response.json();
}

gameStatePanelCloseBtn.addEventListener("click", () => {
  gameStatePanelClosed = true;
  localStorage.setItem(GAME_STATE_CLOSED_KEY, "true");
  gameStatePanel.hidden = true;
});

// Restore a dragged position, or fall back to the default top-right CSS anchor. A saved
// position is only applied when it's valid finite numbers, clamped into the current viewport -
// a corrupt entry (a click-without-drag used to persist {top: null, left: null}) or a window
// that shrank since the drag would otherwise strand the panel off-screen while "visible".
(() => {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(GAME_STATE_POS_KEY) || "null");
  } catch (err) { /* corrupt entry - fall through to the CSS anchor */ }
  if (saved && Number.isFinite(saved.top) && Number.isFinite(saved.left)) {
    const maxLeft = Math.max(0, window.innerWidth - 260);  // panel CSS width
    const maxTop = Math.max(0, window.innerHeight - 40);   // keep at least the header on-screen
    gameStatePanel.style.top = `${Math.min(Math.max(0, saved.top), maxTop)}px`;
    gameStatePanel.style.left = `${Math.min(Math.max(0, saved.left), maxLeft)}px`;
    gameStatePanel.style.right = "auto";
  } else if (saved !== null) {
    localStorage.removeItem(GAME_STATE_POS_KEY);
  }
})();

(() => {
  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;

  gameStatePanelHeader.addEventListener("mousedown", (event) => {
    if (event.target.closest(".floating-panel-close")) return;
    dragging = true;
    const rect = gameStatePanel.getBoundingClientRect();
    offsetX = event.clientX - rect.left;
    offsetY = event.clientY - rect.top;
    event.preventDefault();
  });

  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const maxLeft = window.innerWidth - gameStatePanel.offsetWidth;
    const maxTop = window.innerHeight - gameStatePanel.offsetHeight;
    const left = Math.min(Math.max(0, event.clientX - offsetX), maxLeft);
    const top = Math.min(Math.max(0, event.clientY - offsetY), maxTop);
    gameStatePanel.style.left = `${left}px`;
    gameStatePanel.style.top = `${top}px`;
    gameStatePanel.style.right = "auto";
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    const top = parseFloat(gameStatePanel.style.top);
    const left = parseFloat(gameStatePanel.style.left);
    // A click on the header without any movement never sets style.top/left - parseFloat("")
    // is NaN, which used to get persisted as {top: null, left: null} and strand the panel
    // off-screen on every subsequent load. Only persist an actual dragged position.
    if (Number.isFinite(top) && Number.isFinite(left)) {
      localStorage.setItem(GAME_STATE_POS_KEY, JSON.stringify({ top, left }));
    }
  });
})();

// Fast enough that the panel feels immediate when tracking starts/stops (the backend itself
// flips tracking via game_state.start_tracking() the moment it notices, via game_state_extraction.
// _capture_tick()) without hammering the endpoint - a slow fixed interval here would reintroduce
// the "state changed, but the panel doesn't show it yet" lag even though the backend is instant.
const GAME_STATE_PANEL_POLL_MS = 2000;

fetchGameState().then(updateGameStatePanel).catch(() => {});
setInterval(() => {
  fetchGameState().then(updateGameStatePanel).catch(() => {});
}, GAME_STATE_PANEL_POLL_MS);

// --- Game-state process blacklist / whitelist (Settings > General) ---

const gameStateBlacklistEl = document.getElementById("game-state-blacklist");
const gameStateBlacklistForm = document.getElementById("game-state-blacklist-form");
const gameStateBlacklistInput = document.getElementById("game-state-blacklist-input");
const gameStateWhitelistEl = document.getElementById("game-state-whitelist");

async function loadGameStateProcessLists() {
  const [blacklist, whitelist] = await Promise.all([
    fetch("/api/game-state/blacklist").then((r) => r.json()),
    fetch("/api/game-state/whitelist").then((r) => r.json()),
  ]);
  renderGameStateBlacklist(blacklist);
  renderGameStateWhitelist(whitelist);
  await populateTrackerProcessOptions(whitelist);
}

function renderGameStateBlacklist(blacklist) {
  gameStateBlacklistEl.innerHTML = "";

  if (blacklist.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No blacklisted processes.";
    gameStateBlacklistEl.appendChild(hint);
    return;
  }

  for (const process of blacklist) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("div");
    text.className = "memory-item-text";
    text.textContent = process;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "memory-item-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Remove from blacklist";
    deleteBtn.addEventListener("click", async () => {
      await fetch(`/api/game-state/blacklist/${encodeURIComponent(process)}`, { method: "DELETE" });
      await loadGameStateProcessLists();
    });

    item.appendChild(text);
    item.appendChild(deleteBtn);
    gameStateBlacklistEl.appendChild(item);
  }
}

function renderGameStateWhitelist(whitelist) {
  gameStateWhitelistEl.innerHTML = "";

  if (whitelist.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No approved processes yet.";
    gameStateWhitelistEl.appendChild(hint);
    return;
  }

  for (const process of whitelist) {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("div");
    text.className = "memory-item-text";
    text.textContent = process;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "memory-item-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "Revoke approval";
    deleteBtn.addEventListener("click", async () => {
      await fetch(`/api/game-state/whitelist/${encodeURIComponent(process)}`, { method: "DELETE" });
      await loadGameStateProcessLists();
    });

    item.appendChild(text);
    item.appendChild(deleteBtn);
    gameStateWhitelistEl.appendChild(item);
  }
}

// --- Custom trackers (Settings > Game Awareness) ---
// Per-process, user-editable list of fields the Game-State Model fills in each poll window.
// "activity" (Current Activity) is locked server-side - always present, never editable/removable.

const gameStateTrackerProcessEl = document.getElementById("game-state-tracker-process");
const gameStateTrackersListEl = document.getElementById("game-state-trackers-list");
const gameStateTrackerAddForm = document.getElementById("game-state-tracker-add-form");
const gameStateTrackerAddLabel = document.getElementById("game-state-tracker-add-label");
const gameStateTrackerAddDesc = document.getElementById("game-state-tracker-add-desc");
const gameStateTrackerResetBtn = document.getElementById("game-state-tracker-reset");

let currentTrackers = [];

// The locked "activity" tracker's real description (sent to the model) is long and detailed by
// design - shown here instead since it's never editable anyway, so there's no risk of this
// display-only text drifting from what actually gets saved/sent.
const ACTIVITY_TRACKER_SHORT_DESC = "What's happening on screen right now - refreshed every check, never a sticky fact.";

async function populateTrackerProcessOptions(whitelist) {
  const previousValue = gameStateTrackerProcessEl.value;
  gameStateTrackerProcessEl.innerHTML = "";

  if (whitelist.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No approved processes yet";
    gameStateTrackerProcessEl.appendChild(option);
    gameStateTrackerProcessEl.disabled = true;
    currentTrackers = [];
    renderTrackersList();
    return;
  }

  gameStateTrackerProcessEl.disabled = false;
  for (const process of whitelist) {
    const option = document.createElement("option");
    option.value = process;
    option.textContent = process;
    gameStateTrackerProcessEl.appendChild(option);
  }
  if (whitelist.includes(previousValue)) {
    gameStateTrackerProcessEl.value = previousValue;
  }
  await loadTrackersForSelectedProcess();
}

async function loadTrackersForSelectedProcess() {
  const process = gameStateTrackerProcessEl.value;
  if (!process) {
    currentTrackers = [];
    renderTrackersList();
    return;
  }
  currentTrackers = await fetch(`/api/game-state/trackers/${encodeURIComponent(process)}`).then((r) => r.json());
  renderTrackersList();
}

async function saveTrackers() {
  const process = gameStateTrackerProcessEl.value;
  if (!process) return;
  const body = currentTrackers
    .filter((t) => !t.locked)
    .map((t) => ({ id: t.id, label: t.label, description: t.description, overlay: t.overlay !== false }));
  currentTrackers = await fetch(`/api/game-state/trackers/${encodeURIComponent(process)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  renderTrackersList();
}

function renderTrackersList() {
  gameStateTrackersListEl.innerHTML = "";

  if (currentTrackers.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "Select an approved process to edit its trackers.";
    gameStateTrackersListEl.appendChild(hint);
    return;
  }

  for (const tracker of currentTrackers) {
    const item = document.createElement("div");
    item.className = "tracker-item";

    const header = document.createElement("div");
    header.className = "tracker-item-header";

    const label = document.createElement("div");
    label.className = "tracker-item-label";
    label.textContent = tracker.label;

    const desc = document.createElement("div");
    desc.className = "tracker-item-desc";
    desc.textContent = tracker.locked ? ACTIVITY_TRACKER_SHORT_DESC : tracker.description || "";

    if (tracker.locked) {
      label.title = "Always tracked - can't be edited or removed";
    } else {
      label.contentEditable = "true";
      desc.contentEditable = "true";
      desc.title = "What the model should look for";

      label.addEventListener("blur", () => {
        const text = label.textContent.trim();
        if (!text) {
          label.textContent = tracker.label;
          return;
        }
        if (text === tracker.label) return;
        tracker.label = text;
        saveTrackers();
      });

      desc.addEventListener("blur", () => {
        const text = desc.textContent.trim();
        if (text === tracker.description) return;
        tracker.description = text;
        saveTrackers();
      });

      for (const el of [label, desc]) {
        el.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            el.blur();
          }
        });
      }
    }

    header.appendChild(label);

    if (!tracker.locked) {
      const overlayToggle = document.createElement("label");
      overlayToggle.className = "tracker-overlay-toggle";
      overlayToggle.title = "Show this tracker in the in-game overlay panel";
      const overlayCb = document.createElement("input");
      overlayCb.type = "checkbox";
      overlayCb.checked = tracker.overlay !== false;
      overlayCb.addEventListener("change", () => {
        tracker.overlay = overlayCb.checked;
        saveTrackers();
      });
      overlayToggle.appendChild(overlayCb);
      overlayToggle.appendChild(document.createTextNode("Overlay"));
      header.appendChild(overlayToggle);

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "memory-item-delete";
      deleteBtn.textContent = "×";
      deleteBtn.title = "Remove tracker";
      deleteBtn.addEventListener("click", () => {
        currentTrackers = currentTrackers.filter((t) => t.id !== tracker.id);
        saveTrackers();
      });
      header.appendChild(deleteBtn);
    }

    item.appendChild(header);
    item.appendChild(desc);

    gameStateTrackersListEl.appendChild(item);
  }
}

gameStateTrackerProcessEl.addEventListener("change", loadTrackersForSelectedProcess);

gameStateTrackerAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const label = gameStateTrackerAddLabel.value.trim();
  if (!label || !gameStateTrackerProcessEl.value) return;
  const description = gameStateTrackerAddDesc.value.trim();
  currentTrackers.push({ id: "", label, description, locked: false, overlay: true });
  gameStateTrackerAddLabel.value = "";
  gameStateTrackerAddDesc.value = "";
  await saveTrackers();
});

gameStateTrackerResetBtn.addEventListener("click", async () => {
  const process = gameStateTrackerProcessEl.value;
  if (!process) return;
  currentTrackers = await fetch(`/api/game-state/trackers/${encodeURIComponent(process)}/reset`, {
    method: "POST",
  }).then((r) => r.json());
  renderTrackersList();
});

// Training Data is now shown per-game in the Gaming Journal detail view (memory-journal.js)
// instead of a process-picker subtab here.

gameStateBlacklistForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const process = gameStateBlacklistInput.value.trim();
  if (!process) return;
  gameStateBlacklistInput.value = "";
  await fetch("/api/game-state/blacklist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process }),
  });
  await loadGameStateProcessLists();
});

loadGameStateProcessLists();

// --- Pending process approvals ---
// Surfaced entirely via persistent toasts (bottom-right, stay until acted on, "+N more" when
// there are several) - see _toasts near the top of this file. No separate bell/badge/modal.

const _shownProcessToasts = new Set();

async function checkPendingApprovals() {
  let data;
  try {
    const response = await fetch("/api/game-state/pending");
    data = await response.json();
  } catch (err) {
    return; // transient server hiccup - the next poll tick will catch up
  }
  const processes = data.processes || [];

  for (const proc of processes) {
    if (_shownProcessToasts.has(proc)) continue;
    _shownProcessToasts.add(proc);
    showToast(`process-${proc}`, {
      title: "Process detected",
      body: `Allow <strong>${proc}</strong> to use game-state OCR?`,
      duration: 0,
      actions: [
        {
          label: "Allow",
          variant: "primary",
          onClick: async () => {
            await fetch("/api/game-state/whitelist", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ process: proc }),
            });
            _shownProcessToasts.delete(proc);
            await Promise.all([checkPendingApprovals(), loadGameStateProcessLists()]);
          },
        },
        {
          label: "Blacklist",
          variant: "danger",
          onClick: async () => {
            await fetch("/api/game-state/blacklist", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ process: proc }),
            });
            _shownProcessToasts.delete(proc);
            await Promise.all([checkPendingApprovals(), loadGameStateProcessLists()]);
          },
        },
      ],
    });
  }

  // Clean up resolved processes so their toast can reappear if they come back as pending
  for (const proc of _shownProcessToasts) {
    if (!processes.includes(proc)) _shownProcessToasts.delete(proc);
  }
}

let _pendingApprovalIntervalId = null;

function restartPendingApprovalPolling(intervalSeconds) {
  if (_pendingApprovalIntervalId) clearInterval(_pendingApprovalIntervalId);
  _pendingApprovalIntervalId = setInterval(checkPendingApprovals, intervalSeconds * 1000);
}

checkPendingApprovals();
// Interval started by loadConfig() on startup — see restartPendingApprovalPolling call there.

// --- Consumption modal ---
// Fetches the full per-call record list once per open/filter-change and aggregates client-side
// (totals + per-feature breakdown) for the selected time range - same "fetch whole list, filter
// in JS" pattern already used for the game-state blacklist/whitelist.

