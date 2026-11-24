"""VST 插件扫描、清理与定位。"""
from __future__ import annotations

import json
import os
import subprocess
import time

from .paths import log

VST3_DIRS = [
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Common Files", "VST3"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Common Files", "VST3"),
]
VST2_DIRS = [
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "VSTPlugins"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "VSTPlugins"),
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Steinberg", "VSTPlugins"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Steinberg", "VSTPlugins"),
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Common Files", "VST2"),
]

MAX_DEPTH = 5


def _walk_vst3(root: str, items: list, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        return
    try:
        entries = list(os.scandir(root))
    except Exception:
        return
    for entry in entries:
        try:
            if entry.is_file():
                if entry.name.lower().endswith(".vst3"):
                    items.append(_item(entry.name, "VST3", entry.path))
            elif entry.is_dir(follow_symlinks=False):
                if entry.name.lower().endswith(".vst3"):
                    items.append(_item(entry.name, "VST3", entry.path))
                else:
                    _walk_vst3(entry.path, items, depth + 1)
        except Exception:
            continue


def _walk_vst2(root: str, items: list, depth: int = 0) -> None:
    if depth > 2:
        return
    try:
        entries = list(os.scandir(root))
    except Exception:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.name.lower().endswith((".dll", ".vst")):
                items.append(_item(entry.name, "VST2", entry.path))
            elif entry.is_dir(follow_symlinks=False):
                _walk_vst2(entry.path, items, depth + 1)
        except Exception:
            continue


def _item(name: str, fmt: str, path: str) -> dict:
    is_link = os.path.islink(path)
    actual = os.path.realpath(path) if is_link else path
    return {
        "name": name,
        "format": fmt,
        "path": path,
        "actualPath": actual,
        "isLink": bool(is_link),
    }


def scan() -> list:
    started = time.time()
    items = []
    for root in VST3_DIRS:
        if os.path.isdir(root):
            _walk_vst3(root, items)
    for root in VST2_DIRS:
        if os.path.isdir(root):
            _walk_vst2(root, items)
    items.sort(key=lambda x: (0 if x["format"] == "VST3" else 1, x["name"].lower()))
    log("插件扫描", "识别到 %d 个插件，耗时 %.0f ms" % (len(items), (time.time() - started) * 1000.0), "成功")
    return items


def scan_message() -> str:
    return "vst_plugins_result|success|" + json.dumps(scan(), ensure_ascii=False)


def _recycle(path: str) -> bool:
    """删除到回收站，避免误删无法恢复。"""
    if not os.path.exists(path):
        return False
    script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic;"
        "$p = [System.IO.Path]::GetFullPath('%s');"
        "if (Test-Path -LiteralPath $p -PathType Container) {"
        "  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p,'OnlyErrorDialogs','SendToRecycleBin')"
        "} else {"
        "  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p,'OnlyErrorDialogs','SendToRecycleBin')"
        "}" % path.replace("'", "''")
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25, creationflags=0x08000000,
        )
        return completed.returncode == 0
    except Exception as exc:
        log("插件清理", "回收失败：%s（%s）" % (path, exc), "错误")
        return False


def delete_one(path: str) -> str:
    ok = _recycle(path)
    log("插件清理", "%s：%s" % ("已删除" if ok else "删除失败", path), "成功" if ok else "错误")
    return "vst_plugin_delete_result|%s|%s" % ("success" if ok else "failed", path)


def open_location(path: str) -> str:
    try:
        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return "vst_plugin_location_result|success"
    except Exception as exc:
        log("插件清理", "打开位置失败：%s" % exc, "错误")
    return "vst_plugin_location_result|failed|%s" % path


def cleanup_plugins(bus) -> None:
    """清理全部插件（带确认，逐步回传进度）。"""
    items = scan()
    bus.push("plugin_cleanup_result|plugins|running")
    deleted = 0
    failed = 0
    for item in items:
        if os.path.exists(item["path"]):
            ok = _recycle(item["path"])
        else:
            ok = False
        if ok:
            deleted += 1
        else:
            failed += 1
        bus.push("plugin_cleanup_item|plugins|%s|%s|%s" % (
            "success" if ok else "error", item["format"], item["path"]))
    bus.push("plugin_cleanup_result|plugins|%s|deleted=%d|failed=%d" % (
        "success" if failed == 0 else "failed", deleted, failed))
    log("插件清理", "清理完成：成功 %d，失败 %d" % (deleted, failed), "成功" if failed == 0 else "警告")


def cleanup_waves(bus) -> None:
    bus.push("plugin_cleanup_result|waves|running")
    candidates = []
    for root in VST3_DIRS + VST2_DIRS:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.scandir(root):
                if "waveshell" in entry.name.lower() or entry.name.lower() == "waves":
                    candidates.append((entry.path, "VST3" if entry.name.lower().endswith(".vst3") else "VST2"))
        except Exception:
            continue
    deleted = 0
    failed = 0
    for path, fmt in candidates:
        ok = _recycle(path)
        deleted += 1 if ok else 0
        failed += 0 if ok else 1
        bus.push("plugin_cleanup_item|waves|%s|%s|%s" % ("success" if ok else "error", fmt, path))
    bus.push("plugin_cleanup_result|waves|%s|deleted=%d|failed=%d" % (
        "success" if failed == 0 else "failed", deleted, failed))
    log("插件清理", "Waves 清理完成：成功 %d，失败 %d" % (deleted, failed), "成功" if failed == 0 else "警告")
