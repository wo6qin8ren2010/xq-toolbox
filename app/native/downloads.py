"""内置下载引擎：断点续传、暂停/恢复、进度回传。"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import urllib.error

from .paths import log

CHUNK = 256 * 1024
MB = 1024.0 * 1024.0


class Downloader:
    def __init__(self, bus):
        self.bus = bus
        self._tasks = {}
        self._lock = threading.RLock()

    # ---------- 对外命令 ----------
    def start(self, task_id: str, name: str, save_path: str, url: str, notify_open: bool = True) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task["status"] in ("paused", "error"):
                task["status"] = "downloading"
                task["paused"].clear()
                task["cancelled"].clear()
                task["notify_open"] = notify_open
                threading.Thread(target=self._run, args=(task,), daemon=True).start()
                log("下载", "恢复任务：%s" % name)
                return
            if task and task["status"] in ("downloading", "merging"):
                return
            task = {
                "id": task_id,
                "name": name,
                "path": save_path,
                "url": url,
                "notify_open": notify_open,
                "paused": threading.Event(),
                "cancelled": threading.Event(),
                "status": "downloading",
                "down": 0.0,
                "total": 0.0,
            }
            self._tasks[task_id] = task
        log("下载", "开始任务：%s -> %s" % (name, save_path))
        threading.Thread(target=self._run, args=(task,), daemon=True).start()

    def pause(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return
        task["paused"].set()
        task["status"] = "paused"
        self._push(task, paused=True)
        log("下载", "已暂停：%s" % task["name"])

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.pop(task_id, None)
        if not task:
            return
        task["cancelled"].set()
        task["paused"].clear()
        log("下载", "已取消：%s" % task["name"])

    # ---------- 内部实现 ----------
    def _push(self, task, done=False, paused=False, error=None) -> None:
        payload = {
            "type": "dl_progress",
            "id": task["id"],
            "down": round(task["down"], 3),
            "total": round(task["total"], 3),
            "path": task["path"],
        }
        if done:
            payload["done"] = True
        if paused:
            payload["paused"] = True
        if error:
            payload["err"] = str(error)
        self.bus.push(json.dumps(payload, ensure_ascii=False))

    def _run(self, task) -> None:
        part = task["path"] + ".part"
        try:
            os.makedirs(os.path.dirname(task["path"]) or ".", exist_ok=True)
            offset = os.path.getsize(part) if os.path.isfile(part) else 0
            request = urllib.request.Request(task["url"], headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MixerTools/1.0",
                "Accept": "*/*",
            })
            if offset > 0:
                request.add_header("Range", "bytes=%d-" % offset)
            with urllib.request.urlopen(request, timeout=25) as response:
                status = getattr(response, "status", 200)
                length = int(response.headers.get("Content-Length") or 0)
                if status != 206 and offset > 0:
                    offset = 0
                total_bytes = offset + length if length else 0
                task["total"] = round(total_bytes / MB, 3) if total_bytes else 0.0
                task["down"] = round(offset / MB, 3)
                mode = "ab" if offset else "wb"
                last_push = 0.0
                downloaded = offset
                with open(part, mode) as fh:
                    while True:
                        if task["cancelled"].is_set():
                            return
                        if task["paused"].is_set():
                            self._push(task, paused=True)
                            while task["paused"].is_set() and not task["cancelled"].is_set():
                                time.sleep(0.15)
                            if task["cancelled"].is_set():
                                return
                            task["status"] = "downloading"
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        task["down"] = round(downloaded / MB, 3)
                        now = time.time()
                        if now - last_push >= 0.25:
                            last_push = now
                            self._push(task)

            if task["cancelled"].is_set():
                return
            if total_bytes and downloaded < total_bytes:
                raise IOError("连接提前中断")

            task["status"] = "merging"
            os.replace(part, task["path"])
            task["down"] = task["total"] or round(downloaded / MB, 3)
            task["status"] = "completed"
            self._push(task, done=True)
            log("下载", "下载完成：%s（%.2f MB）" % (task["name"], downloaded / MB), "成功")
            if task["notify_open"]:
                try:
                    os.startfile(task["path"])
                except Exception:
                    pass
        except Exception as exc:
            if task["cancelled"].is_set():
                return
            task["status"] = "error"
            self._push(task, error=exc)
            log("下载", "下载失败：%s（%s）" % (task["name"], exc), "错误")

    # ---------- 辅助 ----------
    @staticmethod
    def file_exists(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except Exception:
            return False

    @staticmethod
    def delete_file(path: str) -> bool:
        try:
            if os.path.isfile(path):
                os.remove(path)
                return True
            if os.path.isfile(path + ".part"):
                os.remove(path + ".part")
                return True
        except Exception as exc:
            log("下载", "删除文件失败：%s（%s）" % (path, exc), "警告")
        return False
