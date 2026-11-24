"""配置与下载历史的持久化。"""
from __future__ import annotations

import json
import os

from . import paths

CONFIG_FILE = "config.ini"
HISTORY_FILE = "history.json"


def _config_path() -> str:
    return os.path.join(paths.data_dir(), CONFIG_FILE)


def _history_path() -> str:
    return os.path.join(paths.data_dir(), HISTORY_FILE)


def _parse_original_ini() -> dict:
    """首次运行时继承原版程序的设置，避免用户重新配置。"""
    result = {}
    candidates = [
        os.path.join(paths.original_data_dir(), "config.ini"),
        os.path.join(paths.data_dir(), "config.ini"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    result[key.strip()] = value.strip()
            break
        except Exception:
            continue
    return result


ORIGINAL_KEYS = {
    "DownloadPath": "downloadPath",
    "LayoutMode": "layout",
    "AutoOpenExe": "autoOpenExe",
    "SidebarPos": "sidebarPos",
    "AutoPopupDl": "autoPopupDl",
    "Theme": "theme",
    "RoutePref": "routePref",
    "SpeedLimit": "speedLimit",
}

STATE_KEYS = [
    "downloadPath", "layout", "autoOpenExe", "sidebarPos",
    "autoPopupDl", "theme", "routePref", "speedLimit",
]


def default_state() -> dict:
    ini = _parse_original_ini()
    state = {
        "downloadPath": paths.default_download_dir(),
        "layout": "list",
        "autoOpenExe": "1",
        "sidebarPos": "left",
        "autoPopupDl": "1",
        "theme": "light",
        "routePref": "auto",
        "speedLimit": "0",
    }
    for key, value in ini.items():
        mapped = ORIGINAL_KEYS.get(key) or (key if key in state else None)
        if mapped:
            state[mapped] = value
    return state


def load_config_string() -> str:
    state = default_state()
    overrides = {}
    path = _config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    overrides[key.strip()] = value.strip()
        except Exception:
            overrides = {}
    state.update({k: v for k, v in overrides.items() if k in state})
    return "|".join([
        state["downloadPath"],
        state["layout"] or "list",
        state["autoOpenExe"],
        state["sidebarPos"],
        state["autoPopupDl"],
        state["theme"],
        state["routePref"],
        state["speedLimit"],
    ])


def save_config_string(raw: str) -> None:
    parts = (raw or "").split("|")
    state = default_state()
    for index, key in enumerate(STATE_KEYS):
        if index < len(parts) and parts[index] != "":
            state[key] = parts[index]
    try:
        with open(_config_path(), "w", encoding="utf-8") as fh:
            for key, value in state.items():
                fh.write("%s=%s\n" % (key, value))
    except Exception:
        pass


def load_history_string() -> str:
    path = _history_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read().strip()
            if content:
                json.loads(content)
                return content
        except Exception:
            pass
    return "[]"


def save_history_string(raw: str) -> None:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return
    try:
        with open(_history_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception:
        pass


def get_download_path() -> str:
    return default_state()["downloadPath"]
