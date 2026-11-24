"""原生 -> 前端 的消息队列（由前端轮询拉取，避免跨线程调用 UI）。"""
from __future__ import annotations

import collections
import json
import threading


class PushBus:
    def __init__(self, limit: int = 500):
        self._queue = collections.deque(maxlen=limit)
        self._lock = threading.Lock()
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def push(self, message: str) -> None:
        if isinstance(message, (dict, list)):
            message = json.dumps(message, ensure_ascii=False)
        with self._lock:
            self._queue.append(str(message))

    def poll(self, limit: int = 40) -> list:
        items = []
        with self._lock:
            while self._queue and len(items) < limit:
                items.append(self._queue.popleft())
        return items
