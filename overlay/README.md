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
{"type":"quit"}
```

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

Press **Ctrl+Shift+O** (a global hotkey the exe registers itself) to toggle edit
mode: widgets become draggable, empty ones show a placeholder, and a top-center
toolbar exposes **opacity**, **text size**, **font**, an **accent-color** picker,
and per-area **show/hide** toggles (Chat = reply/reminder/image toasts, Memory =
memory save/remove toasts, Panel = game-state panel, Mic = hands-free indicator).
The memory toasts live in their **own draggable area** (bottom-right by default),
separate from the reply/reminder toasts (top-right). Positions + appearance persist
to `%LOCALAPPDATA%\Lykompanion\overlay_layout.json`.

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
