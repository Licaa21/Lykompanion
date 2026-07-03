// Lykompanion native overlay — Sprint C1 skeleton.
//
// A standalone, self-contained layered window drawn with Direct2D + DirectWrite
// and committed each frame via UpdateLayeredWindow (software per-pixel alpha).
//
// Deliberate constraints (see TODO.md "Native C++ overlay"):
//   - NO GDI painting. gdi32 is used only for the CreateDIBSection / memory-DC
//     plumbing that UpdateLayeredWindow requires; every visible pixel is drawn
//     by Direct2D / DirectWrite.
//   - NO DLL injection / Present-hooking. The window composits outside every game
//     process, so anti-cheat has nothing to flag. Trade-off: no exclusive-fullscreen.
//
// C1 scope: `overlay.exe --demo` shows a rounded translucent card with crisp
// anti-aliased text over the desktop for 10 seconds, then exits. Later sprints
// add the local API server, multiple widgets, edit-mode and layout persistence.

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>
#include <d2d1.h>
#include <dwrite.h>
#include <cstdint>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")

namespace {

// ---- Small COM helper: release + null on scope exit is done manually here to
// keep the single-TU skeleton dependency-free (no ATL / wil / _com_ptr_t).
template <typename T>
void SafeRelease(T** pp) {
    if (*pp) {
        (*pp)->Release();
        *pp = nullptr;
    }
}

constexpr wchar_t kWindowClass[] = L"LykompanionOverlayC1";

// Card geometry for the demo (device pixels). Later sprints size to content.
constexpr int kCardW = 380;
constexpr int kCardH = 132;

// ---- Direct2D / DirectWrite factories (process-wide, created once).
ID2D1Factory*        g_d2dFactory   = nullptr;
IDWriteFactory*      g_dwriteFactory = nullptr;

// ---- Per-window render resources.
struct RenderTargetResources {
    HDC              memDC   = nullptr;   // memory DC holding the DIB
    HBITMAP          dib     = nullptr;   // 32bpp top-down PARGB DIB section
    HBITMAP          oldBmp  = nullptr;   // original bitmap to restore on teardown
    void*            bits    = nullptr;   // raw DIB pixels (unused directly; D2D writes it)
    int              width   = 0;
    int              height  = 0;
    ID2D1DCRenderTarget* rt  = nullptr;

    // Device-dependent brushes (recreated with the render target).
    ID2D1SolidColorBrush* cardBrush   = nullptr;
    ID2D1SolidColorBrush* borderBrush = nullptr;
    ID2D1SolidColorBrush* textBrush   = nullptr;
    ID2D1SolidColorBrush* dimBrush    = nullptr;

    IDWriteTextFormat* titleFormat = nullptr;
    IDWriteTextFormat* bodyFormat  = nullptr;
};

RenderTargetResources g_res;

bool CreateFactories() {
    HRESULT hr = D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED,
                                   &g_d2dFactory);
    if (FAILED(hr)) return false;

    hr = DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,
                             __uuidof(IDWriteFactory),
                             reinterpret_cast<IUnknown**>(&g_dwriteFactory));
    return SUCCEEDED(hr);
}

// Create (or recreate on resize) the DIB-backed DC render target + resources.
bool CreateDeviceResources(int width, int height) {
    if (g_res.rt && g_res.width == width && g_res.height == height)
        return true;

    // Tear down any previous resources first.
    SafeRelease(&g_res.textBrush);
    SafeRelease(&g_res.borderBrush);
    SafeRelease(&g_res.cardBrush);
    SafeRelease(&g_res.dimBrush);
    SafeRelease(&g_res.rt);
    if (g_res.memDC) {
        if (g_res.oldBmp) SelectObject(g_res.memDC, g_res.oldBmp);
        DeleteDC(g_res.memDC);
        g_res.memDC = nullptr;
    }
    if (g_res.dib) {
        DeleteObject(g_res.dib);
        g_res.dib = nullptr;
    }

    // ---- 32bpp top-down premultiplied-ARGB DIB (top-down: negative height).
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth       = width;
    bmi.bmiHeader.biHeight      = -height;   // top-down
    bmi.bmiHeader.biPlanes      = 1;
    bmi.bmiHeader.biBitCount    = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    HDC screenDC = GetDC(nullptr);
    g_res.dib = CreateDIBSection(screenDC, &bmi, DIB_RGB_COLORS,
                                 &g_res.bits, nullptr, 0);
    g_res.memDC = CreateCompatibleDC(screenDC);
    ReleaseDC(nullptr, screenDC);
    if (!g_res.dib || !g_res.memDC) return false;

    g_res.oldBmp = static_cast<HBITMAP>(SelectObject(g_res.memDC, g_res.dib));

    // ---- Direct2D DC render target bound to the memory DC.
    D2D1_RENDER_TARGET_PROPERTIES props = D2D1::RenderTargetProperties(
        D2D1_RENDER_TARGET_TYPE_DEFAULT,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM,
                          D2D1_ALPHA_MODE_PREMULTIPLIED),
        0.0f, 0.0f,
        D2D1_RENDER_TARGET_USAGE_NONE,
        D2D1_FEATURE_LEVEL_DEFAULT);

    HRESULT hr = g_d2dFactory->CreateDCRenderTarget(&props, &g_res.rt);
    if (FAILED(hr)) return false;

    RECT rc = {0, 0, width, height};
    hr = g_res.rt->BindDC(g_res.memDC, &rc);
    if (FAILED(hr)) return false;

    // CRITICAL for layered windows: ClearType text does not preserve the alpha
    // channel, so committed pixels come out with garbage alpha. Force grayscale
    // AA so DirectWrite writes correct premultiplied alpha.
    g_res.rt->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);

    // ---- Brushes (device-dependent).
    g_res.rt->CreateSolidColorBrush(
        D2D1::ColorF(0.07f, 0.08f, 0.11f, 0.88f), &g_res.cardBrush);
    g_res.rt->CreateSolidColorBrush(
        D2D1::ColorF(0.40f, 0.55f, 0.95f, 0.65f), &g_res.borderBrush);
    g_res.rt->CreateSolidColorBrush(
        D2D1::ColorF(0.95f, 0.96f, 0.98f, 1.0f), &g_res.textBrush);
    g_res.rt->CreateSolidColorBrush(
        D2D1::ColorF(0.62f, 0.66f, 0.74f, 1.0f), &g_res.dimBrush);

    g_res.width  = width;
    g_res.height = height;
    return true;
}

bool CreateTextFormats() {
    if (g_res.titleFormat && g_res.bodyFormat) return true;

    HRESULT hr = g_dwriteFactory->CreateTextFormat(
        L"Segoe UI", nullptr,
        DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_FONT_STYLE_NORMAL,
        DWRITE_FONT_STRETCH_NORMAL, 19.0f, L"en-us", &g_res.titleFormat);
    if (FAILED(hr)) return false;

    hr = g_dwriteFactory->CreateTextFormat(
        L"Segoe UI", nullptr,
        DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
        DWRITE_FONT_STRETCH_NORMAL, 14.0f, L"en-us", &g_res.bodyFormat);
    return SUCCEEDED(hr);
}

// Draw one frame and commit it to the layered window.
void Render(HWND hwnd) {
    if (!CreateDeviceResources(kCardW, kCardH)) return;
    if (!CreateTextFormats()) return;

    g_res.rt->BeginDraw();
    g_res.rt->Clear(D2D1::ColorF(0, 0, 0, 0));   // fully transparent

    const float pad = 1.5f;
    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(pad, pad, kCardW - pad, kCardH - pad), 14.0f, 14.0f);
    g_res.rt->FillRoundedRectangle(card, g_res.cardBrush);
    g_res.rt->DrawRoundedRectangle(card, g_res.borderBrush, 1.4f);

    g_res.rt->DrawText(
        L"Lykompanion", 11,
        g_res.titleFormat,
        D2D1::RectF(20, 16, kCardW - 20, 48),
        g_res.textBrush);

    g_res.rt->DrawText(
        L"Overlay skeleton alive — Direct2D layered window, no GDI paint.",
        60,
        g_res.bodyFormat,
        D2D1::RectF(20, 52, kCardW - 20, kCardH - 16),
        g_res.dimBrush);

    HRESULT hr = g_res.rt->EndDraw();
    if (hr == D2DERR_RECREATE_TARGET) {
        // Device lost: drop resources so the next frame rebuilds them.
        SafeRelease(&g_res.rt);
        g_res.width = g_res.height = 0;
        return;
    }

    // ---- Commit the DIB to the layered window (per-pixel alpha).
    RECT wr;
    GetWindowRect(hwnd, &wr);
    POINT ptDst = {wr.left, wr.top};
    SIZE  size  = {kCardW, kCardH};
    POINT ptSrc = {0, 0};
    BLENDFUNCTION blend = {};
    blend.BlendOp             = AC_SRC_OVER;
    blend.SourceConstantAlpha = 255;
    blend.AlphaFormat         = AC_SRC_ALPHA;

    HDC screenDC = GetDC(nullptr);
    UpdateLayeredWindow(hwnd, screenDC, &ptDst, &size,
                        g_res.memDC, &ptSrc, 0, &blend, ULW_ALPHA);
    ReleaseDC(nullptr, screenDC);
}

void DestroyDeviceResources() {
    SafeRelease(&g_res.bodyFormat);
    SafeRelease(&g_res.titleFormat);
    SafeRelease(&g_res.textBrush);
    SafeRelease(&g_res.borderBrush);
    SafeRelease(&g_res.cardBrush);
    SafeRelease(&g_res.dimBrush);
    SafeRelease(&g_res.rt);
    if (g_res.memDC) {
        if (g_res.oldBmp) SelectObject(g_res.memDC, g_res.oldBmp);
        DeleteDC(g_res.memDC);
        g_res.memDC = nullptr;
    }
    if (g_res.dib) {
        DeleteObject(g_res.dib);
        g_res.dib = nullptr;
    }
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_PAINT:
            Render(hwnd);
            ValidateRect(hwnd, nullptr);
            return 0;
        case WM_TIMER:
            // Demo lifetime timer (id 1) tells us to quit.
            if (wParam == 1) PostQuitMessage(0);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}

HWND CreateOverlayWindow(HINSTANCE hInst) {
    WNDCLASSEX wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hCursor       = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = kWindowClass;
    RegisterClassEx(&wc);

    // Anchor near the top-right of the primary monitor for the demo.
    int screenW = GetSystemMetrics(SM_CXSCREEN);
    int x = screenW - kCardW - 40;
    int y = 40;

    // WS_EX_LAYERED     : per-pixel alpha via UpdateLayeredWindow.
    // WS_EX_TRANSPARENT : mouse clicks pass through to the game (lifted in edit mode later).
    // WS_EX_TOPMOST     : stays above the (borderless) game window.
    // WS_EX_NOACTIVATE  : never steals focus / activation from the game.
    // WS_EX_TOOLWINDOW  : no taskbar button, no alt-tab entry.
    DWORD exStyle = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
                    WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;

    HWND hwnd = CreateWindowEx(
        exStyle, kWindowClass, L"Lykompanion Overlay",
        WS_POPUP,
        x, y, kCardW, kCardH,
        nullptr, nullptr, hInst, nullptr);
    return hwnd;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE, LPWSTR lpCmdLine, int) {
    const bool demo = (wcsstr(lpCmdLine ? lpCmdLine : L"", L"--demo") != nullptr);

    if (!CreateFactories())
        return 1;

    HWND hwnd = CreateOverlayWindow(hInstance);
    if (!hwnd)
        return 2;

    // First paint (layered windows are not shown until UpdateLayeredWindow runs).
    Render(hwnd);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);

    if (demo) {
        // Auto-quit after 10 seconds for the C1 acceptance check.
        SetTimer(hwnd, 1, 10000, nullptr);
    }

    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    DestroyDeviceResources();
    SafeRelease(&g_dwriteFactory);
    SafeRelease(&g_d2dFactory);
    return 0;
}
