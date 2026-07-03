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

function dpr() {
  return window.devicePixelRatio || 1;
}

function moveWindow(cssX, cssY) {
  // JS coordinates (screenX, layout math) are CSS px; the native bridge wants physical px.
  window.pywebview?.api?.move_overlay(Math.round(cssX * dpr()), Math.round(cssY * dpr()));
}

// --- Fit-to-content window sizing ---
// The native window is moved+resized (one atomic SetWindowPos via the fit_overlay bridge)
// to exactly match its visible content, so nothing ever needs to be transparent or clipped:
// no content -> invisible 1px sliver, a toast -> the window IS the toast card, edit mode ->
// the full widget bounds as an opaque panel. Transparency (rounds 1-5) and SetWindowRgn
// clipping (round 6) are both dead ends for WebView2 - see CLAUDE.md's in-game overlay
// section. The widget's FULL bounds (what the user positions in the layout editor) exist
// virtually as (virtualX, virtualY, FULL_W, FULL_H) in CSS px; content anchors to the
// bottom of those bounds for toasts and the top for game-state. Width is always FULL_W so
// text wrapping never depends on the current window size (no reflow feedback loop).

const FULL_W = window.innerWidth; // CSS px - captured at load, while the window is still full-size
const FULL_H = window.innerHeight;
let virtualX = window.screenX; // top-left of the widget's full bounds, CSS px screen coords
let virtualY = window.screenY;
let lastFit = null;

function contentHeightCss() {
  if (editing) return FULL_H;
  if (WIDGET === "toasts") return toastStack.childElementCount ? toastStack.offsetHeight : 0;
  if (WIDGET === "game-state") return gsPanel.classList.contains("visible") ? gsPanel.offsetHeight : 0;
  return 0;
}

function syncWindowFit() {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.fit_overlay) return; // bridge not injected yet - retried by the safety interval
  const h = Math.max(1, contentHeightCss());
  const y = WIDGET === "toasts" ? virtualY + (FULL_H - h) : virtualY; // bottom- vs top-anchored
  const key = `${Math.round(virtualX)},${Math.round(y)},${h}`;
  if (key === lastFit) return;
  lastFit = key;
  const s = dpr();
  api.fit_overlay(Math.round(virtualX * s), Math.round(y * s), Math.round(FULL_W * s), Math.round(h * s));
}

// Safety net for anything that shifts layout without an explicit syncWindowFit() call
// (fonts settling, the bridge appearing after first content, toast heights reflowing).
setInterval(syncWindowFit, 1000);
window.addEventListener("pywebviewready", syncWindowFit);

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
  if (pos) {
    virtualX = pos.x * screen.width;
    virtualY = pos.y * screen.height;
    lastFit = null;
    syncWindowFit(); // repositions (and re-anchors) the fitted window inside the new bounds
  }
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
  syncWindowFit();
  requestAnimationFrame(() => toast.classList.add("visible"));

  // Linger long enough to read: base time plus a per-character allowance, capped.
  const lingerMs = Math.min(6000 + clean.length * 45, 25000);
  setTimeout(() => {
    toast.classList.add("leaving");
    setTimeout(() => {
      toast.remove();
      syncWindowFit();
    }, 400);
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
  if (tracking) {
    gsTitle.textContent = state.session_name || state.process || "In game";
    gsRows.innerHTML = "";
    for (const tracker of state.trackers || []) {
      if (tracker.value === null || tracker.value === undefined || tracker.value === "") continue;
      renderGsRow(tracker.label, String(tracker.value));
    }
  }
  syncWindowFit();
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
  syncWindowFit();
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
    pollGameState(); // restore real content/visibility (also re-syncs the window region)
  }
  syncWindowFit();
}

document.body.addEventListener("pointerdown", (e) => {
  if (!editing) return;
  drag = { grabScreenX: e.screenX, grabScreenY: e.screenY, winX: window.screenX, winY: window.screenY };
  document.body.setPointerCapture(e.pointerId);
});
document.body.addEventListener("pointermove", (e) => {
  if (!drag) return;
  // While editing, the window IS the full bounds - dragging moves the virtual origin too,
  // so the fitted window re-anchors correctly when edit mode ends.
  virtualX = drag.winX + (e.screenX - drag.grabScreenX);
  virtualY = drag.winY + (e.screenY - drag.grabScreenY);
  moveWindow(virtualX, virtualY);
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
