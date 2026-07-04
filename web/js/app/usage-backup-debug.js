const USAGE_RANGES = [
  ["1h", "Last hour", 1 / 24],
  ["24h", "Last 24h", 1],
  ["7d", "Last 7d", 7],
  ["30d", "Last 30d", 30],
  ["all", "All time", null],
  ["custom", "Custom", null],
];

let usageRange = "all";
let usageRecordsCache = [];

const usageRangePillsEl = document.getElementById("usage-range-pills");
const usageCustomRangeEl = document.getElementById("usage-custom-range");
const usageRangeStartInput = document.getElementById("usage-range-start");
const usageRangeEndInput = document.getElementById("usage-range-end");
const usageBySourceEl = document.getElementById("usage-by-source");
const usageClearBtn = document.getElementById("usage-clear-btn");

function renderUsageRangePills() {
  usageRangePillsEl.innerHTML = "";
  for (const [value, label] of USAGE_RANGES) {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "memory-filter-pill" + (usageRange === value ? " active" : "");
    pill.textContent = label;
    pill.addEventListener("click", () => {
      usageRange = value;
      renderUsageRangePills();
      usageCustomRangeEl.hidden = usageRange !== "custom";
      renderUsageStats();
    });
    usageRangePillsEl.appendChild(pill);
  }
}

function filteredUsageRecords() {
  if (usageRange === "all") return usageRecordsCache;

  let startMs;
  let endMs = Infinity;
  if (usageRange === "custom") {
    startMs = usageRangeStartInput.value ? new Date(usageRangeStartInput.value).getTime() : -Infinity;
    endMs = usageRangeEndInput.value ? new Date(usageRangeEndInput.value).getTime() : Infinity;
  } else {
    const days = USAGE_RANGES.find(([value]) => value === usageRange)[2];
    startMs = Date.now() - days * 24 * 60 * 60 * 1000;
  }

  return usageRecordsCache.filter((r) => {
    const ts = new Date(r.timestamp).getTime();
    return ts >= startMs && ts <= endMs;
  });
}

function renderUsageStats() {
  const records = filteredUsageRecords();

  const totalPromptTokens = records.reduce((sum, r) => sum + r.prompt_tokens, 0);
  const totalCompletionTokens = records.reduce((sum, r) => sum + r.completion_tokens, 0);
  const totalCost = records.reduce((sum, r) => sum + r.cost_usd, 0);

  document.getElementById("usage-requests").textContent = records.length.toLocaleString();
  document.getElementById("usage-prompt-tokens").textContent = totalPromptTokens.toLocaleString();
  document.getElementById("usage-completion-tokens").textContent = totalCompletionTokens.toLocaleString();
  document.getElementById("usage-cost").textContent = `$${totalCost.toFixed(4)}`;

  const bySource = new Map();
  for (const r of records) {
    const entry = bySource.get(r.source) || { count: 0, promptTokens: 0, completionTokens: 0, cost: 0 };
    entry.count += 1;
    entry.promptTokens += r.prompt_tokens;
    entry.completionTokens += r.completion_tokens;
    entry.cost += r.cost_usd;
    bySource.set(r.source, entry);
  }

  usageBySourceEl.innerHTML = "";
  const sorted = [...bySource.entries()].sort((a, b) => b[1].cost - a[1].cost);
  if (sorted.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No requests in this range.";
    usageBySourceEl.appendChild(hint);
  }
  for (const [source, entry] of sorted) {
    const row = document.createElement("div");
    row.className = "usage-source-row";
    row.innerHTML = `
      <span class="usage-source-name">${source}</span>
      <span class="usage-source-detail">${entry.count} req</span>
      <span class="usage-source-detail">${(entry.promptTokens + entry.completionTokens).toLocaleString()} tok</span>
      <span class="usage-source-detail">$${entry.cost.toFixed(4)}</span>
    `;
    usageBySourceEl.appendChild(row);
  }
}

usageRangeStartInput.addEventListener("change", renderUsageStats);
usageRangeEndInput.addEventListener("change", renderUsageStats);

const usageBalanceListEl = document.getElementById("usage-balance-list");

const PROVIDER_LABELS = {
  openrouter: "OpenRouter",
  google_ai_studio: "Google AI Studio",
  custom: "Custom provider",
};

// Only OpenRouter exposes a real balance/credits API right now - Google AI Studio and custom
// endpoints are fetched too (in case that changes) but silently omitted whenever unavailable,
// same as today's single-provider behavior.
async function fetchAndRenderBalance() {
  usageBalanceListEl.innerHTML = "";
  const balances = await fetch("/api/usage/balance").then((r) => r.json());
  for (const data of balances) {
    const row = document.createElement("div");
    row.className = "usage-balance-row";

    const label = document.createElement("span");
    label.className = "usage-balance-label";
    label.textContent = `${PROVIDER_LABELS[data.provider] || data.provider} balance`;
    row.appendChild(label);

    const value = document.createElement("span");
    value.className = "usage-balance-value";
    if (data.limit_usd !== null && data.limit_usd !== undefined) {
      const remaining = data.remaining_usd ?? 0;
      value.textContent = `$${remaining.toFixed(4)} remaining of $${data.limit_usd.toFixed(2)} ($${data.spent_usd.toFixed(4)} spent)`;
    } else {
      const tierLabel = data.is_free_tier ? " (free tier)" : "";
      value.textContent = `$${data.spent_usd.toFixed(4)} spent${tierLabel} · no credit limit set`;
    }
    row.appendChild(value);

    usageBalanceListEl.appendChild(row);
  }
}

diagnosticsBtn.addEventListener("click", async () => {
  openModal(diagnosticsModal);
  renderUsageRangePills();
  usageCustomRangeEl.hidden = usageRange !== "custom";
  const [recordsRes] = await Promise.all([fetch("/api/usage/records"), fetchAndRenderBalance()]);
  usageRecordsCache = await recordsRes.json();
  renderUsageStats();
  const debugRes = await fetch("/api/debug/requests");
  renderDebugRequests(await debugRes.json());
});

let usageClearConfirming = false;
let usageClearConfirmTimeout = null;

usageClearBtn.addEventListener("click", async () => {
  if (!usageClearConfirming) {
    usageClearConfirming = true;
    usageClearBtn.textContent = "Click to confirm";
    usageClearBtn.classList.add("confirming");
    usageClearConfirmTimeout = setTimeout(() => {
      usageClearConfirming = false;
      usageClearBtn.textContent = "Clear Consumption Data";
      usageClearBtn.classList.remove("confirming");
    }, 4000);
    return;
  }

  clearTimeout(usageClearConfirmTimeout);
  usageClearConfirming = false;
  usageClearBtn.textContent = "Clear Consumption Data";
  usageClearBtn.classList.remove("confirming");

  await fetch("/api/usage", { method: "DELETE" });
  usageRecordsCache = [];
  renderUsageStats();
});

// --- Backup / restore (General > Backup tab) ---
// A plain <a download> for export (needs the token as a query param, since it isn't a fetch()
// call the auth patch above can intercept); a hidden file input + fetch for import.
const backupExportBtn = document.getElementById("backup-export-btn");
const backupImportBtn = document.getElementById("backup-import-btn");
const backupImportInput = document.getElementById("backup-import-input");

if (backupExportBtn) backupExportBtn.href = apiUrl("/api/backup/export");

backupImportBtn?.addEventListener("click", () => backupImportInput.click());

backupImportInput?.addEventListener("change", async () => {
  const file = backupImportInput.files[0];
  backupImportInput.value = "";
  if (!file) return;
  if (!(await showConfirm("Importing overwrites current chats, memories, and settings with the backup's contents. Continue?", { title: "Import backup", danger: true, confirmText: "Import" }))) return;

  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/backup/import", { method: "POST", body: formData });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      await showAlert(`Import failed: ${error.detail || "unknown error"}`, { title: "Import failed" });
      return;
    }
    await showAlert("Backup imported. Restart Lykompanion for the restored settings to take effect.", { title: "Import complete" });
  } catch (err) {
    await showAlert("Import failed: connection error.", { title: "Import failed" });
  }
});

// --- Debug tab (Usage & Debug modal) ---
// Last 50 individual LLM API calls and tool executions - persisted to data/debug_log.json when
// debug mode is enabled so history survives restarts. Lets the user inspect exactly what was
// sent/received for each request, including tool round-trips.

const debugRequestsListEl = document.getElementById("debug-requests-list");

// This checkbox lives in the Diagnostics modal, not the Settings modal, so it has no "Save"
// button of its own - persist it immediately on toggle (like the Gaming Journal's Game
// Awareness tab does via its own save button), otherwise flipping it here silently does
// nothing until the user separately opens Settings and clicks Save.
document.getElementById("cfg-debug-mode-enabled").addEventListener("change", () => saveSettings());

function formatDebugMessage(message) {
  const block = document.createElement("div");
  block.className = "debug-message-block";

  const role = document.createElement("div");
  role.className = "debug-message-role";
  role.textContent = message.role + (message.tool_call_id ? ` (tool: ${message.tool_call_id})` : "");
  block.appendChild(role);

  const content = document.createElement("div");
  content.className = "debug-message-content";
  if (typeof message.content === "string") {
    content.textContent = message.content;
  } else if (Array.isArray(message.content)) {
    content.textContent = message.content
      .map((part) => (part.type === "text" ? part.text : `[${part.type}]`))
      .join("\n");
  } else if (message.tool_calls) {
    content.textContent = message.tool_calls
      .map((tc) => `→ ${tc.function?.name || tc.name}(${tc.function?.arguments || tc.arguments})`)
      .join("\n");
  } else {
    content.textContent = "(empty)";
  }
  block.appendChild(content);

  return block;
}

function openDebugDetail(entry) {
  debugDetailTitleEl.textContent = `${entry.source} · ${entry.model}`;
  debugDetailBodyEl.innerHTML = "";

  // ── Left pane: metadata + full prompt messages ───────────────────────
  const leftPane = document.createElement("div");
  leftPane.className = "debug-detail-pane";
  const leftLabel = document.createElement("div");
  leftLabel.className = "debug-detail-pane-label";
  leftLabel.textContent = "Prompt";
  leftPane.appendChild(leftLabel);

  const time = new Date(entry.timestamp).toLocaleString();
  const metaBlock = document.createElement("div");
  metaBlock.className = "debug-message-block";
  metaBlock.innerHTML = `<div class="debug-message-role">metadata</div><div class="debug-message-content"></div>`;
  metaBlock.querySelector(".debug-message-content").textContent =
    `${time}\n${entry.prompt_tokens}+${entry.completion_tokens} tok · $${entry.cost_usd.toFixed(4)} · ${entry.duration_ms ? Math.round(entry.duration_ms) + "ms" : "-"}`;
  leftPane.appendChild(metaBlock);

  for (const message of entry.messages) {
    leftPane.appendChild(formatDebugMessage(message));
  }

  // ── Right pane: tools available + LLM response ───────────────────────
  const rightPane = document.createElement("div");
  rightPane.className = "debug-detail-pane";
  const rightLabel = document.createElement("div");
  rightLabel.className = "debug-detail-pane-label";
  rightLabel.textContent = "Response";
  rightPane.appendChild(rightLabel);

  if (entry.tools && entry.tools.length > 0) {
    const toolsBlock = document.createElement("div");
    toolsBlock.className = "debug-message-block";
    toolsBlock.innerHTML = `<div class="debug-message-role">tools available</div><div class="debug-message-content"></div>`;
    toolsBlock.querySelector(".debug-message-content").textContent = entry.tools.join(", ");
    rightPane.appendChild(toolsBlock);
  }

  const replyBlock = document.createElement("div");
  replyBlock.className = "debug-message-block";
  const toolCallsText = entry.tool_calls
    ? entry.tool_calls.map((tc) => `→ ${tc.name}(${tc.arguments})`).join("\n")
    : "";
  replyBlock.innerHTML = `<div class="debug-message-role">result</div><div class="debug-message-content"></div>`;
  replyBlock.querySelector(".debug-message-content").textContent =
    [entry.reply, toolCallsText].filter(Boolean).join("\n") || "(empty)";
  rightPane.appendChild(replyBlock);

  debugDetailBodyEl.appendChild(leftPane);
  debugDetailBodyEl.appendChild(rightPane);

  openModal(debugDetailModal);
}

function renderDebugRequests(entries) {
  debugRequestsListEl.innerHTML = "";

  if (entries.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No requests recorded. Enable debug capture and make a request.";
    debugRequestsListEl.appendChild(hint);
    return;
  }

  for (const entry of entries) {
    const card = document.createElement("div");
    card.className = "debug-request-card";

    const summary = document.createElement("div");
    summary.className = "debug-request-summary";
    const time = new Date(entry.timestamp).toLocaleTimeString();
    summary.innerHTML = `
      <span class="debug-request-source">${entry.source}</span>
      <span class="debug-request-meta">${entry.model} · ${time} · ${entry.prompt_tokens}+${entry.completion_tokens} tok
      · $${entry.cost_usd.toFixed(4)} · ${entry.duration_ms ? Math.round(entry.duration_ms) + "ms" : "-"}</span>
      <span>▾</span>
    `;

    summary.addEventListener("click", () => openDebugDetail(entry));

    card.appendChild(summary);
    debugRequestsListEl.appendChild(card);
  }
}


// Release the Speech Recognition DLL before pywebview cleans up its temp profile folder,
// otherwise Windows locks the file and pywebview logs a WinError 5 access-denied warning.
window.addEventListener("beforeunload", () => {
  stopWakeWordRecognition();
});

// Block the browser's right-click context menu (hides Inspect, View Source, Save As, etc.).
// We use JS rather than AreDefaultContextMenusEnabled=False in Python because that flag also
// kills the <audio> player's 3-dot menu — contextmenu events are only right-click, not button clicks.
document.addEventListener("contextmenu", (e) => e.preventDefault());

// --- Setup banner + Quick Setup wizard ---
// The wizard walks through the minimum viable configuration (name → provider/key → model →
// voice), page by page. It auto-opens on first run on a machine (no "done" flag in this
// webview profile AND no API key configured for the active provider), and can always be
// re-launched from Settings → "Quick Setup…" or the chat banner.

