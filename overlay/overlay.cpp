// Lykompanion native overlay — Sprints C1 + C2.
//
// A standalone, self-contained set of layered windows drawn with Direct2D +
// DirectWrite and committed via UpdateLayeredWindow (software per-pixel alpha).
// The overlay hosts its OWN local API (a named-pipe JSON server) that any
// process can push commands into — Lykompanion's Python side is just one client.
//
// Deliberate constraints (see TODO.md "Native C++ overlay"):
//   - NO GDI painting. gdi32 is used only for the CreateDIBSection / memory-DC
//     plumbing UpdateLayeredWindow requires; every visible pixel is Direct2D.
//   - NO DLL injection / Present-hooking. Composits outside every game process,
//     so anti-cheat has nothing to flag. Trade-off: no exclusive-fullscreen.
//
// C2 scope: named-pipe server on a background thread; newline-delimited JSON
// commands (toast / game_state / edit_mode / quit); a stacking toast widget and
// a game-state panel widget, both auto-sized from wrapped text and anchored to a
// screen corner. Edit-mode (drag + layout persistence) is Sprint C3 — here the
// command is parsed and stored but only stubbed.
//
// Pipe name: \\.\pipe\lykompanion-overlay   (byte stream, newline-delimited JSON)

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS   // _wgetenv is fine for our use
#endif

#include <windows.h>
#include <windowsx.h>
#include <d2d1.h>
#include <dwrite.h>
#include <wincodec.h>
#include <urlmon.h>
#include <cstdint>
#include <string>
#include <vector>
#include <deque>
#include <mutex>
#include <thread>
#include <utility>
#include <fstream>
#include <iterator>
#include <cwctype>
#include <cmath>
#include <memory>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "urlmon.lib")
#pragma comment(lib, "ole32.lib")

namespace {

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

template <typename T>
void SafeRelease(T** pp) {
    if (*pp) { (*pp)->Release(); *pp = nullptr; }
}

std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

constexpr wchar_t kPipeName[]   = L"\\\\.\\pipe\\lykompanion-overlay";
constexpr wchar_t kCtrlClass[]  = L"LykoOverlayCtrl";
constexpr wchar_t kWinClass[]   = L"LykoOverlayWin";

constexpr UINT WM_APP_COMMAND     = WM_APP + 1;  // a JSON line is queued
constexpr UINT WM_APP_IMAGE_READY = WM_APP + 2;  // a worker finished decoding an image
constexpr UINT_PTR TIMER_TICK   = 1;         // toast-expiry tick
constexpr UINT_PTR TIMER_DEMO   = 2;         // --demo auto-quit
constexpr UINT_PTR TIMER_BANNER = 3;         // startup banner fade animation
constexpr UINT_PTR TIMER_PARENT = 4;         // watch the parent process for exit
constexpr UINT_PTR TIMER_HANDSFREE = 5;      // hands-free indicator animation
constexpr int      HOTKEY_EDIT = 100;        // Ctrl+Shift+O edit-mode toggle

constexpr int   AVATAR = 22;        // logo avatar size in reply toasts

constexpr wchar_t kEditHint[] = L"Drag to move  \x2022  Ctrl+Shift+O to lock";

// Widget geometry (device pixels @ 96 DPI; DPI scaling is a later concern).
constexpr int   TOAST_W   = 340;
constexpr int   PANEL_W   = 320;
constexpr float PAD       = 14.0f;
constexpr int   MARGIN    = 24;     // gap from the screen edge
constexpr int   TOAST_GAP = 10;     // vertical gap between stacked toasts
constexpr int   ROW_H     = 24;     // game-state row height
constexpr DWORD TOAST_MS  = 6000;   // default toast lifetime
constexpr DWORD IMAGE_MS  = 22000;  // image toasts linger longer than text
constexpr int   IMAGE_MAX_H = 340;  // cap displayed image height

// ---------------------------------------------------------------------------
// Minimal JSON parser (objects/arrays/strings/numbers/bool/null) over wchar_t.
// Sufficient for our command schema; not a general-purpose library.
// ---------------------------------------------------------------------------

struct JsonValue {
    enum Type { Null, Bool, Num, Str, Arr, Obj } type = Null;
    bool        b = false;
    double      num = 0;
    std::wstring str;
    std::vector<JsonValue> arr;
    std::vector<std::pair<std::wstring, JsonValue>> obj;

    const JsonValue* find(const wchar_t* key) const {
        if (type != Obj) return nullptr;
        for (auto& kv : obj)
            if (kv.first == key) return &kv.second;
        return nullptr;
    }
    std::wstring asStr() const { return type == Str ? str : std::wstring(); }
    bool asBool() const { return type == Bool ? b : false; }
};

struct JsonParser {
    const wchar_t* p;
    const wchar_t* end;

    void skipWs() {
        while (p < end && (*p == L' ' || *p == L'\t' || *p == L'\n' || *p == L'\r')) ++p;
    }
    bool parseValue(JsonValue& out) {
        skipWs();
        if (p >= end) return false;
        switch (*p) {
            case L'{': return parseObject(out);
            case L'[': return parseArray(out);
            case L'"': out.type = JsonValue::Str; return parseString(out.str);
            case L't': case L'f': return parseBool(out);
            case L'n': return parseNull(out);
            default:   return parseNumber(out);
        }
    }
    bool parseObject(JsonValue& out) {
        out.type = JsonValue::Obj;
        ++p;  // '{'
        skipWs();
        if (p < end && *p == L'}') { ++p; return true; }
        while (p < end) {
            skipWs();
            if (p >= end || *p != L'"') return false;
            std::wstring key;
            if (!parseString(key)) return false;
            skipWs();
            if (p >= end || *p != L':') return false;
            ++p;
            JsonValue val;
            if (!parseValue(val)) return false;
            out.obj.emplace_back(std::move(key), std::move(val));
            skipWs();
            if (p < end && *p == L',') { ++p; continue; }
            if (p < end && *p == L'}') { ++p; return true; }
            return false;
        }
        return false;
    }
    bool parseArray(JsonValue& out) {
        out.type = JsonValue::Arr;
        ++p;  // '['
        skipWs();
        if (p < end && *p == L']') { ++p; return true; }
        while (p < end) {
            JsonValue val;
            if (!parseValue(val)) return false;
            out.arr.push_back(std::move(val));
            skipWs();
            if (p < end && *p == L',') { ++p; continue; }
            if (p < end && *p == L']') { ++p; return true; }
            return false;
        }
        return false;
    }
    bool parseString(std::wstring& out) {
        if (*p != L'"') return false;
        ++p;
        while (p < end && *p != L'"') {
            if (*p == L'\\') {
                ++p;
                if (p >= end) return false;
                switch (*p) {
                    case L'"':  out.push_back(L'"');  break;
                    case L'\\': out.push_back(L'\\'); break;
                    case L'/':  out.push_back(L'/');  break;
                    case L'n':  out.push_back(L'\n'); break;
                    case L't':  out.push_back(L'\t'); break;
                    case L'r':  out.push_back(L'\r'); break;
                    case L'b':  out.push_back(L'\b'); break;
                    case L'f':  out.push_back(L'\f'); break;
                    case L'u': {
                        if (end - p < 5) return false;
                        int cp = 0;
                        for (int i = 1; i <= 4; ++i) {
                            wchar_t c = p[i];
                            cp <<= 4;
                            if (c >= L'0' && c <= L'9') cp |= (c - L'0');
                            else if (c >= L'a' && c <= L'f') cp |= (c - L'a' + 10);
                            else if (c >= L'A' && c <= L'F') cp |= (c - L'A' + 10);
                            else return false;
                        }
                        out.push_back((wchar_t)cp);
                        p += 4;
                        break;
                    }
                    default: return false;
                }
                ++p;
            } else {
                out.push_back(*p++);
            }
        }
        if (p >= end || *p != L'"') return false;
        ++p;  // closing quote
        return true;
    }
    bool parseNumber(JsonValue& out) {
        const wchar_t* start = p;
        if (p < end && (*p == L'-' || *p == L'+')) ++p;
        while (p < end && ((*p >= L'0' && *p <= L'9') || *p == L'.' ||
                           *p == L'e' || *p == L'E' || *p == L'-' || *p == L'+'))
            ++p;
        if (p == start) return false;
        out.type = JsonValue::Num;
        out.num = _wtof(std::wstring(start, p).c_str());
        return true;
    }
    bool parseBool(JsonValue& out) {
        if (end - p >= 4 && wcsncmp(p, L"true", 4) == 0) {
            out.type = JsonValue::Bool; out.b = true; p += 4; return true;
        }
        if (end - p >= 5 && wcsncmp(p, L"false", 5) == 0) {
            out.type = JsonValue::Bool; out.b = false; p += 5; return true;
        }
        return false;
    }
    bool parseNull(JsonValue& out) {
        if (end - p >= 4 && wcsncmp(p, L"null", 4) == 0) {
            out.type = JsonValue::Null; p += 4; return true;
        }
        return false;
    }
};

bool ParseJson(const std::wstring& text, JsonValue& out) {
    JsonParser jp{text.c_str(), text.c_str() + text.size()};
    return jp.parseValue(out);
}

// ---------------------------------------------------------------------------
// Direct2D / DirectWrite globals
// ---------------------------------------------------------------------------

ID2D1Factory*     g_d2dFactory    = nullptr;
IDWriteFactory*   g_dwriteFactory = nullptr;
IDWriteTextFormat* g_fmtTitle = nullptr;  // toast title / panel title
IDWriteTextFormat* g_fmtBody  = nullptr;  // toast body / panel value
IDWriteTextFormat* g_fmtLabel = nullptr;  // panel label (dim, leading)
IDWriteTextFormat* g_fmtSmall = nullptr;  // panel row label (11px uppercase)
IDWriteTextFormat* g_fmtHead  = nullptr;  // memory/handsfree header (semibold 14)
IDWriteTextFormat* g_fmtIcon  = nullptr;  // emoji glyphs (color font)
IDWriteTextFormat* g_fmtCenter = nullptr; // centered (config buttons / banner)
ID2D1StrokeStyle*  g_dashStroke = nullptr; // dashed edit-mode outline

// A layered window plus its DIB-backed Direct2D render surface.
struct LayeredWindow {
    HWND     hwnd    = nullptr;
    HDC      memDC   = nullptr;
    HBITMAP  dib     = nullptr;
    HBITMAP  oldBmp  = nullptr;
    void*    bits    = nullptr;
    int      width   = 0;
    int      height  = 0;
    ID2D1DCRenderTarget* rt = nullptr;
    bool     visible = false;
    int      posX    = 0;      // committed screen position (top-left)
    int      posY    = 0;
    bool     hasPos  = false;  // false => derive from anchor on first commit
};

enum Anchor { AnchorTopRight, AnchorTopLeft, AnchorTopCenter, AnchorBottomCenter, AnchorFixed };

// ---- Appearance (user-adjustable in edit mode, persisted) ------------------
struct Rgb { float r, g, b; };
const Rgb kAccents[] = {
    {0.40f, 0.55f, 0.95f},  // blue
    {0.42f, 0.82f, 0.55f},  // green
    {0.95f, 0.70f, 0.30f},  // amber
    {0.67f, 0.51f, 0.92f},  // purple
    {0.92f, 0.47f, 0.47f},  // red
    {0.28f, 0.80f, 0.85f},  // teal
};
constexpr int kAccentCount = (int)(sizeof(kAccents) / sizeof(kAccents[0]));
int   g_accentIdx = 0;
float g_opacity   = 0.92f;  // whole-overlay alpha multiplier, 0.40..1.00

// Color helpers: every widget color routes through these so opacity/accent
// apply uniformly. `a` is the color's own alpha before the opacity multiply.
D2D1_COLOR_F Acc(float a) {
    const Rgb& c = kAccents[g_accentIdx];
    return D2D1::ColorF(c.r, c.g, c.b, a * g_opacity);
}
D2D1_COLOR_F Bg(float a)   { return D2D1::ColorF(0.08f, 0.09f, 0.12f, a * g_opacity); }
D2D1_COLOR_F Warm(float a) { return D2D1::ColorF(0.95f, 0.70f, 0.30f, a * g_opacity); }
D2D1_COLOR_F Txt(float a)  { return D2D1::ColorF(0.96f, 0.97f, 0.99f, a * g_opacity); }
D2D1_COLOR_F Dim(float a)  { return D2D1::ColorF(0.62f, 0.66f, 0.74f, a * g_opacity); }

// ---- Widget content --------------------------------------------------------

// Decoded image pixels handed from a download worker to the UI thread (32bpp PBGRA).
struct ImageData {
    std::vector<BYTE> pixels;
    UINT width = 0, height = 0, stride = 0;
    bool ok = false;
};

enum ImageState { ImgNone, ImgLoading, ImgReady, ImgFailed };

struct Toast {
    std::wstring text;
    std::wstring kind;   // "reply" | "reminder" | "image" | "memory"
    ULONGLONG    expire; // GetTickCount64() deadline
    // Image toasts only:
    bool         isImage = false;
    int          imageId = 0;        // correlates the async download to this toast
    ImageState   imgState = ImgNone;
    std::shared_ptr<ImageData> image;
    // Memory toasts only:
    bool         isMemory = false;
    std::wstring memAction;          // "save" | "remove"
    std::wstring memScope;           // "user" | "game" | "session"
};

struct Panel {
    bool valid = false;
    std::wstring title;
    std::vector<std::pair<std::wstring, std::wstring>> rows;
};

// ---- Global state ----------------------------------------------------------

HWND          g_ctrl = nullptr;    // message-only controller window
LayeredWindow g_toastWin;
LayeredWindow g_panelWin;
LayeredWindow g_configWin;         // edit-mode appearance toolbar
LayeredWindow g_bannerWin;         // startup fade-in/out hint
ULONGLONG     g_bannerStart = 0;
HANDLE        g_parentProcess = nullptr;  // Lykompanion's process; overlay self-exits if it dies
int           g_nextImageId = 0;
ImageData     g_logo;                     // decoded logo.png (reply-toast avatar)
bool          g_logoOk = false;
LayeredWindow g_handsfreeWin;             // persistent hands-free (live-mic) indicator
bool          g_handsfreeActive = false;
ULONGLONG     g_handsfreeStart = 0;       // animation clock
std::vector<Toast> g_toasts;
Panel         g_panel;
bool          g_editMode = false;

// Config-toolbar hit rects (window coords), filled in by RenderConfig().
constexpr int CONFIG_W = 300;
D2D1_RECT_F   g_rcOpacMinus = {};
D2D1_RECT_F   g_rcOpacPlus  = {};
D2D1_RECT_F   g_rcSwatch[kAccentCount] = {};

std::mutex               g_queueMx;
std::deque<std::wstring> g_queue;   // raw JSON lines from the pipe thread

// ---------------------------------------------------------------------------
// Direct2D surface management (shared by both widgets)
// ---------------------------------------------------------------------------

bool CreateFactories() {
    if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, &g_d2dFactory)))
        return false;
    if (FAILED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory),
                                   reinterpret_cast<IUnknown**>(&g_dwriteFactory))))
        return false;

    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 18.0f, L"en-us", &g_fmtTitle)))
        return false;
    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 14.0f, L"en-us", &g_fmtBody)))
        return false;
    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 13.0f, L"en-us", &g_fmtLabel)))
        return false;

    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 11.0f, L"en-us", &g_fmtSmall)))
        return false;
    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 14.0f, L"en-us", &g_fmtHead)))
        return false;
    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI Emoji", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 16.0f, L"en-us", &g_fmtIcon)))
        return false;
    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 15.0f, L"en-us", &g_fmtCenter)))
        return false;
    g_fmtCenter->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
    g_fmtCenter->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);

    D2D1_STROKE_STYLE_PROPERTIES sp = D2D1::StrokeStyleProperties();
    sp.dashStyle = D2D1_DASH_STYLE_DASH;
    sp.dashCap   = D2D1_CAP_STYLE_ROUND;
    g_d2dFactory->CreateStrokeStyle(sp, nullptr, 0, &g_dashStroke);
    return true;
}

// ---------------------------------------------------------------------------
// Layout persistence — the overlay owns its own config file; Python never
// touches it. %LOCALAPPDATA%\Lykompanion\overlay_layout.json
// ---------------------------------------------------------------------------

std::wstring LayoutPath() {
    const wchar_t* base = _wgetenv(L"LOCALAPPDATA");
    std::wstring dir = base ? base : L".";
    dir += L"\\Lykompanion";
    CreateDirectoryW(dir.c_str(), nullptr);
    return dir + L"\\overlay_layout.json";
}

void SaveLayout() {
    std::string js = "{\"toast\":{\"x\":" + std::to_string(g_toastWin.posX) +
                     ",\"y\":" + std::to_string(g_toastWin.posY) +
                     "},\"panel\":{\"x\":" + std::to_string(g_panelWin.posX) +
                     ",\"y\":" + std::to_string(g_panelWin.posY) +
                     "},\"handsfree\":{\"x\":" + std::to_string(g_handsfreeWin.posX) +
                     ",\"y\":" + std::to_string(g_handsfreeWin.posY) +
                     "},\"opacity\":" + std::to_string(g_opacity) +
                     ",\"accent\":" + std::to_string(g_accentIdx) + "}";
    HANDLE h = CreateFileW(LayoutPath().c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD n = 0;
        WriteFile(h, js.data(), (DWORD)js.size(), &n, nullptr);
        CloseHandle(h);
    }
}

void LoadLayout() {
    std::ifstream f(LayoutPath(), std::ios::binary);
    if (!f) return;
    std::string content((std::istreambuf_iterator<char>(f)),
                        std::istreambuf_iterator<char>());
    JsonValue v;
    if (!ParseJson(Utf8ToWide(content), v) || v.type != JsonValue::Obj) return;

    auto apply = [&](const wchar_t* key, LayeredWindow& lw) {
        const JsonValue* o = v.find(key);
        if (!o || o->type != JsonValue::Obj) return;
        const JsonValue* x = o->find(L"x");
        const JsonValue* y = o->find(L"y");
        if (x && x->type == JsonValue::Num && y && y->type == JsonValue::Num) {
            lw.posX = (int)x->num;
            lw.posY = (int)y->num;
            lw.hasPos = true;
        }
    };
    apply(L"toast", g_toastWin);
    apply(L"panel", g_panelWin);
    apply(L"handsfree", g_handsfreeWin);

    const JsonValue* op = v.find(L"opacity");
    if (op && op->type == JsonValue::Num) {
        g_opacity = (float)op->num;
        if (g_opacity < 0.40f) g_opacity = 0.40f;
        if (g_opacity > 1.00f) g_opacity = 1.00f;
    }
    const JsonValue* ac = v.find(L"accent");
    if (ac && ac->type == JsonValue::Num) {
        g_accentIdx = (int)ac->num;
        if (g_accentIdx < 0) g_accentIdx = 0;
        if (g_accentIdx >= kAccentCount) g_accentIdx = kAccentCount - 1;
    }
}

void DiscardSurface(LayeredWindow& lw) {
    SafeRelease(&lw.rt);
    if (lw.memDC) {
        if (lw.oldBmp) SelectObject(lw.memDC, lw.oldBmp);
        DeleteDC(lw.memDC);
        lw.memDC = nullptr;
    }
    if (lw.dib) { DeleteObject(lw.dib); lw.dib = nullptr; }
    lw.oldBmp = nullptr;
    lw.width = lw.height = 0;
}

// (Re)create the DIB + DC render target for the given size.
bool EnsureSurface(LayeredWindow& lw, int width, int height) {
    if (lw.rt && lw.width == width && lw.height == height) return true;
    DiscardSurface(lw);

    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth       = width;
    bmi.bmiHeader.biHeight      = -height;   // top-down
    bmi.bmiHeader.biPlanes      = 1;
    bmi.bmiHeader.biBitCount    = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    HDC screenDC = GetDC(nullptr);
    lw.dib   = CreateDIBSection(screenDC, &bmi, DIB_RGB_COLORS, &lw.bits, nullptr, 0);
    lw.memDC = CreateCompatibleDC(screenDC);
    ReleaseDC(nullptr, screenDC);
    if (!lw.dib || !lw.memDC) return false;
    lw.oldBmp = (HBITMAP)SelectObject(lw.memDC, lw.dib);

    D2D1_RENDER_TARGET_PROPERTIES props = D2D1::RenderTargetProperties(
        D2D1_RENDER_TARGET_TYPE_DEFAULT,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED),
        0.0f, 0.0f, D2D1_RENDER_TARGET_USAGE_NONE, D2D1_FEATURE_LEVEL_DEFAULT);
    if (FAILED(g_d2dFactory->CreateDCRenderTarget(&props, &lw.rt))) return false;

    RECT rc = {0, 0, width, height};
    if (FAILED(lw.rt->BindDC(lw.memDC, &rc))) return false;
    // ClearType corrupts the alpha channel for layered windows; grayscale AA
    // writes correct premultiplied alpha.
    lw.rt->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);

    lw.width = width;
    lw.height = height;
    return true;
}

// Push the rendered DIB to screen at an anchored position. `constAlpha` scales
// the whole window uniformly (used for banner fades) on top of per-pixel alpha.
void CommitWindow(LayeredWindow& lw, Anchor anchor, BYTE constAlpha = 255) {
    int screenW = GetSystemMetrics(SM_CXSCREEN);
    if (anchor == AnchorTopCenter) {
        // The config toolbar is always top-center; it isn't dragged or saved.
        lw.posX = (screenW - lw.width) / 2;
        lw.posY = MARGIN;
    } else if (anchor == AnchorFixed) {
        // Caller set posX/posY explicitly (banner).
    } else if (!lw.hasPos) {
        lw.posX = (anchor == AnchorTopRight) ? screenW - MARGIN - lw.width
                : (anchor == AnchorBottomCenter) ? (screenW - lw.width) / 2
                : MARGIN;
        lw.posY = (anchor == AnchorBottomCenter)
                      ? GetSystemMetrics(SM_CYSCREEN) - MARGIN - lw.height
                      : MARGIN;
        lw.hasPos = true;
    }
    int x = lw.posX, y = lw.posY;

    if (!lw.visible) {
        ShowWindow(lw.hwnd, SW_SHOWNOACTIVATE);
        lw.visible = true;
    }

    POINT ptDst = {x, y};
    SIZE  size  = {lw.width, lw.height};
    POINT ptSrc = {0, 0};
    BLENDFUNCTION blend = {};
    blend.BlendOp             = AC_SRC_OVER;
    blend.SourceConstantAlpha = constAlpha;
    blend.AlphaFormat         = AC_SRC_ALPHA;

    HDC screenDC = GetDC(nullptr);
    UpdateLayeredWindow(lw.hwnd, screenDC, &ptDst, &size,
                        lw.memDC, &ptSrc, 0, &blend, ULW_ALPHA);
    ReleaseDC(nullptr, screenDC);
}

void HideWindow(LayeredWindow& lw) {
    if (lw.visible) { ShowWindow(lw.hwnd, SW_HIDE); lw.visible = false; }
}

// Toggle WS_EX_TRANSPARENT so a widget receives (or ignores) the mouse.
void SetClickThrough(LayeredWindow& lw, bool through) {
    LONG ex = GetWindowLong(lw.hwnd, GWL_EXSTYLE);
    if (through) ex |= WS_EX_TRANSPARENT;
    else         ex &= ~WS_EX_TRANSPARENT;
    SetWindowLong(lw.hwnd, GWL_EXSTYLE, ex);
}

// Dashed outline drawn over a widget while in edit mode.
void DrawEditDecoration(ID2D1RenderTarget* rt, int w, int h) {
    if (!g_dashStroke) return;
    ID2D1SolidColorBrush* b = nullptr;
    rt->CreateSolidColorBrush(D2D1::ColorF(0.45f, 0.85f, 0.55f, 0.95f), &b);
    D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(
        D2D1::RectF(1.5f, 1.5f, w - 1.5f, h - 1.5f), 12.0f, 12.0f);
    rt->DrawRoundedRectangle(rr, b, 1.6f, g_dashStroke);
    SafeRelease(&b);
}

// ---------------------------------------------------------------------------
// Text measurement + drawing helpers
// ---------------------------------------------------------------------------

// Create a text layout constrained to maxW and return its measured height.
IDWriteTextLayout* MakeLayout(const std::wstring& text, IDWriteTextFormat* fmt,
                              float maxW, float* outH) {
    IDWriteTextLayout* layout = nullptr;
    if (FAILED(g_dwriteFactory->CreateTextLayout(
            text.c_str(), (UINT32)text.size(), fmt, maxW, 4096.0f, &layout)))
        return nullptr;
    DWRITE_TEXT_METRICS m = {};
    layout->GetMetrics(&m);
    if (outH) *outH = m.height;
    return layout;
}

// Draw a color emoji glyph within rect (color-font enabled so it renders in color).
void DrawEmoji(ID2D1RenderTarget* rt, const wchar_t* glyph, const D2D1_RECT_F& rect,
               ID2D1SolidColorBrush* fallback) {
    rt->DrawText(glyph, (UINT32)wcslen(glyph), g_fmtIcon, rect, fallback,
                 D2D1_DRAW_TEXT_OPTIONS_ENABLE_COLOR_FONT);
}

// Create a device bitmap from decoded PBGRA pixels (caller releases).
ID2D1Bitmap* MakeBitmap(ID2D1RenderTarget* rt, const ImageData& img) {
    ID2D1Bitmap* bmp = nullptr;
    D2D1_BITMAP_PROPERTIES bp = D2D1::BitmapProperties(
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED));
    rt->CreateBitmap(D2D1::SizeU(img.width, img.height), img.pixels.data(),
                     img.stride, &bp, &bmp);
    return bmp;
}

// While editing, an empty widget still shows a draggable placeholder card.
void RenderPlaceholder(LayeredWindow& lw, const wchar_t* label,
                       Anchor anchor, int width) {
    const int h = 64;
    if (!EnsureSurface(lw, width, h)) return;
    ID2D1RenderTarget* rt = lw.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush* bg = nullptr, *white = nullptr, *dim = nullptr;
    rt->CreateSolidColorBrush(Bg(0.80f), &bg);            // previews live opacity
    rt->CreateSolidColorBrush(Txt(1.0f), &white);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.55f, 0.85f, 0.60f, 0.95f), &dim);  // edit green

    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.5f, 1.5f, width - 1.5f, h - 1.5f), 12.0f, 12.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawText(label, (UINT32)wcslen(label), g_fmtBody,
                 D2D1::RectF(PAD, 12, width - PAD, 34), white);
    rt->DrawText(kEditHint, (UINT32)wcslen(kEditHint), g_fmtLabel,
                 D2D1::RectF(PAD, 36, width - PAD, 58), dim);
    DrawEditDecoration(rt, width, h);

    SafeRelease(&dim); SafeRelease(&white); SafeRelease(&bg);
    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) { DiscardSurface(lw); return; }
    CommitWindow(lw, anchor);
}

// ---------------------------------------------------------------------------
// Toast widget
// ---------------------------------------------------------------------------

// Per-toast layout plan (namespace scope: MSVC rejects a default member
// initializer referencing a function-local enum in the generated constructor).
enum PlanKind { PkText, PkImage, PkMemory };
struct ToastPlan {
    PlanKind kind = PkText;
    IDWriteTextLayout* layout = nullptr;  // text body / image caption / memory content
    float textH = 0;
    bool  avatar = false;                 // reply toast: draw the logo avatar
    float dispW = 0, dispH = 0;           // image display size (ImgReady)
    IDWriteTextLayout* header = nullptr;  // memory header line
    float headerH = 0;
};

void RelayoutToasts() {
    if (g_toasts.empty()) {
        if (g_editMode) RenderPlaceholder(g_toastWin, L"Toasts appear here",
                                          AnchorTopRight, TOAST_W);
        else HideWindow(g_toastWin);
        return;
    }

    const float innerW = TOAST_W - 2 * PAD;

    using Plan = ToastPlan;
    std::vector<Plan> plans(g_toasts.size());
    std::vector<float> cardH(g_toasts.size());
    int total = 0;

    for (size_t i = 0; i < g_toasts.size(); ++i) {
        Toast& t = g_toasts[i];
        Plan& p = plans[i];

        if (t.isImage && t.imgState == ImgReady && t.image && t.image->ok) {
            p.kind = PkImage;
            float iw = (float)t.image->width, ih = (float)t.image->height;
            float scale = innerW / iw;
            if (ih * scale > IMAGE_MAX_H) scale = IMAGE_MAX_H / ih;
            if (scale > 1.0f) scale = 1.0f;  // never upscale past native
            p.dispW = iw * scale;
            p.dispH = ih * scale;
            if (!t.text.empty())  // optional caption below the image
                p.layout = MakeLayout(t.text, g_fmtLabel, innerW, &p.textH);
            cardH[i] = PAD + p.dispH + (p.layout ? 4.0f + p.textH : 0.0f) + PAD;
        } else if (t.isMemory) {
            p.kind = PkMemory;
            std::wstring scope = t.memScope.empty() ? L"user" : t.memScope;
            std::wstring head = (t.memAction == L"remove" ? L"Removed from " : L"Saved to ")
                                + scope + L" memory";
            float iconCol = 30.0f;  // brain glyph + gap
            p.header = MakeLayout(head, g_fmtHead, innerW - iconCol, &p.headerH);
            p.layout = MakeLayout(t.text, g_fmtLabel, innerW, &p.textH);
            float top = (p.headerH > 22.0f ? p.headerH : 22.0f);
            cardH[i] = PAD + top + 4.0f + p.textH + PAD;
        } else {
            const wchar_t* status = nullptr;
            if (t.isImage && t.imgState == ImgLoading) status = L"Loading image…";
            else if (t.isImage && t.imgState == ImgFailed) status = L"[image unavailable]";
            std::wstring body = status ? status : t.text;
            // Reply toasts get the logo avatar; text wraps in the reduced width.
            p.avatar = (t.kind == L"reply" && g_logoOk);
            float textW = p.avatar ? innerW - (AVATAR + 8.0f) : innerW;
            p.layout = MakeLayout(body, g_fmtBody, textW, &p.textH);
            float contentH = p.avatar ? (p.textH > AVATAR ? p.textH : (float)AVATAR) : p.textH;
            cardH[i] = (contentH + 2 * PAD < 44.0f) ? 44.0f : (contentH + 2 * PAD);
        }
        total += (int)cardH[i];
        if (i + 1 < g_toasts.size()) total += TOAST_GAP;
    }

    auto freePlans = [&]() {
        for (auto& p : plans) { SafeRelease(&p.layout); SafeRelease(&p.header); }
    };

    if (!EnsureSurface(g_toastWin, TOAST_W, total)) {
        freePlans();
        return;
    }

    ID2D1DCRenderTarget* rt = g_toastWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush* text = nullptr;
    rt->CreateSolidColorBrush(Txt(1.0f), &text);

    float y = 0;
    for (size_t i = 0; i < g_toasts.size(); ++i) {
        Toast& t = g_toasts[i];
        Plan& p = plans[i];
        bool reminder = (t.kind == L"reminder");
        ID2D1SolidColorBrush* bg = nullptr;
        ID2D1SolidColorBrush* border = nullptr;
        rt->CreateSolidColorBrush(Bg(0.92f), &bg);
        // Reminders keep a fixed warm border so they read as distinct; replies
        // and images use the user-chosen accent.
        rt->CreateSolidColorBrush(reminder ? Warm(0.80f) : Acc(0.75f), &border);

        D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
            D2D1::RectF(1.0f, y + 1.0f, TOAST_W - 1.0f, y + cardH[i] - 1.0f), 12.0f, 12.0f);
        rt->FillRoundedRectangle(card, bg);
        rt->DrawRoundedRectangle(card, border, 1.3f);

        if (p.kind == PkImage) {
            ID2D1Bitmap* bmp = MakeBitmap(rt, *t.image);
            if (bmp) {
                float ix = PAD + (innerW - p.dispW) / 2.0f;
                D2D1_RECT_F dst = D2D1::RectF(ix, y + PAD, ix + p.dispW, y + PAD + p.dispH);
                rt->DrawBitmap(bmp, dst, 1.0f, D2D1_BITMAP_INTERPOLATION_MODE_LINEAR);
                SafeRelease(&bmp);
            }
            if (p.layout) {
                ID2D1SolidColorBrush* dim = nullptr;
                rt->CreateSolidColorBrush(Dim(1.0f), &dim);
                rt->DrawTextLayout(D2D1::Point2F(PAD, y + PAD + p.dispH + 4.0f), p.layout, dim);
                SafeRelease(&dim);
            }
        } else if (p.kind == PkMemory) {
            // Brain glyph + a colored +/- badge, header line, then the content.
            bool remove = (t.memAction == L"remove");
            DrawEmoji(rt, L"\U0001F9E0", D2D1::RectF(PAD, y + PAD, PAD + 24, y + PAD + 24), text);
            ID2D1SolidColorBrush* badge = nullptr;
            rt->CreateSolidColorBrush(
                remove ? D2D1::ColorF(0.92f, 0.40f, 0.40f, g_opacity)
                       : D2D1::ColorF(0.35f, 0.80f, 0.48f, g_opacity), &badge);
            D2D1_ELLIPSE dot = D2D1::Ellipse(D2D1::Point2F(PAD + 19, y + PAD + 19), 6.5f, 6.5f);
            rt->FillEllipse(dot, badge);
            // "+" or "-" inside the badge.
            rt->DrawLine(D2D1::Point2F(PAD + 15.5f, y + PAD + 19), D2D1::Point2F(PAD + 22.5f, y + PAD + 19),
                         text, 1.6f);
            if (!remove)
                rt->DrawLine(D2D1::Point2F(PAD + 19, y + PAD + 15.5f), D2D1::Point2F(PAD + 19, y + PAD + 22.5f),
                             text, 1.6f);
            SafeRelease(&badge);
            if (p.header)
                rt->DrawTextLayout(D2D1::Point2F(PAD + 30, y + PAD), p.header, text);
            if (p.layout) {
                ID2D1SolidColorBrush* dim = nullptr;
                rt->CreateSolidColorBrush(Dim(1.0f), &dim);
                float top = (p.headerH > 22.0f ? p.headerH : 22.0f);
                rt->DrawTextLayout(D2D1::Point2F(PAD, y + PAD + top + 4.0f), p.layout, dim);
                SafeRelease(&dim);
            }
        } else {
            float tx = PAD;
            if (p.avatar) {
                ID2D1Bitmap* bmp = MakeBitmap(rt, g_logo);
                if (bmp) {
                    D2D1_RECT_F av = D2D1::RectF(PAD, y + PAD, PAD + AVATAR, y + PAD + AVATAR);
                    rt->DrawBitmap(bmp, av, 1.0f, D2D1_BITMAP_INTERPOLATION_MODE_LINEAR);
                    SafeRelease(&bmp);
                }
                tx = PAD + AVATAR + 8.0f;
            }
            if (p.layout)
                rt->DrawTextLayout(D2D1::Point2F(tx, y + PAD), p.layout, text);
        }

        SafeRelease(&border);
        SafeRelease(&bg);
        y += cardH[i] + TOAST_GAP;
    }

    SafeRelease(&text);
    freePlans();

    if (g_editMode) DrawEditDecoration(rt, TOAST_W, total);

    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) {
        DiscardSurface(g_toastWin);
        return;
    }
    CommitWindow(g_toastWin, AnchorTopRight);
}

void AddToast(const std::wstring& textStr, const std::wstring& kind) {
    Toast t;
    t.text = textStr;
    t.kind = kind.empty() ? L"reply" : kind;
    t.expire = GetTickCount64() + TOAST_MS;
    g_toasts.push_back(std::move(t));
    RelayoutToasts();
}

// Background: download an http(s) image, decode + downscale via WIC into raw
// PBGRA pixels, and hand them to the UI thread. Runs on its own COM apartment
// so nothing here touches the shared render state.
// Decode an image file into 32bpp PBGRA pixels (downscaled so the long side fits
// maxDim). Creates its own WIC factory; the caller must be in a COM apartment.
bool DecodeImageToPixels(const wchar_t* path, UINT maxDim, ImageData& out) {
    IWICImagingFactory* wic = nullptr;
    IWICBitmapDecoder* dec = nullptr;
    IWICBitmapFrameDecode* frame = nullptr;
    IWICBitmapScaler* scaler = nullptr;
    IWICFormatConverter* conv = nullptr;
    IWICBitmapSource* src = nullptr;

    if (SUCCEEDED(CoCreateInstance(CLSID_WICImagingFactory, nullptr,
                                   CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&wic))) &&
        SUCCEEDED(wic->CreateDecoderFromFilename(path, nullptr, GENERIC_READ,
                                                 WICDecodeMetadataCacheOnLoad, &dec)) &&
        SUCCEEDED(dec->GetFrame(0, &frame))) {
        UINT w = 0, h = 0;
        frame->GetSize(&w, &h);
        src = frame;
        if (w > maxDim || h > maxDim) {
            double s = (double)maxDim / w;
            if ((double)maxDim / h < s) s = (double)maxDim / h;
            UINT nw = (UINT)(w * s), nh = (UINT)(h * s);
            if (nw < 1) nw = 1;
            if (nh < 1) nh = 1;
            if (SUCCEEDED(wic->CreateBitmapScaler(&scaler)) &&
                SUCCEEDED(scaler->Initialize(frame, nw, nh, WICBitmapInterpolationModeFant)))
                src = scaler;
        }
        if (SUCCEEDED(wic->CreateFormatConverter(&conv)) &&
            SUCCEEDED(conv->Initialize(src, GUID_WICPixelFormat32bppPBGRA,
                                       WICBitmapDitherTypeNone, nullptr, 0.0,
                                       WICBitmapPaletteTypeCustom))) {
            UINT fw = 0, fh = 0;
            conv->GetSize(&fw, &fh);
            UINT stride = fw * 4;
            out.pixels.resize((size_t)stride * fh);
            if (SUCCEEDED(conv->CopyPixels(nullptr, stride, (UINT)out.pixels.size(),
                                           out.pixels.data()))) {
                out.width = fw;
                out.height = fh;
                out.stride = stride;
                out.ok = true;
            }
        }
    }
    if (conv) conv->Release();
    if (scaler) scaler->Release();
    if (frame) frame->Release();
    if (dec) dec->Release();
    if (wic) wic->Release();
    return out.ok;
}

// Download an http(s) image and decode it on a background COM apartment, then
// hand the pixels to the UI thread. Nothing here touches shared render state.
void LoadImageWorker(std::wstring url, int id) {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    ImageData* data = new ImageData();
    wchar_t cache[MAX_PATH] = {};
    if (SUCCEEDED(URLDownloadToCacheFileW(nullptr, url.c_str(), cache, MAX_PATH, 0, nullptr)))
        DecodeImageToPixels(cache, 460, *data);
    CoUninitialize();

    if (!g_ctrl || !PostMessage(g_ctrl, WM_APP_IMAGE_READY, (WPARAM)id, (LPARAM)data))
        delete data;
}

// Load the reply-toast avatar (logo.png next to the exe). Called once at startup.
void LoadLogo() {
    wchar_t exePath[MAX_PATH] = {};
    GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    std::wstring dir(exePath);
    size_t slash = dir.find_last_of(L"\\/");
    std::wstring logoPath = (slash == std::wstring::npos ? L"" : dir.substr(0, slash + 1)) + L"logo.png";
    g_logoOk = DecodeImageToPixels(logoPath.c_str(), 64, g_logo);
}

void AddImageToast(const std::wstring& url, const std::wstring& alt) {
    Toast t;
    t.kind = L"image";
    t.isImage = true;
    t.text = alt;  // caption
    t.imgState = ImgLoading;
    t.imageId = ++g_nextImageId;
    t.expire = GetTickCount64() + IMAGE_MS;
    g_toasts.push_back(std::move(t));
    std::thread(LoadImageWorker, url, g_nextImageId).detach();
    RelayoutToasts();
}

void AddMemoryToast(const std::wstring& action, const std::wstring& scope,
                    const std::wstring& content) {
    Toast t;
    t.kind = L"memory";
    t.isMemory = true;
    t.memAction = action;
    t.memScope = scope;
    t.text = content;
    t.expire = GetTickCount64() + TOAST_MS;
    g_toasts.push_back(std::move(t));
    RelayoutToasts();
}

// Drop expired toasts; returns true if the set changed.
bool ExpireToasts() {
    ULONGLONG now = GetTickCount64();
    size_t before = g_toasts.size();
    for (size_t i = 0; i < g_toasts.size();) {
        if (g_toasts[i].expire <= now) g_toasts.erase(g_toasts.begin() + i);
        else ++i;
    }
    return g_toasts.size() != before;
}

// ---------------------------------------------------------------------------
// Game-state panel widget
// ---------------------------------------------------------------------------

// Split a value on ';' into trimmed non-empty parts (mirrors the web panel's
// multi-part "Known Stats"-style rendering).
std::vector<std::wstring> SplitValueParts(const std::wstring& value) {
    std::vector<std::wstring> parts;
    size_t start = 0;
    while (true) {
        size_t pos = value.find(L';', start);
        std::wstring part = value.substr(
            start, pos == std::wstring::npos ? std::wstring::npos : pos - start);
        size_t a = part.find_first_not_of(L" \t\r\n");
        size_t b = part.find_last_not_of(L" \t\r\n");
        if (a != std::wstring::npos) parts.push_back(part.substr(a, b - a + 1));
        if (pos == std::wstring::npos) break;
        start = pos + 1;
    }
    return parts;
}

// Each row is a stacked sub-card: an uppercase dim label above a wrapping value
// (bulleted when the value is ';'-separated) — matching web/js/app/game-state.js.
void RenderPanel() {
    if (!g_panel.valid ||
        (g_panel.title.empty() && g_panel.rows.empty())) {
        if (g_editMode) RenderPlaceholder(g_panelWin, L"Game state panel",
                                          AnchorTopLeft, PANEL_W);
        else HideWindow(g_panelWin);
        return;
    }

    const float innerW   = PANEL_W - 2 * PAD;         // panel content width
    const float rowPadX  = 10.0f, rowPadY = 7.0f;     // padding inside a row card
    const float rowGap   = 6.0f, lblGap = 3.0f;
    const float rowTextW = innerW - 2 * rowPadX;

    float titleH = 0;
    IDWriteTextLayout* titleLayout = nullptr;
    if (!g_panel.title.empty())
        titleLayout = MakeLayout(g_panel.title, g_fmtTitle, innerW, &titleH);

    struct RowLayout {
        IDWriteTextLayout* label = nullptr;
        float labelH = 0;
        std::vector<IDWriteTextLayout*> values;
        std::vector<float> valueH;
        float rowH = 0;
    };
    std::vector<RowLayout> rows;
    rows.reserve(g_panel.rows.size());

    for (auto& row : g_panel.rows) {
        RowLayout r;
        std::wstring upper = row.first;
        for (auto& ch : upper) ch = (wchar_t)towupper(ch);
        r.label = MakeLayout(upper, g_fmtSmall, rowTextW, &r.labelH);

        std::vector<std::wstring> parts = SplitValueParts(row.second);
        if (parts.empty()) parts.push_back(L"(not seen yet)");
        bool bullet = parts.size() > 1;
        float valuesH = 0;
        for (size_t i = 0; i < parts.size(); ++i) {
            std::wstring line = bullet ? (L"\x2022  " + parts[i]) : parts[i];
            float hh = 0;
            r.values.push_back(MakeLayout(line, g_fmtBody, rowTextW, &hh));
            r.valueH.push_back(hh);
            valuesH += hh + (i + 1 < parts.size() ? 2.0f : 0.0f);
        }
        r.rowH = rowPadY + r.labelH + lblGap + valuesH + rowPadY;
        rows.push_back(std::move(r));
    }

    float total = PAD + (titleLayout ? titleH + 8.0f : 0.0f);
    for (auto& r : rows) total += r.rowH + rowGap;
    if (!rows.empty()) total -= rowGap;
    total += PAD;
    int height = (int)(total + 0.5f);

    auto freeRows = [&]() {
        for (auto& r : rows) {
            SafeRelease(&r.label);
            for (auto* v : r.values) SafeRelease(&v);
        }
    };

    if (!EnsureSurface(g_panelWin, PANEL_W, height)) {
        SafeRelease(&titleLayout);
        freeRows();
        return;
    }

    ID2D1DCRenderTarget* rt = g_panelWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush *bg = nullptr, *border = nullptr, *white = nullptr,
                         *dim = nullptr, *rowBg = nullptr, *rowBorder = nullptr;
    rt->CreateSolidColorBrush(Bg(0.92f), &bg);
    rt->CreateSolidColorBrush(Acc(0.55f), &border);
    rt->CreateSolidColorBrush(Txt(1.0f), &white);
    rt->CreateSolidColorBrush(Dim(1.0f), &dim);
    rt->CreateSolidColorBrush(D2D1::ColorF(1, 1, 1, 0.05f * g_opacity), &rowBg);
    rt->CreateSolidColorBrush(D2D1::ColorF(1, 1, 1, 0.10f * g_opacity), &rowBorder);

    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, PANEL_W - 1.0f, height - 1.0f), 12.0f, 12.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawRoundedRectangle(card, border, 1.3f);

    float y = PAD;
    if (titleLayout) {
        rt->DrawTextLayout(D2D1::Point2F(PAD, y), titleLayout, white);
        y += titleH + 8.0f;
    }

    for (auto& r : rows) {
        D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(
            D2D1::RectF(PAD, y, PAD + innerW, y + r.rowH), 8.0f, 8.0f);
        rt->FillRoundedRectangle(rr, rowBg);
        rt->DrawRoundedRectangle(rr, rowBorder, 1.0f);

        float ty = y + rowPadY;
        if (r.label) {
            rt->DrawTextLayout(D2D1::Point2F(PAD + rowPadX, ty), r.label, dim);
            ty += r.labelH + lblGap;
        }
        for (size_t i = 0; i < r.values.size(); ++i) {
            rt->DrawTextLayout(D2D1::Point2F(PAD + rowPadX, ty), r.values[i], white);
            ty += r.valueH[i] + 2.0f;
        }
        y += r.rowH + rowGap;
    }

    SafeRelease(&rowBorder); SafeRelease(&rowBg);
    SafeRelease(&dim); SafeRelease(&white);
    SafeRelease(&border); SafeRelease(&bg);
    SafeRelease(&titleLayout);
    freeRows();

    if (g_editMode) DrawEditDecoration(rt, PANEL_W, height);

    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) {
        DiscardSurface(g_panelWin);
        return;
    }
    CommitWindow(g_panelWin, AnchorTopLeft);
}

bool InRect(const D2D1_RECT_F& r, int x, int y) {
    return x >= r.left && x < r.right && y >= r.top && y < r.bottom;
}

// ---------------------------------------------------------------------------
// Hands-free (live-mic) indicator: a persistent pill with a mic glyph and a
// pulsing, scrolling accent gradient while listening.
// ---------------------------------------------------------------------------

constexpr int HANDSFREE_W = 190;

void RenderHandsFree() {
    if (!g_handsfreeActive && !g_editMode) { HideWindow(g_handsfreeWin); return; }
    const int w = HANDSFREE_W, h = 46;
    if (!EnsureSurface(g_handsfreeWin, w, h)) return;

    ID2D1DCRenderTarget* rt = g_handsfreeWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    D2D1_ROUNDED_RECT pill = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, w - 1.0f, h - 1.0f), h / 2.0f, h / 2.0f);

    ID2D1SolidColorBrush* bg = nullptr;
    rt->CreateSolidColorBrush(Bg(0.90f), &bg);
    rt->FillRoundedRectangle(pill, bg);

    // Scrolling accent gradient (wrapped) — animated only while active.
    D2D1_GRADIENT_STOP stops[3] = {
        {0.0f, Acc(0.05f)}, {0.5f, Acc(0.42f)}, {1.0f, Acc(0.05f)}};
    ID2D1GradientStopCollection* gsc = nullptr;
    if (SUCCEEDED(rt->CreateGradientStopCollection(
            stops, 3, D2D1_GAMMA_2_2, D2D1_EXTEND_MODE_WRAP, &gsc)) && gsc) {
        float phase = (GetTickCount64() - g_handsfreeStart) / 1000.0f;
        const float span = 95.0f;
        float off = g_handsfreeActive ? fmodf(phase * 55.0f, span) : 0.0f;
        ID2D1LinearGradientBrush* grad = nullptr;
        D2D1_LINEAR_GRADIENT_BRUSH_PROPERTIES gp = {{off, 0}, {off + span, 0}};
        if (SUCCEEDED(rt->CreateLinearGradientBrush(gp, gsc, &grad)) && grad) {
            rt->FillRoundedRectangle(pill, grad);
            SafeRelease(&grad);
        }
        SafeRelease(&gsc);
    }

    ID2D1SolidColorBrush* border = nullptr, *white = nullptr;
    rt->CreateSolidColorBrush(Acc(0.75f), &border);
    rt->CreateSolidColorBrush(Txt(1.0f), &white);
    rt->DrawRoundedRectangle(pill, border, 1.4f);

    DrawEmoji(rt, L"\U0001F3A4",
              D2D1::RectF(PAD, (h - 24) / 2.0f, PAD + 24, (h + 24) / 2.0f), white);
    float th = 0;
    IDWriteTextLayout* tl = MakeLayout(L"Listening…", g_fmtHead, (float)(w - PAD - 34), &th);
    if (tl) {
        rt->DrawTextLayout(D2D1::Point2F(PAD + 30, (h - th) / 2.0f), tl, white);
        SafeRelease(&tl);
    }

    SafeRelease(&white); SafeRelease(&border); SafeRelease(&bg);
    if (g_editMode) DrawEditDecoration(rt, w, h);
    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) { DiscardSurface(g_handsfreeWin); return; }
    CommitWindow(g_handsfreeWin, AnchorBottomCenter);
}

void SetHandsFree(bool active) {
    g_handsfreeActive = active;
    if (active) {
        g_handsfreeStart = GetTickCount64();
        SetTimer(g_ctrl, TIMER_HANDSFREE, 33, nullptr);  // ~30fps sweep
    } else {
        KillTimer(g_ctrl, TIMER_HANDSFREE);
    }
    RenderHandsFree();
}

// ---------------------------------------------------------------------------
// Edit-mode appearance toolbar (opacity + accent color). Fixed opaque colors so
// it stays usable regardless of the overlay opacity it is editing.
// ---------------------------------------------------------------------------

void RenderConfig() {
    const int h = 108;
    if (!EnsureSurface(g_configWin, CONFIG_W, h)) return;
    ID2D1RenderTarget* rt = g_configWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush* bg = nullptr, *white = nullptr, *dim = nullptr, *acc = nullptr;
    const Rgb& a = kAccents[g_accentIdx];
    rt->CreateSolidColorBrush(D2D1::ColorF(0.10f, 0.11f, 0.14f, 0.97f), &bg);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.95f, 0.96f, 0.98f, 1.0f), &white);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.62f, 0.66f, 0.74f, 1.0f), &dim);
    rt->CreateSolidColorBrush(D2D1::ColorF(a.r, a.g, a.b, 1.0f), &acc);

    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, CONFIG_W - 1.0f, h - 1.0f), 12.0f, 12.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawRoundedRectangle(card, acc, 1.4f);

    // Title.
    rt->DrawText(L"Overlay appearance", 18, g_fmtLabel,
                 D2D1::RectF(PAD, 8, CONFIG_W - PAD, 28), dim);

    // --- Opacity row: label + [-] [xx%] [+] on the right.
    rt->DrawText(L"Opacity", 7, g_fmtBody,
                 D2D1::RectF(PAD, 34, 120, 58), white);
    const float by = 34, bh = 24, bw = 26;
    g_rcOpacPlus  = D2D1::RectF(CONFIG_W - PAD - bw, by, CONFIG_W - PAD, by + bh);
    D2D1_RECT_F pct = D2D1::RectF(g_rcOpacPlus.left - 4 - 48, by,
                                  g_rcOpacPlus.left - 4, by + bh);
    g_rcOpacMinus = D2D1::RectF(pct.left - 4 - bw, by, pct.left - 4, by + bh);
    for (auto* r : {&g_rcOpacMinus, &g_rcOpacPlus}) {
        D2D1_ROUNDED_RECT br = D2D1::RoundedRect(*r, 6, 6);
        rt->DrawRoundedRectangle(br, dim, 1.2f);
    }
    rt->DrawText(L"\x2212", 1, g_fmtCenter, g_rcOpacMinus, white);  // minus sign
    rt->DrawText(L"+", 1, g_fmtCenter, g_rcOpacPlus, white);
    wchar_t pctText[8];
    swprintf(pctText, 8, L"%d%%", (int)(g_opacity * 100 + 0.5f));
    rt->DrawText(pctText, (UINT32)wcslen(pctText), g_fmtCenter, pct, white);

    // --- Accent row: swatches, selected one ringed.
    rt->DrawText(L"Accent", 6, g_fmtBody, D2D1::RectF(PAD, 72, 90, 96), white);
    const float sw = 24, sgap = 6;
    float sx = CONFIG_W - PAD - (kAccentCount * (sw + sgap) - sgap);
    for (int i = 0; i < kAccentCount; ++i) {
        g_rcSwatch[i] = D2D1::RectF(sx, 72, sx + sw, 72 + sw);
        ID2D1SolidColorBrush* sb = nullptr;
        rt->CreateSolidColorBrush(
            D2D1::ColorF(kAccents[i].r, kAccents[i].g, kAccents[i].b, 1.0f), &sb);
        D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(g_rcSwatch[i], 6, 6);
        rt->FillRoundedRectangle(rr, sb);
        if (i == g_accentIdx) {
            D2D1_ROUNDED_RECT ring = D2D1::RoundedRect(
                D2D1::RectF(sx - 2, 70, sx + sw + 2, 74 + sw), 8, 8);
            rt->DrawRoundedRectangle(ring, white, 1.8f);
        }
        SafeRelease(&sb);
        sx += sw + sgap;
    }

    SafeRelease(&acc); SafeRelease(&dim); SafeRelease(&white); SafeRelease(&bg);
    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) { DiscardSurface(g_configWin); return; }
    CommitWindow(g_configWin, AnchorTopCenter);
}

// Handle a click inside the config toolbar; returns true if something changed.
bool ConfigClick(int x, int y) {
    bool changed = false;
    if (InRect(g_rcOpacMinus, x, y)) {
        g_opacity = (g_opacity - 0.05f < 0.40f) ? 0.40f : g_opacity - 0.05f;
        changed = true;
    } else if (InRect(g_rcOpacPlus, x, y)) {
        g_opacity = (g_opacity + 0.05f > 1.00f) ? 1.00f : g_opacity + 0.05f;
        changed = true;
    } else {
        for (int i = 0; i < kAccentCount; ++i)
            if (InRect(g_rcSwatch[i], x, y)) { g_accentIdx = i; changed = true; break; }
    }
    if (changed) {
        RelayoutToasts();
        RenderPanel();
        RenderConfig();
        SaveLayout();
    }
    return changed;
}

// Toggle edit mode: lift/restore click-through, show/hide the appearance
// toolbar, re-render every widget, and on exit persist to the config file.
void ApplyEditMode(bool on) {
    g_editMode = on;
    SetClickThrough(g_toastWin, !on);
    SetClickThrough(g_panelWin, !on);
    SetClickThrough(g_configWin, !on);
    SetClickThrough(g_handsfreeWin, !on);
    RelayoutToasts();
    RenderPanel();
    RenderHandsFree();  // shows a positionable placeholder in edit mode
    if (on) RenderConfig();
    else    HideWindow(g_configWin);
    if (!on) SaveLayout();
}

// ---------------------------------------------------------------------------
// Startup banner — fades in, holds, fades out; tells the user about edit mode.
// ---------------------------------------------------------------------------

constexpr int BANNER_W = 430;

void RenderBanner() {
    const int w = BANNER_W, h = 78;
    if (!EnsureSurface(g_bannerWin, w, h)) return;

    int screenW = GetSystemMetrics(SM_CXSCREEN);
    int screenH = GetSystemMetrics(SM_CYSCREEN);
    g_bannerWin.posX = (screenW - w) / 2;
    g_bannerWin.posY = screenH / 6;

    ID2D1RenderTarget* rt = g_bannerWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    const Rgb& a = kAccents[g_accentIdx];
    ID2D1SolidColorBrush* bg = nullptr, *acc = nullptr, *white = nullptr, *dim = nullptr;
    rt->CreateSolidColorBrush(D2D1::ColorF(0.08f, 0.09f, 0.12f, 0.94f), &bg);
    rt->CreateSolidColorBrush(D2D1::ColorF(a.r, a.g, a.b, 1.0f), &acc);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.96f, 0.97f, 0.99f, 1.0f), &white);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.66f, 0.70f, 0.78f, 1.0f), &dim);

    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, w - 1.0f, h - 1.0f), 14.0f, 14.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawRoundedRectangle(card, acc, 1.4f);

    rt->DrawText(L"Lykompanion overlay active", 26, g_fmtCenter,
                 D2D1::RectF(PAD, 14, w - PAD, 40), white);
    const wchar_t* sub = L"Press Ctrl+Shift+O to move widgets & customize appearance";
    IDWriteTextLayout* subLayout = nullptr;
    if (SUCCEEDED(g_dwriteFactory->CreateTextLayout(
            sub, (UINT32)wcslen(sub), g_fmtLabel, w - 2 * PAD, 26.0f, &subLayout)) &&
        subLayout) {
        subLayout->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
        rt->DrawTextLayout(D2D1::Point2F(PAD, 44), subLayout, dim);
        SafeRelease(&subLayout);
    }

    SafeRelease(&dim); SafeRelease(&white); SafeRelease(&acc); SafeRelease(&bg);
    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) { DiscardSurface(g_bannerWin); return; }
    CommitWindow(g_bannerWin, AnchorFixed, 0);  // starts transparent; timer fades in
}

// Advance the banner fade; returns false when the animation is finished.
bool TickBanner() {
    const ULONGLONG kIn = 350, kHold = 2600, kOut = 800, kTotal = kIn + kHold + kOut;
    ULONGLONG t = GetTickCount64() - g_bannerStart;
    float alpha;
    if (t < kIn)                 alpha = (float)t / kIn;
    else if (t < kIn + kHold)    alpha = 1.0f;
    else if (t < kTotal)         alpha = 1.0f - (float)(t - kIn - kHold) / kOut;
    else                         { HideWindow(g_bannerWin); return false; }
    CommitWindow(g_bannerWin, AnchorFixed, (BYTE)(alpha * 255.0f));
    return true;
}

// ---------------------------------------------------------------------------
// Command dispatch (UI thread)
// ---------------------------------------------------------------------------

void HandleCommand(const std::wstring& line) {
    JsonValue v;
    if (!ParseJson(line, v) || v.type != JsonValue::Obj) return;
    const JsonValue* t = v.find(L"type");
    if (!t || t->type != JsonValue::Str) return;
    const std::wstring& type = t->str;

    if (type == L"toast") {
        const JsonValue* text = v.find(L"text");
        const JsonValue* kind = v.find(L"kind");
        if (text && text->type == JsonValue::Str && !text->str.empty())
            AddToast(text->str, kind ? kind->asStr() : L"reply");
    } else if (type == L"image") {
        const JsonValue* url = v.find(L"url");
        const JsonValue* alt = v.find(L"alt");
        if (url && url->type == JsonValue::Str && !url->str.empty())
            AddImageToast(url->str, alt ? alt->asStr() : L"");
    } else if (type == L"memory") {
        const JsonValue* action = v.find(L"action");
        const JsonValue* scope = v.find(L"scope");
        const JsonValue* text = v.find(L"text");
        if (text && text->type == JsonValue::Str && !text->str.empty())
            AddMemoryToast(action ? action->asStr() : L"save",
                           scope ? scope->asStr() : L"user", text->str);
    } else if (type == L"handsfree") {
        const JsonValue* active = v.find(L"active");
        SetHandsFree(active ? active->asBool() : false);
    } else if (type == L"game_state") {
        g_panel = Panel{};
        g_panel.valid = true;
        const JsonValue* title = v.find(L"title");
        if (title && title->type == JsonValue::Str) g_panel.title = title->str;
        const JsonValue* rows = v.find(L"rows");
        if (rows && rows->type == JsonValue::Arr) {
            for (auto& r : rows->arr) {
                if (r.type == JsonValue::Arr && r.arr.size() >= 2)
                    g_panel.rows.emplace_back(r.arr[0].asStr(), r.arr[1].asStr());
            }
        }
        RenderPanel();
    } else if (type == L"edit_mode") {
        const JsonValue* en = v.find(L"enabled");
        ApplyEditMode(en ? en->asBool() : false);
    } else if (type == L"quit") {
        PostQuitMessage(0);
    }
}

// ---------------------------------------------------------------------------
// Named-pipe server (background thread)
// ---------------------------------------------------------------------------

void EnqueueLine(const std::string& utf8) {
    std::wstring w = Utf8ToWide(utf8);
    {
        std::lock_guard<std::mutex> lk(g_queueMx);
        g_queue.push_back(std::move(w));
    }
    if (g_ctrl) PostMessage(g_ctrl, WM_APP_COMMAND, 0, 0);
}

void PipeThread() {
    for (;;) {
        HANDLE h = CreateNamedPipe(
            kPipeName, PIPE_ACCESS_INBOUND,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1, 0, 8192, 0, nullptr);
        if (h == INVALID_HANDLE_VALUE) { Sleep(500); continue; }

        BOOL connected = ConnectNamedPipe(h, nullptr)
                             ? TRUE
                             : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (connected) {
            std::string buf;
            char tmp[1024];
            DWORD n = 0;
            while (ReadFile(h, tmp, sizeof(tmp), &n, nullptr) && n > 0) {
                buf.append(tmp, n);
                size_t pos;
                while ((pos = buf.find('\n')) != std::string::npos) {
                    std::string line = buf.substr(0, pos);
                    buf.erase(0, pos + 1);
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    if (!line.empty()) EnqueueLine(line);
                }
            }
        }
        DisconnectNamedPipe(h);
        CloseHandle(h);
    }
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

LRESULT CALLBACK CtrlProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_APP_COMMAND: {
            for (;;) {
                std::wstring line;
                {
                    std::lock_guard<std::mutex> lk(g_queueMx);
                    if (g_queue.empty()) break;
                    line = std::move(g_queue.front());
                    g_queue.pop_front();
                }
                HandleCommand(line);
            }
            return 0;
        }
        case WM_APP_IMAGE_READY: {
            int id = (int)wParam;
            ImageData* data = (ImageData*)lParam;
            bool owned = false;
            for (auto& t : g_toasts) {
                if (t.isImage && t.imageId == id) {
                    if (data && data->ok) {
                        t.image.reset(data);  // toast takes ownership
                        t.imgState = ImgReady;
                        owned = true;
                    } else {
                        t.imgState = ImgFailed;
                    }
                    t.expire = GetTickCount64() + IMAGE_MS;  // count lifetime from load
                    break;
                }
            }
            if (data && !owned) delete data;  // toast gone, or decode failed
            RelayoutToasts();
            return 0;
        }
        case WM_HOTKEY:
            if (wParam == HOTKEY_EDIT) ApplyEditMode(!g_editMode);
            return 0;
        case WM_TIMER:
            if (wParam == TIMER_TICK) {
                // Don't let toasts expire out from under you while arranging.
                if (!g_editMode && ExpireToasts()) RelayoutToasts();
            } else if (wParam == TIMER_BANNER) {
                if (!TickBanner()) KillTimer(g_ctrl, TIMER_BANNER);
            } else if (wParam == TIMER_HANDSFREE) {
                RenderHandsFree();  // animate the gradient sweep
            } else if (wParam == TIMER_PARENT) {
                // Parent (Lykompanion) exited — including a hard kill that skips
                // its graceful stop() — so tear ourselves down too.
                if (g_parentProcess &&
                    WaitForSingleObject(g_parentProcess, 0) == WAIT_OBJECT_0)
                    PostQuitMessage(0);
            } else if (wParam == TIMER_DEMO) {
                PostQuitMessage(0);
            }
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}

LayeredWindow* FromHwnd(HWND h) {
    if (h == g_toastWin.hwnd) return &g_toastWin;
    if (h == g_panelWin.hwnd) return &g_panelWin;
    if (h == g_handsfreeWin.hwnd) return &g_handsfreeWin;
    return nullptr;
}

LRESULT CALLBACK WinProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    // The appearance toolbar holds clickable controls — it is never a drag
    // handle, and clicks are dispatched to ConfigClick().
    if (hwnd == g_configWin.hwnd) {
        switch (msg) {
            case WM_NCHITTEST:
                return g_editMode ? HTCLIENT : HTTRANSPARENT;
            case WM_LBUTTONDOWN:
                if (g_editMode) ConfigClick(GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam));
                return 0;
            default:
                return DefWindowProc(hwnd, msg, wParam, lParam);
        }
    }

    switch (msg) {
        case WM_NCHITTEST:
            // In edit mode the whole widget is a drag handle; DefWindowProc then
            // moves the window for us. Otherwise it's click-through anyway.
            return g_editMode ? HTCAPTION : HTTRANSPARENT;
        case WM_MOVE: {
            // Keep the stored position in sync so later re-renders (new toast,
            // etc.) commit at the dragged spot instead of snapping back.
            LayeredWindow* lw = FromHwnd(hwnd);
            if (lw) {
                lw->posX = (int)(short)LOWORD(lParam);
                lw->posY = (int)(short)HIWORD(lParam);
                lw->hasPos = true;
            }
            return 0;
        }
        case WM_EXITSIZEMOVE:
            SaveLayout();
            return 0;
        default:
            return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}

HWND CreateLayeredHwnd(HINSTANCE hInst) {
    DWORD exStyle = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
                    WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
    return CreateWindowEx(exStyle, kWinClass, L"", WS_POPUP,
                          0, 0, TOAST_W, 64, nullptr, nullptr, hInst, nullptr);
}

void InjectDemo() {
    HandleCommand(L"{\"type\":\"game_state\",\"title\":\"Elden Ring\",\"rows\":"
                  L"[[\"Runes\",\"48,210\"],[\"Location\",\"Limgrave\"],"
                  L"[\"Deaths\",\"17\"]]}");
    HandleCommand(L"{\"type\":\"toast\",\"kind\":\"reply\",\"text\":"
                  L"\"Hey! The boss room is just north of you \\u2014 watch the cliff edge.\"}");
    HandleCommand(L"{\"type\":\"toast\",\"kind\":\"reminder\",\"text\":"
                  L"\"Reminder: take a short break in 5 minutes.\"}");
    HandleCommand(L"{\"type\":\"memory\",\"action\":\"save\",\"scope\":\"user\","
                  L"\"text\":\"Prefers concise answers and plays on hard difficulty.\"}");
    HandleCommand(L"{\"type\":\"handsfree\",\"active\":true}");
    // Image toast (needs network; shows "[image unavailable]" if offline).
    HandleCommand(L"{\"type\":\"image\",\"alt\":\"Sample map image\","
                  L"\"url\":\"https://picsum.photos/400/240\"}");
}

}  // namespace

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE, LPWSTR, int) {
    // Args: --demo (sample content, auto-quit) and --parent <pid> (self-exit if
    // that process dies — our safety net against the parent being hard-killed).
    bool demo = false;
    DWORD parentPid = 0;
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        for (int i = 1; i < argc; ++i) {
            if (wcscmp(argv[i], L"--demo") == 0) demo = true;
            else if (wcscmp(argv[i], L"--parent") == 0 && i + 1 < argc)
                parentPid = (DWORD)_wtoi(argv[++i]);
        }
        LocalFree(argv);
    }

    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);  // for WIC / URL image downloads

    if (!CreateFactories()) return 1;

    WNDCLASSEX cc = {};
    cc.cbSize = sizeof(cc);
    cc.lpfnWndProc = CtrlProc;
    cc.hInstance = hInstance;
    cc.lpszClassName = kCtrlClass;
    RegisterClassEx(&cc);

    WNDCLASSEX wc = {};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WinProc;
    wc.hInstance = hInstance;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = kWinClass;
    RegisterClassEx(&wc);

    g_ctrl = CreateWindowEx(0, kCtrlClass, L"LykoOverlay", 0,
                            0, 0, 0, 0, HWND_MESSAGE, nullptr, hInstance, nullptr);
    if (!g_ctrl) return 2;

    g_toastWin.hwnd     = CreateLayeredHwnd(hInstance);
    g_panelWin.hwnd     = CreateLayeredHwnd(hInstance);
    g_configWin.hwnd    = CreateLayeredHwnd(hInstance);
    g_bannerWin.hwnd    = CreateLayeredHwnd(hInstance);
    g_handsfreeWin.hwnd = CreateLayeredHwnd(hInstance);
    if (!g_toastWin.hwnd || !g_panelWin.hwnd || !g_configWin.hwnd ||
        !g_bannerWin.hwnd || !g_handsfreeWin.hwnd)
        return 3;

    LoadLogo();    // reply-toast avatar (logo.png next to the exe)
    LoadLayout();  // restore saved positions + appearance before first commit

    // Ctrl+Shift+O toggles edit mode. Registered on the controller window so its
    // message loop (already running) delivers WM_HOTKEY.
    RegisterHotKey(g_ctrl, HOTKEY_EDIT, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 'O');

    SetTimer(g_ctrl, TIMER_TICK, 250, nullptr);

    // Watch the parent process (if given) so we never outlive Lykompanion.
    if (parentPid) {
        g_parentProcess = OpenProcess(SYNCHRONIZE, FALSE, parentPid);
        if (g_parentProcess) SetTimer(g_ctrl, TIMER_PARENT, 1000, nullptr);
    }

    // Startup hint banner (fade in/out).
    g_bannerStart = GetTickCount64();
    RenderBanner();
    SetTimer(g_ctrl, TIMER_BANNER, 30, nullptr);

    // Local API server thread (detached: process exit tears it down).
    std::thread(PipeThread).detach();

    if (demo) {
        InjectDemo();
        SetTimer(g_ctrl, TIMER_DEMO, 20000, nullptr);  // auto-quit after 20s
    }

    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    UnregisterHotKey(g_ctrl, HOTKEY_EDIT);
    DiscardSurface(g_toastWin);
    DiscardSurface(g_panelWin);
    DiscardSurface(g_configWin);
    DiscardSurface(g_bannerWin);
    DiscardSurface(g_handsfreeWin);
    if (g_parentProcess) CloseHandle(g_parentProcess);
    CoUninitialize();
    SafeRelease(&g_dashStroke);
    SafeRelease(&g_fmtCenter);
    SafeRelease(&g_fmtIcon);
    SafeRelease(&g_fmtHead);
    SafeRelease(&g_fmtSmall);
    SafeRelease(&g_fmtLabel);
    SafeRelease(&g_fmtBody);
    SafeRelease(&g_fmtTitle);
    SafeRelease(&g_dwriteFactory);
    SafeRelease(&g_d2dFactory);
    return 0;
}
