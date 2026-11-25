"""全局磨砂（Windows 亚克力/毛玻璃）。

做法：窗口本身设为透明背景，Win32 SetWindowCompositionAttribute 打开
ACCENT_ENABLE_ACRYLICBLURBEHIND，让桌面/壁纸透过窗口被高斯模糊。
网页端只需把大面积背景改成半透明，就能看到磨砂效果。
"""
from __future__ import annotations

import ctypes
import threading
import time as _time
from ctypes import wintypes

_LOCK = threading.Lock()
_HWND = None
_ENABLED = False
_READY = False
_UI_THREAD = None
_LIGHT = False

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_int),
        ("AnimationId", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _handle_of(window):
    """取窗口句柄，兼容 pywebview 不同平台的 native 结构（只能在 GUI 线程调用）。"""
    try:
        native = getattr(window, "native", None)
        if native is None:
            return None
        candidates = [getattr(native, "Handle", None)]
        browser = getattr(native, "browser", None)
        if browser is not None:
            candidates.append(getattr(browser, "Handle", None))
        for handle in candidates:
            if handle is None:
                continue
            try:
                return int(handle.ToInt64())
            except AttributeError:
                try:
                    return int(handle)
                except (TypeError, ValueError):
                    continue
    except Exception:
        return None
    return None


def _make_webview_transparent(window):
    """让 WebView2 的默认背景透明，窗口磨砂才能透出来。必须在 GUI 线程调用。"""
    targets = []
    native = getattr(window, "native", None)
    if native is not None:
        browser = getattr(native, "browser", None)
        if browser is not None:
            targets.append(getattr(browser, "webview", None))
        targets.append(getattr(native, "webview", None))
    try:
        from System.Drawing import Color  # pythonnet
    except Exception:
        return False
    ok = False
    for ctrl in targets:
        if ctrl is None:
            continue
        try:
            ctrl.DefaultBackgroundColor = Color.Transparent
            ok = True
        except Exception:
            continue
    return ok


def _find_by_title(title: str = "小庆工具箱"):
    """按窗口标题兜底查找，并校验属于当前进程，避免误抓同名窗口。"""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if pid.value and pid.value == kernel32.GetCurrentProcessId():
            return int(hwnd)
    except Exception:
        return None
    return None


def _find_by_process():
    """枚举本进程的可见顶层窗口，取面积最大的那个（不依赖窗口标题）。"""
    try:
        user32 = ctypes.windll.user32
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        hits = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                wpid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value == pid and user32.IsWindowVisible(hwnd):
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if w > 400 and h > 300:
                        hits.append((int(hwnd), w * h))
            except Exception:
                pass
            return True

        user32.EnumWindows(proto(_cb), 0)
        if hits:
            return max(hits, key=lambda item: item[1])[0]
    except Exception:
        return None
    return None


def _locate():
    hwnd = _find_by_title()
    if hwnd:
        return hwnd
    return _find_by_process()


def _install(window, hwnd: int) -> bool:
    global _HWND, _READY, _ENABLED, _UI_THREAD
    with _LOCK:
        _HWND = hwnd
        _READY = True
        enabled = _ENABLED
        on_ui_thread = _UI_THREAD is not None and _UI_THREAD == threading.get_ident()
    # 只有 GUI 线程能碰 pywebview 的原生控件，否则会抛 COM 异常
    if on_ui_thread:
        try:
            _make_webview_transparent(window)
        except Exception:
            pass
    return _push(enabled)


def prepare(window) -> bool:
    """缓存窗口句柄并把 WebView2 背景设透明。拿不到句柄就后台重试。"""
    global _UI_THREAD
    if _UI_THREAD is None:
        _UI_THREAD = threading.get_ident()
    hwnd = _handle_of(window) or _locate()
    if hwnd:
        return _install(window, hwnd)
    threading.Thread(target=_retry_install, daemon=True).start()
    return False


def _retry_install(timeout: float = 8.0) -> None:
    """后台只做纯 Win32 操作，绝不访问 pywebview 对象。"""
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            hwnd = _locate()
            if hwnd:
                _install(None, hwnd)
                return
        except Exception:
            pass
        _time.sleep(0.1)


def _push(on: bool) -> bool:
    hwnd = _HWND
    if not hwnd:
        return False
    policy = _AccentPolicy()
    if on:
        policy.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        policy.AccentFlags = 2
        # 0xAABBGGRR：深色用冷灰底，浅色用乳白底，透出桌面都不发灰
        policy.GradientColor = 0x7AF3F4F8 if _LIGHT else 0x7A1A1D24
    else:
        policy.AccentState = ACCENT_DISABLED
    data = _WindowCompositionAttributeData()
    data.Attribute = WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(policy)
    try:
        ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data)
        )
        return True
    except Exception:
        return False


def set_enabled(on: bool, light: bool = False) -> bool:
    """运行时开关磨砂；窗口还没建好时先记下状态。light=True 用乳白色磨砂底。"""
    global _ENABLED, _LIGHT
    with _LOCK:
        _ENABLED = bool(on)
        _LIGHT = bool(light)
        ready = _READY
    if not ready:
        return True
    return _push(bool(on))
