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
#include <xinput.h>
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
#include <cstdlib>
#include <memory>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "urlmon.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "xinput.lib")

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
constexpr UINT_PTR TIMER_GAMEPAD = 6;        // XInput poll, runs only while in edit mode
constexpr int      HOTKEY_EDIT = 100;        // configurable edit-mode toggle

constexpr int   AVATAR = 22;        // logo avatar size in reply toasts

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
IDWriteTextFormat* g_fmtUi    = nullptr;  // edit-toolbar labels (fixed size/font)
ID2D1StrokeStyle*  g_dashStroke = nullptr; // dashed edit-mode outline

// Base sizes for the user-scalable text formats (multiplied by g_fontScale).
constexpr float kSzTitle = 18.0f;  // toast title / panel title
constexpr float kSzBody  = 14.0f;  // toast body / panel value
constexpr float kSzLabel = 13.0f;  // panel label / image caption
constexpr float kSzSmall = 11.0f;  // panel row label (uppercase)
constexpr float kSzHead  = 14.0f;  // memory / handsfree header

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

enum Anchor { AnchorTopRight, AnchorTopLeft, AnchorTopCenter, AnchorBottomCenter, AnchorBottomRight, AnchorFixed };

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

// Text customization (persisted, live-adjustable in edit mode).
const wchar_t* const kFonts[] = {
    L"Segoe UI", L"Arial", L"Verdana", L"Georgia", L"Consolas", L"Comic Sans MS",
};
constexpr int kFontCount = (int)(sizeof(kFonts) / sizeof(kFonts[0]));
int   g_fontIdx   = 0;
float g_fontScale = 1.0f;    // 0.70..1.60 multiplier over the base text sizes

// Per-area visibility (persisted, toggled in edit mode). When a widget is
// disabled it hides entirely — even in edit mode; re-enable it from the toolbar.
bool  g_showToasts    = true;   // reply / reminder / image toasts
bool  g_showMemories  = true;   // memory save/remove toasts (own area)
bool  g_showPanel     = true;   // game-state panel
bool  g_showHandsfree = true;   // live-mic indicator

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
LayeredWindow g_memWin;            // memory save/remove toasts (separate area)
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
std::vector<Toast> g_toasts;     // reply / reminder / image toasts
std::vector<Toast> g_memToasts;  // memory toasts (rendered in g_memWin)
Panel         g_panel;
bool          g_editMode = false;

// ---------------------------------------------------------------------------
// Position/appearance presets, dirty tracking, hotkey config, gamepad state,
// input modality — all namespace-scope structs/enums (MSVC rejects a
// function-local struct with a default member initializer referencing a
// function-local enum; keep every new type declared here, never inside a fn).
// ---------------------------------------------------------------------------

struct LayoutPreset {
    std::wstring name = L"Default";
    int toastX=0, toastY=0; bool toastHasPos=false;
    int memX=0, memY=0; bool memHasPos=false;
    int panelX=0, panelY=0; bool panelHasPos=false;
    int handsfreeX=0, handsfreeY=0; bool handsfreeHasPos=false;
    float opacity = 0.92f;
    int accentIdx = 0;
    int fontIdx = 0;
    float fontScale = 1.0f;
    bool showToasts=true, showMemories=true, showPanel=true, showHandsfree=true;
};
std::vector<LayoutPreset> g_presets;
int           g_activePreset = 0;
bool          g_dirty = false;             // any live change since edit mode was entered
LayoutPreset  g_editSnapshot;               // state at the moment edit mode was entered
bool          g_showExitConfirm = false;    // "unsaved changes" prompt (hotkey-triggered exit)

// Rename (keyboard-only, mouse-triggered — no gamepad path drives this).
bool          g_renamingPreset = false;
std::wstring  g_renameBuffer;
int           g_renameIdx = -1;
constexpr size_t kRenameMaxLen = 24;

// Configurable edit-mode hotkey (defaults match the historical Ctrl+Shift+O).
std::vector<std::wstring> g_hotkeyMods = {L"ctrl", L"shift"};
wchar_t       g_hotkeyKey = L'O';

enum InputModality { ModalityMouse, ModalityGamepad };
InputModality g_lastModality = ModalityMouse;

// Indexable widget registry (gamepad cycling target), parallel arrays.
LayeredWindow* g_widgets[4] = { nullptr, nullptr, nullptr, nullptr };  // filled in wWinMain
const wchar_t* g_widgetLabels[4] = { L"Chat toasts", L"Memory toasts", L"Game panel", L"Mic indicator" };
int           g_selectedWidgetIdx = 0;   // gamepad-only concept, independent of mouse drag

WORD          g_prevButtons = 0;         // previous-frame XInput button state (edge detection)
ULONGLONG     g_lastCycleTick = 0;       // debounce for D-pad/stick widget cycling
constexpr DWORD CYCLE_DEBOUNCE_MS = 220;
constexpr float GAMEPAD_MOVE_SPEED = 480.0f;  // px/sec at full stick deflection

// Config-toolbar hit rects (window coords), filled in by RenderConfig().
constexpr int CONFIG_W = 340;
constexpr int CONFIG_H = 324;
D2D1_RECT_F   g_rcOpacMinus = {};
D2D1_RECT_F   g_rcOpacPlus  = {};
D2D1_RECT_F   g_rcTextMinus = {};
D2D1_RECT_F   g_rcTextPlus  = {};
D2D1_RECT_F   g_rcFontPrev  = {};
D2D1_RECT_F   g_rcFontNext  = {};
D2D1_RECT_F   g_rcSwatch[kAccentCount] = {};
D2D1_RECT_F   g_rcToggle[4] = {};   // show: toasts / memories / panel / mic
D2D1_RECT_F   g_rcPresetNew = {};
D2D1_RECT_F   g_rcPresetDelete = {};
D2D1_RECT_F   g_rcPresetRename = {};
std::vector<D2D1_RECT_F> g_rcPresetChips;  // parallel to g_presets, rebuilt each render
D2D1_RECT_F   g_rcSave = {};
D2D1_RECT_F   g_rcDiscardClose = {};
D2D1_RECT_F   g_rcConfirmSave = {};
D2D1_RECT_F   g_rcConfirmDiscard = {};
D2D1_RECT_F   g_rcConfirmKeep = {};

std::mutex               g_queueMx;
std::deque<std::wstring> g_queue;   // raw JSON lines from the pipe thread

// ---------------------------------------------------------------------------
// Direct2D surface management (shared by both widgets)
// ---------------------------------------------------------------------------

// (Re)create the user-scalable text formats from the current font + scale. Called
// once at startup and again whenever the user changes text size / font in edit mode.
bool RebuildTextFormats() {
    SafeRelease(&g_fmtTitle);
    SafeRelease(&g_fmtBody);
    SafeRelease(&g_fmtLabel);
    SafeRelease(&g_fmtSmall);
    SafeRelease(&g_fmtHead);

    const wchar_t* fam = kFonts[g_fontIdx];
    const float s = g_fontScale;
    auto mk = [&](DWRITE_FONT_WEIGHT w, float px, IDWriteTextFormat** out) -> bool {
        return SUCCEEDED(g_dwriteFactory->CreateTextFormat(
            fam, nullptr, w, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_STRETCH_NORMAL,
            px * s, L"en-us", out));
    };
    return mk(DWRITE_FONT_WEIGHT_SEMI_BOLD, kSzTitle, &g_fmtTitle)
        && mk(DWRITE_FONT_WEIGHT_NORMAL,    kSzBody,  &g_fmtBody)
        && mk(DWRITE_FONT_WEIGHT_NORMAL,    kSzLabel, &g_fmtLabel)
        && mk(DWRITE_FONT_WEIGHT_NORMAL,    kSzSmall, &g_fmtSmall)
        && mk(DWRITE_FONT_WEIGHT_SEMI_BOLD, kSzHead,  &g_fmtHead);
}

bool CreateFactories() {
    if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, &g_d2dFactory)))
        return false;
    if (FAILED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory),
                                   reinterpret_cast<IUnknown**>(&g_dwriteFactory))))
        return false;

    // Fixed-size chrome formats (never scaled — they must stay usable/legible
    // regardless of the text size the user is editing).
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
    if (FAILED(g_dwriteFactory->CreateTextFormat(
            L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL, 13.0f, L"en-us", &g_fmtUi)))
        return false;

    if (!RebuildTextFormats()) return false;

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

// Build a LayoutPreset from the CURRENT live globals (widget positions +
// appearance + visibility). Used for snapshots, saves, and new-preset cloning.
LayoutPreset CaptureSnapshot() {
    LayoutPreset p;
    p.toastX = g_toastWin.posX; p.toastY = g_toastWin.posY; p.toastHasPos = g_toastWin.hasPos;
    p.memX = g_memWin.posX; p.memY = g_memWin.posY; p.memHasPos = g_memWin.hasPos;
    p.panelX = g_panelWin.posX; p.panelY = g_panelWin.posY; p.panelHasPos = g_panelWin.hasPos;
    p.handsfreeX = g_handsfreeWin.posX; p.handsfreeY = g_handsfreeWin.posY;
    p.handsfreeHasPos = g_handsfreeWin.hasPos;
    p.opacity = g_opacity;
    p.accentIdx = g_accentIdx;
    p.fontIdx = g_fontIdx;
    p.fontScale = g_fontScale;
    p.showToasts = g_showToasts; p.showMemories = g_showMemories;
    p.showPanel = g_showPanel; p.showHandsfree = g_showHandsfree;
    if (g_activePreset >= 0 && g_activePreset < (int)g_presets.size())
        p.name = g_presets[g_activePreset].name;
    return p;
}

// Write a LayoutPreset's fields back into the live globals + re-render every
// widget. Deliberately does NOT touch g_configWin's visibility (that's owned
// by ApplyEditMode) — these are forward declarations to widget render
// functions defined later in the file (after the widget render functions).
void RelayoutToasts();
void RelayoutMemories();
void RenderPanel();
void RenderHandsFree();
void RenderConfig();
void ApplySnapshot(const LayoutPreset& p) {
    g_toastWin.posX = p.toastX; g_toastWin.posY = p.toastY; g_toastWin.hasPos = p.toastHasPos;
    g_memWin.posX = p.memX; g_memWin.posY = p.memY; g_memWin.hasPos = p.memHasPos;
    g_panelWin.posX = p.panelX; g_panelWin.posY = p.panelY; g_panelWin.hasPos = p.panelHasPos;
    g_handsfreeWin.posX = p.handsfreeX; g_handsfreeWin.posY = p.handsfreeY;
    g_handsfreeWin.hasPos = p.handsfreeHasPos;
    g_opacity = p.opacity;
    g_accentIdx = p.accentIdx;
    g_fontIdx = p.fontIdx;
    g_fontScale = p.fontScale;
    g_showToasts = p.showToasts; g_showMemories = p.showMemories;
    g_showPanel = p.showPanel; g_showHandsfree = p.showHandsfree;
    RebuildTextFormats();
    RelayoutToasts();
    RelayoutMemories();
    RenderPanel();
    RenderHandsFree();
    if (g_editMode) RenderConfig();  // toolbar visibility/showing is owned by ApplyEditMode
}

std::wstring JsonEscape(const std::wstring& s) {
    std::wstring out;
    for (wchar_t c : s) {
        if (c == L'"' || c == L'\\') out.push_back(L'\\');
        out.push_back(c);
    }
    return out;
}

std::string WideToUtf8(const std::wstring& w) {
    if (w.empty()) return std::string();
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
    return s;
}

std::string SerializePreset(const LayoutPreset& p) {
    return "{\"name\":\"" + WideToUtf8(JsonEscape(p.name)) + "\"" +
           ",\"toast\":{\"x\":" + std::to_string(p.toastX) + ",\"y\":" + std::to_string(p.toastY) + "}" +
           ",\"memory\":{\"x\":" + std::to_string(p.memX) + ",\"y\":" + std::to_string(p.memY) + "}" +
           ",\"panel\":{\"x\":" + std::to_string(p.panelX) + ",\"y\":" + std::to_string(p.panelY) + "}" +
           ",\"handsfree\":{\"x\":" + std::to_string(p.handsfreeX) + ",\"y\":" + std::to_string(p.handsfreeY) + "}" +
           ",\"opacity\":" + std::to_string(p.opacity) +
           ",\"accent\":" + std::to_string(p.accentIdx) +
           ",\"font\":" + std::to_string(p.fontIdx) +
           ",\"textScale\":" + std::to_string(p.fontScale) +
           ",\"showToasts\":" + (p.showToasts ? "true" : "false") +
           ",\"showMemories\":" + (p.showMemories ? "true" : "false") +
           ",\"showPanel\":" + (p.showPanel ? "true" : "false") +
           ",\"showHandsfree\":" + (p.showHandsfree ? "true" : "false") + "}";
}

void SaveLayout() {
    std::string hotkeyMods = "[";
    for (size_t i = 0; i < g_hotkeyMods.size(); ++i) {
        if (i) hotkeyMods += ",";
        hotkeyMods += "\"" + WideToUtf8(g_hotkeyMods[i]) + "\"";
    }
    hotkeyMods += "]";

    std::string presets = "[";
    for (size_t i = 0; i < g_presets.size(); ++i) {
        if (i) presets += ",";
        presets += SerializePreset(g_presets[i]);
    }
    presets += "]";

    std::string js = "{\"hotkeyMods\":" + hotkeyMods +
                     ",\"hotkeyKey\":\"" + WideToUtf8(std::wstring(1, g_hotkeyKey)) + "\"" +
                     ",\"activePreset\":" + std::to_string(g_activePreset) +
                     ",\"presets\":" + presets + "}";
    HANDLE h = CreateFileW(LayoutPath().c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD n = 0;
        WriteFile(h, js.data(), (DWORD)js.size(), &n, nullptr);
        CloseHandle(h);
    }
}

// Parse one preset object (mirrors the old flat-shape fields, plus "name").
LayoutPreset ParsePreset(const JsonValue& v) {
    LayoutPreset p;
    const JsonValue* nm = v.find(L"name");
    if (nm && nm->type == JsonValue::Str && !nm->str.empty()) p.name = nm->str;

    auto applyPos = [&](const wchar_t* key, int& px, int& py, bool& has) {
        const JsonValue* o = v.find(key);
        if (!o || o->type != JsonValue::Obj) return;
        const JsonValue* x = o->find(L"x");
        const JsonValue* y = o->find(L"y");
        if (x && x->type == JsonValue::Num && y && y->type == JsonValue::Num) {
            px = (int)x->num; py = (int)y->num; has = true;
        }
    };
    applyPos(L"toast", p.toastX, p.toastY, p.toastHasPos);
    applyPos(L"memory", p.memX, p.memY, p.memHasPos);
    applyPos(L"panel", p.panelX, p.panelY, p.panelHasPos);
    applyPos(L"handsfree", p.handsfreeX, p.handsfreeY, p.handsfreeHasPos);

    const JsonValue* op = v.find(L"opacity");
    if (op && op->type == JsonValue::Num) {
        p.opacity = (float)op->num;
        if (p.opacity < 0.40f) p.opacity = 0.40f;
        if (p.opacity > 1.00f) p.opacity = 1.00f;
    }
    const JsonValue* ac = v.find(L"accent");
    if (ac && ac->type == JsonValue::Num) {
        p.accentIdx = (int)ac->num;
        if (p.accentIdx < 0) p.accentIdx = 0;
        if (p.accentIdx >= kAccentCount) p.accentIdx = kAccentCount - 1;
    }
    const JsonValue* fn = v.find(L"font");
    if (fn && fn->type == JsonValue::Num) {
        p.fontIdx = (int)fn->num;
        if (p.fontIdx < 0) p.fontIdx = 0;
        if (p.fontIdx >= kFontCount) p.fontIdx = kFontCount - 1;
    }
    const JsonValue* ts = v.find(L"textScale");
    if (ts && ts->type == JsonValue::Num) {
        p.fontScale = (float)ts->num;
        if (p.fontScale < 0.70f) p.fontScale = 0.70f;
        if (p.fontScale > 1.60f) p.fontScale = 1.60f;
    }
    auto applyBool = [&](const wchar_t* key, bool& flag) {
        const JsonValue* b = v.find(key);
        if (b && b->type == JsonValue::Bool) flag = b->b;
    };
    applyBool(L"showToasts", p.showToasts);
    applyBool(L"showMemories", p.showMemories);
    applyBool(L"showPanel", p.showPanel);
    applyBool(L"showHandsfree", p.showHandsfree);
    return p;
}

void LoadLayout() {
    std::ifstream f(LayoutPath(), std::ios::binary);
    if (!f) return;
    std::string content((std::istreambuf_iterator<char>(f)),
                        std::istreambuf_iterator<char>());
    JsonValue v;
    if (!ParseJson(Utf8ToWide(content), v) || v.type != JsonValue::Obj) return;

    const JsonValue* hm = v.find(L"hotkeyMods");
    if (hm && hm->type == JsonValue::Arr) {
        g_hotkeyMods.clear();
        for (auto& m : hm->arr) if (m.type == JsonValue::Str) g_hotkeyMods.push_back(m.str);
        if (g_hotkeyMods.empty()) g_hotkeyMods = {L"ctrl", L"shift"};
    }
    const JsonValue* hk = v.find(L"hotkeyKey");
    if (hk && hk->type == JsonValue::Str && !hk->str.empty()) g_hotkeyKey = hk->str[0];

    const JsonValue* presets = v.find(L"presets");
    if (presets && presets->type == JsonValue::Arr && !presets->arr.empty()) {
        // Current shape: an array of presets.
        g_presets.clear();
        for (auto& pv : presets->arr)
            if (pv.type == JsonValue::Obj) g_presets.push_back(ParsePreset(pv));
        const JsonValue* ap = v.find(L"activePreset");
        g_activePreset = (ap && ap->type == JsonValue::Num) ? (int)ap->num : 0;
    } else if (v.find(L"toast") || v.find(L"opacity")) {
        // One-time forward migration from the old flat shape (no "presets" key).
        g_presets.clear();
        g_presets.push_back(ParsePreset(v));
        g_presets[0].name = L"Default";
        g_activePreset = 0;
    }
    if (g_presets.empty()) g_presets.push_back(LayoutPreset{});
    if (g_activePreset < 0 || g_activePreset >= (int)g_presets.size()) g_activePreset = 0;

    ApplySnapshot(g_presets[g_activePreset]);
    if (presets == nullptr || presets->type != JsonValue::Arr || presets->arr.empty())
        SaveLayout();  // persist the migrated shape immediately
}

// Compute the RegisterHotKey MOD_* bitmask from g_hotkeyMods.
UINT HotkeyModsBitmask() {
    UINT mods = MOD_NOREPEAT;
    for (auto& m : g_hotkeyMods) {
        if (m == L"ctrl")       mods |= MOD_CONTROL;
        else if (m == L"shift") mods |= MOD_SHIFT;
        else if (m == L"alt")   mods |= MOD_ALT;
        else if (m == L"win")   mods |= MOD_WIN;
    }
    return mods;
}

// (Re)register the global edit-mode hotkey from the current g_hotkeyMods/g_hotkeyKey.
void RegisterEditHotkey() {
    RegisterHotKey(g_ctrl, HOTKEY_EDIT, HotkeyModsBitmask(), (UINT)towupper(g_hotkeyKey));
}
void ReregisterEditHotkey() {
    UnregisterHotKey(g_ctrl, HOTKEY_EDIT);
    RegisterEditHotkey();
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
        bool right  = (anchor == AnchorTopRight || anchor == AnchorBottomRight);
        bool bottom = (anchor == AnchorBottomCenter || anchor == AnchorBottomRight);
        lw.posX = right ? screenW - MARGIN - lw.width
                : (anchor == AnchorBottomCenter) ? (screenW - lw.width) / 2
                : MARGIN;
        lw.posY = bottom ? GetSystemMetrics(SM_CYSCREEN) - MARGIN - lw.height : MARGIN;
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

// Solid accent-colored ring marking the gamepad-cycling "selected" widget —
// distinct from the dashed DrawEditDecoration, and only relevant in gamepad
// modality (mouse dragging has no notion of "selected", it drags directly).
void DrawSelectionRing(ID2D1RenderTarget* rt, int w, int h) {
    const Rgb& a = kAccents[g_accentIdx];
    ID2D1SolidColorBrush* b = nullptr;
    rt->CreateSolidColorBrush(D2D1::ColorF(a.r, a.g, a.b, 1.0f), &b);
    D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(
        D2D1::RectF(2.0f, 2.0f, w - 2.0f, h - 2.0f), 12.0f, 12.0f);
    rt->DrawRoundedRectangle(rr, b, 3.0f);
    SafeRelease(&b);
}

// True while `lw` is the gamepad-selected widget and a gamepad is the active
// input modality (index looked up by hwnd since g_widgets isn't populated yet
// at the point some widgets render for the first time during startup).
bool IsGamepadSelected(const LayeredWindow& lw) {
    if (g_lastModality != ModalityGamepad) return false;
    if (g_selectedWidgetIdx < 0 || g_selectedWidgetIdx >= 4) return false;
    return g_widgets[g_selectedWidgetIdx] == &lw;
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

// Human-readable name for a modifier/key token, used both for the hint text
// and for RegisterHotKey's MOD_* bitmask (see RegisterEditHotkey).
std::wstring FormatHotkeyCombo() {
    std::wstring s;
    for (auto& m : g_hotkeyMods) {
        std::wstring cap = m;
        if (!cap.empty()) cap[0] = (wchar_t)towupper(cap[0]);
        s += cap + L"+";
    }
    s += std::wstring(1, g_hotkeyKey);
    return s;
}

// The edit-mode hint line adapts to the live configured hotkey and to whichever
// input the user is currently using (mouse vs. gamepad) — see g_lastModality.
std::wstring EditHintText() {
    if (g_lastModality == ModalityGamepad) {
        return L"Left stick/D-pad: select  \x2022  Right stick: move  \x2022  "
               L"A: Save  X: Delete  Y: New  B: Discard/Exit  LB/RB: Preset";
    }
    return L"Drag to move  \x2022  " + FormatHotkeyCombo() + L" to lock";
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
    std::wstring hint = EditHintText();
    rt->DrawText(hint.c_str(), (UINT32)hint.size(), g_fmtLabel,
                 D2D1::RectF(PAD, 36, width - PAD, 58), dim);
    DrawEditDecoration(rt, width, h);
    if (IsGamepadSelected(lw)) DrawSelectionRing(rt, width, h);

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

// Render a stack of toasts into `win`, anchored to `anchor`. Shared by the
// reply/reminder/image area (g_toasts → g_toastWin) and the separate memory
// area (g_memToasts → g_memWin). `show` gates the whole area on/off.
void RenderToastList(std::vector<Toast>& toasts, LayeredWindow& win,
                     Anchor anchor, const wchar_t* placeholder, bool show) {
    if (!show) { HideWindow(win); return; }
    if (toasts.empty()) {
        if (g_editMode) RenderPlaceholder(win, placeholder, anchor, TOAST_W);
        else HideWindow(win);
        return;
    }

    const float innerW = TOAST_W - 2 * PAD;

    using Plan = ToastPlan;
    std::vector<Plan> plans(toasts.size());
    std::vector<float> cardH(toasts.size());
    int total = 0;

    for (size_t i = 0; i < toasts.size(); ++i) {
        Toast& t = toasts[i];
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
            if (t.isImage && t.imgState == ImgLoading) status = L"Loading image\x2026";
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
        if (i + 1 < toasts.size()) total += TOAST_GAP;
    }

    auto freePlans = [&]() {
        for (auto& p : plans) { SafeRelease(&p.layout); SafeRelease(&p.header); }
    };

    if (!EnsureSurface(win, TOAST_W, total)) {
        freePlans();
        return;
    }

    ID2D1DCRenderTarget* rt = win.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    ID2D1SolidColorBrush* text = nullptr;
    rt->CreateSolidColorBrush(Txt(1.0f), &text);

    float y = 0;
    for (size_t i = 0; i < toasts.size(); ++i) {
        Toast& t = toasts[i];
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

    if (g_editMode) {
        DrawEditDecoration(rt, TOAST_W, total);
        if (IsGamepadSelected(win)) DrawSelectionRing(rt, TOAST_W, total);
    }

    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) {
        DiscardSurface(win);
        return;
    }
    CommitWindow(win, anchor);
}

// The two toast areas. Replies/reminders/images stack top-right; memory
// save/remove toasts get their own draggable area (bottom-right by default).
void RelayoutToasts() {
    RenderToastList(g_toasts, g_toastWin, AnchorTopRight,
                    L"Replies & reminders appear here", g_showToasts);
}
void RelayoutMemories() {
    RenderToastList(g_memToasts, g_memWin, AnchorBottomRight,
                    L"Memory saves appear here", g_showMemories);
}

// durationMs = 0 uses the default TOAST_MS; a positive value (e.g. sent by the frontend to match
// how long a sentence is narrated, at the current TTS speed) overrides it so the toast stays up
// exactly as long as it's being spoken.
void AddToast(const std::wstring& textStr, const std::wstring& kind, ULONGLONG durationMs = 0) {
    Toast t;
    t.text = textStr;
    t.kind = kind.empty() ? L"reply" : kind;
    t.expire = GetTickCount64() + (durationMs > 0 ? durationMs : TOAST_MS);
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
    g_memToasts.push_back(std::move(t));
    RelayoutMemories();
}

// Drop expired toasts from a list; returns true if it changed.
bool ExpireList(std::vector<Toast>& toasts) {
    ULONGLONG now = GetTickCount64();
    size_t before = toasts.size();
    for (size_t i = 0; i < toasts.size();) {
        if (toasts[i].expire <= now) toasts.erase(toasts.begin() + i);
        else ++i;
    }
    return toasts.size() != before;
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
    if (!g_showPanel) { HideWindow(g_panelWin); return; }
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

    if (g_editMode) {
        DrawEditDecoration(rt, PANEL_W, height);
        if (IsGamepadSelected(g_panelWin)) DrawSelectionRing(rt, PANEL_W, height);
    }

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
// Hands-free (live-mic) indicator: a small circular badge with a mic glyph and
// a pulsing accent glow while listening.
// ---------------------------------------------------------------------------

constexpr int HANDSFREE_W = 44;

void RenderHandsFree() {
    if (!g_showHandsfree) { HideWindow(g_handsfreeWin); return; }
    if (!g_handsfreeActive && !g_editMode) { HideWindow(g_handsfreeWin); return; }
    const int w = HANDSFREE_W, h = HANDSFREE_W;
    if (!EnsureSurface(g_handsfreeWin, w, h)) return;

    ID2D1DCRenderTarget* rt = g_handsfreeWin.rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));

    D2D1_ELLIPSE circle = D2D1::Ellipse(
        D2D1::Point2F(w / 2.0f, h / 2.0f), w / 2.0f - 1.0f, h / 2.0f - 1.0f);

    ID2D1SolidColorBrush* bg = nullptr;
    rt->CreateSolidColorBrush(Bg(0.90f), &bg);
    rt->FillEllipse(circle, bg);

    // Pulsing accent fill — breathes only while active.
    float phase = (GetTickCount64() - g_handsfreeStart) / 1000.0f;
    float pulse = g_handsfreeActive ? 0.18f + 0.24f * (0.5f + 0.5f * sinf(phase * 3.0f)) : 0.15f;
    ID2D1SolidColorBrush* glow = nullptr;
    rt->CreateSolidColorBrush(Acc(pulse), &glow);
    rt->FillEllipse(circle, glow);

    ID2D1SolidColorBrush* border = nullptr, *white = nullptr;
    rt->CreateSolidColorBrush(Acc(0.75f), &border);
    rt->CreateSolidColorBrush(Txt(1.0f), &white);
    rt->DrawEllipse(circle, border, 1.4f);

    DrawEmoji(rt, L"\U0001F3A4",
              D2D1::RectF((w - 22) / 2.0f, (h - 22) / 2.0f,
                          (w + 22) / 2.0f, (h + 22) / 2.0f), white);

    SafeRelease(&white); SafeRelease(&border); SafeRelease(&glow); SafeRelease(&bg);
    if (g_editMode) {
        DrawEditDecoration(rt, w, h);
        if (IsGamepadSelected(g_handsfreeWin)) DrawSelectionRing(rt, w, h);
    }
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

// The "unsaved changes" prompt, drawn INSTEAD of the normal toolbar contents
// while g_showExitConfirm is true (see RequestExit()).
void RenderExitConfirm(ID2D1RenderTarget* rt, ID2D1SolidColorBrush* bg, ID2D1SolidColorBrush* white,
                       ID2D1SolidColorBrush* dim, ID2D1SolidColorBrush* acc) {
    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, CONFIG_W - 1.0f, CONFIG_H - 1.0f), 12.0f, 12.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawRoundedRectangle(card, acc, 1.4f);

    rt->DrawText(L"Unsaved changes", 15, g_fmtUi,
                 D2D1::RectF(PAD, 16, CONFIG_W - PAD, 40), white);
    const wchar_t* sub = L"Save them to the active preset, discard, or keep editing?";
    IDWriteTextLayout* subLayout = MakeLayout(sub, g_fmtLabel, CONFIG_W - 2 * PAD, nullptr);
    if (subLayout) {
        rt->DrawTextLayout(D2D1::Point2F(PAD, 44), subLayout, dim);
        SafeRelease(&subLayout);
    }

    const float bw = CONFIG_W - 2 * PAD, bh = 34, gap = 10;
    float y = 100;
    g_rcConfirmSave = D2D1::RectF(PAD, y, PAD + bw, y + bh);
    y += bh + gap;
    g_rcConfirmDiscard = D2D1::RectF(PAD, y, PAD + bw, y + bh);
    y += bh + gap;
    g_rcConfirmKeep = D2D1::RectF(PAD, y, PAD + bw, y + bh);

    auto drawBtn = [&](const D2D1_RECT_F& r, const wchar_t* label, bool filled) {
        D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(r, 8, 8);
        if (filled) rt->FillRoundedRectangle(rr, acc);
        else        rt->DrawRoundedRectangle(rr, dim, 1.2f);
        rt->DrawText(label, (UINT32)wcslen(label), g_fmtCenter, r, white);
    };
    drawBtn(g_rcConfirmSave, L"Save & Exit  (A)", true);
    drawBtn(g_rcConfirmDiscard, L"Discard & Exit  (X)", false);
    drawBtn(g_rcConfirmKeep, L"Keep Editing  (B)", false);
}

// One preset "chip" row (click/LB/RB switch, +/×/pencil new/delete/rename) plus
// the Save / Discard & Close action row underneath.
void RenderPresetRow(ID2D1RenderTarget* rt, ID2D1SolidColorBrush* white,
                     ID2D1SolidColorBrush* dim, ID2D1SolidColorBrush* acc, float y) {
    const float bh = 24, bw = 26;
    rt->DrawText(L"Presets", 7, g_fmtUi, D2D1::RectF(PAD, y, 120, y + bh), white);

    g_rcPresetRename = D2D1::RectF(CONFIG_W - PAD - bw, y, CONFIG_W - PAD, y + bh);
    g_rcPresetDelete = D2D1::RectF(g_rcPresetRename.left - 4 - bw, y, g_rcPresetRename.left - 4, y + bh);
    g_rcPresetNew    = D2D1::RectF(g_rcPresetDelete.left - 4 - bw, y, g_rcPresetDelete.left - 4, y + bh);
    for (auto* r : {&g_rcPresetNew, &g_rcPresetDelete, &g_rcPresetRename}) {
        D2D1_ROUNDED_RECT br = D2D1::RoundedRect(*r, 6, 6);
        rt->DrawRoundedRectangle(br, dim, 1.2f);
    }
    rt->DrawText(L"+", 1, g_fmtCenter, g_rcPresetNew, white);
    rt->DrawText(L"\x00D7", 1, g_fmtCenter, g_rcPresetDelete, white);   // ×
    rt->DrawText(L"\x270E", 1, g_fmtCenter, g_rcPresetRename, white);  // pencil

    float chipY = y + bh + 8.0f, chipH = 28.0f, chipX = PAD;
    g_rcPresetChips.assign(g_presets.size(), D2D1_RECT_F{});
    for (size_t i = 0; i < g_presets.size(); ++i) {
        bool renaming = g_renamingPreset && (int)i == g_renameIdx;
        const std::wstring& shown = renaming ? g_renameBuffer : g_presets[i].name;
        float chipW = 24.0f + (float)shown.size() * 7.0f;
        if (chipW < 50.0f) chipW = 50.0f;
        if (chipW > 140.0f) chipW = 140.0f;
        D2D1_RECT_F rc = D2D1::RectF(chipX, chipY, chipX + chipW, chipY + chipH);
        g_rcPresetChips[i] = rc;
        D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(rc, 8, 8);
        bool active = (int)i == g_activePreset;
        if (active) rt->FillRoundedRectangle(rr, acc);
        else        rt->DrawRoundedRectangle(rr, dim, 1.2f);
        if (renaming) rt->DrawRoundedRectangle(rr, white, 1.6f);  // edit-mode highlight
        std::wstring label = renaming ? shown + L"_" : shown;
        rt->DrawText(label.c_str(), (UINT32)label.size(), g_fmtUi, rc, active ? white : dim);
        chipX += chipW + 6.0f;
    }
}

void RenderActionRow(ID2D1RenderTarget* rt, ID2D1SolidColorBrush* white,
                     ID2D1SolidColorBrush* dim, ID2D1SolidColorBrush* acc, float y) {
    const float bw = (CONFIG_W - 2 * PAD - 10.0f) / 2.0f, bh = 30;
    g_rcSave = D2D1::RectF(PAD, y, PAD + bw, y + bh);
    g_rcDiscardClose = D2D1::RectF(PAD + bw + 10.0f, y, CONFIG_W - PAD, y + bh);

    D2D1_ROUNDED_RECT saveR = D2D1::RoundedRect(g_rcSave, 8, 8);
    rt->FillRoundedRectangle(saveR, acc);
    const wchar_t* saveLabel = g_lastModality == ModalityGamepad ? L"Save  (A)" : L"Save";
    rt->DrawText(saveLabel, (UINT32)wcslen(saveLabel), g_fmtCenter, g_rcSave, white);

    D2D1_ROUNDED_RECT discR = D2D1::RoundedRect(g_rcDiscardClose, 8, 8);
    rt->DrawRoundedRectangle(discR, dim, 1.2f);
    const wchar_t* discLabel = g_lastModality == ModalityGamepad ? L"Discard & Close  (B)" : L"Discard & Close";
    rt->DrawText(discLabel, (UINT32)wcslen(discLabel), g_fmtCenter, g_rcDiscardClose, white);
}

void RenderConfig() {
    const int h = CONFIG_H;
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

    if (g_showExitConfirm) {
        RenderExitConfirm(rt, bg, white, dim, acc);
        SafeRelease(&acc); SafeRelease(&dim); SafeRelease(&white); SafeRelease(&bg);
        if (rt->EndDraw() == D2DERR_RECREATE_TARGET) { DiscardSurface(g_configWin); return; }
        CommitWindow(g_configWin, AnchorTopCenter);
        return;
    }

    D2D1_ROUNDED_RECT card = D2D1::RoundedRect(
        D2D1::RectF(1.0f, 1.0f, CONFIG_W - 1.0f, h - 1.0f), 12.0f, 12.0f);
    rt->FillRoundedRectangle(card, bg);
    rt->DrawRoundedRectangle(card, acc, 1.4f);

    // Title.
    rt->DrawText(L"Overlay appearance", 18, g_fmtUi,
                 D2D1::RectF(PAD, 8, CONFIG_W - PAD, 28), dim);

    const float bh = 24, bw = 26;
    // A right-aligned [-] [value] [+] stepper at row `by`; fills the passed rects.
    auto stepper = [&](float by, const wchar_t* value,
                       D2D1_RECT_F& minus, D2D1_RECT_F& plus) {
        plus = D2D1::RectF(CONFIG_W - PAD - bw, by, CONFIG_W - PAD, by + bh);
        D2D1_RECT_F val = D2D1::RectF(plus.left - 4 - 56, by, plus.left - 4, by + bh);
        minus = D2D1::RectF(val.left - 4 - bw, by, val.left - 4, by + bh);
        for (auto* r : {&minus, &plus}) {
            D2D1_ROUNDED_RECT br = D2D1::RoundedRect(*r, 6, 6);
            rt->DrawRoundedRectangle(br, dim, 1.2f);
        }
        rt->DrawText(L"\x2212", 1, g_fmtCenter, minus, white);  // minus sign
        rt->DrawText(L"+", 1, g_fmtCenter, plus, white);
        rt->DrawText(value, (UINT32)wcslen(value), g_fmtCenter, val, white);
    };

    // --- Opacity row.
    rt->DrawText(L"Opacity", 7, g_fmtUi, D2D1::RectF(PAD, 34, 120, 58), white);
    wchar_t opacText[8];
    swprintf(opacText, 8, L"%d%%", (int)(g_opacity * 100 + 0.5f));
    stepper(34, opacText, g_rcOpacMinus, g_rcOpacPlus);

    // --- Text size row.
    rt->DrawText(L"Text size", 9, g_fmtUi, D2D1::RectF(PAD, 64, 120, 88), white);
    wchar_t sizeText[8];
    swprintf(sizeText, 8, L"%d%%", (int)(g_fontScale * 100 + 0.5f));
    stepper(64, sizeText, g_rcTextMinus, g_rcTextPlus);

    // --- Font row: [<] name [>].
    rt->DrawText(L"Font", 4, g_fmtUi, D2D1::RectF(PAD, 94, 120, 118), white);
    const float fy = 94;
    g_rcFontNext = D2D1::RectF(CONFIG_W - PAD - bw, fy, CONFIG_W - PAD, fy + bh);
    D2D1_RECT_F nameRect = D2D1::RectF(g_rcFontNext.left - 4 - 120, fy,
                                       g_rcFontNext.left - 4, fy + bh);
    g_rcFontPrev = D2D1::RectF(nameRect.left - 4 - bw, fy, nameRect.left - 4, fy + bh);
    for (auto* r : {&g_rcFontPrev, &g_rcFontNext}) {
        D2D1_ROUNDED_RECT br = D2D1::RoundedRect(*r, 6, 6);
        rt->DrawRoundedRectangle(br, dim, 1.2f);
    }
    rt->DrawText(L"\x2039", 1, g_fmtCenter, g_rcFontPrev, white);  // ‹
    rt->DrawText(L"\x203A", 1, g_fmtCenter, g_rcFontNext, white);  // ›
    rt->DrawText(kFonts[g_fontIdx], (UINT32)wcslen(kFonts[g_fontIdx]),
                 g_fmtCenter, nameRect, white);

    // --- Accent row: swatches, selected one ringed.
    const float ay = 126, sw = 22, sgap = 6;
    rt->DrawText(L"Accent", 6, g_fmtUi, D2D1::RectF(PAD, ay, 90, ay + sw), white);
    float sx = CONFIG_W - PAD - (kAccentCount * (sw + sgap) - sgap);
    for (int i = 0; i < kAccentCount; ++i) {
        g_rcSwatch[i] = D2D1::RectF(sx, ay, sx + sw, ay + sw);
        ID2D1SolidColorBrush* sb = nullptr;
        rt->CreateSolidColorBrush(
            D2D1::ColorF(kAccents[i].r, kAccents[i].g, kAccents[i].b, 1.0f), &sb);
        D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(g_rcSwatch[i], 6, 6);
        rt->FillRoundedRectangle(rr, sb);
        if (i == g_accentIdx) {
            D2D1_ROUNDED_RECT ring = D2D1::RoundedRect(
                D2D1::RectF(sx - 2, ay - 2, sx + sw + 2, ay + sw + 2), 8, 8);
            rt->DrawRoundedRectangle(ring, white, 1.8f);
        }
        SafeRelease(&sb);
        sx += sw + sgap;
    }

    // --- Show toggles: one chip per area, filled (accent) when visible.
    rt->DrawText(L"Show", 4, g_fmtUi, D2D1::RectF(PAD, 158, 90, 178), white);
    const wchar_t* labels[4] = {L"Chat", L"Memory", L"Panel", L"Mic"};
    bool* flags[4] = {&g_showToasts, &g_showMemories, &g_showPanel, &g_showHandsfree};
    const float chy = 178, chh = 26, chgap = 6;
    const float innerW = CONFIG_W - 2 * PAD;
    const float chw = (innerW - 3 * chgap) / 4.0f;
    for (int i = 0; i < 4; ++i) {
        float cx = PAD + i * (chw + chgap);
        g_rcToggle[i] = D2D1::RectF(cx, chy, cx + chw, chy + chh);
        D2D1_ROUNDED_RECT rr = D2D1::RoundedRect(g_rcToggle[i], 7, 7);
        if (*flags[i]) rt->FillRoundedRectangle(rr, acc);
        else           rt->DrawRoundedRectangle(rr, dim, 1.2f);
        rt->DrawText(labels[i], (UINT32)wcslen(labels[i]), g_fmtUi,
                     D2D1::RectF(cx, chy + 4, cx + chw, chy + chh),
                     *flags[i] ? white : dim);
    }

    RenderPresetRow(rt, white, dim, acc, 214.0f);
    RenderActionRow(rt, white, dim, acc, 284.0f);

    SafeRelease(&acc); SafeRelease(&dim); SafeRelease(&white); SafeRelease(&bg);
    if (rt->EndDraw() == D2DERR_RECREATE_TARGET) { DiscardSurface(g_configWin); return; }
    CommitWindow(g_configWin, AnchorTopCenter);
}

// Re-render everything whose look depends on the toolbar (accent/opacity/toggles).
void RerenderAll() {
    RelayoutToasts();
    RelayoutMemories();
    RenderPanel();
    RenderHandsFree();
    RenderConfig();
}

void ApplyEditMode(bool on);  // forward decl — RequestExit/click handlers below call it

// Commit the current live state to the active preset and persist it. Stays in
// edit mode ("A button will save to the selected preset").
void SavePreset() {
    if (g_activePreset >= 0 && g_activePreset < (int)g_presets.size())
        g_presets[g_activePreset] = CaptureSnapshot();
    SaveLayout();
    g_dirty = false;
}

// Revert to how things looked when edit mode was entered, then close. No
// confirmation — pressing this button/gamepad-B already declares the intent.
void DiscardAndClose() {
    ApplySnapshot(g_editSnapshot);
    g_showExitConfirm = false;
    ApplyEditMode(false);
}

// Switch the active preset (LB/RB, chip click) — a preview/select action, not
// an implicit save.
void SwitchPreset(int idx) {
    if (g_presets.empty()) return;
    idx = ((idx % (int)g_presets.size()) + (int)g_presets.size()) % (int)g_presets.size();
    g_activePreset = idx;
    ApplySnapshot(g_presets[idx]);
    g_dirty = true;
}

// Create a new preset cloned from the current live layout, auto-named.
void CreatePreset() {
    LayoutPreset p = CaptureSnapshot();
    p.name = L"Preset " + std::to_wstring(g_presets.size() + 1);
    g_presets.push_back(p);
    g_activePreset = (int)g_presets.size() - 1;
    g_dirty = true;
    RenderConfig();
}

// Delete the active preset (refuses if it's the only one left).
void DeletePreset() {
    if (g_presets.size() <= 1) return;
    g_presets.erase(g_presets.begin() + g_activePreset);
    if (g_activePreset >= (int)g_presets.size()) g_activePreset = (int)g_presets.size() - 1;
    ApplySnapshot(g_presets[g_activePreset]);
    g_dirty = true;
    RenderConfig();
}

// Enter in-place rename for the active preset (mouse-triggered, keyboard-typed
// — see PollRenameKeys(); no gamepad path drives this).
void BeginRenamePreset() {
    if (g_activePreset < 0 || g_activePreset >= (int)g_presets.size()) return;
    g_renamingPreset = true;
    g_renameIdx = g_activePreset;
    g_renameBuffer = g_presets[g_activePreset].name;
    RenderConfig();
}

void CommitRenamePreset() {
    if (g_renamingPreset && g_renameIdx >= 0 && g_renameIdx < (int)g_presets.size() &&
        !g_renameBuffer.empty())
        g_presets[g_renameIdx].name = g_renameBuffer;
    g_renamingPreset = false;
    g_renameIdx = -1;
    SaveLayout();
    RenderConfig();
}

void CancelRenamePreset() {
    g_renamingPreset = false;
    g_renameIdx = -1;
    RenderConfig();
}

// Handle a click inside the config toolbar; returns true if something changed.
bool ConfigClick(int x, int y) {
    g_lastModality = ModalityMouse;

    if (g_showExitConfirm) {
        if (InRect(g_rcConfirmSave, x, y)) { SavePreset(); ApplyEditMode(false); g_showExitConfirm = false; }
        else if (InRect(g_rcConfirmDiscard, x, y)) { DiscardAndClose(); }
        else if (InRect(g_rcConfirmKeep, x, y)) { g_showExitConfirm = false; RenderConfig(); }
        return true;
    }

    // Clicking anywhere in the toolbar while renaming commits the buffer first
    // (so the user isn't stuck if they click away instead of pressing Enter).
    if (g_renamingPreset) CommitRenamePreset();

    bool changed = false;
    bool rebuildText = false;
    if (InRect(g_rcOpacMinus, x, y)) {
        g_opacity = (g_opacity - 0.05f < 0.40f) ? 0.40f : g_opacity - 0.05f;
        changed = true;
    } else if (InRect(g_rcOpacPlus, x, y)) {
        g_opacity = (g_opacity + 0.05f > 1.00f) ? 1.00f : g_opacity + 0.05f;
        changed = true;
    } else if (InRect(g_rcTextMinus, x, y)) {
        g_fontScale = (g_fontScale - 0.10f < 0.70f) ? 0.70f : g_fontScale - 0.10f;
        changed = rebuildText = true;
    } else if (InRect(g_rcTextPlus, x, y)) {
        g_fontScale = (g_fontScale + 0.10f > 1.60f) ? 1.60f : g_fontScale + 0.10f;
        changed = rebuildText = true;
    } else if (InRect(g_rcFontPrev, x, y)) {
        g_fontIdx = (g_fontIdx + kFontCount - 1) % kFontCount;
        changed = rebuildText = true;
    } else if (InRect(g_rcFontNext, x, y)) {
        g_fontIdx = (g_fontIdx + 1) % kFontCount;
        changed = rebuildText = true;
    } else if (InRect(g_rcSave, x, y)) {
        SavePreset();
        RenderConfig();
        return true;
    } else if (InRect(g_rcDiscardClose, x, y)) {
        DiscardAndClose();
        return true;
    } else if (InRect(g_rcPresetNew, x, y)) {
        CreatePreset();
        return true;
    } else if (InRect(g_rcPresetDelete, x, y)) {
        DeletePreset();
        return true;
    } else if (InRect(g_rcPresetRename, x, y)) {
        BeginRenamePreset();
        return true;
    } else {
        for (int i = 0; i < kAccentCount; ++i)
            if (InRect(g_rcSwatch[i], x, y)) { g_accentIdx = i; changed = true; break; }
        bool* flags[4] = {&g_showToasts, &g_showMemories, &g_showPanel, &g_showHandsfree};
        for (int i = 0; !changed && i < 4; ++i)
            if (InRect(g_rcToggle[i], x, y)) { *flags[i] = !*flags[i]; changed = true; break; }
        for (size_t i = 0; !changed && i < g_rcPresetChips.size(); ++i)
            if (InRect(g_rcPresetChips[i], x, y)) { SwitchPreset((int)i); changed = true; break; }
    }
    if (changed) {
        g_dirty = true;
        if (rebuildText) RebuildTextFormats();
        RerenderAll();
    }
    return changed;
}

// A hotkey press is an ambiguous toggle (the same key opens and closes), so it
// never silently picks save-or-discard: if nothing changed this session it
// just exits; otherwise it opens the Save & Exit / Discard & Exit / Keep
// Editing prompt. Explicit button/gamepad presses (Save, Discard & Close)
// already declare their own intent and act immediately (see ConfigClick /
// PollGamepad) without going through this.
void RequestExit() {
    if (!g_editMode) { ApplyEditMode(true); return; }
    if (g_showExitConfirm) return;  // already showing the prompt
    if (!g_dirty) { ApplyEditMode(false); return; }
    g_showExitConfirm = true;
    RenderConfig();
}

// Toggle edit mode: lift/restore click-through, show/hide the appearance
// toolbar, re-render every widget. Persistence now only happens via an
// explicit Save (or Save & Exit) — see RequestExit()/SavePreset().
void ApplyEditMode(bool on) {
    g_editMode = on;
    SetClickThrough(g_toastWin, !on);
    SetClickThrough(g_memWin, !on);
    SetClickThrough(g_panelWin, !on);
    SetClickThrough(g_configWin, !on);
    SetClickThrough(g_handsfreeWin, !on);
    if (on) {
        g_editSnapshot = CaptureSnapshot();
        g_dirty = false;
        g_showExitConfirm = false;
        g_renamingPreset = false;
        g_selectedWidgetIdx = 0;
        g_prevButtons = 0;
        SetTimer(g_ctrl, TIMER_GAMEPAD, 33, nullptr);
    } else {
        KillTimer(g_ctrl, TIMER_GAMEPAD);
        g_showExitConfirm = false;
        g_renamingPreset = false;
    }
    RelayoutToasts();
    RelayoutMemories();
    RenderPanel();
    RenderHandsFree();  // shows a positionable placeholder in edit mode
    if (on) RenderConfig();
    else    HideWindow(g_configWin);
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
    std::wstring sub = L"Press " + FormatHotkeyCombo() + L" to move widgets & customize appearance";
    IDWriteTextLayout* subLayout = nullptr;
    if (SUCCEEDED(g_dwriteFactory->CreateTextLayout(
            sub.c_str(), (UINT32)sub.size(), g_fmtUi, w - 2 * PAD, 26.0f, &subLayout)) &&
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
        const JsonValue* dur = v.find(L"duration_ms");
        ULONGLONG durationMs = (dur && dur->type == JsonValue::Num && dur->num > 0)
                                   ? (ULONGLONG)dur->num : 0;
        if (text && text->type == JsonValue::Str && !text->str.empty())
            AddToast(text->str, kind ? kind->asStr() : L"reply", durationMs);
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
        // Same ambiguous-toggle logic as the physical hotkey when re-enabling
        // (the pipe caller may not know whether edit mode is already open).
        bool wantOn = en ? en->asBool() : false;
        if (wantOn && !g_editMode) ApplyEditMode(true);
        else if (!wantOn && g_editMode) RequestExit();
    } else if (type == L"set_hotkey") {
        const JsonValue* mods = v.find(L"mods");
        const JsonValue* key = v.find(L"key");
        if (mods && mods->type == JsonValue::Arr) {
            g_hotkeyMods.clear();
            for (auto& m : mods->arr) if (m.type == JsonValue::Str) g_hotkeyMods.push_back(m.str);
            if (g_hotkeyMods.empty()) g_hotkeyMods = {L"ctrl", L"shift"};
        }
        if (key && key->type == JsonValue::Str && !key->str.empty()) g_hotkeyKey = key->str[0];
        ReregisterEditHotkey();
        SaveLayout();
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
// Rename-mode keyboard polling (keyboard-only — no gamepad path drives this).
// The overlay's layered windows are WS_EX_NOACTIVATE and never take keyboard
// focus (the game does), so instead of WM_CHAR we poll GetAsyncKeyState for a
// small character set, the same "global input" approach RegisterHotKey already
// relies on to work while a game has focus.
// ---------------------------------------------------------------------------

void PollRenameKeys() {
    static bool prevDown[256] = {};
    bool nowDown[256] = {};

    BYTE kbState[256] = {};
    GetKeyboardState(kbState);

    auto edge = [&](int vk) {
        nowDown[vk] = (GetAsyncKeyState(vk) & 0x8000) != 0;
        bool e = nowDown[vk] && !prevDown[vk];
        return e;
    };

    if (edge(VK_RETURN)) { CommitRenamePreset(); memcpy(prevDown, nowDown, sizeof(nowDown)); return; }
    if (edge(VK_ESCAPE)) { CancelRenamePreset(); memcpy(prevDown, nowDown, sizeof(nowDown)); return; }
    if (edge(VK_BACK)) {
        if (!g_renameBuffer.empty()) g_renameBuffer.pop_back();
        RenderConfig();
    }
    for (int vk = 0x30; vk <= 0x39; ++vk) {  // digits
        if (edge(vk) && g_renameBuffer.size() < kRenameMaxLen) {
            wchar_t buf[4] = {};
            UINT sc = MapVirtualKey(vk, MAPVK_VK_TO_VSC);
            if (ToUnicode(vk, sc, kbState, buf, 4, 0) == 1) g_renameBuffer.push_back(buf[0]);
            RenderConfig();
        }
    }
    for (int vk = 0x41; vk <= 0x5A; ++vk) {  // letters
        if (edge(vk) && g_renameBuffer.size() < kRenameMaxLen) {
            wchar_t buf[4] = {};
            UINT sc = MapVirtualKey(vk, MAPVK_VK_TO_VSC);
            if (ToUnicode(vk, sc, kbState, buf, 4, 0) == 1) g_renameBuffer.push_back(buf[0]);
            RenderConfig();
        }
    }
    if (edge(VK_SPACE) && g_renameBuffer.size() < kRenameMaxLen) {
        g_renameBuffer.push_back(L' ');
        RenderConfig();
    }

    memcpy(prevDown, nowDown, sizeof(nowDown));
}

// ---------------------------------------------------------------------------
// XInput gamepad polling — runs only while edit mode is open (TIMER_GAMEPAD,
// started/killed in ApplyEditMode). No WM_INPUT/XInput message exists, so
// button "presses" are detected as edges against the previous poll's state.
// ---------------------------------------------------------------------------

void CycleSelectedWidget(int dir) {
    for (int step = 0; step < 4; ++step) {
        g_selectedWidgetIdx = ((g_selectedWidgetIdx + dir) % 4 + 4) % 4;
        LayeredWindow* w = g_widgets[g_selectedWidgetIdx];
        bool* flags[4] = {&g_showToasts, &g_showMemories, &g_showPanel, &g_showHandsfree};
        if (w && *flags[g_selectedWidgetIdx]) break;  // skip hidden widgets
    }
    RerenderAll();
}

void PollGamepad() {
    XINPUT_STATE state = {};
    if (XInputGetState(0, &state) != ERROR_SUCCESS) return;  // no controller connected

    const XINPUT_GAMEPAD& gp = state.Gamepad;
    WORD buttons = gp.wButtons;
    auto pressed = [&](WORD mask) { return (buttons & mask) && !(g_prevButtons & mask); };

    bool anyActivity = (buttons != 0) ||
        (abs(gp.sThumbLX) > XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE) ||
        (abs(gp.sThumbLY) > XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE) ||
        (abs(gp.sThumbRX) > XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE) ||
        (abs(gp.sThumbRY) > XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE);
    if (anyActivity) g_lastModality = ModalityGamepad;

    if (g_showExitConfirm) {
        if (pressed(XINPUT_GAMEPAD_A)) { SavePreset(); ApplyEditMode(false); g_showExitConfirm = false; }
        else if (pressed(XINPUT_GAMEPAD_X)) { DiscardAndClose(); }
        else if (pressed(XINPUT_GAMEPAD_B)) { g_showExitConfirm = false; RenderConfig(); }
        g_prevButtons = buttons;
        return;
    }

    if (g_renamingPreset) {
        PollRenameKeys();
        g_prevButtons = buttons;
        return;  // no gamepad action while renaming — keyboard/mouse only
    }

    if (pressed(XINPUT_GAMEPAD_A)) { SavePreset(); RenderConfig(); }
    else if (pressed(XINPUT_GAMEPAD_B)) { DiscardAndClose(); }
    else if (pressed(XINPUT_GAMEPAD_X)) { DeletePreset(); }
    else if (pressed(XINPUT_GAMEPAD_Y)) { CreatePreset(); }
    else if (pressed(XINPUT_GAMEPAD_LEFT_SHOULDER)) { SwitchPreset(g_activePreset - 1); RenderConfig(); }
    else if (pressed(XINPUT_GAMEPAD_RIGHT_SHOULDER)) { SwitchPreset(g_activePreset + 1); RenderConfig(); }

    // D-pad / left-stick widget cycling — debounced (stick deflection isn't a
    // discrete press the way a button is).
    ULONGLONG now = GetTickCount64();
    bool wantCycle = (buttons & (XINPUT_GAMEPAD_DPAD_UP | XINPUT_GAMEPAD_DPAD_DOWN |
                                 XINPUT_GAMEPAD_DPAD_LEFT | XINPUT_GAMEPAD_DPAD_RIGHT)) ||
                     abs(gp.sThumbLX) > XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE ||
                     abs(gp.sThumbLY) > XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE;
    if (wantCycle && now - g_lastCycleTick > CYCLE_DEBOUNCE_MS) {
        int dir = (buttons & (XINPUT_GAMEPAD_DPAD_DOWN)) || gp.sThumbLY < -XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE
                      ? 1 : -1;
        CycleSelectedWidget(dir);
        g_lastCycleTick = now;
    }

    // Right stick moves the selected widget directly (bypasses the OS drag
    // path entirely — there's no window being dragged, just a position delta).
    if (abs(gp.sThumbRX) > XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE ||
        abs(gp.sThumbRY) > XINPUT_GAMEPAD_RIGHT_THUMB_DEADZONE) {
        LayeredWindow* w = g_widgets[g_selectedWidgetIdx];
        if (w) {
            float nx = gp.sThumbRX / 32767.0f, ny = gp.sThumbRY / 32767.0f;
            const float dt = 0.033f;  // ~33ms tick
            w->posX += (int)(nx * GAMEPAD_MOVE_SPEED * dt);
            w->posY += (int)(-ny * GAMEPAD_MOVE_SPEED * dt);  // XInput's Y+ is up
            w->hasPos = true;
            g_dirty = true;
            RerenderAll();
        }
    }

    g_prevButtons = buttons;
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
            if (wParam == HOTKEY_EDIT) RequestExit();
            return 0;
        case WM_TIMER:
            if (wParam == TIMER_TICK) {
                // Don't let toasts expire out from under you while arranging.
                if (!g_editMode) {
                    if (ExpireList(g_toasts)) RelayoutToasts();
                    if (ExpireList(g_memToasts)) RelayoutMemories();
                }
            } else if (wParam == TIMER_BANNER) {
                if (!TickBanner()) KillTimer(g_ctrl, TIMER_BANNER);
            } else if (wParam == TIMER_HANDSFREE) {
                RenderHandsFree();  // animate the gradient sweep
            } else if (wParam == TIMER_GAMEPAD) {
                PollGamepad();
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
    if (h == g_memWin.hwnd) return &g_memWin;
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
            if (g_editMode) g_lastModality = ModalityMouse;
            return g_editMode ? HTCAPTION : HTTRANSPARENT;
        case WM_MOVE: {
            // Keep the stored position in sync so later re-renders (new toast,
            // etc.) commit at the dragged spot instead of snapping back.
            LayeredWindow* lw = FromHwnd(hwnd);
            if (lw) {
                lw->posX = (int)(short)LOWORD(lParam);
                lw->posY = (int)(short)HIWORD(lParam);
                lw->hasPos = true;
                if (g_editMode) g_dirty = true;
            }
            return 0;
        }
        case WM_EXITSIZEMOVE:
            // Persistence now only happens via an explicit Save — dragging just
            // marks the session dirty (see WM_MOVE above).
            return 0;
        default:
            return DefWindowProc(hwnd, msg, wParam, lParam);
    }
}

HWND CreateLayeredHwnd(HINSTANCE hInst) {
    DWORD exStyle = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
                    WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
    HWND hwnd = CreateWindowEx(exStyle, kWinClass, L"", WS_POPUP,
                               0, 0, TOAST_W, 64, nullptr, nullptr, hInst, nullptr);
    // Exclude every overlay window from screen capture so the game-state OCR poller (which
    // captures the whole monitor via Windows Graphics Capture) reads only the game, never the
    // overlay's own panel/toasts — otherwise the companion would OCR its own output back in.
    // WDA_EXCLUDEFROMCAPTURE (0x11) needs Win10 2004+; the call no-ops harmlessly on older builds.
    if (hwnd) SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE);
    return hwnd;
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
    g_memWin.hwnd       = CreateLayeredHwnd(hInstance);
    g_panelWin.hwnd     = CreateLayeredHwnd(hInstance);
    g_configWin.hwnd    = CreateLayeredHwnd(hInstance);
    g_bannerWin.hwnd    = CreateLayeredHwnd(hInstance);
    g_handsfreeWin.hwnd = CreateLayeredHwnd(hInstance);
    if (!g_toastWin.hwnd || !g_memWin.hwnd || !g_panelWin.hwnd || !g_configWin.hwnd ||
        !g_bannerWin.hwnd || !g_handsfreeWin.hwnd)
        return 3;

    g_widgets[0] = &g_toastWin;
    g_widgets[1] = &g_memWin;
    g_widgets[2] = &g_panelWin;
    g_widgets[3] = &g_handsfreeWin;

    LoadLogo();    // reply-toast avatar (logo.png next to the exe)
    LoadLayout();  // restore saved presets + appearance (also applies the font/scale/hotkey)

    // Toggles edit mode; combo is user-configurable (see RegisterEditHotkey).
    // Registered on the controller window so its message loop (already
    // running) delivers WM_HOTKEY.
    RegisterEditHotkey();

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
    DiscardSurface(g_memWin);
    DiscardSurface(g_panelWin);
    DiscardSurface(g_configWin);
    DiscardSurface(g_bannerWin);
    DiscardSurface(g_handsfreeWin);
    if (g_parentProcess) CloseHandle(g_parentProcess);
    CoUninitialize();
    SafeRelease(&g_dashStroke);
    SafeRelease(&g_fmtUi);
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
