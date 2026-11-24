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
import time
import urllib.parse
import urllib.request

from .library import DATA_DIR
from .paths import log

APP_VERSION = "1.0.12"
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


def _get_retry(url: str, timeout: float, tries: int = 2):
    """网络抖动时重试一次，避免偶发超时把整次检查判死。"""
    last = None
    for idx in range(max(1, tries)):
        try:
            return _get(url, timeout)
        except Exception as exc:
            last = exc
            if idx + 1 < tries:
                time.sleep(1.0)
    raise last


def check(base: str) -> dict:
    """拉 version.json 比对版本；返回 status=latest/available/nobase/error。"""
    base = str(base or "").strip()
    if not base:
        return {"status": "nobase", "msg": "尚未填写更新源直链前缀"}
    if not base.startswith(("http://", "https://")):
        return {"status": "error", "msg": "更新源地址需以 http:// 或 https:// 开头"}
    url = base.rstrip("/") + "/version.json"
    try:
        with _get_retry(url, 25, 2) as resp:
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
    """下载到 更新下载\\pending.exe 并校验 SHA256，返回落盘路径。失败自动重试一次。"""
    os.makedirs(DL_DIR, exist_ok=True)
    target = os.path.join(DL_DIR, "pending.exe")
    last = None
    for attempt in range(2):
        digest = hashlib.sha256()
        size = 0
        started = time.time()
        try:
            with _get(url, 120) as resp, open(target, "wb") as fh:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except Exception as exc:
            last = exc
            log("自动更新", "下载中断（第 %d 次）：%s" % (attempt + 1, exc), "警告")
            time.sleep(1.0)
            continue
        if size < 1024 * 1024:
            last = ValueError("下载文件过小（%d 字节），疑似直链失效" % size)
            log("自动更新", "下载文件异常：%d 字节，准备重试" % size, "警告")
            time.sleep(1.0)
            continue
        got = digest.hexdigest()
        if sha256 and got != sha256.lower():
            last = ValueError("SHA256 校验不一致，已丢弃下载包")
            log("自动更新", "SHA256 不一致（期望 %s，实际 %s），准备重试" % (str(sha256).lower(), got), "警告")
            time.sleep(1.0)
            continue
        log("自动更新", "更新包下载完成：%d 字节，用时 %.1fs，sha256=%s" % (size, time.time() - started, got), "成功")
        return target
    raise last if last is not None else ValueError("下载失败")


UPDATER_PS1 = """param([string]$New,[string]$Target,[string]$ProcName,[string]$Meta,[switch]$NoRelaunch,[string]$Sha)
$ErrorActionPreference = 'Stop'
# 全部使用 .NET 接口，不依赖任何 PowerShell 模块（避免 PSModulePath 异常导致 cmdlet 缺失）
if (-not [System.IO.File]::Exists($New)) { exit }
# 同一时间只允许一个替换脚本运行，避免重复覆盖与重复重启
$lock = "${Target}.updlock"
$haveLock = $false
for ($t = 0; $t -lt 2; $t++) {
    try {
        $lf = [System.IO.File]::Open($lock, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $lb = [System.Text.Encoding]::ASCII.GetBytes([string]$PID)
        $lf.Write($lb, 0, $lb.Length)
        $lf.Dispose()
        $haveLock = $true
        break
    } catch {
        $old = 0
        try { $old = [int]([System.IO.File]::ReadAllText($lock).Trim()) } catch {}
        $alive = $false
        if ($old -gt 0) { try { [System.Diagnostics.Process]::GetProcessById($old) | Out-Null; $alive = $true } catch {} }
        if ($alive) { break }
        try { [System.IO.File]::Delete($lock) } catch {}
    }
}
if (-not $haveLock) { exit }
for ($i = 0; $i -lt 7200; $i++) {
    if ([System.Diagnostics.Process]::GetProcessesByName($ProcName).Length -eq 0) { break }
    [System.Threading.Thread]::Sleep(1000)
}
[System.Threading.Thread]::Sleep(800)
$stage = "${Target}.new"
$backup = "${Target}.old"
$done = $false
for ($k = 0; $k -lt 30; $k++) {
    try {
        [System.IO.File]::Copy($New, $stage, $true)
        if ($Sha) {
            $fs = [System.IO.File]::OpenRead($stage)
            try {
                $sha256 = [System.Security.Cryptography.SHA256]::Create()
                $h = ([System.BitConverter]::ToString($sha256.ComputeHash($fs)) -replace '-', '').ToLower()
            } finally { $fs.Dispose() }
            if ($h -ne $Sha.ToLower()) { throw 'hash mismatch' }
        }
        if ([System.IO.File]::Exists($backup)) { [System.IO.File]::Delete($backup) }
        [System.IO.File]::Move($Target, $backup)
        [System.IO.File]::Move($stage, $Target)
        $done = $true
        break
    } catch {
        [System.Threading.Thread]::Sleep(2000)
    }
}
if ($done) {
    try { [System.IO.File]::Delete($New) } catch {}
    try { if ($Meta -and [System.IO.File]::Exists($Meta)) { [System.IO.File]::Delete($Meta) } } catch {}
    try { [System.IO.File]::Delete($backup) } catch {}
    if (-not $NoRelaunch) {
        # 覆盖成功后自动拉起新版，工作目录设为程序所在目录
        try {
            $psi = [System.Diagnostics.ProcessStartInfo]::new()
            $psi.FileName = $Target
            $psi.WorkingDirectory = [System.IO.Path]::GetDirectoryName($Target)
            $psi.UseShellExecute = $true
            [System.Diagnostics.Process]::Start($psi) | Out-Null
        } catch {}
    }
} else {
    try { if ([System.IO.File]::Exists($stage)) { [System.IO.File]::Delete($stage) } } catch {}
}
try { [System.IO.File]::Delete($lock) } catch {}
try { [System.IO.File]::Delete($PSCommandPath) } catch {}
"""


def apply(pending: str, relaunch: bool = True, meta: str = "", sha: str = "") -> None:
    """写自替换脚本并 detached 拉起；relaunch=False 时只覆盖不重启（静默更新用）。"""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("开发模式不支持自替换，请用打包后的 exe 测试更新")
    target = os.path.abspath(sys.executable)
    ps1 = os.path.join(DL_DIR, "updater.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as fh:
        fh.write(UPDATER_PS1)
    proc_name = os.path.splitext(os.path.basename(target))[0]
    # CREATE_NO_WINDOW：子进程带独立隐藏控制台，主进程退出后被系统一并回收
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    cmd = ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", ps1,
           "-New", pending, "-Target", target, "-ProcName", proc_name]
    if meta:
        cmd += ["-Meta", meta]
    if sha:
        cmd += ["-Sha", sha]
    if not relaunch:
        cmd += ["-NoRelaunch"]
    # 剔除继承来的 PSModulePath，避免子 PowerShell 加载到不匹配的模块目录
    env = dict(os.environ)
    env.pop("PSModulePath", None)
    subprocess.Popen(cmd, creationflags=flags, close_fds=True, env=env)
    log("自动更新", "自替换脚本已拉起（relaunch=%s），退出后覆盖%s" % (relaunch, "并自动重启新版" if relaunch else ""), "成功")


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


_ARMED_SHA = ""


def arm_pending() -> bool:
    """有待套用更新包时：挂一个等本进程退出后覆盖并自动重启新版的脚本。"""
    global _ARMED_SHA
    meta = read_pending()
    if not meta:
        return False
    sha = str(meta.get("sha256", "") or "")
    if sha and sha == _ARMED_SHA:
        return True  # 同一个包已挂载，避免重复拉起导致重复重启
    try:
        apply(meta["path"], relaunch=True, meta=PENDING_META, sha=sha)
    except Exception as exc:
        log("自动更新", "挂载静默更新失败：%s" % exc, "警告")
        return False
    _ARMED_SHA = sha
    return True