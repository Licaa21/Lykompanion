"""Native in-game overlay window - NO WebView2/pywebview involved.

Rebuild of the overlay after the WebView2 approach was fully reverted (see the overlay
gotcha in CLAUDE.md): WebView2 windows can't be made transparent (Chromium's GPU compositor
can't render into layered windows) or region-clipped (DirectComposition ignores GDI window
regions). This module instead draws with GDI+ into a premultiplied-ARGB DIB and commits it
with UpdateLayeredWindow - the classic per-pixel-alpha technique real game overlays use.
Software-rendered, so none of the GPU-compositor limitations apply. Works over
borderless/windowed games; exclusive fullscreen bypasses the desktop compositor and can
never show any overlay (acceptable - most games are played borderless).

Everything is ctypes against user32/gdi32/gdiplus - no C++ build step, no new deps.

Sprint 1 (this file): window class + message-loop thread + GDI+ rendering + a demo card.
Standalone test:  .venv\\Scripts\\python.exe overlay_native.py
shows a sample card near the top-right of the screen for 10 seconds, then exits. It should
be a dark rounded card with crisp text and the desktop visible around it - NOT a black box.
Later sprints: toast/game-state content, events wiring, run_app.py start/kill, edit mode.
"""

import ctypes
import threading
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdiplus = ctypes.WinDLL("gdiplus")

# --- win32 constants ---
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x0008
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20
WS_EX_NOACTIVATE = 0x8000000
ULW_ALPHA = 2
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_APP_RENDER = 0x8001  # WM_APP+1: re-render request posted from other threads

# --- GDI+ constants ---
_PIXFMT_32BPP_PARGB = 0xE200B
_UNIT_PIXEL = 2
_SMOOTHING_ANTIALIAS = 4
_TEXT_HINT_ANTIALIAS = 4


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class _GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", wt.BOOL), ("SuppressExternalCodecs", wt.BOOL),
    ]


class _RectF(ctypes.Structure):
    _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float),
                ("W", ctypes.c_float), ("H", ctypes.c_float)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]

_gdiplus_token = None
_wndclass_atom = None
_CLASS_NAME = "LykompanionNativeOverlay"


def _ensure_gdiplus() -> None:
    global _gdiplus_token
    if _gdiplus_token is None:
        token = ctypes.c_void_p()
        startup = _GdiplusStartupInput(1, None, False, False)
        gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(startup), None)
        _gdiplus_token = token


def _argb(a: int, r: int, g: int, b: int) -> int:
    return (a << 24) | (r << 16) | (g << 8) | b


class OverlayWindow:
    """One layered, topmost, click-through, non-activating overlay window, driven by its
    own message-loop thread (window creation and GetMessage must share a thread). Drawing
    happens through a caller-supplied paint callback: paint(graphics, width, height) draws
    GDI+ content onto a fully transparent PARGB surface, and the result is committed with
    UpdateLayeredWindow (which also positions/sizes the window - there is no WM_PAINT)."""

    def __init__(self, x: int, y: int, width: int, height: int, paint) -> None:
        self._x, self._y, self._w, self._h = x, y, width, height
        self._paint = paint
        self._hwnd = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    # -- public API (any thread) --

    def start(self) -> None:
        _ensure_gdiplus()
        self._thread.start()
        self._ready.wait(timeout=10)

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._thread.join(timeout=5)

    def request_render(self) -> None:
        """Re-run the paint callback and recommit the surface (thread-safe)."""
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_APP_RENDER, 0, 0)

    def move(self, x: int, y: int) -> None:
        self._x, self._y = x, y
        self.request_render()

    def set_click_through(self, enabled: bool) -> None:
        if not self._hwnd:
            return
        style = user32.GetWindowLongW(self._hwnd, -20)
        style = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
        user32.SetWindowLongW(self._hwnd, -20, style)

    # -- message-loop thread --

    def _run(self) -> None:
        global _wndclass_atom
        if _wndclass_atom is None:
            wc = wt.WNDCLASSW()
            wc.lpfnWndProc = _WNDPROC(self._wndproc_static)
            wc.lpszClassName = _CLASS_NAME
            wc.hInstance = None
            self.__class__._wndproc_ref = wc.lpfnWndProc  # keep alive - GC'd callback = crash
            _wndclass_atom = user32.RegisterClassW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
            _CLASS_NAME, "Lykompanion Overlay", WS_POPUP,
            self._x, self._y, self._w, self._h, None, None, None, None,
        )
        self._render()
        user32.ShowWindow(self._hwnd, 8)  # SW_SHOWNA - visible, never activated
        self._ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_RENDER and msg.hwnd == self._hwnd:
                self._render()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None

    @staticmethod
    def _wndproc_static(hwnd, message, wparam, lparam):
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    # -- rendering --

    def _render(self) -> None:
        w, h = self._w, self._h
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)

        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = w, -h  # negative = top-down rows
        bmi.biPlanes, bmi.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        old_bmp = gdi32.SelectObject(mem_dc, dib)

        # GDI+ bitmap aliasing the DIB memory as premultiplied ARGB - GDI+ then writes
        # correct alpha (plain GdipCreateFromHDC would treat the surface as opaque RGB).
        bitmap = ctypes.c_void_p()
        gdiplus.GdipCreateBitmapFromScan0(w, h, w * 4, _PIXFMT_32BPP_PARGB, bits, ctypes.byref(bitmap))
        graphics = ctypes.c_void_p()
        gdiplus.GdipGetImageGraphicsContext(bitmap, ctypes.byref(graphics))
        gdiplus.GdipSetSmoothingMode(graphics, _SMOOTHING_ANTIALIAS)
        gdiplus.GdipSetTextRenderingHint(graphics, _TEXT_HINT_ANTIALIAS)
        gdiplus.GdipGraphicsClear(graphics, 0)  # fully transparent

        try:
            self._paint(graphics, w, h)
        except Exception:
            pass  # a bad paint frame must never kill the message loop

        gdiplus.GdipDeleteGraphics(graphics)
        gdiplus.GdipDisposeImage(bitmap)

        blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        pos = wt.POINT(self._x, self._y)
        size = wt.SIZE(w, h)
        src = wt.POINT(0, 0)
        user32.UpdateLayeredWindow(self._hwnd, screen_dc, ctypes.byref(pos), ctypes.byref(size),
                                   mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)

        gdi32.SelectObject(mem_dc, old_bmp)
        gdi32.DeleteObject(dib)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


# --- GDI+ drawing helpers (used by paint callbacks) ---

def draw_rounded_card(graphics, x, y, w, h, radius, fill_argb, border_argb) -> None:
    path = ctypes.c_void_p()
    gdiplus.GdipCreatePath(0, ctypes.byref(path))
    d = radius * 2
    for cx, cy, start in ((x, y, 180), (x + w - d, y, 270), (x + w - d, y + h - d, 0), (x, y + h - d, 90)):
        gdiplus.GdipAddPathArc(path, ctypes.c_float(cx), ctypes.c_float(cy),
                               ctypes.c_float(d), ctypes.c_float(d),
                               ctypes.c_float(start), ctypes.c_float(90))
    gdiplus.GdipClosePathFigure(path)

    brush = ctypes.c_void_p()
    gdiplus.GdipCreateSolidFill(ctypes.c_uint(fill_argb), ctypes.byref(brush))
    gdiplus.GdipFillPath(graphics, brush, path)
    gdiplus.GdipDeleteBrush(brush)

    pen = ctypes.c_void_p()
    gdiplus.GdipCreatePen1(ctypes.c_uint(border_argb), ctypes.c_float(1.0), _UNIT_PIXEL, ctypes.byref(pen))
    gdiplus.GdipDrawPath(graphics, pen, path)
    gdiplus.GdipDeletePen(pen)
    gdiplus.GdipDeletePath(path)


def draw_text(graphics, text, x, y, w, h, size_px, argb, bold=False) -> None:
    family = ctypes.c_void_p()
    gdiplus.GdipCreateFontFamilyFromName("Segoe UI", None, ctypes.byref(family))
    font = ctypes.c_void_p()
    gdiplus.GdipCreateFont(family, ctypes.c_float(size_px), 1 if bold else 0, _UNIT_PIXEL, ctypes.byref(font))
    brush = ctypes.c_void_p()
    gdiplus.GdipCreateSolidFill(ctypes.c_uint(argb), ctypes.byref(brush))
    rect = _RectF(x, y, w, h)
    gdiplus.GdipDrawString(graphics, text, -1, font, ctypes.byref(rect), None, brush)
    gdiplus.GdipDeleteBrush(brush)
    gdiplus.GdipDeleteFont(font)
    gdiplus.GdipDeleteFontFamily(family)


# --- standalone demo (sprint-1 acceptance test, run by the user) ---

if __name__ == "__main__":
    import time

    def _demo_paint(graphics, w, h):
        draw_rounded_card(graphics, 0, 0, w, h, 10,
                          _argb(235, 16, 18, 26), _argb(60, 255, 255, 255))
        draw_text(graphics, "COMPANION", 14, 10, w - 28, 20, 11, _argb(170, 232, 234, 242), bold=True)
        draw_text(graphics, "Native overlay test - the desktop should be visible all around "
                            "this card, with crisp readable text.", 14, 30, w - 28, h - 40,
                  15, _argb(255, 232, 234, 242))

    user32.SetProcessDPIAware()
    screen_w = user32.GetSystemMetrics(0)
    win = OverlayWindow(screen_w - 460, 40, 420, 110, _demo_paint)
    win.start()
    print("Overlay demo visible for 10 seconds (top-right of the screen)...")
    time.sleep(10)
    win.stop()
    print("Done.")
