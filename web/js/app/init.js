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

    // Bounds we read (screenX/innerWidth/screen.avail*) are logical CSS px. setBounds scales by
    // devicePixelRatio because pywebview's move/resize expect physical px (else the window drifts
    // by the display scale on HiDPI). saveBounds persists LOGICAL px for next-launch restore
    // (Python's create_window is logical too) — called only at gesture end, not every frame.
    const setBounds = (x, y, w, h) => {
      const r = window.devicePixelRatio || 1;
      api()?.window_set_bounds?.(Math.round(x * r), Math.round(y * r), Math.round(w * r), Math.round(h * r));
    };
    const saveBounds = (x, y, w, h) =>
      api()?.window_save_bounds?.(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
    const commit = (x, y, w, h) => { setBounds(x, y, w, h); saveBounds(x, y, w, h); };
    const curBounds = () => ({ x: window.screenX, y: window.screenY, w: window.innerWidth, h: window.innerHeight });
    const workArea = () => ({ x: screen.availLeft || 0, y: screen.availTop || 0, w: screen.availWidth, h: screen.availHeight });

    // Lightweight Windows-Snap: snapZone tracks where the window is snapped; restoreBounds is the
    // floating rect to return to. No OS snap overlay/layouts — this is a pure JS reimplementation.
    let snapZone = null;      // 'max' | 'left' | 'right' | null
    let restoreBounds = null;
    const snapTarget = (zone) => {
      const a = workArea();
      const half = Math.round(a.w / 2);
      if (zone === 'left') return { x: a.x, y: a.y, w: half, h: a.h };
      if (zone === 'right') return { x: a.x + half, y: a.y, w: a.w - half, h: a.h };
      return { x: a.x, y: a.y, w: a.w, h: a.h }; // max
    };
    const applySnap = (zone, restore) => {
      restoreBounds = restore; snapZone = zone;
      const b = snapTarget(zone);
      commit(b.x, b.y, b.w, b.h);
    };
    const restoreFromSnap = () => {
      if (!restoreBounds) return;
      const b = restoreBounds;
      restoreBounds = null; snapZone = null;
      commit(b.x, b.y, b.w, b.h);
    };

    const drag = document.querySelector('.titlebar-drag');
    if (drag) {
      // Double-click titlebar → maximize/restore.
      drag.addEventListener('dblclick', () => {
        if (!api()?.window_set_bounds) return;
        if (snapZone) restoreFromSnap();
        else applySnap('max', curBounds());
      });

      // Titlebar drag — we own it (no pywebview-drag-region) so we can snap on release. Dragging
      // into a screen edge snaps: top = maximize, left/right = half. Moving = one bridge call/frame.
      drag.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || !api()?.window_set_bounds) return;
        const sx = e.screenX, sy = e.screenY;
        let start;
        if (snapZone) {
          // Un-snap: restore the floating size, keeping the cursor over the titlebar horizontally.
          const size = restoreBounds || curBounds();
          const fracX = e.clientX / window.innerWidth;
          start = { x: Math.round(e.screenX - fracX * size.w), y: window.screenY, w: size.w, h: size.h };
          snapZone = null; restoreBounds = null;
        } else {
          start = curBounds();
        }
        drag.setPointerCapture(e.pointerId);
        const EDGE = 6;
        let raf = 0, pending = null, zone = null, moved = false;
        const flush = () => { raf = 0; if (pending) setBounds(pending.x, pending.y, pending.w, pending.h); };
        const onMove = (ev) => {
          moved = true;
          pending = { x: Math.round(start.x + (ev.screenX - sx)), y: Math.round(start.y + (ev.screenY - sy)), w: start.w, h: start.h };
          const a = workArea();
          zone = ev.screenY <= a.y + EDGE ? 'max'
            : ev.screenX <= a.x + EDGE ? 'left'
            : ev.screenX >= a.x + a.w - EDGE ? 'right'
            : null;
          if (!raf) raf = requestAnimationFrame(flush);
        };
        const onUp = () => {
          try { drag.releasePointerCapture(e.pointerId); } catch (_) {}
          drag.removeEventListener('pointermove', onMove);
          drag.removeEventListener('pointerup', onUp);
          if (!moved) return;
          if (zone) applySnap(zone, start);
          else saveBounds(pending.x, pending.y, pending.w, pending.h);
        };
        drag.addEventListener('pointermove', onMove);
        drag.addEventListener('pointerup', onUp);
      });
    }

    // Edge/corner resize. Coalesce moves to one bridge call per frame so pywebview keeps up.
    const MIN_W = 800, MIN_H = 600;
    document.querySelectorAll('.resize-grip').forEach((grip) => {
      grip.addEventListener('pointerdown', (e) => {
        if (!api()?.window_set_bounds) return;
        e.preventDefault();
        snapZone = null; restoreBounds = null;
        const dir = grip.dataset.dir;
        const sx = e.screenX, sy = e.screenY;
        const start = curBounds();
        grip.setPointerCapture(e.pointerId);
        let raf = 0, pending = null;
        const flush = () => { raf = 0; if (pending) setBounds(pending.x, pending.y, pending.w, pending.h); };
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
          if (pending) saveBounds(pending.x, pending.y, pending.w, pending.h);
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
