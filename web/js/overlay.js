// In-game overlay logic (web/overlay.html), shared by both widget windows (toasts,
// game-state) that run_app.py creates - each is its own small native window, isolated from
// the main app and from each other. This script only acts on the widget named by the
// ?widget= query param (see WIDGET below); everything else in the page stays hidden by CSS.
// Data flow: SSE for companion replies/reminders/edit-mode toggles, polling for game state
// and config. Layout editing moves the ACTUAL native window via a Python-exposed bridge
// (window.pywebview.api.move_overlay) and saves the resulting position on drag release.

const PARAMS = new URLSearchParams(location.search);
const TOKEN = PARAMS.get("token") || "";
const WIDGET = PARAMS.get("widget") || "";

function api(path) {
  // EventSource can't set headers, and keeping one URL style everywhere is simpler - the
  // token middleware accepts the query-param form for all /api/* routes.
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}`;
}

// Live-disable: the launcher only creates these windows when overlay_enabled is on, but the
// user can turn the setting off mid-session - hide content (can't hide the native window
// itself from here) until the next launch.
let overlayEnabled = true;

async function pollConfig() {
  try {
    const cfg = await fetch(api("/api/config")).then((r) => r.json());
    overlayEnabled = cfg.overlay_enabled !== false;
  } catch (err) {
    /* transient - keep last known state */
  }
}

// --- Per-game layout ---
// Positions live server-side (data/overlay_layouts.json) keyed by process, "default" when no
// game is tracked - each entry is THIS widget's native window's top-left corner as a fraction
// of the primary screen. Switching games moves the window via the Python bridge instead of
// re-laying-out a shared canvas (there is no shared canvas anymore, one widget = one window).

let currentProcess = null; // lowercased tracked process, or null when not in a game
let appliedLayoutKey = null; // process the window's current position was last moved for

function moveWindow(x, y) {
  window.pywebview?.api?.move_overlay(Math.round(x), Math.round(y));
}

async function applyLayoutForProcess(force) {
  const key = currentProcess || "default";
  if (!force && key === appliedLayoutKey) return;
  let layout;
  try {
    ({ layout } = await fetch(api(`/api/overlay/layout/${encodeURIComponent(key)}`)).then((r) => r.json()));
  } catch (err) {
    return; // transient - retried on the next game-state tick via the appliedLayoutKey check
  }
  appliedLayoutKey = key;
  const pos = layout[WIDGET];
  if (pos) moveWindow(pos.x * screen.width, pos.y * screen.height);
}

async function saveCurrentPosition() {
  const key = currentProcess || "default";
  const payload = { x: window.screenX / screen.width, y: window.screenY / screen.height };
  try {
    await fetch(api(`/api/overlay/layout/${encodeURIComponent(key)}/${encodeURIComponent(WIDGET)}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    /* the drag itself already took effect visually - a failed save just means it reverts
       next time this game's layout is (re-)applied, not silently corrupting anything */
  }
}

// --- Toasts widget ---

const toastStack = document.getElementById("toast-stack");
const MAX_TOASTS = 4;
const MAX_TOAST_CHARS = 500;

function cleanReplyText(text) {
  // Replies are markdown-ish; the overlay renders plain text. Drop images, unwrap links,
  // strip emphasis markers, and cap the length so a long reply doesn't wallpaper the game.
  let out = text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`#]+/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  if (out.length > MAX_TOAST_CHARS) out = `${out.slice(0, MAX_TOAST_CHARS)}…`;
  return out;
}

function buildToast(text, kind) {
  const toast = document.createElement("div");
  toast.className = `toast${kind === "reminder" ? " reminder" : ""}`;
  const tag = document.createElement("span");
  tag.className = "toast-tag";
  tag.textContent = kind === "reminder" ? "Reminder" : "Companion";
  toast.appendChild(tag);
  toast.appendChild(document.createTextNode(text));
  return toast;
}

function showToast(text, kind) {
  const clean = cleanReplyText(text);
  if (!clean) return;

  const toast = buildToast(clean, kind);
  toastStack.appendChild(toast);
  while (toastStack.children.length > MAX_TOASTS) toastStack.removeChild(toastStack.firstChild);
  requestAnimationFrame(() => toast.classList.add("visible"));

  // Linger long enough to read: base time plus a per-character allowance, capped.
  const lingerMs = Math.min(6000 + clean.length * 45, 25000);
  setTimeout(() => {
    toast.classList.add("leaving");
    setTimeout(() => toast.remove(), 400);
  }, lingerMs);
}

// --- Game-state widget ---

const gsPanel = document.getElementById("gs-panel");
const gsTitle = document.getElementById("gs-title");
const gsRows = document.getElementById("gs-rows");

function renderGsRow(label, value) {
  const row = document.createElement("div");
  row.className = "gs-row";
  const labelEl = document.createElement("span");
  labelEl.className = "gs-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "gs-value";
  valueEl.textContent = value;
  row.appendChild(labelEl);
  row.appendChild(valueEl);
  gsRows.appendChild(row);
}

async function pollGameState() {
  let state;
  try {
    state = await fetch(api("/api/game-state")).then((r) => r.json());
  } catch (err) {
    return; // transient - next tick catches up
  }
  currentProcess = state.tracking && state.process ? state.process.toLowerCase() : null;
  // Don't auto-reposition mid-drag - the user is actively moving this window right now.
  if (!editing) applyLayoutForProcess(false);

  if (WIDGET !== "game-state" || editing) return; // editor owns visibility/content right now

  const tracking = overlayEnabled && state.enabled && state.tracking;
  gsPanel.classList.toggle("visible", Boolean(tracking));
  if (!tracking) return;

  gsTitle.textContent = state.session_name || state.process || "In game";
  gsRows.innerHTML = "";
  for (const tracker of state.trackers || []) {
    if (tracker.value === null || tracker.value === undefined || tracker.value === "") continue;
    renderGsRow(tracker.label, String(tracker.value));
  }
}

// --- Layout editor ---
// Entered via SSE edit_mode event (Settings "Edit layout" button, or the Ctrl+Shift+O global
// hotkey - both toggle click-through for every overlay widget window server-side). Dragging
// moves the real native window via the Python bridge and saves on release - no Save/Cancel.

let editing = false;
let sampleToast = null;
let drag = null;

function enterEditMode() {
  if (editing) return;
  editing = true;
  document.body.classList.add("editing");

  if (WIDGET === "toasts") {
    sampleToast = buildToast("Companion replies and reminders will appear here.", "reply");
    sampleToast.classList.add("visible");
    toastStack.appendChild(sampleToast);
  } else if (WIDGET === "game-state") {
    gsTitle.textContent = currentProcess || "Game state";
    gsRows.innerHTML = "";
    renderGsRow("Example tracker", "42");
    gsPanel.classList.add("visible");
  }
}

function exitEditMode() {
  if (!editing) return;
  editing = false;
  document.body.classList.remove("editing");
  if (sampleToast) {
    sampleToast.remove();
    sampleToast = null;
  }
  if (WIDGET === "game-state") {
    gsPanel.classList.remove("visible");
    pollGameState(); // restore real content/visibility
  }
}

document.body.addEventListener("pointerdown", (e) => {
  if (!editing) return;
  drag = { grabScreenX: e.screenX, grabScreenY: e.screenY, winX: window.screenX, winY: window.screenY };
  document.body.setPointerCapture(e.pointerId);
});
document.body.addEventListener("pointermove", (e) => {
  if (!drag) return;
  moveWindow(drag.winX + (e.screenX - drag.grabScreenX), drag.winY + (e.screenY - drag.grabScreenY));
});
document.body.addEventListener("pointerup", () => {
  if (!drag) return;
  drag = null;
  saveCurrentPosition();
});
document.body.addEventListener("pointercancel", () => {
  drag = null;
});

// --- SSE event feed ---
// EventSource auto-reconnects on drop, so no manual retry loop is needed.

function connectEvents() {
  const source = new EventSource(api("/api/overlay/events"));
  source.onmessage = (e) => {
    let event;
    try {
      event = JSON.parse(e.data);
    } catch (err) {
      return;
    }
    if (event.type === "edit_mode") {
      if (event.enabled) enterEditMode();
      else exitEditMode();
      return;
    }
    if (!overlayEnabled || editing || WIDGET !== "toasts") return;
    if (event.type === "reply") showToast(event.text, "reply");
    else if (event.type === "reminder") showToast(event.text, "reminder");
  };
}

connectEvents();
pollGameState();
setInterval(pollGameState, 5000);
pollConfig();
setInterval(pollConfig, 30000);
