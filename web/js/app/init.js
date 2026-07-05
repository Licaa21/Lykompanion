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
    let snapZone = null;
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

    // Windows-Snap-style gray preview box, drawn inside our own (frameless) window while the
    // titlebar is dragged near an edge. Positioned in client px = snap-target screen rect minus the
    // window's live screen origin, so it stays pinned to the screen even as the window follows the
    // cursor. Clamped to the window (we can't paint outside it) — a hint, not the true OS overlay.
    let previewEl = null;
    const preview = () => {
      if (!previewEl) {
        previewEl = document.createElement('div');
        previewEl.className = 'snap-preview';
        document.body.appendChild(previewEl);
      }
      return previewEl;
    };
    const showPreview = (zone) => {
      const t = snapTarget(zone);
      const el = preview();
      el.style.left = (t.x - window.screenX) + 'px';
      el.style.top = (t.y - window.screenY) + 'px';
      el.style.width = t.w + 'px';
      el.style.height = t.h + 'px';
      el.classList.add('visible');
    };
    const hidePreview = () => { if (previewEl) previewEl.classList.remove('visible'); };

    const drag = document.querySelector('.titlebar-drag');
    if (drag) {
      // Double-click titlebar: maximize when floating; when already maximized, shrink to a
      // half-size window centered on the work area (min-size clamped).
      const centerHalf = () => {
        const a = workArea();
        const w = Math.max(Math.round(a.w / 2), 800);
        const h = Math.max(Math.round(a.h / 2), 600);
        snapZone = null; restoreBounds = null;
        commit(Math.round(a.x + (a.w - w) / 2), Math.round(a.y + (a.h - h) / 2), w, h);
      };
      const toggleMax = () => {
        if (!api()?.window_set_bounds) return;
        if (snapZone === 'max') centerHalf();
        else applySnap('max', curBounds());
      };
      drag.addEventListener('dblclick', toggleMax);
      const max = document.getElementById('win-max');
      if (max) max.addEventListener('click', toggleMax);

      // Titlebar drag — we own it (no pywebview-drag-region) so we can snap on release. Dragging
      // into a screen edge snaps: top = maximize, left/right = half. Moving = one bridge call/frame.
      drag.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || !api()?.window_set_bounds) return;
        const sx = e.screenX, sy = e.screenY, downClientX = e.clientX;
        // Capture the snap state at press time but DON'T un-snap yet — un-snapping happens on the
        // first actual move. A press with no move (e.g. a double-click) must leave snapZone intact
        // so the dblclick handler can see it (else the pointerdown would clear it first).
        const wasSnapped = snapZone;
        const snappedRestore = restoreBounds;
        let start = null; // computed on first move
        drag.setPointerCapture(e.pointerId);
        const EDGE = 6;
        let raf = 0, pending = null, zone = null, moved = false;
        const flush = () => { raf = 0; if (pending) setBounds(pending.x, pending.y, pending.w, pending.h); };
        const onMove = (ev) => {
          if (!moved) {
            moved = true;
            if (wasSnapped) {
              // Un-snap: restore the floating size, cursor kept over the titlebar horizontally.
              const size = snappedRestore || curBounds();
              start = { x: Math.round(sx - (downClientX / window.innerWidth) * size.w), y: window.screenY, w: size.w, h: size.h };
              snapZone = null; restoreBounds = null;
            } else {
              start = curBounds();
            }
          }
          pending = { x: Math.round(start.x + (ev.screenX - sx)), y: Math.round(start.y + (ev.screenY - sy)), w: start.w, h: start.h };
          const a = workArea();
          // Keep the titlebar reachable, like the OS does: never let it slip above the work area,
          // behind the taskbar, or so far off the sides that too little is left to grab. Without this
          // (frameless + JS-only drag) the titlebar can vanish under the taskbar and become unmovable.
          const TITLEBAR = 34, EDGE_KEEP = 80;
          pending.x = Math.min(Math.max(pending.x, a.x - (start.w - EDGE_KEEP)), a.x + a.w - EDGE_KEEP);
          pending.y = Math.min(Math.max(pending.y, a.y), a.y + a.h - TITLEBAR);
          const prevZone = zone;
          zone = ev.screenY <= a.y + EDGE ? 'max'
            : ev.screenX <= a.x + EDGE ? 'left'
            : ev.screenX >= a.x + a.w - EDGE ? 'right'
            : null;
          if (zone) showPreview(zone);        // recompute each frame so it stays pinned to the screen
          else if (prevZone) hidePreview();
          if (!raf) raf = requestAnimationFrame(flush);
        };
        const onUp = () => {
          try { drag.releasePointerCapture(e.pointerId); } catch (_) {}
          drag.removeEventListener('pointermove', onMove);
          drag.removeEventListener('pointerup', onUp);
          hidePreview();
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

    // run_app.py launches the window hidden, at half work-area width. 100ms after the window is
    // ready, drive it to full size through the same maximize path the titlebar's maximize button
    // uses (so snapZone/restoreBounds end up correctly seeded - restoreBounds becomes this
    // initial half-width window), then reveal it - the whole half->full transition happens while
    // still hidden, so the window only ever appears already full-size.
    setTimeout(() => {
      applySnap('max', curBounds());
      api()?.window_show?.();
    }, 100);
  };

  if (api()) apply();
  else window.addEventListener('pywebviewready', apply);
}
setupDesktopTitlebar();

// F11 app-wide fullscreen. Desktop app: pywebview's native toggle (same mechanism as the YouTube
// pop-out window's own fullscreen button) resizes the actual OS window over the whole screen;
// body.app-fullscreen (CSS) hides the custom titlebar for it, since pywebview's fullscreen only
// touches the OS window frame/bounds, not our own HTML chrome — flexbox then reflows .app-shell
// to fill the freed space on its own, no extra sizing needed. Plain browser: falls back to the
// real Fullscreen API, which has no equivalent "hide the titlebar" concern (it's not shown there).
function setupAppFullscreen() {
  const bridge = () => window.pywebview?.api;
  let pywebviewFullscreen = false; // pywebview gives no change event - we're the only toggler

  function toggle() {
    const api = bridge();
    if (api?.window_toggle_fullscreen) {
      api.window_toggle_fullscreen();
      pywebviewFullscreen = !pywebviewFullscreen;
      document.body.classList.toggle('app-fullscreen', pywebviewFullscreen);
    } else if (document.fullscreenEnabled) {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen().catch(() => {});
    }
  }
  document.addEventListener('fullscreenchange', () => {
    if (!bridge()?.window_toggle_fullscreen) {
      document.body.classList.toggle('app-fullscreen', !!document.fullscreenElement);
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'F11') return;
    e.preventDefault();
    toggle();
  });
}
setupAppFullscreen();

init();
