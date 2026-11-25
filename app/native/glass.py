"""全局磨砂（Windows 亚克力 / 云母）。

Windows 11 已不再支持老式的 ACCENT_ENABLE_ACRYLICBLURBEHIND（AccentState=4），
所以这里按优先级尝试三种实现：
  1) DWM 系统背景材质 SYSTEMBACKDROP_TYPE = Acrylic（Win11 22H2+ 推荐）
  2) DWM Mica
  3) SetWindowCompositionAttribute AccentState=5（宿主背景）/ 4（Win10 亚克力）
配合把 WebView2 默认背景设为透明，网页里的半透明层就能透出桌面壁纸。
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time as _time
from ctypes import wintypes

_LOCK = threading.Lock()
_HWND = None
_WINDOW = None
_ENABLED = False
_READY = False
_UI_THREAD = None
_LIGHT = False
_METHOD = "none"
_TRANSPARENT = "未尝试"

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_ENABLE_HOSTBACKDROP = 5

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 1
DWMSBT_ACRYLIC = 2
DWMSBT_MICA = 3


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


class _Margins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _accent(hwnd: int, state: int) -> bool:
    policy = _AccentPolicy()
    policy.AccentState = state
    policy.AccentFlags = 2
    policy.GradientColor = 0x7AF3F4F8 if _LIGHT else 0x7A1A1D24
    data = _WindowCompositionAttributeData()
    data.Attribute = WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(policy)
    try:
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def _dwm_backdrop(hwnd: int, on: bool, kind: int = DWMSBT_ACRYLIC) -> bool:
    """DWM 系统背景材质：需把玻璃区扩展到整个客户区才看得到。"""
    try:
        dwm = ctypes.windll.dwmapi
        margins = _Margins(-1, -1, -1, -1) if on else _Margins(0, 0, 0, 0)
        dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        value = ctypes.c_int(kind if on else DWMSBT_NONE)
        hr = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(value), ctypes.sizeof(value))
        return int(hr) == 0
    except Exception:
        return False


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


def _make_webview_transparent(window) -> str:
    """让 WebView2 的默认背景透明，磨砂才能透出来。必须在 GUI 线程调用。"""
    global _TRANSPARENT
    targets = []
    try:
        native = getattr(window, "native", None)
        if native is not None:
            browser = getattr(native, "browser", None)
            if browser is not None:
                targets.append(getattr(browser, "webview", None))
            targets.append(getattr(native, "webview", None))
    except Exception as exc:
        _TRANSPARENT = "取控件失败:%s" % exc
        return _TRANSPARENT
    try:
        from System.Drawing import Color
    except Exception as exc:
        _TRANSPARENT = "无 pythonnet:%s" % exc
        return _TRANSPARENT
    notes = []
    for ctrl in targets:
        if ctrl is None:
            continue
        try:
            ctrl.DefaultBackgroundColor = Color.Transparent
            notes.append("ok")
        except Exception as exc:
            notes.append("fail:%s" % str(exc)[:40])
    if not notes:
        _TRANSPARENT = "未找到 WebView2 控件"
    else:
        _TRANSPARENT = "|".join(notes)
    return _TRANSPARENT


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
    return _find_by_title() or _find_by_process()


def _install(window, hwnd: int) -> bool:
    global _HWND, _READY, _ENABLED, _WINDOW, _TRANSPARENT
    with _LOCK:
        _HWND = hwnd
        _READY = True
        if window is not None:
            _WINDOW = window
        enabled = _ENABLED
    # 透明背景随时可设（不依赖线程判断），失败也要留痕而不是静默吞掉
    if window is not None:
        try:
            _TRANSPARENT = _make_webview_transparent(window)
        except Exception as exc:
            _TRANSPARENT = "异常:%s" % str(exc)[:60]
    # 顺手把壁纸糊图落到页面同级目录，前端用相对路径就能直接当磨砂底
    try:
        ensure_wallpaper()
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
    """磨砂视觉由页面内的壁纸糊图 + 半透明面板实现。

    不再调用 DWM 系统背景材质 / SetWindowCompositionAttribute：
    在 Win11 25H2 的无边框窗口上，这类合成操作实测既透不出桌面，
    还会导致窗口卡死（用户实测反馈），因此整条窗口合成链路停用。
    """
    global _METHOD
    _METHOD = "页面磨砂" if on else "none"
    return True


def set_enabled(on: bool, light: bool = False) -> bool:
    """运行时开关磨砂；窗口还没建好时先记下状态。light=True 用乳白色磨砂底。

    注意：本函数在桥接线程池里执行，绝不能触碰 pywebview / WebView2 的
    COM 对象（跨线程访问会挂死 GUI），只做纯状态记录。
    """
    global _ENABLED, _LIGHT
    with _LOCK:
        _ENABLED = bool(on)
        _LIGHT = bool(light)
    _save_state()
    return _push(bool(on))


def describe() -> str:
    return "方式=%s，WebView2背景=%s，壁纸=%s" % (
        _METHOD, _TRANSPARENT, _WALL_FILE or (_WALL_NOTE or "未生成"))


def is_enabled() -> bool:
    return bool(_ENABLED)


def is_light() -> bool:
    return bool(_LIGHT)


_STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ".", ), "MixerToolsRebuild")
_STATE_FILE = os.path.join(_STATE_DIR, "glass.json")
_CACHE_DIR = os.path.join(_STATE_DIR, "磨砂缓存")


def _wallpaper_path() -> str:
    """当前桌面壁纸路径（纯色/幻灯片可能为空）。"""
    try:
        buf = ctypes.create_unicode_buffer(2600)
        if ctypes.windll.user32.SystemParametersInfoW(0x0073, 2600, buf, 0):
            return buf.value or ""
    except Exception:
        return ""
    return ""


def _virtual_screen() -> tuple:
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))


def _primary_screen() -> tuple:
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))


def _window_rect() -> tuple:
    hwnd = _HWND
    if not hwnd:
        return (0, 0, 0, 0)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _build_wall_image() -> str:
    """把壁纸按"填充"模式画成虚拟屏大小的模糊底图，缓存成 JPEG（半尺寸）。"""
    src = _wallpaper_path()
    if not src or not os.path.isfile(src):
        return ""
    try:
        vx, vy, vw, vh = _virtual_screen()
        pw, ph = _primary_screen()
        if vw < 8 or vh < 8 or pw < 8 or ph < 8:
            return ""
        out = os.path.join(_CACHE_DIR, "壁纸_%dx%d_v2.jpg" % (vw, vh))
        if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            return out
        os.makedirs(_CACHE_DIR, exist_ok=True)

        try:
            import clr
            clr.AddReference("System.Drawing")
        except Exception:
            pass
        from System.Drawing import Bitmap, Graphics, Image, Rectangle, GraphicsUnit
        from System.Drawing.Drawing2D import InterpolationMode
        from System.Drawing.Imaging import ImageFormat

        source = Image.FromFile(src)
        sw, sh = float(source.Width), float(source.Height)
        # 填充模式：等比放大铺满主屏，再居中裁掉溢出部分
        ratio = max(pw / sw, ph / sh)
        crop_w, crop_h = pw / ratio, ph / ratio
        src_rect = Rectangle(int((sw - crop_w) / 2), int((sh - crop_h) / 2),
                             max(1, int(crop_w)), max(1, int(crop_h)))

        half_w, half_h = max(1, vw // 2), max(1, vh // 2)
        bmp = Bitmap(half_w, half_h)
        g = Graphics.FromImage(bmp)
        g.InterpolationMode = InterpolationMode.HighQualityBicubic
        # 主屏区域
        g.DrawImage(source, Rectangle(0, 0, max(1, pw // 2), max(1, ph // 2)),
                    src_rect, GraphicsUnit.Pixel)
        # 副屏：用同一张图延伸铺满剩余区域，避免出现纯黑边
        rest_w = half_w - pw // 2
        if rest_w > 0:
            g.DrawImage(source, Rectangle(pw // 2, 0, rest_w, max(1, ph // 2)),
                        src_rect, GraphicsUnit.Pixel)
        cache = os.path.join(_CACHE_DIR, "壁纸_work_v2.jpg")
        bmp.Save(cache, ImageFormat.Jpeg)
        g.Dispose()
        bmp.Dispose()
        source.Dispose()
        os.replace(cache, out)
        return out
    except Exception as exc:
        global _WALL_NOTE
        _WALL_NOTE = "生成失败:%s" % str(exc)[:60]
        return ""


_WALL_NOTE = ""
_WALL_FILE = ""


def ensure_wallpaper() -> str:
    """把壁纸渲染成一张图放进页面同级目录，页面用相对路径 ../glass_wallpaper.jpg 引用。
    这样不依赖任何原生消息通道，磨砂底图一定能显示。"""
    global _WALL_FILE, _WALL_NOTE
    path = _build_wall_image()
    if not path:
        _WALL_NOTE = _WALL_NOTE or "没有壁纸图"
        return ""
    try:
        from .paths import ui_dir
        target = os.path.join(os.path.dirname(ui_dir()), "glass_wallpaper.jpg")
        import shutil
        if not (os.path.isfile(target) and os.path.getsize(target) > 1024):
            shutil.copyfile(path, target)
        _WALL_FILE = target
        return target
    except Exception as exc:
        _WALL_NOTE = "落盘失败:%s" % str(exc)[:60]
        return ""


def payload() -> str:
    """给前端的磨砂底图信息：图片地址 + 屏幕/窗口几何（物理像素）。"""
    path = _build_wall_image()
    if not path:
        return '{"ok":0,"note":"%s"}' % (_WALL_NOTE or "没有壁纸图")
    vx, vy, vw, vh = _virtual_screen()
    wx, wy, ww, wh = _window_rect()
    url = path.replace("\\", "/")
    return ('{"ok":1,"src":"file:///%s","vx":%d,"vy":%d,"vw":%d,"vh":%d,'
            '"wx":%d,"wy":%d,"ww":%d,"wh":%d}' % (url, vx, vy, vw, vh, wx, wy, ww, wh))


def _load_state() -> None:
    """记住上次的开关，启动时直接套用（前端还没就绪时也能生效）。"""
    global _ENABLED, _LIGHT
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        _ENABLED = bool(data.get("on"))
        _LIGHT = bool(data.get("light"))
    except Exception:
        pass


def _save_state() -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as fp:
            json.dump({"on": _ENABLED, "light": _LIGHT}, fp)
    except Exception:
        pass


_load_state()
