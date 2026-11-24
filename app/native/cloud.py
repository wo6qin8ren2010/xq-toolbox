"""云端栏目：优先读取本地缓存，无网络时保持可用。"""
from __future__ import annotations

import json
import os

from . import paths

CACHE_FILES = {
    "cloud": "cloud_data_cache.json",
    "dashboard": "cloud_dashboard_cache.json",
    "driver": "cloud_driver_cache.json",
    "shortcut": "cloud_shortcut_cache.json",
}

PREFIX = {
    "cloud": "cloud_data_cache|",
    "dashboard": "dashboard_cloud_data_cache|",
    "driver": "driver_cloud_data_cache|",
    "shortcut": "shortcut_cloud_data_cache|",
}


def _load(kind: str) -> str:
    name = CACHE_FILES.get(kind)
    if not name:
        return "[]"
    for base in (paths.data_dir(), paths.original_data_dir()):
        file_path = os.path.join(base, name)
        if not os.path.isfile(file_path):
            continue
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read().strip()
            json.loads(content)
            return content
        except Exception:
            continue
    return "[]"


def message(kind: str) -> str:
    return PREFIX.get(kind, "cloud_data_cache|") + _load(kind)
