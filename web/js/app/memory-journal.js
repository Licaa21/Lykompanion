const memoryList = document.getElementById("memory-list");
const memoryAddForm = document.getElementById("memory-add-form");
const memoryAddInput = document.getElementById("memory-add-input");

// Auto-grow the add-memory textarea between its CSS min/max-height as the user types, instead
// of a fixed single-line input that scrolled long facts sideways.
memoryAddInput.addEventListener("input", () => {
  memoryAddInput.style.height = "auto";
  memoryAddInput.style.height = `${memoryAddInput.scrollHeight}px`;
});

// Textareas don't submit their form on Enter like a single-line input did - restore that,
// keeping Shift+Enter free for an actual newline in a longer fact.
memoryAddInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    memoryAddForm.requestSubmit();
  }
});

let allMemories = [];

function renderMemoryList() {
  memoryList.innerHTML = "";

  const visible = allMemories.filter((m) => (m.scope || "user") === "user");

  if (visible.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No memories here yet.";
    memoryList.appendChild(hint);
    return;
  }

  for (const entry of visible) {
    memoryList.appendChild(buildMemoryItem(entry, () => {
      allMemories = allMemories.filter((m) => m.id !== entry.id);
      renderMemoryList();
    }));
  }
}

// Shared editable memory row (Personal Data + Gaming Journal): contentEditable text saved on
// blur via PUT (keeping the entry's existing placement), and a delete button.
function buildMemoryItem(entry, onDeleted) {
  const item = document.createElement("div");
  item.className = "memory-item";

  const text = document.createElement("div");
  text.className = "memory-item-text";
  text.contentEditable = "true";
  text.textContent = entry.content;

  text.addEventListener("blur", async () => {
    const content = text.textContent.trim();
    if (!content || content === entry.content) {
      text.textContent = entry.content;
      return;
    }
    const response = await fetch(`/api/memory/${entry.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, process: entry.process, session_id: entry.session_id }),
    });
    if (response.ok) {
      entry.content = content;
    } else {
      text.textContent = entry.content;
    }
  });
  text.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      text.blur();
    }
  });

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "memory-item-delete";
  deleteBtn.textContent = "×";
  deleteBtn.title = "Remove memory";
  deleteBtn.addEventListener("click", async () => {
    const response = await fetch(`/api/memory/${entry.id}`, { method: "DELETE" });
    if (response.ok) onDeleted();
  });

  item.appendChild(text);
  item.appendChild(deleteBtn);
  return item;
}

async function loadMemories() {
  const response = await fetch("/api/memory");
  allMemories = await response.json();
  renderMemoryList();
}

memoryAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = memoryAddInput.value.trim();
  if (!content) return;
  memoryAddInput.value = "";
  memoryAddInput.style.height = "auto";
  await fetch("/api/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, scope: "user" }),
  });
  await loadMemories();
});

document.getElementById("memory-delete-all").addEventListener("click", async () => {
  const count = allMemories.filter((m) => (m.scope || "user") === "user").length;
  if (count === 0) return;
  if (!(await showConfirm(`Delete all ${count} personal ${count === 1 ? "memory" : "memories"}? This can't be undone.`, { title: "Delete personal memories", danger: true, confirmText: "Delete all" }))) return;
  await fetch("/api/memory?scope=user", { method: "DELETE" });
  await loadMemories();
});

// --- Gaming Journal modal ---
// "My Games": a Steam-like library grid (cover art fetched from Steam/IGDB) of every game the
// companion knows something about. Clicking a card swaps in that game's full detail view -
// game-scope memories (hold across all playthroughs), profiles (named sessions) with their
// playthrough-scope memories, unconfirmed screen observations, and its training-data document.
// "Journal Settings" (the former "Game Awareness" tab) still hosts the global OCR/tracker settings.

const gamingJournalModal = document.getElementById("gaming-journal-modal");
const gamingJournalBtn = document.getElementById("gaming-journal-btn");
const gamingJournalList = document.getElementById("gaming-journal-list");
const journalLibraryView = document.getElementById("journal-library-view");
const journalDetailView = document.getElementById("journal-detail-view");
const journalDetailContent = document.getElementById("journal-detail-content");
const journalDetailBack = document.getElementById("journal-detail-back");

let allJournalGames = [];

gamingJournalBtn.addEventListener("click", () => {
  openModal(gamingJournalModal);
  showJournalLibrary();
  loadGamingJournal();
  loadGameStateProcessLists();
});

document.getElementById("journal-delete-all").addEventListener("click", async () => {
  if (!(await showConfirm("Delete ALL game and playthrough memories across every game? Unconfirmed observations are left alone. This can't be undone.", { title: "Delete game memories", danger: true, confirmText: "Delete all" }))) return;
  await fetch("/api/memory?scope=game&scope=session", { method: "DELETE" });
  await loadGamingJournal();
});

journalDetailBack.addEventListener("click", showJournalLibrary);

function showJournalLibrary() {
  journalLibraryView.hidden = false;
  journalDetailView.hidden = true;
  journalDetailContent.innerHTML = "";
}

async function loadGamingJournal() {
  let games = [];
  try {
    const response = await fetch("/api/gaming-journal");
    if (response.ok) games = await response.json();
  } catch (err) {
    games = [];
  }
  allJournalGames = games;
  renderLibrary(games);
}

function buildJournalAddForm(placeholder, buildBody) {
  const form = document.createElement("form");
  form.className = "memory-add-form";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  const btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "secondary-btn";
  btn.textContent = "Add";
  form.appendChild(input);
  form.appendChild(btn);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = input.value.trim();
    if (!content) return;
    input.value = "";
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildBody(content)),
    });
    await refreshJournalDetail(buildBody(content).process);
  });
  return form;
}

// Small text-button used for per-game / per-profile actions (switch, delete). `danger` tints it red.
function buildJournalActionBtn(text, title, onClick, danger = false) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "journal-action-btn" + (danger ? " journal-action-btn--danger" : "");
  btn.textContent = text;
  btn.title = title;
  btn.addEventListener("click", onClick);
  return btn;
}

// "Add a new profile (playthrough)" form for one game.
function buildProfileAddForm(process) {
  const form = document.createElement("form");
  form.className = "memory-add-form";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "New profile (e.g. \"NG+\", \"Dark Urge run\")…";
  input.autocomplete = "off";
  const btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "secondary-btn";
  btn.textContent = "Add profile";
  form.appendChild(input);
  form.appendChild(btn);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    input.value = "";
    const response = await fetch(`/api/game-state/sessions/${encodeURIComponent(process)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (response.ok) await refreshJournalDetail(process);
  });
  return form;
}

function buildSectionLabel(text) {
  const label = document.createElement("div");
  label.className = "journal-section-label";
  label.textContent = text;
  return label;
}

// Re-fetches the journal list and, if the detail view is still open on this process, re-renders
// it in place instead of bouncing back to the library grid.
async function refreshJournalDetail(process) {
  await loadGamingJournal();
  if (!journalDetailView.hidden) {
    const game = allJournalGames.find((g) => g.process.toLowerCase() === (process || "").toLowerCase());
    if (game) openGameDetail(game);
    else showJournalLibrary();
  }
}

function renderLibrary(games) {
  gamingJournalList.innerHTML = "";

  if (games.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No games yet - approve a game for tracking (Journal Settings tab) or let the companion learn about one in conversation.";
    gamingJournalList.appendChild(hint);
    return;
  }

  for (const game of games) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "journal-game-card" + (game.cover_url ? "" : " journal-game-card--placeholder");
    card.title = game.title;

    if (game.cover_url) {
      const img = document.createElement("img");
      img.src = game.cover_url;
      img.alt = game.title;
      card.appendChild(img);
    } else {
      const initial = document.createElement("span");
      initial.className = "journal-game-card-initial";
      initial.textContent = (game.title || "?").trim().charAt(0).toUpperCase();
      card.appendChild(initial);
    }

    const caption = document.createElement("span");
    caption.className = "journal-game-card-caption";
    caption.textContent = game.title;
    card.appendChild(caption);

    if (game.tracked) {
      const badge = document.createElement("span");
      badge.className = "journal-badge journal-game-card-badge";
      badge.textContent = "tracked";
      badge.title = "Approved for background OCR awareness";
      card.appendChild(badge);
    }

    card.addEventListener("click", () => openGameDetail(game));
    gamingJournalList.appendChild(card);

    if (!game.cover_url) {
      fetch(`/api/game-art/${encodeURIComponent(game.process)}/fetch`, { method: "POST" })
        .then((r) => (r.ok ? r.json() : null))
        .then((art) => {
          if (!art || !art.cover_url) return;
          const img = document.createElement("img");
          img.src = art.cover_url;
          img.alt = art.title || game.title;
          card.classList.remove("journal-game-card--placeholder");
          card.replaceChild(img, card.firstChild);
        })
        .catch(() => {});
    }
  }
}

function openGameDetail(game) {
  journalLibraryView.hidden = true;
  journalDetailView.hidden = false;
  journalDetailContent.innerHTML = "";

  const header = document.createElement("div");
  header.className = "journal-game-header";

  const title = document.createElement("span");
  title.className = "journal-game-title";
  title.contentEditable = "true";
  title.textContent = game.title;
  title.title = "Click to correct the title (used for cover art lookup)";
  title.addEventListener("blur", async () => {
    const newTitle = title.textContent.trim();
    if (!newTitle || newTitle === game.title) {
      title.textContent = game.title;
      return;
    }
    const response = await fetch(`/api/game-art/${encodeURIComponent(game.process)}/title`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: newTitle }),
    });
    if (response.ok) {
      game.title = newTitle;
      await loadGamingJournal();
    } else {
      title.textContent = game.title;
    }
  });
  title.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      title.blur();
    }
  });
  header.appendChild(title);

  if (game.tracked) {
    const badge = document.createElement("span");
    badge.className = "journal-badge";
    badge.textContent = "tracked";
    badge.title = "Approved for background OCR awareness";
    header.appendChild(badge);
  }
  header.appendChild(buildJournalActionBtn(
    "Delete game",
    "Remove this game and everything tracked for it (profiles, memories, trackers, training, observations)",
    async () => {
      if (!(await showConfirm(`Delete "${game.title}" and ALL its profiles, game/playthrough memories, trackers, training data, and observations? It will also be un-approved for OCR. This can't be undone.`, { title: "Delete game", danger: true, confirmText: "Delete" }))) return;
      const response = await fetch(`/api/game-state/games/${encodeURIComponent(game.process)}`, { method: "DELETE" });
      if (response.ok) { await loadGamingJournal(); showJournalLibrary(); }
    },
    true,
  ));
  header.appendChild(buildJournalActionBtn(
    "Delete game and blacklist",
    "Delete this game entirely, and blacklist its process so it's never picked up for tracking again",
    async () => {
      if (!(await showConfirm(`Delete "${game.title}" entirely AND blacklist "${game.process}" so it's never tracked again? This can't be undone.`, { title: "Delete game and blacklist", danger: true, confirmText: "Delete and blacklist" }))) return;
      const deleteResponse = await fetch(`/api/game-state/games/${encodeURIComponent(game.process)}`, { method: "DELETE" });
      if (!deleteResponse.ok) return;
      await fetch("/api/game-state/blacklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ process: game.process }),
      });
      await loadGamingJournal();
      showJournalLibrary();
    },
    true,
  ));
  journalDetailContent.appendChild(header);

  // Game-scope memories: hold across every playthrough of this game.
  journalDetailContent.appendChild(buildSectionLabel("Game memories (all playthroughs)"));
  const gameMemList = document.createElement("div");
  gameMemList.className = "memory-list";
  if (game.memories.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "Nothing saved for this game yet.";
    gameMemList.appendChild(hint);
  }
  for (const entry of game.memories) {
    gameMemList.appendChild(buildMemoryItem(entry, () => refreshJournalDetail(game.process)));
  }
  journalDetailContent.appendChild(gameMemList);
  journalDetailContent.appendChild(buildJournalAddForm(
    "Add a game memory (true across playthroughs)…",
    (content) => ({ content, scope: "game", process: game.process })
  ));

  // Profiles (named sessions), each with its playthrough-scope memories + observations.
  for (const session of game.sessions) {
    const profile = document.createElement("div");
    profile.className = "journal-profile";

    const pHeader = document.createElement("div");
    pHeader.className = "journal-game-header";
    const pTitle = document.createElement("span");
    pTitle.className = "journal-profile-title";
    pTitle.textContent = `Profile: ${session.name}`;
    pHeader.appendChild(pTitle);
    if (session.active) {
      const badge = document.createElement("span");
      badge.className = "journal-badge journal-badge--active";
      badge.textContent = "active";
      badge.title = "The profile new playthrough facts currently go to";
      pHeader.appendChild(badge);
    } else {
      pHeader.appendChild(buildJournalActionBtn(
        "Make active",
        "Switch to this profile — new playthrough facts will go here",
        async () => {
          const response = await fetch(
            `/api/game-state/sessions/${encodeURIComponent(game.process)}/${encodeURIComponent(session.session_id)}/active`,
            { method: "PUT" },
          );
          if (response.ok) await refreshJournalDetail(game.process);
        },
      ));
    }
    pHeader.appendChild(buildJournalActionBtn(
      "Delete",
      "Delete this profile and its playthrough memories",
      async () => {
        if (!(await showConfirm(`Delete profile "${session.name}" and its playthrough memories? Game-wide memories stay. This can't be undone.`, { title: "Delete profile", danger: true, confirmText: "Delete" }))) return;
        const response = await fetch(
          `/api/game-state/sessions/${encodeURIComponent(game.process)}/${encodeURIComponent(session.session_id)}`,
          { method: "DELETE" },
        );
        if (response.ok) await refreshJournalDetail(game.process);
      },
      true,
    ));
    profile.appendChild(pHeader);

    const sessMemList = document.createElement("div");
    sessMemList.className = "memory-list";
    if (session.memories.length === 0) {
      const hint = document.createElement("div");
      hint.className = "memory-empty-hint";
      hint.textContent = "Nothing saved for this playthrough yet.";
      sessMemList.appendChild(hint);
    }
    for (const entry of session.memories) {
      sessMemList.appendChild(buildMemoryItem(entry, () => refreshJournalDetail(game.process)));
    }
    profile.appendChild(sessMemList);
    profile.appendChild(buildJournalAddForm(
      "Add a playthrough memory…",
      (content) => ({ content, scope: "session", process: game.process, session_id: session.session_id })
    ));

    if (session.observations.length > 0) {
      profile.appendChild(buildSectionLabel("Unconfirmed observations (auto-read from screen)"));
      const obsList = document.createElement("div");
      obsList.className = "memory-list";
      for (const obs of session.observations) {
        const item = document.createElement("div");
        item.className = "memory-item journal-observation";
        const text = document.createElement("div");
        text.className = "memory-item-text";
        text.textContent = obs.content;
        const deleteBtn = document.createElement("button");
        deleteBtn.className = "memory-item-delete";
        deleteBtn.textContent = "×";
        deleteBtn.title = "Discard observation";
        deleteBtn.addEventListener("click", async () => {
          const response = await fetch(`/api/observations/${obs.id}`, { method: "DELETE" });
          if (response.ok) await refreshJournalDetail(game.process);
        });
        item.appendChild(text);
        item.appendChild(deleteBtn);
        obsList.appendChild(item);
      }
      profile.appendChild(obsList);
    }

    journalDetailContent.appendChild(profile);
  }

  // Add a new profile (playthrough) for this game.
  journalDetailContent.appendChild(buildSectionLabel("New profile"));
  journalDetailContent.appendChild(buildProfileAddForm(game.process));

  // Training data: the living reference document the Game-State Model self-maintains for this
  // process (see app/core/game_state_training_data.py), editable directly.
  journalDetailContent.appendChild(buildSectionLabel("Training data"));
  const trainingHint = document.createElement("p");
  trainingHint.className = "modal-hint";
  trainingHint.textContent = "Notes the Game-State Model keeps for itself about how to read this game's HUD/UI. Edit directly if something's wrong or stale.";
  journalDetailContent.appendChild(trainingHint);
  const trainingTextarea = document.createElement("textarea");
  trainingTextarea.rows = 10;
  trainingTextarea.placeholder = "No training data yet - the Game-State Model writes it automatically as it learns this game's UI (requires self-training to be enabled).";
  journalDetailContent.appendChild(trainingTextarea);

  let currentTrainingContent = "";
  fetch(`/api/game-state/training-data/${encodeURIComponent(game.process)}`)
    .then((r) => r.json())
    .then((data) => {
      currentTrainingContent = data.content || "";
      trainingTextarea.value = currentTrainingContent;
    })
    .catch(() => {});

  trainingTextarea.addEventListener("blur", async () => {
    const content = trainingTextarea.value;
    if (content === currentTrainingContent) return;
    const data = await fetch(`/api/game-state/training-data/${encodeURIComponent(game.process)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then((r) => r.json());
    currentTrainingContent = data.content;
  });
}

// --- Reminders & Alarms modal ---
// Read-only management views: the agent creates/removes these itself via tool calls during
// conversation (add_reminder/remove_reminder, add_alarm/cancel_alarm) - this just lets the user
// audit and delete them directly, mirroring the Memory tab's list/delete pattern.

