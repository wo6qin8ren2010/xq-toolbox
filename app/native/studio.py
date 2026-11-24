"""Studio One 相关工具：进程检测、工程解密 / 降级的调用入口。"""
from __future__ import annotations

import json
import os
import subprocess

import psutil

from . import paths

TARGETS = ("studio one", "studio pro", "studioone")
HELPER_NAMES = ("StudioProjectHelper.exe",)

OUTPUT_DIR = "studio_output"


def _helper_path() -> str:
    candidates = []
    runtime = os.path.join(paths.data_dir(), "runtime")
    if os.path.isdir(runtime):
        for name in os.listdir(runtime):
            if name.lower().startswith("studioprojecthelper") and name.lower().endswith(".exe"):
                candidates.append(os.path.join(runtime, name))
    original_runtime = os.path.join(paths.original_data_dir(), "runtime")
    if os.path.isdir(original_runtime):
        for name in os.listdir(original_runtime):
            if name.lower().startswith("studioprojecthelper") and name.lower().endswith(".exe"):
                candidates.append(os.path.join(original_runtime, name))
    return candidates[0] if candidates else ""


def output_dir() -> str:
    path = os.path.join(paths.data_dir(), OUTPUT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def processes_message() -> str:
    items = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if any(key in name for key in TARGETS):
                items.append({"pid": proc.info["pid"], "name": proc.info["name"]})
        except Exception:
            continue
    return "studio_hook_processes|" + json.dumps(items, ensure_ascii=False)


def _delegate(kind: str, path: str, version: str, bus) -> str:
    helper = _helper_path()
    prefix = "studio_downgrade_result|" if kind == "downgrade" else "studio_file_decrypt_result|"
    if not helper:
        payload = {
            "ok": False,
            "message": "未找到 StudioProjectHelper 外挂组件；工程解密/降级依赖该内核组件",
        }
        return prefix + json.dumps(payload, ensure_ascii=False)
    arguments = [helper, path]
    if kind == "downgrade" and version:
        arguments.append(version)
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, timeout=180)
        output = (completed.stdout or "").strip().splitlines()
        last = output[-1] if output else ""
        try:
            payload = json.loads(last)
        except Exception:
            payload = {"ok": completed.returncode == 0, "message": last or completed.stderr.strip()[:200]}
        return prefix + json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        paths.log("工程处理", "调用外挂组件失败：%s" % exc, "错误")
        return prefix + json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False)


def decrypt_start(path: str, bus) -> str:
    paths.log("文件解密", "开始处理工程：%s" % path)
    bus.push("studio_file_decrypt_progress|正在解析工程文件...")
    return _delegate("decrypt", path, "", bus)


def downgrade_start(path: str, version: str, bus) -> str:
    paths.log("工程降级", "开始降级：%s -> %s" % (path, version))
    bus.push("studio_downgrade_progress|正在降级工程...")
    return _delegate("downgrade", path, version, bus)


def hook_result(action: str) -> str:
    messages = {
        "inject": "不内置 Hook 注入模块，无法挂载目标进程",
        "extract": "不内置 Hook 注入模块，无法从进程提取工程",
    }
    payload = {"ok": False, "status": "failed", "message": messages.get(action, "未实现的操作"), "targets": []}
    return "studio_hook_result|" + json.dumps(payload, ensure_ascii=False)
