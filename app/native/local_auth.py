"""本机访问密码：PBKDF2-HMAC-SHA256 哈希存本机，不写明文到 config.ini。

文件：%LOCALAPPDATA%\\MixerToolsRebuild\\本机密码.json
格式：{"algo": "pbkdf2_sha256", "iterations": 200000, "salt": b64, "hash": b64, "updated": ...}
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time

from .paths import data_dir, log

_ITERATIONS = 200000
_MIN_LEN = 4
_MAX_LEN = 64
_MAX_FAILS = 5
_LOCK_SECONDS = 30
DEFAULT_PASSWORD = "45787107"

_lock = threading.Lock()
_fails = {"count": 0, "since": 0.0, "until": 0.0}


def _file() -> str:
    return os.path.join(data_dir(), "本机密码.json")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 32)


def _load() -> dict:
    try:
        with open(_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not data.get("salt") or not data.get("hash"):
            return {}
        return data
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log("本机密码", "读取密码文件失败：%s" % exc, "警告")
        return {}


def _save(data: dict) -> None:
    path = _file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def is_enabled() -> bool:
    return bool(_load())


def ensure_default() -> None:
    """首次运行自动启用默认查看密码（只补建，不覆盖用户已设的密码）。"""
    if is_enabled():
        return
    ok, message = set_password("", DEFAULT_PASSWORD)
    if ok:
        log("本机密码", "首次运行：已启用默认查看密码（%s）" % DEFAULT_PASSWORD, "信息")
    else:
        log("本机密码", "启用默认查看密码失败：%s" % message, "错误")


def _reset_window(now: float) -> None:
    if now - _fails["since"] > 60:
        _fails["count"] = 0
        _fails["since"] = now


def check(password: str) -> tuple:
    """返回 (是否通过, 状态码, 剩余秒数)。状态码：1 通过 / 0 错误 / 2 锁定 / 3 未就绪。"""
    data = _load()
    if not data:
        return True, "1", 0
    now = time.time()
    with _lock:
        _reset_window(now)
        if _fails["until"] > now:
            return False, "2", int(_fails["until"] - now) + 1
        try:
            salt = _unb64(data.get("salt", ""))
            iterations = int(data.get("iterations") or _ITERATIONS)
            expected = _unb64(data.get("hash", ""))
        except (binascii.Error, ValueError):
            return False, "3", 0
        actual = _hash(str(password or ""), salt, iterations)
        if hmac.compare_digest(actual, expected):
            _fails["count"] = 0
            return True, "1", 0
        _fails["count"] += 1
        if not _fails["since"]:
            _fails["since"] = now
        if _fails["count"] >= _MAX_FAILS:
            _fails["until"] = now + _LOCK_SECONDS
            _fails["count"] = 0
            log("本机密码", "连续输错 %d 次，锁定 %d 秒" % (_MAX_FAILS, _LOCK_SECONDS), "警告")
            return False, "2", _LOCK_SECONDS
        log("本机密码", "访问密码错误（第 %d 次）" % _fails["count"], "用户操作")
        return False, "0", 0


def set_password(old: str, new: str) -> tuple:
    """设置或修改密码。返回 (ok, 提示文本)。"""
    new = str(new or "")
    if not new:
        return False, "新密码不能为空"
    if len(new) < _MIN_LEN:
        return False, "新密码至少 %d 位" % _MIN_LEN
    if len(new) > _MAX_LEN:
        return False, "新密码最多 %d 位" % _MAX_LEN
    if any(ch in new for ch in "\r\n"):
        return False, "新密码不能包含换行"
    data = _load()
    if data:
        ok, _code, _wait = check(old)
        if not ok:
            return False, "当前密码不正确"
    salt = os.urandom(16)
    payload = {
        "algo": "pbkdf2_sha256",
        "iterations": _ITERATIONS,
        "salt": _b64(salt),
        "hash": _b64(_hash(new, salt, _ITERATIONS)),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        _save(payload)
    except Exception as exc:
        log("本机密码", "保存密码失败：%s" % exc, "错误")
        return False, "写入密码文件失败"
    log("本机密码", ("已修改本机访问密码" if data else "已设置本机访问密码"), "用户操作")
    return True, ("密码已更新" if data else "密码已设置")


def clear(old: str) -> tuple:
    """清除密码（已设置时必须校验当前密码）。"""
    data = _load()
    if not data:
        return True, "当前没有设置密码"
    ok, code, wait = check(old)
    if not ok:
        return False, ("尝试次数过多，请 %d 秒后再试" % wait) if code == "2" else "当前密码不正确"
    try:
        os.remove(_file())
    except FileNotFoundError:
        pass
    except Exception as exc:
        log("本机密码", "清除密码失败：%s" % exc, "错误")
        return False, "删除密码文件失败"
    log("本机密码", "已清除本机访问密码", "用户操作")
    return True, "密码已清除"
