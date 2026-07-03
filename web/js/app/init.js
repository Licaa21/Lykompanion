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

// Custom titlebar + window controls (frameless desktop app only). Reveals the .titlebar/resize
// grips and drives minimize/close, double-click-maximize, and edge/corner resize through the
// Python-exposed pywebview controls. In a plain browser window.pywebview never appears, so this
// all stays inert and the native browser chrome is used instead.
function setupDesktopTitlebar() {
  const api = () => window.pywebview?.api;

  const apply = () => {
    document.body.classList.add('desktop-app');
    const min = document.getElementById('win-min');
    const close = document.getElementById('win-close');
    if (min) min.addEventListener('click', () => api()?.window_minimize?.());
    if (close) close.addEventListener('click', () => api()?.window_close?.());

    // All bounds we read (screenX/innerWidth/screen.avail*) are logical CSS px; pywebview's
    // move/resize expect physical px, so scale by devicePixelRatio on the way out or the window
    // drifts by the display scale factor on HiDPI screens.
    const setBounds = (x, y, w, h) => {
      const r = window.devicePixelRatio || 1;
      api()?.window_set_bounds?.(Math.round(x * r), Math.round(y * r), Math.round(w * r), Math.round(h * r));
    };

    // Maximize/restore: fit the screen work area (taskbar-aware via screen.avail*), remembering
    // the pre-maximize bounds. Any manual resize clears the memory, like a native window.
    let savedBounds = null;
    const toggleMaximize = () => {
      if (!api()?.window_set_bounds) return;
      if (savedBounds) {
        const b = savedBounds;
        savedBounds = null;
        setBounds(b.x, b.y, b.w, b.h);
      } else {
        savedBounds = { x: window.screenX, y: window.screenY, w: window.innerWidth, h: window.innerHeight };
        setBounds(screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight);
      }
    };
    const drag = document.querySelector('.titlebar-drag');
    if (drag) drag.addEventListener('dblclick', toggleMaximize);

    // Edge/corner resize. Coalesce moves to one bridge call per frame so pywebview keeps up.
    const MIN_W = 800, MIN_H = 600;
    document.querySelectorAll('.resize-grip').forEach((grip) => {
      grip.addEventListener('pointerdown', (e) => {
        const a = api();
        if (!a?.window_set_bounds) return;
        e.preventDefault();
        savedBounds = null;
        const dir = grip.dataset.dir;
        const sx = e.screenX, sy = e.screenY;
        const start = { x: window.screenX, y: window.screenY, w: window.innerWidth, h: window.innerHeight };
        grip.setPointerCapture(e.pointerId);
        let raf = 0, pending = null;
        const flush = () => { raf = 0; if (pending) a.window_set_bounds(pending.x, pending.y, pending.w, pending.h); };
        const onMove = (ev) => {
          const dx = ev.screenX - sx, dy = ev.screenY - sy;
          let x = start.x, y = start.y, w = start.w, h = start.h;
          if (dir.includes('e')) w = start.w + dx;
          if (dir.includes('s')) h = start.h + dy;
          if (dir.includes('w')) { w = start.w - dx; x = start.x + dx; }
          if (dir.includes('n')) { h = start.h - dy; y = start.y + dy; }
          if (w < MIN_W) { if (dir.includes('w')) x -= (MIN_W - w); w = MIN_W; }
          if (h < MIN_H) { if (dir.includes('n')) y -= (MIN_H - h); h = MIN_H; }
          pending = { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
          if (!raf) raf = requestAnimationFrame(flush);
        };
        const onUp = () => {
          try { grip.releasePointerCapture(e.pointerId); } catch (_) {}
          grip.removeEventListener('pointermove', onMove);
          grip.removeEventListener('pointerup', onUp);
        };
        grip.addEventListener('pointermove', onMove);
        grip.addEventListener('pointerup', onUp);
      });
    });
  };

  if (api()) apply();
  else window.addEventListener('pywebviewready', apply);
}
setupDesktopTitlebar();

init();
