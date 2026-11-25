# -*- coding: utf-8 -*-
"""小庆自用页「软件 Dock」：固定文件夹一键扫描、手动添加、点击启动。

- 清单持久化在 %LOCALAPPDATA%\\MixerToolsRebuild\\dock.json；
- 一键刷新：扫描文件夹顶层的 .exe / .lnk，自动补齐新软件、清掉已删除的；
- 图标：System.Drawing 提取 exe 关联图标，落成 PNG 放页面同级目录，
  前端用相对路径 ../dock_icons/<hash>.png 引用（与磨砂底图同一套路）；
- 启动：exe 用 Popen（工作目录设为软件所在目录，绿色软件常依赖这个），
  lnk 走 ShellExecute。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import threading
from ctypes import wintypes

_LOCK = threading.Lock()
_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "MixerToolsRebuild")
_FILE = os.path.join(_DIR, "dock.json")
_DEFAULT_FOLDER = r"E:\黑科技\绿色版"
_EXTS = (".exe", ".lnk")


def _load() -> dict:
    try:
        with open(_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            data.setdefault("folder", _DEFAULT_FOLDER)
            data.setdefault("items", [])
            return data
    except Exception:
        pass
    return {"folder": _DEFAULT_FOLDER, "items": []}


def _save(data: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path or "").strip().strip('"')))


def _name_of(path: str) -> str:
    base = os.path.basename(path)
    if base.lower().endswith(".lnk"):
        base = base[:-4]
    elif base.lower().endswith(".exe"):
        base = base[:-4]
    return base or path


def _icon_url(path: str) -> str:
    """提取软件图标，返回 data: URL（内嵌 base64）。

    不走相对路径：WebView2 对 file:// 页面加载子目录图片有限制，
    内嵌 data URL 与页面同源策略无关，任何情况下都能显示。
    PNG 仍落盘缓存，避免每次重复提取。
    """
    try:
        from .paths import ui_dir
        out_dir = os.path.join(os.path.dirname(ui_dir()), "dock_icons")
        os.makedirs(out_dir, exist_ok=True)
        key = hashlib.md5(_norm(path).encode("utf-8")).hexdigest()[:16]
        out = os.path.join(out_dir, key + ".png")
        if not (os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(path)):
            try:
                import clr
                clr.AddReference("System.Drawing")
            except Exception:
                pass
            from System.Drawing import Icon
            from System.Drawing.Imaging import ImageFormat
            ico = Icon.ExtractAssociatedIcon(path)
            if ico is None:
                return ""
            bmp = ico.ToBitmap()
            tmp = out + ".tmp"
            bmp.Save(tmp, ImageFormat.Png)
            bmp.Dispose()
            ico.Dispose()
            os.replace(tmp, out)
        import base64
        with open(out, "rb") as fp:
            return "data:image/png;base64," + base64.b64encode(fp.read()).decode("ascii")
    except Exception:
        return ""


def _payload(data: dict) -> dict:
    items = []
    for item in data.get("items", []):
        path = item.get("path") or ""
        if not path:
            continue
        items.append({
            "path": path,
            "name": item.get("name") or _name_of(path),
            "icon": _icon_url(path) if os.path.isfile(path) else "",
            "missing": not os.path.isfile(path),
        })
    return {"folder": data.get("folder") or _DEFAULT_FOLDER, "items": items}


def list_payload() -> dict:
    with _LOCK:
        return _payload(_load())


def scan() -> dict:
    """一键刷新：把文件夹里所有软件补进 Dock，已删除的自动移除。"""
    with _LOCK:
        data = _load()
        folder = data.get("folder") or _DEFAULT_FOLDER
        found = []
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder), key=str.lower):
                low = name.lower()
                if not low.endswith(_EXTS):
                    continue
                if low.startswith(("unins", "uninstall")):
                    continue
                full = os.path.join(folder, name)
                if os.path.isfile(full):
                    found.append(full)
        found_norm = {_norm(p) for p in found}
        kept = []
        for item in data.get("items", []):
            path = item.get("path") or ""
            np = _norm(path)
            in_folder = _norm(os.path.dirname(path)) == _norm(folder)
            if in_folder and np not in found_norm:
                continue  # 文件夹里已删除
            if np in found_norm:
                continue  # 稍后按文件夹顺序统一补回
            kept.append(item)
        for full in found:
            kept.append({"path": full, "name": _name_of(full)})
        data["items"] = kept
        _save(data)
        return _payload(data)


def add(path: str) -> dict:
    with _LOCK:
        data = _load()
        np = _norm(path)
        if not np or not os.path.isfile(path):
            return _payload(data)
        if any(_norm(i.get("path")) == np for i in data["items"]):
            return _payload(data)
        data["items"].append({"path": os.path.normpath(path), "name": _name_of(path)})
        _save(data)
        return _payload(data)


def remove(path: str) -> dict:
    with _LOCK:
        data = _load()
        np = _norm(path)
        data["items"] = [i for i in data["items"] if _norm(i.get("path")) != np]
        _save(data)
        return _payload(data)


def set_folder(path: str) -> dict:
    with _LOCK:
        data = _load()
        data["folder"] = os.path.normpath(str(path or "").strip().strip('"')) or _DEFAULT_FOLDER
        _save(data)
        return _payload(data)


def launch(path: str) -> tuple:
    """返回 (ok, 提示)。exe 以软件所在目录为工作目录启动。"""
    if not os.path.isfile(path):
        return False, "文件不存在：%s" % path
    try:
        if path.lower().endswith(".lnk"):
            os.startfile(path)
        else:
            subprocess.Popen([path], cwd=os.path.dirname(path), close_fds=True)
        return True, "已启动：%s" % _name_of(path)
    except Exception as exc:
        return False, "启动失败：%s" % str(exc)[:80]


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
        ("lpfnHook", wintypes.LPVOID), ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", wintypes.LPVOID), ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def pick_exe() -> str:
    """原生「选择软件」文件对话框（纯 Win32，可在桥接线程里弹）。"""
    try:
        buf = ctypes.create_unicode_buffer(4096)
        ofn = _OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(ofn)
        try:
            from . import glass
            ofn.hwndOwner = wintypes.HWND(glass._HWND or 0)
        except Exception:
            pass
        ofn.lpstrFilter = "软件 (*.exe;*.lnk)\0*.exe;*.lnk\0所有文件 (*.*)\0*.*\0\0"
        ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
        ofn.nMaxFile = 4096
        ofn.lpstrTitle = "选择要添加到 Dock 的软件"
        ofn.Flags = 0x1000 | 0x800 | 0x8  # FILEMUSTEXIST | PATHMUSTEXIST | NOCHANGEDIR
        if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return buf.value
    except Exception:
        pass
    return ""
