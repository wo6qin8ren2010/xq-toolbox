# -*- coding: utf-8 -*-
"""首次运行释放内置数据：自建库内容 + 自动更新源。

只在目标文件不存在时写入，绝不覆盖用户已有的数据。
打包时通过 --add-data "app\\seed;seed" 把 seed 目录带进 exe。
"""
from __future__ import annotations

import os
import shutil

from . import library, update
from .paths import app_root, log


def _seed_dir() -> str:
    return os.path.join(app_root(), "seed")


def ensure_seed() -> None:
    """新机器首次运行：把内置的自建库与更新源落到数据目录。"""
    try:
        os.makedirs(library.DATA_DIR, exist_ok=True)
    except Exception as exc:
        log("首次运行", "创建数据目录失败：%s" % exc, "警告")
        return

    if not os.path.isfile(library.DATA_FILE):
        src = os.path.join(_seed_dir(), "自建库.json")
        if os.path.isfile(src):
            try:
                shutil.copyfile(src, library.DATA_FILE)
                log("首次运行", "已释放内置自建库内容", "成功")
            except Exception as exc:
                log("首次运行", "释放自建库失败：%s" % exc, "警告")

    if not os.path.isfile(update.CFG_FILE):
        try:
            update.write_cfg(True, update.DEFAULT_BASE, True)
            log("首次运行", "已启用自动更新源：%s" % update.DEFAULT_BASE, "成功")
        except Exception as exc:
            log("首次运行", "写入更新设置失败：%s" % exc, "警告")