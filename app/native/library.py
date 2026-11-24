# -*- coding: utf-8 -*-
"""自建库读取（与 admin/server.py 共用同一份 JSON）。

软件本体只读这里的数据，写入统一走网页后台，避免两个进程同时改同一文件。
"""

from __future__ import annotations

import json
import os

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MixerToolsRebuild")
DATA_FILE = os.path.join(DATA_DIR, "自建库.json")
TOKEN_FILE = os.path.join(DATA_DIR, "后台口令.txt")
GATE_FILE = os.path.join(DATA_DIR, "后台密码.txt")

EMPTY = {"version": 1, "software": [], "projects": []}


def load() -> dict:
    if not os.path.isfile(DATA_FILE):
        return dict(EMPTY)
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return dict(EMPTY)
    if not isinstance(data, dict):
        return dict(EMPTY)
    software = data.get("software") if isinstance(data.get("software"), list) else []
    projects = data.get("projects") if isinstance(data.get("projects"), list) else []
    return {"version": data.get("version") or 1, "software": software, "projects": projects}


def read_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def read_gate() -> str:
    """打开网页后台的入口密码，默认 admin；可在 后台密码.txt 中自定义。"""
    try:
        with open(GATE_FILE, "r", encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val
    except Exception:
        pass
    return "admin"


def write_gate(value: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(GATE_FILE, "w", encoding="utf-8") as fh:
        fh.write(value)
