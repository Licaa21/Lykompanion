async function init() {
  await refreshAvatarStatus();
  await loadChatsFromStorage();
  const firstWithMessages = chats.find((c) => c.messages.length > 0);
  if (firstWithMessages) {
    switchChat(firstWithMessages.id);
  } else {
    activeChatId = null;
    renderChatList();
    renderChatLog();
  }
  const cfg = await loadConfig();
  // First run on this machine with nothing configured - walk through setup automatically.
  if (cfg && !localStorage.getItem(QS_DONE_KEY) && providerKeyMissing(cfg)) {
    openQuickSetup();
  }
}

// Custom titlebar (frameless desktop app only). Reveals the .titlebar and wires its buttons to
// the Python-exposed window controls. In a plain browser window.pywebview never appears, so the
// bar stays hidden and the native browser chrome is used instead.
function setupDesktopTitlebar() {
  const apply = () => {
    document.body.classList.add('desktop-app');
    const min = document.getElementById('win-min');
    const close = document.getElementById('win-close');
    if (min) min.addEventListener('click', () => window.pywebview?.api?.window_minimize?.());
    if (close) close.addEventListener('click', () => window.pywebview?.api?.window_close?.());
  };
  if (window.pywebview?.api) apply();
  else window.addEventListener('pywebviewready', apply);
}
setupDesktopTitlebar();

init();
