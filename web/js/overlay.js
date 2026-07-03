// In-game overlay logic (web/overlay.html). Runs in its own WebView2 window, isolated from
// the main app - it gets companion replies and fired reminders over the /api/overlay/events
// SSE bus and polls /api/game-state for the tracker snapshot. Normally display-only (the
// window is click-through); the layout editor (triggered from the main window's Settings)
// lifts the click-through so the elements can be dragged, then positions are saved per-game
// via /api/overlay/layout.

const TOKEN = new URLSearchParams(location.search).get("token") || "";

function api(path) {
  // EventSource can't set headers, and keeping one URL style everywhere is simpler - the
  // token middleware accepts the query-param form for all /api/* routes.
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}`;
}

// Live-disable: the launcher only creates this window when overlay_enabled is on, but the
// user can turn the setting off mid-session - hide everything until the next launch.
let overlayEnabled = true;

async function pollConfig() {
  try {
    const cfg = await fetch(api("/api/config")).then((r) => r.json());
    overlayEnabled = cfg.overlay_enabled !== false;
  } catch (err) {
    /* transient - keep last known state */
  }
  document.body.style.display = overlayEnabled || editing ? "" : "none";
}

// --- Per-game layout ---
// Positions live server-side (data/overlay_layouts.json) keyed by process, "default" when
// no game is tracked. Values are the element's top-left corner as viewport fractions.

const DRAGGABLES = Array.from(document.querySelectorAll(".draggable"));
let currentProcess = null; // lowercased tracked process, or null when not in a game
let layoutKey = null; // process the currently applied layout was fetched for

function applyPosition(el, pos) {
  if (pos) {
    el.style.left = `${pos.x * window.innerWidth}px`;
    el.style.top = `${pos.y * window.innerHeight}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
  } else {
    // No saved position - fall back to the stylesheet defaults.
    el.style.left = "";
    el.style.top = "";
    el.style.right = "";
    el.style.bottom = "";
  }
}

async function loadLayout(force) {
  const key = currentProcess || "default";
  if (!force && key === layoutKey) return;
  let layout;
  try {
    ({ layout } = await fetch(api(`/api/overlay/layout/${encodeURIComponent(key)}`)).then((r) => r.json()));
  } catch (err) {
    return; // transient - retried on the next game-state tick via the layoutKey check
  }
  layoutKey = key;
  for (const el of DRAGGABLES) applyPosition(el, layout[el.dataset.elementId]);
}

// --- Toasts (replies + reminders) ---

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

// --- Game-state mini panel ---

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
  loadLayout(false);
  if (editing) return; // editor owns the panel's visibility and content right now

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
// Entered via SSE edit_mode event (the main window POSTs /api/overlay/edit-mode, which also
// lifts the native click-through). Elements get sample content so there is something to see
// and drag even when idle, then Save PUTs the fractions for the current game (or "default").

let editing = false;
let sampleToast = null;

function enterEditMode() {
  if (editing) return;
  editing = true;
  document.body.classList.add("editing");
  document.body.style.display = "";

  document.getElementById("edit-label").textContent =
    `Overlay layout — ${currentProcess || "default (no game)"}`;

  // Sample content so both elements are visible and meaningfully sized while dragging.
  sampleToast = buildToast("Companion replies and reminders will appear here.", "reply");
  sampleToast.classList.add("visible");
  toastStack.appendChild(sampleToast);
  if (!gsPanel.classList.contains("visible")) {
    gsTitle.textContent = currentProcess || "Game state";
    gsRows.innerHTML = "";
    renderGsRow("Example tracker", "42");
  }
  gsPanel.classList.add("visible");
}

function exitEditMode() {
  if (!editing) return;
  editing = false;
  document.body.classList.remove("editing");
  if (sampleToast) {
    sampleToast.remove();
    sampleToast = null;
  }
  gsPanel.classList.remove("visible");
  pollGameState(); // restore real panel visibility/content
  pollConfig(); // re-hide everything if the overlay was live-disabled while editing
}

async function postEditMode(enabled) {
  try {
    await fetch(api("/api/overlay/edit-mode"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  } catch (err) {
    /* the SSE echo won't come; leave current mode as-is */
  }
}

document.getElementById("edit-save").addEventListener("click", async () => {
  const layout = {};
  for (const el of DRAGGABLES) {
    const rect = el.getBoundingClientRect();
    layout[el.dataset.elementId] = {
      x: rect.left / window.innerWidth,
      y: rect.top / window.innerHeight,
    };
  }
  const key = currentProcess || "default";
  try {
    await fetch(api(`/api/overlay/layout/${encodeURIComponent(key)}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout }),
    });
    layoutKey = key;
  } catch (err) {
    /* keep editing; the user can retry Save */
    return;
  }
  postEditMode(false);
});

document.getElementById("edit-cancel").addEventListener("click", async () => {
  await loadLayout(true); // discard unsaved drags by re-applying the stored layout
  postEditMode(false);
});

// Dragging: plain pointer events, positions pinned to left/top in pixels while moving.
for (const el of DRAGGABLES) {
  let grab = null;
  el.addEventListener("pointerdown", (e) => {
    if (!editing) return;
    const rect = el.getBoundingClientRect();
    grab = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    el.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  el.addEventListener("pointermove", (e) => {
    if (!grab) return;
    const rect = el.getBoundingClientRect();
    const x = Math.min(Math.max(e.clientX - grab.dx, 0), window.innerWidth - rect.width);
    const y = Math.min(Math.max(e.clientY - grab.dy, 0), window.innerHeight - rect.height);
    applyPosition(el, { x: x / window.innerWidth, y: y / window.innerHeight });
  });
  const release = () => { grab = null; };
  el.addEventListener("pointerup", release);
  el.addEventListener("pointercancel", release);
}

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
    if (!overlayEnabled) return;
    if (event.type === "reply") showToast(event.text, "reply");
    else if (event.type === "reminder") showToast(event.text, "reminder");
  };
}

connectEvents();
pollGameState();
setInterval(pollGameState, 5000);
pollConfig();
setInterval(pollConfig, 30000);
