"""开机自启动：写当前用户注册表 Run 项，登录 Windows 后自动运行本程序。

只用 HKCU（不需要管理员权限）；关闭即删除该值。
exe 被挪位置后，启动时 heal() 会把启动项重写为当前路径。
"""
from __future__ import annotations

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "XQToolbox"


def exe_command() -> str:
    """启动命令行。打包后就是 exe 自身；开发模式用 pythonw 跑 main.py（无黑窗）。"""
    if getattr(sys, "frozen", False):
        return '"' + sys.executable + '"'
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if os.path.isfile(pyw):
        py = pyw
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return '"' + py + '" "' + main + '"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return bool(v)
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    try:
        if on:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, exe_command())
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, VALUE_NAME)
            except OSError:
                pass
        return True
    except OSError:
        return False


def heal() -> None:
    """已开启时把启动项重写为当前 exe 路径（程序被移动后自动纠正）。"""
    try:
        if is_enabled():
            set_enabled(True)
    except Exception:
        pass