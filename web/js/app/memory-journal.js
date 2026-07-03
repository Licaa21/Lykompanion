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

// --- Gaming Journal modal ---
// Per-game view of everything the companion knows: game-scope memories (hold across all
// playthroughs), profiles (named sessions) with their playthrough-scope memories, and each
// profile's unconfirmed screen observations. Also hosts the Game Awareness settings tab,
// moved out of Settings.

const gamingJournalModal = document.getElementById("gaming-journal-modal");
const gamingJournalBtn = document.getElementById("gaming-journal-btn");
const gamingJournalList = document.getElementById("gaming-journal-list");

gamingJournalBtn.addEventListener("click", () => {
  openModal(gamingJournalModal);
  loadGamingJournal();
  loadGameStateProcessLists();
});

async function loadGamingJournal() {
  let games = [];
  try {
    const response = await fetch("/api/gaming-journal");
    if (response.ok) games = await response.json();
  } catch (err) {
    games = [];
  }
  renderGamingJournal(games);
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
    await loadGamingJournal();
  });
  return form;
}

function buildSectionLabel(text) {
  const label = document.createElement("div");
  label.className = "journal-section-label";
  label.textContent = text;
  return label;
}

function renderGamingJournal(games) {
  gamingJournalList.innerHTML = "";

  if (games.length === 0) {
    const hint = document.createElement("div");
    hint.className = "memory-empty-hint";
    hint.textContent = "No games yet - approve a game for tracking (Game Awareness tab) or let the companion learn about one in conversation.";
    gamingJournalList.appendChild(hint);
    return;
  }

  for (const game of games) {
    const card = document.createElement("div");
    card.className = "journal-game";

    const header = document.createElement("div");
    header.className = "journal-game-header";
    const title = document.createElement("span");
    title.className = "journal-game-title";
    title.textContent = game.process;
    header.appendChild(title);
    if (game.tracked) {
      const badge = document.createElement("span");
      badge.className = "journal-badge";
      badge.textContent = "tracked";
      badge.title = "Approved for background OCR awareness";
      header.appendChild(badge);
    }
    card.appendChild(header);

    // Game-scope memories: hold across every playthrough of this game.
    card.appendChild(buildSectionLabel("Game memories (all playthroughs)"));
    const gameMemList = document.createElement("div");
    gameMemList.className = "memory-list";
    if (game.memories.length === 0) {
      const hint = document.createElement("div");
      hint.className = "memory-empty-hint";
      hint.textContent = "Nothing saved for this game yet.";
      gameMemList.appendChild(hint);
    }
    for (const entry of game.memories) {
      gameMemList.appendChild(buildMemoryItem(entry, loadGamingJournal));
    }
    card.appendChild(gameMemList);
    card.appendChild(buildJournalAddForm(
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
      }
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
        sessMemList.appendChild(buildMemoryItem(entry, loadGamingJournal));
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
            if (response.ok) loadGamingJournal();
          });
          item.appendChild(text);
          item.appendChild(deleteBtn);
          obsList.appendChild(item);
        }
        profile.appendChild(obsList);
      }

      card.appendChild(profile);
    }

    gamingJournalList.appendChild(card);
  }
}

// --- Reminders & Alarms modal ---
// Read-only management views: the agent creates/removes these itself via tool calls during
// conversation (add_reminder/remove_reminder, add_alarm/cancel_alarm) - this just lets the user
// audit and delete them directly, mirroring the Memory tab's list/delete pattern.

