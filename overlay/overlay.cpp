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

#include <windows.h>
#include <d2d1.h>
#include <dwrite.h>
#include <cstdint>
#include <string>
#include <vector>
#include <deque>
#include <mutex>
#include <thread>
#include <utility>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")

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

constexpr UINT WM_APP_COMMAND = WM_APP + 1;  // a JSON line is queued
constexpr UINT_PTR TIMER_TICK = 1;           // toast-expiry tick
constexpr UINT_PTR TIMER_DEMO = 2;           // --demo auto-quit

// Widget geometry (device pixels @ 96 DPI; DPI scaling is a later concern).
constexpr int   TOAST_W   = 340;
constexpr int   PANEL_W   = 300;
constexpr float PAD       = 14.0f;
constexpr int   MARGIN    = 24;     // gap from the screen edge
constexpr int   TOAST_GAP = 10;     // vertical gap between stacked toasts
constexpr int   ROW_H     = 24;     // game-state row height
constexpr DWORD TOAST_MS  = 6000;   // default toast lifetime

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
};

enum Anchor { AnchorTopRight, AnchorTopLeft };

// ---- Widget content --------------------------------------------------------

struct Toast {
    std::wstring text;
    std::wstring kind;   // "reply" | "reminder"
    ULONGLONG    expire; // GetTickCount64() deadline
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
std::vector<Toast> g_toasts;
Panel         g_panel;
bool          g_editMode = false;  // Sprint C3 will act on this

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
    return true;
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

// Push the rendered DIB to screen at an anchored position.
void CommitWindow(LayeredWindow& lw, Anchor anchor) {
    int screenW = GetSystemMetrics(SM_CXSCREEN);
    int x = (anchor == AnchorTopRight) ? screenW - MARGIN - lw.width : MARGIN;
    int y = MARGIN;

    if (!lw.visible) {
        ShowWindow(lw.hwnd, SW_SHOWNOACTIVATE);
        lw.visible = true;
    }

    POINT ptDst = {x, y};
    SIZE  size  = {lw.width, lw.height};
    POINT ptSrc = {0, 0};
    BLENDFUNCTION blend = {};
    blend.BlendOp             = AC_SRC_OVER;
    blend.SourceConstantAlpha = 255;
    blend.AlphaFormat         = AC_SRC_ALPHA;

    HDC screenDC = GetDC(nullptr);
    UpdateLayeredWindow(lw.hwnd, screenDC, &ptDst, &size,
                        lw.memDC, &ptSrc, 0, &blend, ULW_ALPHA);
    ReleaseDC(nullptr, screenDC);
}

void HideWindow(LayeredWindow& lw) {
    if (lw.visible) { ShowWindow(lw.hwnd, SW_HIDE); lw.visible = false; }
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

// ---------------------------------------------------------------------------
// Toast widget
// ---------------------------------------------------------------------------

void RelayoutToasts() {
    if (g_toasts.empty()) { HideWindow(g_toastWin); return; }

    const float innerW = TOAST_W - 2 * PAD;

    // Measure each toast's wrapped body height.
    std::vector<float> cardH(g_toasts.size());
    std::vector<IDWriteTextLayout*> layouts(g_toasts.size(), nullptr);
    int total = 0;
    for (size_t i = 0; i < g_toasts.size(); ++i) {
        float h = 0;
        layouts[i] = MakeLayout(g_toasts[i].text, g_fmtBody, innerW, &h);
        cardH[i] = (h + 2 * PAD < 44.0f) ? 44.0f : (h + 2 * PAD);
        total += (int)cardH[i];
        if (i + 1 < g_toasts.size()) total += TOAST_GAP;
    }

    if (!EnsureSurface(g_toastWin, TOAST_W, total)) {
        for (auto* l : layouts) SafeRelease(&l);
        return;
    }

    ID2D1DCRenderTarget* rt = g_toastWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush* text = nullptr;
    rt->CreateSolidColorBrush(D2D1::ColorF(0.96f, 0.97f, 0.99f, 1.0f), &text);

    float y = 0;
    for (size_t i = 0; i < g_toasts.size(); ++i) {
        bool reminder = (g_toasts[i].kind == L"reminder");
        ID2D1SolidColorBrush* bg = nullptr;
        ID2D1SolidColorBrush* border = nullptr;
        if (reminder) {
            rt->CreateSolidColorBrush(D2D1::ColorF(0.20f, 0.15f, 0.05f, 0.92f), &bg);
            rt->CreateSolidColorBrush(D2D1::ColorF(0.95f, 0.70f, 0.30f, 0.75f), &border);
        } else {
            rt->CreateSolidColorBrush(D2D1::ColorF(0.10f, 0.13f, 0.20f, 0.92f), &bg);
            rt->CreateSolidColorBrush(D2D1::ColorF(0.40f, 0.55f, 0.95f, 0.70f), &border);
        }

        D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
            D2D1::RectF(1.0f, y + 1.0f, TOAST_W - 1.0f, y + cardH[i] - 1.0f), 12.0f, 12.0f);
        rt->FillRoundedRectangle(card, bg);
        rt->DrawRoundedRectangle(card, border, 1.3f);

        if (layouts[i])
            rt->DrawTextLayout(D2D1::Point2F(PAD, y + PAD), layouts[i], text);

        SafeRelease(&border);
        SafeRelease(&bg);
        y += cardH[i] + TOAST_GAP;
    }

    SafeRelease(&text);
    for (auto* l : layouts) SafeRelease(&l);

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

void RenderPanel() {
    if (!g_panel.valid ||
        (g_panel.title.empty() && g_panel.rows.empty())) {
        HideWindow(g_panelWin);
        return;
    }

    const float innerW = PANEL_W - 2 * PAD;
    float titleH = 0;
    IDWriteTextLayout* titleLayout = nullptr;
    if (!g_panel.title.empty())
        titleLayout = MakeLayout(g_panel.title, g_fmtTitle, innerW, &titleH);

    int height = (int)(PAD + titleH + (titleH > 0 ? 8 : 0) +
                       g_panel.rows.size() * ROW_H + PAD);

    if (!EnsureSurface(g_panelWin, PANEL_W, height)) {
        SafeRelease(&titleLayout);
        return;
    }

    ID2D1DCRenderTarget* rt = g_panelWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush* bg = nullptr, *border = nullptr;
    ID2D1SolidColorBrush* white = nullptr, *dim = nullptr;
    rt->CreateSolidColorBrush(D2D1::ColorF(0.07f, 0.08f, 0.11f, 0.90f), &bg);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.35f, 0.40f, 0.55f, 0.55f), &border);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.96f, 0.97f, 0.99f, 1.0f), &white);
    rt->CreateSolidColorBrush(D2D1::ColorF(0.62f, 0.66f, 0.74f, 1.0f), &dim);

    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, PANEL_W - 1.0f, height - 1.0f), 12.0f, 12.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawRoundedRectangle(card, border, 1.3f);

    float y = PAD;
    if (titleLayout) {
        rt->DrawTextLayout(D2D1::Point2F(PAD, y), titleLayout, white);
        y += titleH + 8;
    }
    for (auto& row : g_panel.rows) {
        D2D1_RECT_F lr = D2D1::RectF(PAD, y, PAD + innerW, y + ROW_H);
        rt->DrawText(row.first.c_str(), (UINT32)row.first.size(), g_fmtLabel, lr, dim);
        // Value: right-aligned within the same row rect.
        IDWriteTextLayout* vl = nullptr;
        if (SUCCEEDED(g_dwriteFactory->CreateTextLayout(
                row.second.c_str(), (UINT32)row.second.size(), g_fmtBody,
                innerW, (float)ROW_H, &vl)) && vl) {
            vl->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_TRAILING);
            rt->DrawTextLayout(D2D1::Point2F(PAD, y + 1), vl, white);
            SafeRelease(&vl);
        }
        y += ROW_H;
    }

    SafeRelease(&dim); SafeRelease(&white);
    SafeRelease(&border); SafeRelease(&bg);
    SafeRelease(&titleLayout);

    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) {
        DiscardSurface(g_panelWin);
        return;
    }
    CommitWindow(g_panelWin, AnchorTopLeft);
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
        g_editMode = en ? en->asBool() : false;
        // Sprint C3: lift WS_EX_TRANSPARENT, draw drag affordances, persist layout.
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
        case WM_TIMER:
            if (wParam == TIMER_TICK) {
                if (ExpireToasts()) RelayoutToasts();
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

LRESULT CALLBACK WinProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    // Layered widgets are click-through in C2 (WS_EX_TRANSPARENT); no input yet.
    return DefWindowProc(hwnd, msg, wParam, lParam);
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
}

}  // namespace

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE, LPWSTR lpCmdLine, int) {
    const bool demo = (wcsstr(lpCmdLine ? lpCmdLine : L"", L"--demo") != nullptr);

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

    g_toastWin.hwnd = CreateLayeredHwnd(hInstance);
    g_panelWin.hwnd = CreateLayeredHwnd(hInstance);
    if (!g_toastWin.hwnd || !g_panelWin.hwnd) return 3;

    SetTimer(g_ctrl, TIMER_TICK, 250, nullptr);

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

    DiscardSurface(g_toastWin);
    DiscardSurface(g_panelWin);
    SafeRelease(&g_fmtLabel);
    SafeRelease(&g_fmtBody);
    SafeRelease(&g_fmtTitle);
    SafeRelease(&g_dwriteFactory);
    SafeRelease(&g_d2dFactory);
    return 0;
}
