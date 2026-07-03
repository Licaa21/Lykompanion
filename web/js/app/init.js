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

init();
