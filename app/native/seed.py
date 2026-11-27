# -*- coding: utf-8 -*-
"""释放内置数据：自建库内容 + 自动更新源。

自建库不再只在新机器上释放：程序每次启动都会比对内置内容指纹，
发现内置内容有更新就按 id 合并进用户已有数据——
内置新增的条目补进去，用户自己新增/修改过的条目一律保留，不覆盖、不清空。
打包时通过 --add-data "app\\seed;seed" 把 seed 目录带进 exe。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil

from . import library, update
from .paths import app_root, log

STAMP_FILE = os.path.join(library.DATA_DIR, "seed.json")
BAK_FILE = library.DATA_FILE + ".bak"


def _seed_dir() -> str:
    return os.path.join(app_root(), "seed")


def _sha256(path: str) -> str:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(262144), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return ""


def _stamp() -> str:
    """上次已释放的内置内容指纹。"""
    try:
        with open(STAMP_FILE, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("fingerprint", "") or "")
    except Exception:
        return ""


def _write_stamp(fingerprint: str) -> None:
    try:
        os.makedirs(library.DATA_DIR, exist_ok=True)
        with open(STAMP_FILE, "w", encoding="utf-8") as fh:
            json.dump({"fingerprint": fingerprint}, fh, ensure_ascii=False)
    except Exception:
        pass


def _merge_into(target: dict, seed_data: dict) -> int:
    """把内置条目按 id 并入用户数据，只补缺不覆盖；返回新增条数。"""
    added = 0
    for key in ("software", "projects"):
        mine = target.get(key)
        if not isinstance(mine, list):
            mine = []
            target[key] = mine
        have = set()
        for item in mine:
            if isinstance(item, dict) and item.get("id"):
                have.add(item["id"])
        for item in seed_data.get(key) or []:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or "")
            if not iid or iid in have:
                continue
            mine.append(item)
            have.add(iid)
            added += 1
    try:
        if int(seed_data.get("version") or 0) > int(target.get("version") or 0):
            target["version"] = seed_data.get("version")
    except Exception:
        pass
    return added


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _sync_library() -> None:
    src = os.path.join(_seed_dir(), "自建库.json")
    if not os.path.isfile(src):
        return
    fingerprint = _sha256(src)
    if not fingerprint:
        return

    # 新机器：直接放一份完整内置库
    if not os.path.isfile(library.DATA_FILE):
        try:
            shutil.copyfile(src, library.DATA_FILE)
            _write_stamp(fingerprint)
            log("首次运行", "已释放内置自建库内容", "成功")
        except Exception as exc:
            log("首次运行", "释放自建库失败：%s" % exc, "警告")
        return

    # 老机器：内置内容变了才合并，避免每次启动都读写
    if _stamp() == fingerprint:
        return
    try:
        with open(src, "r", encoding="utf-8") as fh:
            seed_data = json.load(fh)
    except Exception as exc:
        log("自建库同步", "内置数据不可读：%s" % exc, "警告")
        return
    try:
        with open(library.DATA_FILE, "r", encoding="utf-8") as fh:
            target = json.load(fh)
        if not isinstance(target, dict):
            raise ValueError("本地自建库结构异常")
    except Exception as exc:
        log("自建库同步", "本地数据不可读，跳过合并以免丢失：%s" % exc, "警告")
        return

    try:
        added = _merge_into(target, seed_data)
    except Exception as exc:
        log("自建库同步", "合并失败：%s" % exc, "警告")
        return

    if added <= 0:
        _write_stamp(fingerprint)
        return
    try:
        shutil.copyfile(library.DATA_FILE, BAK_FILE)
    except Exception:
        pass
    try:
        _write_json(library.DATA_FILE, target)
        _write_stamp(fingerprint)
        log("自建库同步", "已同步内置内容，新增 %d 条（原有数据保留）" % added, "成功")
    except Exception as exc:
        log("自建库同步", "写入失败：%s" % exc, "警告")


def ensure_seed() -> None:
    """启动时释放/同步内置数据。"""
    try:
        os.makedirs(library.DATA_DIR, exist_ok=True)
    except Exception as exc:
        log("首次运行", "创建数据目录失败：%s" % exc, "警告")
        return

    _sync_library()

    if not os.path.isfile(update.CFG_FILE):
        try:
            update.write_cfg(True, update.DEFAULT_BASE, True)
            log("首次运行", "已启用自动更新源：%s" % update.DEFAULT_BASE, "成功")
        except Exception as exc:
            log("首次运行", "写入更新设置失败：%s" % exc, "警告")
