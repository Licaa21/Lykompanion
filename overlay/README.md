# Lykompanion native overlay

A standalone Windows executable that draws the companion's game-state panel and
reply/reminder toasts **on top of the game**. It is fully self-contained C++ —
window management, Direct2D/DirectWrite rendering, input, edit mode and layout
persistence all live here. Lykompanion (the Python app) is only a client that
launches this exe and pushes content to it.

## How it renders (and why)

- **Layered window + `UpdateLayeredWindow`** with per-pixel alpha, drawn by
  **Direct2D** into a 32bpp premultiplied-ARGB DIB. No GDI *painting* (gdi32 is
  used only for the DIB/DC plumbing `UpdateLayeredWindow` requires).
- **No DLL injection / Present-hooking.** The overlay composits entirely outside
  every game process, so anti-cheat (BattlEye/EAC) has nothing to flag. The
  trade-offs: it can't show over **exclusive-fullscreen** games (use borderless /
  windowed), and it doesn't get per-game-object z-ordering.

## Local API (named pipe)

The exe hosts a byte-stream named pipe **`\\.\pipe\lykompanion-overlay`** and
reads **newline-delimited JSON** commands. Any process can drive it — Python is
just one client. Commands:

```json
{"type":"toast","text":"...","kind":"reply"|"reminder"}
{"type":"image","url":"https://...","alt":"caption"}
{"type":"game_state","title":"...","rows":[["Label","Value"], ...]}
{"type":"edit_mode","enabled":true}
{"type":"set_hotkey","mods":["ctrl","shift"],"key":"o"}
{"type":"quit"}
```

`set_hotkey` re-registers the global edit-mode hotkey immediately in an
already-running overlay (`mods` is any of `ctrl`/`shift`/`alt`/`win`); the same
combo is also written into `overlay_layout.json` so a not-yet-running overlay
picks it up at its next launch too (`RegisterHotKey` runs before the pipe
server is guaranteed to be listening).

`image` commands download the URL on a background thread (WIC decode + downscale)
and render it as a toast with an optional caption — so companion replies that
embed a web image (e.g. a map screenshot) show up in the overlay too.

Quick manual test (PowerShell, no Python involved):

```powershell
.\Lykompanion-overlay.exe            # run it (nothing shows until a command arrives)
# in a second shell:
$p = New-Object System.IO.Pipes.NamedPipeClientStream('.', 'lykompanion-overlay', 'Out')
$p.Connect(2000); $w = New-Object System.IO.StreamWriter($p); $w.AutoFlush = $true
$w.WriteLine('{"type":"game_state","title":"Test","rows":[["HP","72/100"],["Zone","Cave"]]}')
$w.WriteLine('{"type":"toast","text":"hello from the pipe","kind":"reply"}')
```

## Edit mode

Press the configured hotkey (**Ctrl+Shift+O** by default, changeable in
Settings — see below) to toggle edit mode: widgets become draggable, empty ones
show a placeholder, and a top-center toolbar exposes **opacity**, **text
size**, **font**, an **accent-color** picker, per-area **show/hide** toggles
(Chat = reply/reminder/image toasts, Memory = memory save/remove toasts, Panel
= game-state panel, Mic = hands-free indicator), a **presets** row, and
**Save** / **Discard & Close** buttons. The memory toasts live in their **own
draggable area** (bottom-right by default), separate from the reply/reminder
toasts (top-right).

Changes are no longer saved on every drag or toolbar click — only an explicit
**Save** commits the current layout to the active preset. Pressing the hotkey
again while something is unsaved opens a **Save & Exit / Discard & Exit / Keep
Editing** prompt instead of guessing; **Discard & Close** always reverts to how
things looked when edit mode was opened. **Presets** let you keep several named
layouts (switch via the toolbar chips or a gamepad's LB/RB) and create/delete
them (+ / × / a mouse-only, keyboard-typed rename — no on-screen keyboard).
Everything persists to `%LOCALAPPDATA%\Lykompanion\overlay_layout.json`.

A connected **Xbox-style gamepad** (via XInput) drives the whole editor
alongside the mouse: D-pad/left-stick cycles the selected widget (shown with a
solid accent ring, gamepad-only — mouse dragging has no "selected" concept),
right-stick moves it, **A** = Save, **B** = Discard & Close, **X** = delete the
active preset, **Y** = create a new one, **LB**/**RB** = switch presets.

Control hints live in ONE place — a row at the bottom of the toolbar — not
repeated on every widget. It shows the configured hotkey text when you're using
the mouse, or real controller-button glyphs (colored A/B/X/Y circles, gray
LS/RS/LB/RB pills) when a gamepad is active, adapting to whichever input you
used most recently. Entering edit mode also takes OS focus away from the game
(best-effort — a game reading raw/exclusive input won't notice), so it stops
processing keyboard/mouse input while you're dragging widgets around; focus
returns to the game automatically on exit.

**Known limitation: a gamepad controls the game AND the editor at the same
time.** Focus-stealing only helps keyboard/mouse, which Windows routes to
whichever window has focus. XInput controllers don't work that way —
`XInputGetState()` is a direct poll of the physical device, and every process
polling it (the game, and this overlay's `PollGamepad`) sees the identical raw
state regardless of which window is focused or foreground. There is no Win32
API to give one process exclusive controller access; the only real fix would
be a virtual-controller driver (e.g. ViGEmBus, as Steam Input/DS4Windows do) —
a materially bigger dependency, out of scope for now. Practically: pause or
stand still in-game before editing with a gamepad, since your inputs will also
reach the game.

Saying a configured phrase (Settings → "edit overlay" phrase, off by default)
also opens edit mode directly — detected locally in the browser, never sent to
the AI; no-ops if the overlay isn't running.

`Lykompanion-overlay.exe --demo` shows sample content for ~20s for a quick visual check.

## Building

A prebuilt `Lykompanion-overlay.exe` is committed so most users don't need a compiler. To
rebuild after changing `overlay.cpp`:

```
build.cmd
```

Requires **VS Build Tools** with the C++ workload (MSVC `cl.exe`; the script
auto-loads `vcvars64` if it isn't already on PATH). Single translation unit,
links `user32 gdi32 d2d1 dwrite` — no CMake, no vcpkg.
