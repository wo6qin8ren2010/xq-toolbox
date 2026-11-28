# -*- coding: utf-8 -*-
"""远程调试面板：把本机 X·Q Desk（CrossDesk 定制版）的 ID、密码、近期连接搬进工具箱。

数据来源全部是本机已有文件，只读不改 X·Q Desk 的配置：
- 本机 ID / 密码：C:\\ProgramData\\CrossDeskUnattended\\status.json（服务实时写入）
- 近期连接与缩略图：%LOCALAPPDATA%\\CrossDesk\\cache\\thumbnails\\
- 程序位置与版本：安装目录 / 卸载注册表项
连接动作只做三件事：复制对端 ID、打开 X·Q Desk、记一条本地历史，
真正的握手交给 X·Q Desk 自己完成。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import time
import winreg

from .library import DATA_DIR
from .paths import log

APP_CANDIDATES = (
    r"C:\Program Files\X·Q Desk\X·Q Desk.exe",
    r"C:\Program Files\CrossDesk\CrossDesk.exe",
    r"C:\Program Files (x86)\X·Q Desk\X·Q Desk.exe",
    r"C:\Program Files (x86)\CrossDesk\CrossDesk.exe",
)
STATUS_FILE = r"C:\ProgramData\CrossDeskUnattended\status.json"
THUMB_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "") or "", "CrossDesk", "cache", "thumbnails")
HISTORY_FILE = os.path.join(DATA_DIR, "远程调试.json")
MAX_IMG_BYTES = 260 * 1024


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        log("远程调试", "写入本地记录失败：%s" % exc, "警告")


def _find_app() -> str:
    for path in APP_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


def _app_version() -> str:
    """优先读卸载项里的展示版本，读不到就看 exe 的文件版本。"""
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{BE18671E-7B55-4B72-A979-9FE8293B55D3}_is1")
        try:
            for name in ("DisplayVersion", "Version"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    if value:
                        return str(value)
                except Exception:
                    continue
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass
    exe = _find_app()
    if exe:
        try:
            import win32api
            info = win32api.GetFileVersionInfo(exe, "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            return "%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16)
        except Exception:
            pass
    return ""


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _status() -> dict:
    st = _read_json(STATUS_FILE)
    if not st:
        return {}
    return {
        "id": str(st.get("device_id", "") or ""),
        "password": str(st.get("password", "") or ""),
        "online": bool(st.get("online", False)),
        "updated_at": int(st.get("updated_at", 0) or 0),
        "running": _pid_alive(st.get("process_id", 0)),
        "mode": str(st.get("mode", "") or ""),
    }


def _thumb_data_uri(png_path: str) -> str:
    try:
        if not os.path.isfile(png_path):
            return ""
        if os.path.getsize(png_path) > MAX_IMG_BYTES:
            return ""
        with open(png_path, "rb") as fh:
            raw = fh.read()
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return ""


def _thumbs() -> list:
    """X·Q Desk 自己保存的近期连接，按最近更新时间倒序。"""
    items = []
    if not THUMB_DIR or not os.path.isdir(THUMB_DIR):
        return items
    for name in os.listdir(THUMB_DIR):
        if not name.endswith(".json"):
            continue
        meta_path = os.path.join(THUMB_DIR, name)
        meta = _read_json(meta_path)
        rid = str(meta.get("remote_id", "") or "").strip()
        if not rid:
            continue
        try:
            stamp = int(os.path.getmtime(meta_path))
        except Exception:
            stamp = 0
        png = os.path.splitext(meta_path)[0] + ".png"
        items.append({
            "id": rid,
            "host": str(meta.get("host_name", "") or ""),
            "kind": str(meta.get("kind", "") or ""),
            "platform": str(meta.get("platform", "") or ""),
            "thumb": _thumb_data_uri(png),
            "last": stamp,
            "source": "desk",
        })
    items.sort(key=lambda item: item["last"], reverse=True)
    return items


def _history() -> list:
    data = _read_json(HISTORY_FILE)
    rows = data.get("recent", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict) and str(row.get("id", "")).strip()]


def _touch_history(peer_id: str, host: str = "") -> None:
    rows = _history()
    kept = [row for row in rows if str(row.get("id", "")) != peer_id]
    kept.insert(0, {"id": peer_id, "host": host, "last": int(time.time()), "source": "toolbox"})
    _write_json(HISTORY_FILE, {"version": 1, "recent": kept[:20]})


def _combine_recent(desk_items: list, history: list) -> list:
    merged = {}
    for row in history:
        merged[str(row["id"])] = {
            "id": str(row["id"]),
            "host": str(row.get("host", "") or ""),
            "last": int(row.get("last", 0) or 0),
            "thumb": "",
            "kind": "cloud_id",
            "source": "toolbox",
        }
    for row in desk_items:
        old = merged.get(row["id"])
        if old:
            row["last"] = max(row["last"], int(old.get("last", 0) or 0))
            if not row.get("host"):
                row["host"] = old.get("host", "")
        merged[row["id"]] = row
    items = list(merged.values())
    items.sort(key=lambda item: item.get("last", 0), reverse=True)
    return items[:12]


def _set_clipboard(text: str) -> bool:
    """直接走 Win32 剪贴板，避免依赖 tkinter / pyperclip。

    其它程序（输入法、浏览器）偶尔会占着剪贴板，OpenClipboard 会失败，
    所以重试几次；再不行退到系统自带的 clip.exe。
    """
    text = str(text or "")
    if not text:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    for _ in range(3):
        if not user32.OpenClipboard(None):
            time.sleep(0.08)
            continue
        ok = False
        try:
            user32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(buf)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if handle:
                ptr = kernel32.GlobalLock(handle)
                if ptr:
                    ctypes.memmove(ptr, buf, size)
                    kernel32.GlobalUnlock(handle)
                    ok = bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
        finally:
            user32.CloseClipboard()
        if ok:
            return True
        time.sleep(0.08)
    # 兜底：ID / 密码 / IP 都是数字，clip.exe 不会因为字符集出问题
    if all(ch.isascii() for ch in text):
        try:
            subprocess.run(["cmd", "/c", "clip"], input=text.encode("ascii"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
            return True
        except Exception:
            return False
    return False


def _launch_app() -> bool:
    exe = _find_app()
    if not exe:
        return False
    try:
        subprocess.Popen([exe], close_fds=True)
        log("远程调试", "已打开 %s" % exe, "信息")
        return True
    except Exception as exc:
        log("远程调试", "打开 X·Q Desk 失败：%s" % exc, "警告")
        return False


def normalize_id(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _settings() -> dict:
    """端口与 IP 直连开关：来自 CrossDesk 的 config.ini 与 lan/identity.json。"""
    port = 52888
    ip_direct = False
    self_hosted = False
    try:
        lan = _read_json(os.path.join(os.environ.get("APPDATA", "") or "", "CrossDesk", "lan", "identity.json"))
        ports = (lan.get("settings", {}) or {}).get("ports", {}) or {}
        for value in ports.values():
            try:
                port = int(value)
                break
            except Exception:
                continue
    except Exception:
        pass
    ini = os.path.join(os.environ.get("LOCALAPPDATA", "") or "", "CrossDesk", "cache", "config.ini")
    try:
        with open(ini, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().lower()
                value = value.strip().lower()
                if key == "enable_ip_direct":
                    ip_direct = value in ("1", "true", "yes", "on")
                elif key == "enable_self_hosted":
                    self_hosted = value in ("1", "true", "yes", "on")
    except Exception:
        pass
    return {"port": port, "ip_direct": ip_direct, "self_hosted": self_hosted}


def state() -> dict:
    exe = _find_app()
    status = _status()
    desk_items = _thumbs()
    recent = _combine_recent(desk_items, _history())
    version = _app_version()
    return {
        "available": bool(exe),
        "app": {
            "name": "X·Q Desk",
            "version": version,
            "exe": exe,
            "running": bool(status.get("running")),
        },
        "self": {
            "id": status.get("id", ""),
            "password": status.get("password", ""),
            "online": bool(status.get("online")),
            "updated_at": int(status.get("updated_at", 0) or 0),
        },
        "recent": recent,
        "cfg": _settings(),
        "msg": "" if exe else "未检测到 X·Q Desk，请先安装或指定程序目录",
    }


def copy_self(field: str) -> dict:
    field = str(field or "").lower()
    status = _status()
    if field == "id":
        value = status.get("id", "")
        label = "本机 ID"
    elif field == "password":
        value = status.get("password", "")
        label = "连接密码"
    else:
        return {"ok": False, "msg": "不认识的复制目标"}
    if not value:
        return {"ok": False, "msg": "还没读到 X·Q Desk 的%s，先启动它再点刷新" % label}
    ok = _set_clipboard(value)
    return {"ok": ok, "msg": ("%s 已复制到剪贴板" % label) if ok else "复制失败，请手动选中复制"}


def connect(peer_id: str) -> dict:
    pid = normalize_id(peer_id)
    if not pid:
        return {"ok": False, "msg": "请输入对方设备的 ID"}
    if len(pid) < 6 or len(pid) > 12:
        return {"ok": False, "msg": "ID 位数不对，通常是 9 位数字"}
    if pid == _status().get("id"):
        return {"ok": False, "msg": "这是本机自己的 ID，请输入对方设备的 ID"}
    host = ""
    for row in _thumbs():
        if row["id"] == pid:
            host = row.get("host", "")
            break
    _set_clipboard(pid)
    _touch_history(pid, host)
    launched = _launch_app()
    if not launched:
        return {"ok": False, "msg": "已复制 ID，但没找到 X·Q Desk 程序，无法打开连接窗口"}
    log("远程调试", "发起连接 %s（%s），已复制 ID 并打开 X·Q Desk" % (pid, host or "未知主机"), "信息")
    return {
        "ok": True,
        "id": pid,
        "msg": "ID 已复制，X·Q Desk 已打开：粘贴到「对端 ID」框后点连接即可",
    }


def copy_text(text: str, launch: bool = True) -> dict:
    value = str(text or "").strip()
    if not value:
        return {"ok": False, "msg": "没有可复制的内容", "target": "ip"}
    ok = _set_clipboard(value)
    if launch:
        _launch_app()
    if not ok:
        return {"ok": False, "msg": "复制失败，请手动输入", "target": "ip"}
    return {
        "ok": True,
        "msg": ("IP 已复制，X·Q Desk 已打开：在「IP 连接」里粘贴后连接" if launch else "IP 已复制到剪贴板"),
        "target": "ip",
    }


def open_tool() -> dict:
    ok = _launch_app()
    return {"ok": ok, "msg": "已打开 X·Q Desk" if ok else "没找到 X·Q Desk 程序"}
