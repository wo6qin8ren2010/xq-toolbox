"""调试工具里的“备份/还原通道”与“备份/恢复效果”。"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.parse

from . import audio, paths

CHANNEL_DIR = "backup/channels"
EFFECT_DIR = "backup/effects"


def _dir(name: str) -> str:
    path = os.path.join(paths.data_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path


def _enc(text: str) -> str:
    return urllib.parse.quote(str(text), safe="")


# ---------------- 通道（音频端点） ----------------
def channel_candidates() -> str:
    records = []
    for device in audio.list_devices():
        if device["state"] != 1:
            continue
        records.append("%s,%s" % (_enc(device["id"]), _enc(device["name"])))
    return "backup_channel_candidates_result|" + ";".join(records)


def backup_channels(ids: list) -> str:
    devices = {d["id"]: d for d in audio.list_devices()}
    payload = []
    for device_id in ids:
        device = devices.get(device_id)
        if not device:
            continue
        payload.append({"id": device["id"], "name": device["name"], "default": device["default"]})
    if not payload:
        return "backup_channel_result|fail"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    file_path = os.path.join(_dir(CHANNEL_DIR), "%s.json" % stamp)
    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump({"created": stamp, "items": payload}, fh, ensure_ascii=False, indent=2)
    paths.log("备份通道", "已备份 %d 个通道项目到 %s" % (len(payload), file_path), "成功")
    return "backup_channel_result|success|backed=%d|failed=0" % len(payload)


def list_channel_snapshots(prefix: str = "") -> str:
    records = []
    for name in sorted(os.listdir(_dir(CHANNEL_DIR)), reverse=True):
        if not name.endswith(".json"):
            continue
        snapshot = name[:-5]
        try:
            with open(os.path.join(_dir(CHANNEL_DIR), name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            count = len(data.get("items", []))
        except Exception:
            count = 0
        records.append("%s,%s,%s" % (_enc(snapshot), _enc("通道备份 " + snapshot), count))
    if prefix == "items":
        return "backup_channel_items_result|" + ";".join(records)
    return "backup_channel_list_result|" + ";".join(records)


def restore_channels(snapshot_ids: list) -> str:
    restored = 0
    failed = 0
    for snapshot in snapshot_ids:
        file_path = os.path.join(_dir(CHANNEL_DIR), "%s.json" % snapshot)
        if not os.path.isfile(file_path):
            failed += 1
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for item in data.get("items", []):
                if item.get("default"):
                    kind = "capture" if "capture" in item.get("name", "").lower() else "render"
                    audio.set_default(kind, item["id"])
            restored += 1
        except Exception:
            failed += 1
    paths.log("备份通道", "还原完成：成功 %d，失败 %d" % (restored, failed), "成功" if failed == 0 else "警告")
    return "restore_backup_channel_result|%s|restored=%d|failed=%d" % (
        "success" if restored else "fail", restored, failed)


def delete_channels(snapshot_ids: list) -> str:
    deleted = 0
    failed = 0
    for snapshot in snapshot_ids:
        file_path = os.path.join(_dir(CHANNEL_DIR), "%s.json" % snapshot)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return "delete_backup_channel_result|success|deleted=%d|failed=%d" % (deleted, failed)


# ---------------- 效果 ----------------
def effect_candidates() -> str:
    usr = os.path.join(paths.desktop_dir(), "效果")
    records = []
    if os.path.isdir(usr):
        for name in sorted(os.listdir(usr)):
            full = os.path.join(usr, name)
            if os.path.isfile(full):
                records.append("%s,%s" % (_enc(full), _enc(name)))
    return "backup_effect_candidates_result|" + ";".join(records)


def backup_effects(paths_list: list) -> str:
    backed = 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = os.path.join(_dir(EFFECT_DIR), stamp)
    os.makedirs(target, exist_ok=True)
    for file_path in paths_list:
        if not os.path.isfile(file_path):
            continue
        shutil.copy2(file_path, os.path.join(target, os.path.basename(file_path)))
        backed += 1
    paths.log("备份效果", "已备份 %d 个效果到 %s" % (backed, target), "成功")
    return "backup_effect_result|success|restored=%d|failed=0" % backed if backed else "backup_effect_result|fail"


def list_effect_snapshots() -> str:
    records = []
    for name in sorted(os.listdir(_dir(EFFECT_DIR)), reverse=True):
        full = os.path.join(_dir(EFFECT_DIR), name)
        if not os.path.isdir(full):
            continue
        count = len(os.listdir(full))
        records.append("%s,%s,%s" % (_enc(name), _enc("效果备份 " + name), count))
    return "backup_effect_list_result|" + ";".join(records)


def restore_effects(snapshot_ids: list) -> str:
    restored = 0
    failed = 0
    out_dir = os.path.join(paths.desktop_dir(), "效果还原")
    os.makedirs(out_dir, exist_ok=True)
    for snapshot in snapshot_ids:
        source = os.path.join(_dir(EFFECT_DIR), snapshot)
        if not os.path.isdir(source):
            failed += 1
            continue
        for name in os.listdir(source):
            try:
                shutil.copy2(os.path.join(source, name), os.path.join(out_dir, name))
                restored += 1
            except Exception:
                failed += 1
    return "restore_backup_effect_result|success|restored=%d|failed=%d" % (restored, failed)


def delete_effects(snapshot_ids: list) -> str:
    deleted = 0
    failed = 0
    for snapshot in snapshot_ids:
        target = os.path.join(_dir(EFFECT_DIR), snapshot)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=False)
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return "delete_backup_effect_result|success|deleted=%d|failed=%d" % (deleted, failed)


def backup_exists() -> str:
    """原版会检测酷狗/QQ音乐/网易云/TT语音的配置备份，本程序统一返回未备份。"""
    return "backup_exists_result|kugou=0|qqmusic=0|netease=0|tt=0"
