# -*- coding: utf-8 -*-
"""软件自动更新：版本清单比对 + 直链下载 + SHA256 校验 + 自替换重启。

发布流程：改 APP_VERSION → 打包 → make_update_manifest.py 生成 version.json
→ 把 version.json 与新 exe 上传到同一目录 → 用户侧填该目录的直链前缀。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

from .library import DATA_DIR
from .paths import log

APP_VERSION = "1.0.0"
CFG_FILE = os.path.join(DATA_DIR, "更新设置.json")
DL_DIR = os.path.join(DATA_DIR, "更新下载")
PENDING_META = os.path.join(DL_DIR, "pending.json")
UA = "XQToolbox-Updater/1.0"


def read_cfg() -> dict:
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        cfg = {}
    return {
        "auto": bool(cfg.get("auto", True)),
        "silent": bool(cfg.get("silent", False)),
        "base": str(cfg.get("base", "") or "").strip(),
    }


def write_cfg(auto: bool, base: str, silent: bool = False) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CFG_FILE, "w", encoding="utf-8") as fh:
        json.dump({"auto": bool(auto), "silent": bool(silent), "base": str(base or "").strip()}, fh, ensure_ascii=False, indent=2)


def _ver_tuple(text: str):
    parts = []
    for chunk in str(text or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return _ver_tuple(remote) > _ver_tuple(local)


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    return urllib.request.urlopen(req, timeout=timeout)


def check(base: str) -> dict:
    """拉 version.json 比对版本；返回 status=latest/available/nobase/error。"""
    base = str(base or "").strip()
    if not base:
        return {"status": "nobase", "msg": "尚未填写更新源直链前缀"}
    if not base.startswith(("http://", "https://")):
        return {"status": "error", "msg": "更新源地址需以 http:// 或 https:// 开头"}
    url = base.rstrip("/") + "/version.json"
    try:
        with _get(url, 8) as resp:
            manifest = json.loads(resp.read().decode("utf-8-sig"))
    except Exception as exc:
        log("自动更新", "拉取版本清单失败：%s" % exc, "警告")
        return {"status": "error", "msg": "拉取版本清单失败：%s" % exc}
    remote = str(manifest.get("version", "") or "")
    if not remote:
        return {"status": "error", "msg": "版本清单缺少 version 字段"}
    if not is_newer(remote):
        return {"status": "latest", "version": remote, "msg": "已是最新版本 v%s" % APP_VERSION}
    file_name = str(manifest.get("file", "") or "小庆工具箱.exe")
    dl_url = str(manifest.get("url", "") or "") or (base.rstrip("/") + "/" + urllib.parse.quote(file_name))
    return {
        "status": "available",
        "version": remote,
        "notes": str(manifest.get("notes", "") or ""),
        "url": dl_url,
        "sha256": str(manifest.get("sha256", "") or "").lower(),
        "msg": "发现新版本 v%s" % remote,
    }


def download_and_verify(url: str, sha256: str) -> str:
    """下载到 更新下载\\pending.exe 并校验 SHA256，返回落盘路径。"""
    os.makedirs(DL_DIR, exist_ok=True)
    target = os.path.join(DL_DIR, "pending.exe")
    digest = hashlib.sha256()
    size = 0
    with _get(url, 60) as resp, open(target, "wb") as fh:
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            fh.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size < 1024 * 1024:
        raise ValueError("下载文件过小（%d 字节），疑似直链失效" % size)
    got = digest.hexdigest()
    if sha256 and got != sha256.lower():
        raise ValueError("SHA256 校验不一致，已丢弃下载包")
    log("自动更新", "更新包下载完成：%d 字节，sha256=%s" % (size, got), "成功")
    return target


UPDATER_PS1 = """param([string]$New,[string]$Target,[string]$ProcName,[string]$Meta,[switch]$NoRelaunch)
$ErrorActionPreference = 'Stop'
for ($i = 0; $i -lt 120; $i++) {
    if (-not (Get-Process -Name $ProcName -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Copy-Item -LiteralPath $New -Destination $Target -Force
Remove-Item -LiteralPath $New -Force
if ($Meta -and (Test-Path -LiteralPath $Meta)) { Remove-Item -LiteralPath $Meta -Force }
if (-not $NoRelaunch) { Start-Process -LiteralPath $Target }
Remove-Item -LiteralPath $PSCommandPath -Force
"""


def apply(pending: str, relaunch: bool = True, meta: str = "") -> None:
    """写自替换脚本并 detached 拉起；relaunch=False 时只覆盖不重启（静默更新用）。"""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("开发模式不支持自替换，请用打包后的 exe 测试更新")
    target = os.path.abspath(sys.executable)
    ps1 = os.path.join(DL_DIR, "updater.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as fh:
        fh.write(UPDATER_PS1)
    proc_name = os.path.splitext(os.path.basename(target))[0]
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
           "-New", pending, "-Target", target, "-ProcName", proc_name]
    if meta:
        cmd += ["-Meta", meta]
    if not relaunch:
        cmd += ["-NoRelaunch"]
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    log("自动更新", "自替换脚本已拉起（relaunch=%s），等待本进程退出后覆盖" % relaunch, "成功")


def stage(url: str, sha256: str, version: str) -> dict:
    """静默更新：下载校验后落盘登记，等退出时套用。"""
    path = download_and_verify(url, sha256)
    meta = {"version": str(version), "sha256": str(sha256 or "").lower(), "path": path}
    with open(PENDING_META, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return meta


def read_pending():
    """读取已校验待套用的更新包；文件缺失或哈希不符视为无效。"""
    try:
        with open(PENDING_META, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        path = str(meta.get("path", "") or "")
        if not os.path.exists(path):
            return None
        if not is_newer(str(meta.get("version", ""))):
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1048576), b""):
                digest.update(chunk)
        if meta.get("sha256") and digest.hexdigest() != str(meta["sha256"]).lower():
            return None
        return meta
    except Exception:
        return None


def arm_pending() -> bool:
    """启动时若有待套用更新包：挂一个等本进程退出后覆盖的脚本（不重启）。"""
    meta = read_pending()
    if not meta:
        return False
    try:
        apply(meta["path"], relaunch=False, meta=PENDING_META)
    except Exception as exc:
        log("自动更新", "挂载静默更新失败：%s" % exc, "警告")
        return False
    return True