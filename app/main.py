# -*- coding: utf-8 -*-
"""小庆工具箱入口：Python 原生后端 + WebView2 前端。

参照桌面程序「调音师助理.exe」的界面与功能结构，
原生层由原生消息协议（window.chrome.webview.postMessage）驱动。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback

_T0 = time.perf_counter()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import webview  # noqa: E402

from native.bridge import Bridge  # noqa: E402
from native import local_auth  # noqa: E402
from native import seed  # noqa: E402
from native.paths import log, ui_dir  # noqa: E402
from native.push import PushBus  # noqa: E402
from native import sysinfo  # noqa: E402
from native import music  # noqa: E402

_T_IMPORT = time.perf_counter()


def _dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _run_admin_mode() -> None:
    """`--admin`：不启动主界面，改为启动“自建库网页后台”服务。"""
    import runpy
    import traceback

    logpath = os.path.join(os.environ.get("TEMP") or ".", "admin_mode.log")

    def _w(msg: str) -> None:
        try:
            with open(logpath, "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except Exception:
            pass

    base = getattr(sys, "_MEIPASS", os.path.dirname(BASE_DIR))
    script = os.path.join(base, "admin", "server.py")
    _w("start argv=%r base=%s exists=%s" % (sys.argv, base, os.path.exists(script)))
    sys.argv = [script] + [a for a in sys.argv[1:] if a != "--admin"]
    try:
        runpy.run_path(script, run_name="__main__")
    except BaseException:
        _w("failed:\n" + traceback.format_exc())
        raise
    _w("exit normally")


def main() -> None:
    if "--admin" in sys.argv:
        _run_admin_mode()
        return
    _dpi_aware()
    local_auth.ensure_default()
    seed.ensure_seed()
    # 系统信息后台采集先跑起来，首屏就有真实数值，不用等子进程
    sysinfo.prime()
    # 系统音乐会话同样先跑起来，侧栏音乐卡首屏就有内容
    music.prime()
    bus = PushBus()
    bridge = Bridge(bus)

    window = webview.create_window(
        "小庆工具箱",
        url=os.path.join(ui_dir(), "index.html"),
        js_api=bridge,
        width=1100,
        height=804,
        min_size=(980, 680),
        frameless=True,
        easy_drag=False,
        background_color="#202020",
    )
    bus.attach(window)
    bridge.attach(window)

    def _prepare_glass(*_args):
        # 窗口句柄 + WebView2 控件就绪后才能开磨砂
        try:
            from native import glass
            ok = glass.prepare(window)
            log("界面", "磨砂背景已就绪" if ok else "磨砂背景等待窗口就绪",
                "信息" if ok else "警告")
        except Exception as exc:
            log("界面", "磨砂背景初始化失败：%s" % exc, "警告")

    try:
        window.events.loaded += _prepare_glass
    except Exception as exc:
        log("界面", "挂接页面加载事件失败：%s" % exc, "警告")

    try:
        window.events.shown += _prepare_glass
    except Exception:
        pass

    def _after_start():
        _prepare_glass()

    log("启动", "小庆工具箱 已启动")
    log("启动", "启动耗时：解包+导入 %.0f ms，初始化+建窗 %.0f ms" % (
        (_T_IMPORT - _T0) * 1000, (time.perf_counter() - _T_IMPORT) * 1000))
    debug = os.environ.get("MIXER_DEBUG") == "1"
    try:
        webview.start(debug=debug, func=_after_start)
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("启动", "启动失败：\n" + traceback.format_exc(), "错误")
        ctypes.windll.user32.MessageBoxW(0, traceback.format_exc(limit=3), "小庆工具箱 启动失败", 0x10)
        raise
