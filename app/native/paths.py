"""路径与日志基础设施。"""
from __future__ import annotations

import os
import sys
import time
import threading

APP_NAME = "MixerToolsRebuild"
ORIGINAL_APP_NAME = "Mixer‘Tools"

_lock = threading.Lock()


def app_root() -> str:
    """程序根目录（源码运行时为 app 目录，打包后为解包目录）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ui_dir() -> str:
    return os.path.join(app_root(), "ui")


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def original_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, ORIGINAL_APP_NAME)


def logs_dir() -> str:
    path = os.path.join(data_dir(), "日志文件")
    os.makedirs(path, exist_ok=True)
    return path


def desktop_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop")


def default_download_dir() -> str:
    return os.path.join(desktop_dir(), "小庆")


def log(tag: str, message: str, level: str = "信息") -> None:
    line = "%s [T%d] [系统引擎] [%s] %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), threading.get_ident() % 65536, level, message
    )
    try:
        with _lock:
            with open(os.path.join(logs_dir(), "ToolsLog.log"), "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass
