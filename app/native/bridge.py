"""原生桥：把前端 postMessage 的命令分发到具体功能模块。"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from . import audio, backup, cloud, config, lestore, library, studio, sysinfo, system_tools, update, vst
from .downloads import Downloader
from .paths import log

MAX_WORKERS = 8


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class Bridge:
    def __init__(self, bus):
        self.bus = bus
        self.window = None
        self.downloader = Downloader(bus)
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bridge")
        self._overview_at = 0.0
        self._overview_lock = threading.Lock()
        self._started = False
        self._lock_announced = False

    def attach(self, window) -> None:
        self.window = window
        if not self._started:
            self._started = True
            threading.Timer(0.6, self._announce).start()

    def _announce(self) -> None:
        self.bus.push("admin_status|%d" % (1 if is_admin() else 0))

    def _announce_lock_once(self) -> None:
        """首条前端命令到达时（页面监听器已就绪）再通知一次上锁状态。"""
        if self._lock_announced:
            return
        self._lock_announced = True
        try:
            from . import local_auth
            if not local_auth.is_enabled():
                return
        except Exception:
            return
        log("本机密码", "检测到已设置访问密码，通知前端进入上锁状态")
        threading.Timer(0.25, lambda: self.bus.push("auth_required")).start()

    def poll(self, limit: int = 40) -> list:
        """前端每 70ms 拉取一次原生队列。"""
        return self.bus.poll(int(limit) if limit else 40)

    # ------------------------------------------------------------------
    # pywebview js_api 入口
    # ------------------------------------------------------------------
    def post_message(self, raw: str) -> str:
        try:
            raw = str(raw or "")
            command, sep, rest = raw.partition("|")
            command = command.strip()
            handler = getattr(self, "_cmd_" + command, None)
            if handler is None:
                if command:
                    log("原生桥", "未实现的命令：%s" % command, "警告")
                return ""
            if sep:
                args = rest.split("|")
            else:
                args = []
            self._announce_lock_once()
            self._pool.submit(self._safe_call, command, handler, args, raw)
        except Exception as exc:
            log("原生桥", "命令分发失败：%s（%s）" % (raw[:120], exc), "错误")
        return ""

    def _safe_call(self, command, handler, args, raw) -> None:
        try:
            result = handler(args, raw)
            if isinstance(result, str) and result:
                self.bus.push(result)
        except Exception as exc:
            log("原生桥", "命令 %s 执行异常：%s" % (command, exc), "错误")

    # ------------------------------------------------------------------
    # 窗口控制
    # ------------------------------------------------------------------
    def _cmd_drag(self, args, raw):
        """标题栏拖拽：按进程句柄定位窗口，自己接管移动循环。

        不能用 ReleaseCapture + WM_NCLBUTTONDOWN 的老写法：
        WebView2 的鼠标捕获属于 Chromium 的 UI 线程，Python 线程里
        ReleaseCapture() 释放不掉，消息会被丢弃，窗口就拖不动。
        """
        try:
            hwnd = self.find_window()
            if not hwnd:
                log("窗口", "拖动失败：未找到本程序窗口句柄", "警告")
                return ""
            log("窗口", "标题栏拖动开始（hwnd=%s）" % hwnd)
            threading.Thread(target=self._drag_loop, args=(hwnd,), daemon=True).start()
        except Exception as exc:
            log("窗口", "拖动失败：%s" % exc, "错误")
        return ""

    def find_window(self):
        """只认本进程的可见顶层窗口，避免和原版同名窗口混淆。"""
        try:
            user32 = ctypes.windll.user32
            pid = os.getpid()
            found = []

            EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.GetWindowTextLengthW(hwnd) <= 0:
                    return True
                owner = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == pid:
                    found.append(hwnd)
                    return False
                return True

            user32.EnumWindows(EnumProc(callback), 0)
            return found[0] if found else 0
        except Exception as exc:
            log("窗口", "查找窗口句柄失败：%s" % exc, "警告")
            return 0

    def _drag_loop(self, hwnd) -> None:
        user32 = ctypes.windll.user32

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        # 最大化状态下拖动：先还原，再按比例把窗口摆到鼠标下方
        if user32.IsZoomed(hwnd):
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            screen_w = rect.right - rect.left
            screen_h = rect.bottom - rect.top
            cursor = POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.05)
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            ratio_x = (cursor.x - rect.left) / float(screen_w) if screen_w else 0.5
            ratio_y = (cursor.y - rect.top) / float(screen_h) if screen_h else 0.02
            new_left = cursor.x - int(width * ratio_x)
            new_top = cursor.y - int(height * min(max(ratio_y, 0.0), 0.12))
            user32.SetWindowPos(hwnd, 0, new_left, new_top, 0, 0, 0x0001 | 0x0004 | 0x0010)

        start_cursor = POINT()
        user32.GetCursorPos(ctypes.byref(start_cursor))
        start_rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(start_rect))

        debug = os.environ.get("XQ_DRAG_DEBUG") == "1"
        if debug:
            log("窗口", "拖动循环启动 hwnd=%r rect=%s,%s 光标=%s,%s"
                % (hwnd, start_rect.left, start_rect.top, start_cursor.x, start_cursor.y))

        for i in range(12000):  # 最长约 2 分钟，松开即结束
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):
                if debug and i < 5:
                    log("窗口", "第%s次轮询：左键已松开，退出" % i)
                break
            cursor = POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            dx = cursor.x - start_cursor.x
            dy = cursor.y - start_cursor.y
            if dx or dy:
                ok = user32.SetWindowPos(
                    hwnd, 0,
                    start_rect.left + dx, start_rect.top + dy,
                    0, 0, 0x0001 | 0x0004 | 0x0010,  # NOSIZE | NOZORDER | NOACTIVATE
                )
                if debug and i < 6:
                    actual = RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(actual))
                    log("窗口", "第%s次：目标=%s,%s 实际=%s,%s SetWindowPos=%s"
                        % (i, start_rect.left + dx, start_rect.top + dy,
                           actual.left, actual.top, bool(ok)))
            time.sleep(0.008)
        log("窗口", "标题栏拖动结束")

    def _cmd_minimize(self, args, raw):
        if self.window:
            self.window.minimize()
        return ""

    def _cmd_maximize(self, args, raw):
        if not self.window:
            return ""
        try:
            if self._is_maximized():
                self.window.restore()
            else:
                self.window.maximize()
        except Exception:
            pass
        return ""

    def _is_maximized(self) -> bool:
        try:
            hwnd = self.find_window()
            return bool(hwnd) and bool(ctypes.windll.user32.IsZoomed(hwnd))
        except Exception:
            return False

    def _cmd_close(self, args, raw):
        if self.window:
            self.window.destroy()
        return ""

    # ------------------------------------------------------------------
    # 基础交互
    # ------------------------------------------------------------------
    def _cmd_set_language(self, args, raw):
        log("界面", "语言切换：%s" % (args[0] if args else "zh"))
        return ""

    def _cmd_user_click(self, args, raw):
        action = args[0] if args else ""
        target = "|".join(args[1:])
        log("用户操作", "动作: %s%s" % (action, (("，点击对象: " + target) if target else "")), "用户操作")
        return ""

    def _cmd_password_ime(self, args, raw):
        return ""

    def _cmd_check_password(self, args, raw):
        from . import local_auth
        ok, code, wait = local_auth.check(args[0] if args else "")
        if ok:
            return "password_result|1"
        if code == "2":
            return "password_result|2|%d" % max(1, int(wait))
        return "password_result|0"

    @staticmethod
    def _b64_arg(text) -> str:
        try:
            return base64.b64decode(str(text or "").encode("ascii")).decode("utf-8")
        except Exception:
            return ""

    def _cmd_local_password_lock_state(self, args, raw):
        """前端启动/进入设置页时询问：本机是否已设置访问密码。"""
        from . import local_auth
        return "local_password_lock_state|%d" % (1 if local_auth.is_enabled() else 0)

    def _cmd_local_password_set(self, args, raw):
        from . import local_auth
        old = self._b64_arg(args[0]) if args else ""
        new = self._b64_arg(args[1]) if len(args) > 1 else ""
        ok, message = local_auth.set_password(old, new)
        return "local_password_set_result|%s|%s" % ("success" if ok else "failed", message)

    def _cmd_local_password_clear(self, args, raw):
        from . import local_auth
        old = self._b64_arg(args[0]) if args else ""
        ok, message = local_auth.clear(old)
        return "local_password_clear_result|%s|%s" % ("success" if ok else "failed", message)

    def _cmd_report_decrypted_urls(self, args, raw):
        log("资源统计", "上报可用下载链接 %d 条" % len([a for a in args if a]))
        return ""

    # ------------------------------------------------------------------
    # 配置 / 历史
    # ------------------------------------------------------------------
    def _cmd_load_config(self, args, raw):
        return "config_data|" + config.load_config_string()

    def _cmd_save_config(self, args, raw):
        config.save_config_string(raw[len("save_config|"):] if raw.startswith("save_config|") else (args[0] if args else ""))
        return "route_status|auto|联通|移动"

    def _cmd_load_history(self, args, raw):
        return "history_data|" + config.load_history_string()

    def _cmd_save_history(self, args, raw):
        config.save_history_string(raw[len("save_history|"):] if raw.startswith("save_history|") else (args[0] if args else ""))
        return ""

    # ------------------------------------------------------------------
    # 系统概述
    # ------------------------------------------------------------------
    def _cmd_request_system_overview(self, args, raw):
        with self._overview_lock:
            now = time.time()
            if now - self._overview_at < 0.9:
                return ""
            self._overview_at = now
        return sysinfo.as_message()

    def _cmd_monitor_snapshot(self, args, raw):
        """侧栏 HUD：实时 CPU / 显卡温度与占用。"""
        lock = getattr(self, "_monitor_lock", None)
        if lock is None:
            import threading
            lock = threading.Lock()
            self._monitor_lock = lock
            self._monitor_at = 0.0
        with lock:
            now = time.time()
            if now - getattr(self, "_monitor_at", 0.0) < 0.8:
                return ""
            self._monitor_at = now
        return sysinfo.monitor_message()

    # ------------------------------------------------------------------
    # 音频
    # ------------------------------------------------------------------
    def _cmd_audio_devices_request(self, args, raw):
        return audio.devices_message()

    def _cmd_audio_sessions_request(self, args, raw):
        return audio.sessions_message()

    def _cmd_audio_set_default(self, args, raw):
        kind = args[0] if args else "render"
        device_id = "|".join(args[1:])
        return audio.set_default(kind, device_id)

    def _cmd_audio_set_enabled(self, args, raw):
        if len(args) < 2:
            return "audio_set_enabled_result|failed"
        enabled = args[0] == "1"
        return audio.set_enabled("|".join(args[1:]), enabled)

    def _cmd_audio_set_sample_rate(self, args, raw):
        return audio.set_sample_rate(args[0] if args else "48000")

    def _cmd_audio_capture_lock(self, args, raw):
        enabled = args[0] == "1" if args else False
        value = int(args[1]) if len(args) > 1 and args[1].isdigit() else 100
        return audio.set_capture_lock(enabled, value)

    def _cmd_audio_capture_volume(self, args, raw):
        value = int(args[0]) if args and args[0].isdigit() else 100
        return audio.set_capture_volume(value)

    def _cmd_audio_capture_lock_status_request(self, args, raw):
        return audio.capture_status()

    def _cmd_audio_session_volume(self, args, raw):
        if len(args) >= 2:
            try:
                audio.set_session_volume(int(args[0]), float(args[1]))
            except Exception:
                pass
        return ""

    # ------------------------------------------------------------------
    # 系统命令面板
    # ------------------------------------------------------------------
    def _cmd_open_system_tool(self, args, raw):
        system_tools.open_tool(args[0] if args else "")
        return ""

    # ------------------------------------------------------------------
    # 文件 / 目录
    # ------------------------------------------------------------------
    def _cmd_open_file(self, args, raw):
        path = "|".join(args)
        try:
            if os.path.exists(path):
                os.startfile(path)
        except Exception as exc:
            log("文件", "打开失败：%s（%s）" % (path, exc), "警告")
        return ""

    def _cmd_open_external_url(self, args, raw):
        url = "|".join(args)
        if url.lower().startswith(("http://", "https://")):
            webbrowser.open(url)
        return ""

    def _cmd_navigate_main(self, args, raw):
        url = "|".join(args)
        if url.lower().startswith(("http://", "https://")):
            webbrowser.open(url)
        return ""

    def _cmd_open_embedded_website(self, args, raw):
        url = "|".join(args)
        if url.lower().startswith(("http://", "https://")):
            log("内嵌网页", "以默认浏览器打开：%s" % url)
            webbrowser.open(url)
        return ""

    def _cmd_close_embedded_website(self, args, raw):
        return ""

    def _cmd_open_download_folder(self, args, raw):
        path = config.get_download_path()
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except Exception:
            pass
        return ""

    def _cmd_select_folder(self, args, raw):
        if not self.window:
            return ""
        try:
            import webview
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                path = result[0] if isinstance(result, (list, tuple)) else result
                return "selected_dir:" + str(path)
        except Exception as exc:
            log("文件", "选择目录失败：%s" % exc, "错误")
        return ""

    def _cmd_delete_file(self, args, raw):
        self.downloader.delete_file("|".join(args))
        return ""

    def _cmd_check_file_exists(self, args, raw):
        if len(args) < 2:
            return ""
        task_id = args[0]
        path = "|".join(args[1:])
        return "file_exists_result|%s|%d" % (task_id, 1 if self.downloader.file_exists(path) else 0)

    def _cmd_clear_metadata_cache(self, args, raw):
        target = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MixerToolsRebuild", "metadata_cache.dat")
        try:
            if os.path.isfile(target):
                os.remove(target)
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    def _start_download(self, args, raw, notify_open: bool):
        if len(args) < 4:
            return ""
        task_id, name, save_path = args[0], urllib.parse.unquote(args[1]), args[2]
        url = urllib.parse.unquote("|".join(args[3:]))
        self.downloader.start(task_id, name, save_path, url, notify_open)
        return ""

    def _cmd_start_download(self, args, raw):
        return self._start_download(args, raw, True)

    def _cmd_start_download_no_open(self, args, raw):
        return self._start_download(args, raw, False)

    def _cmd_pause_download(self, args, raw):
        if args:
            self.downloader.pause(args[0])
        return ""

    def _cmd_cancel_download(self, args, raw):
        if args:
            self.downloader.cancel(args[0])
        return ""

    def _cmd_open_completed_download(self, args, raw):
        # 前端在“下载完成后自动打开文件”开启时，对 .exe/.vbs 下发该命令：
        # 参数为 |任务ID|保存路径|，与 open_file 的语义一致（用系统默认方式打开）。
        if len(args) < 2:
            return ""
        path = args[1]
        try:
            if os.path.isfile(path):
                os.startfile(path)  # noqa: S606 - 与原版一致的“打开已下载文件”
                log("下载", "已打开下载完成的文件：%s" % os.path.basename(path), "成功")
            else:
                log("下载", "下载完成但文件不存在：%s" % path, "警告")
        except Exception as exc:
            log("下载", "打开下载完成的文件失败：%s" % exc, "警告")
        return ""

    def _cmd_restart_download_route(self, args, raw):
        if len(args) < 5:
            return ""
        route, task_id, name, save_path = args[0], args[1], urllib.parse.unquote(args[2]), args[3]
        url = "|".join(args[4:])
        self.downloader.start(task_id, name, save_path, url, True)
        return "download_route_result|%s|started|%s" % (task_id, url)

    def _cmd_speed_test(self, args, raw):
        if len(args) < 2:
            return ""
        test_id, url = args[0], "|".join(args[1:])
        started = time.time()
        try:
            request = urllib.request.Request(url, headers={"Range": "bytes=0-524287", "User-Agent": "MixerTools/1.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read(512 * 1024)
            elapsed = max(0.001, time.time() - started)
            kbps = len(data) / 1024.0 / elapsed
            payload = {"id": test_id, "ok": True, "bytes": len(data), "ms": int(elapsed * 1000), "kbps": round(kbps, 1)}
        except Exception as exc:
            payload = {"id": test_id, "ok": False, "error": str(exc)}
        return "speed_test_result|" + json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 云端缓存
    # ------------------------------------------------------------------
    def _cmd_fetch_cloud(self, args, raw):
        return cloud.message("cloud")

    def _cmd_fetch_dashboard_cloud(self, args, raw):
        return cloud.message("dashboard")

    def _cmd_fetch_driver_cloud(self, args, raw):
        return cloud.message("driver")

    def _cmd_fetch_shortcut_cloud(self, args, raw):
        return cloud.message("shortcut")

    # ------------------------------------------------------------------
    # 联想软件下载 / UU
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 自建库（项目档案 / 自有软件）
    # ------------------------------------------------------------------
    def _cmd_my_library(self, args, raw):
        payload = library.load()
        return "my_library|" + json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 日常维护（快速清理 / 各项占用 / 软件卸载）
    # ------------------------------------------------------------------
    def _cmd_maint_items(self, args, raw):
        from . import maintenance
        payload = {"admin": maintenance.is_admin(), "items": maintenance.clean_items()}
        return "maint_items|" + json.dumps(payload, ensure_ascii=False)

    def _cmd_maint_scan(self, args, raw):
        from . import maintenance
        ids = [x for x in (args[0] if args else "").split(",") if x]
        return "maint_scan|" + json.dumps(maintenance.scan(ids), ensure_ascii=False)

    def _cmd_maint_clean(self, args, raw):
        from . import maintenance
        ids = [x for x in (args[0] if args else "").split(",") if x]
        return "maint_clean|" + json.dumps(maintenance.clean(ids), ensure_ascii=False)

    def _cmd_maint_stats(self, args, raw):
        from . import maintenance
        payload = {"memory": maintenance.memory_stats(), "disks": maintenance.disks(),
                   "admin": maintenance.is_admin()}
        return "maint_stats|" + json.dumps(payload, ensure_ascii=False)

    def _cmd_maint_mem_clean(self, args, raw):
        from . import maintenance
        return "maint_mem_clean|" + json.dumps(maintenance.memory_clean(), ensure_ascii=False)

    def _cmd_maint_apps(self, args, raw):
        from . import maintenance
        force = bool(args and args[0] == "1")
        payload = {"admin": maintenance.is_admin(), "items": maintenance.apps(force=force)}
        return "maint_apps|" + json.dumps(payload, ensure_ascii=False)

    def _cmd_maint_uninstall(self, args, raw):
        from . import maintenance
        app_id = args[0] if args else ""
        mode = args[1] if len(args) > 1 else "normal"
        return "maint_uninstall|" + json.dumps(maintenance.uninstall(app_id, mode), ensure_ascii=False)

    def _cmd_maint_log(self, args, raw):
        from . import maintenance
        payload = {"text": maintenance.uninstall_log_text(), "path": maintenance._log_path()}
        return "maint_log|" + json.dumps(payload, ensure_ascii=False)

    def _cmd_maint_open_log(self, args, raw):
        from . import maintenance
        try:
            os.startfile(maintenance._log_path())
        except Exception as exc:
            log("日常维护", "打开卸载日志失败：%s" % exc, "警告")
        return ""

    def _admin_health(self, port: int) -> bool:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1.2) as response:
                return response.status == 200
        except Exception:
            return False

    def _launch_admin_backend(self, port: int) -> bool:
        """后台没跑就自己拉起来：打包版用 exe --admin，源码版用当前解释器跑 admin/server.py。"""
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--admin", "--no-open", "--port", str(port)]
                cwd = os.path.dirname(os.path.abspath(sys.executable))
            else:
                app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                root = os.path.dirname(app_dir)
                server = os.path.join(root, "admin", "server.py")
                if not os.path.isfile(server):
                    log("自建库", "自动启动失败：找不到 admin\\server.py", "错误")
                    return False
                cmd = [sys.executable, server, "--no-open", "--port", str(port)]
                cwd = root
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                close_fds=True,
            )
            self._admin_proc = proc
            if not getattr(self, "_admin_atexit", False):
                import atexit
                atexit.register(self.shutdown)
                self._admin_atexit = True
            log("自建库", "网页后台未运行，已自动启动（端口 %d）" % port, "提示")
            return True
        except Exception as exc:
            log("自建库", "启动网页后台失败：%s" % exc, "错误")
            return False

    def shutdown(self) -> None:
        """退出时收掉“随程序自动启动”的网页后台（手动开的不管）。"""
        proc = getattr(self, "_admin_proc", None)
        self._admin_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except Exception:
                    proc.kill()
                log("自建库", "关闭程序，已停止随程序启动的网页后台", "信息")
        except Exception as exc:
            log("自建库", "停止网页后台失败：%s" % exc, "警告")

    def _cmd_admin_gate_check(self, args, raw):
        pwd = args[0] if args else ""
        ok = pwd == library.read_gate()
        log("自建库", "网页后台入口密码校验：%s" % ("通过" if ok else "失败"), "信息" if ok else "警告")
        return "admin_gate_result|" + ("1" if ok else "0")

    def _cmd_admin_gate_set(self, args, raw):
        old = self._b64_arg(args[0]) if args else ""
        new = self._b64_arg(args[1]) if len(args) > 1 else ""
        if old != library.read_gate():
            return "admin_gate_set_result|failed|当前入口密码不正确"
        if len(new) < 4:
            return "admin_gate_set_result|failed|新密码至少 4 位"
        if len(new) > 64:
            return "admin_gate_set_result|failed|新密码最多 64 位"
        try:
            library.write_gate(new)
        except Exception as exc:
            return "admin_gate_set_result|failed|保存失败：%s" % exc
        log("自建库", "网页后台入口密码已修改", "成功")
        return "admin_gate_set_result|success|入口密码已保存"

    # ------------------------------------------------------------------
    # 自动更新
    # ------------------------------------------------------------------
    def _cmd_update_state(self, args, raw):
        cfg = update.read_cfg()
        payload = {"version": update.APP_VERSION, "auto": cfg["auto"], "silent": cfg["silent"], "base": cfg["base"]}
        # 上次静默下载好的更新包：挂"退出后覆盖并重启"脚本
        try:
            if update.arm_pending():
                meta = update.read_pending() or {}
                self.bus.push("update_check_result|auto|" + json.dumps({
                    "status": "silent_armed",
                    "version": meta.get("version", ""),
                    "msg": "新版本 v%s 已就绪，本次退出后自动套用并重启新版" % meta.get("version", ""),
                }, ensure_ascii=False))
        except Exception as exc:
            log("自动更新", "挂载待套用更新失败：%s" % exc, "警告")
        if cfg["auto"] and cfg["base"]:
            threading.Timer(
                1.2,
                lambda: self._pool.submit(self._safe_call, "update_check", self._cmd_update_check, ["auto"], "update_check|auto"),
            ).start()
        return "update_state|" + json.dumps(payload, ensure_ascii=False)

    def _cmd_update_config(self, args, raw):
        auto = (args[0] if args else "1") == "1"
        base = (urllib.parse.unquote(args[1]) if len(args) > 1 else "").strip()
        silent = (args[2] if len(args) > 2 else "0") == "1"
        if base and not base.startswith(("http://", "https://")):
            return "update_config_result|failed|更新源地址需以 http:// 或 https:// 开头"
        try:
            update.write_cfg(auto, base, silent)
        except Exception as exc:
            return "update_config_result|failed|保存失败：%s" % exc
        log("自动更新", "更新设置已保存：auto=%s silent=%s base=%s" % (auto, silent, base or "(空)"), "成功")
        return "update_config_result|success|更新设置已保存"

    def _cmd_update_check(self, args, raw):
        mode = args[0] if args else "manual"
        cfg = update.read_cfg()
        try:
            res = update.check(cfg["base"])
            if res.get("status") == "available" and cfg["silent"]:
                meta = update.stage(res["url"], res.get("sha256", ""), res["version"])
                # 下载并校验完成立即挂上"退出即替换"脚本，不依赖后续再进设置页
                armed = False
                try:
                    armed = update.arm_pending()
                except Exception as exc:
                    log("自动更新", "下载完成后挂载替换脚本失败：%s" % exc, "警告")
                res = {
                    "status": "silent_ready",
                    "version": res["version"],
                    "armed": bool(armed),
                    "msg": "新版本 v%s 已后台下载并校验，本次退出后自动套用并重启新版" % res["version"],
                }
        except Exception as exc:
            res = {"status": "error", "msg": "检查更新失败：%s" % exc}
        log("自动更新", "检查更新(%s)：%s" % (mode, res.get("msg", res.get("status"))), "信息")
        return "update_check_result|" + mode + "|" + json.dumps(res, ensure_ascii=False)

    def _cmd_update_apply(self, args, raw):
        url = urllib.parse.unquote(args[0]) if args else ""
        sha = args[1] if len(args) > 1 else ""
        if not url.startswith(("http://", "https://")):
            return "update_apply_result|failed|更新包地址无效"
        try:
            pending = update.download_and_verify(url, sha)
            update.apply(pending)
        except Exception as exc:
            log("自动更新", "更新失败：%s" % exc, "错误")
            return "update_apply_result|failed|%s" % exc
        threading.Timer(1.0, self._exit_for_update).start()
        return "update_apply_result|started|更新包校验通过，程序即将重启完成更新"

    def _exit_for_update(self) -> None:
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:
            pass
        threading.Timer(6.0, lambda: os._exit(0)).start()

    def _cmd_open_admin_panel(self, args, raw):
        port = 8765
        token = library.read_token()
        url = "http://127.0.0.1:%d/" % port + (("?token=" + token) if token else "")
        started = False
        if not self._admin_health(port):
            if not self._launch_admin_backend(port):
                return "my_library_notice|网页后台启动失败，请稍后重试或查看后台日志"
            deadline = time.time() + 14.0
            while time.time() < deadline:
                if self._admin_health(port):
                    started = True
                    break
                time.sleep(0.35)
            if not started:
                log("自建库", "自动启动后健康检查仍未通过", "警告")
                return "my_library_notice|网页后台启动超时，请等几秒再点一次这个按钮"
        try:
            webbrowser.open(url)
            log("自建库", "已打开网页后台：%s" % url, "成功")
        except Exception as exc:
            return "my_library_notice|打开浏览器失败：%s" % exc
        if started:
            return "my_library_notice|网页后台已在后台启动，正在打开浏览器"
        return ""

    def _cmd_lenovo_search(self, args, raw):
        request_id = args[0] if args else ""
        keyword = urllib.parse.unquote(args[1]) if len(args) > 1 else ""
        try:
            payload = lestore.search(keyword)
        except Exception as exc:
            payload = {"status": 1, "message": "联想搜索异常：%s" % exc, "data": None}
        if int(payload.get("status") or 0) == 0:
            count = len(((payload.get("data") or {}).get("apps") or []))
            log("软件下载", "联想商店搜索「%s」：%d 条结果" % (keyword, count), "成功")
        else:
            log("软件下载", "联想商店搜索「%s」失败：%s" % (keyword, payload.get("message")), "警告")
        return "lenovo_search_result|%s|%s" % (request_id, json.dumps(payload, ensure_ascii=False))

    def _cmd_lenovo_download(self, args, raw):
        request_id = args[0] if args else ""
        soft_id = urllib.parse.unquote(args[1]) if len(args) > 1 else ""
        biz_info = urllib.parse.unquote("|".join(args[2:])) if len(args) > 2 else ""
        try:
            payload = lestore.resolve_download(soft_id, biz_info)
        except Exception as exc:
            payload = {"status": 1, "message": "下载链接解析异常：%s" % exc, "data": None}
        if int(payload.get("status") or 0) == 0:
            log("软件下载", "联想商店已解析安装包直链（softId=%s）" % soft_id, "成功")
        else:
            log("软件下载", "联想商店解析直链失败（softId=%s）：%s" % (soft_id, payload.get("message")), "警告")
        return "lenovo_download_result|%s|%s" % (request_id, json.dumps(payload, ensure_ascii=False))

    def _cmd_uu_download(self, args, raw):
        request_id = args[0] if args else ""
        payload = {"status": 1, "message": "未接入 UU 官方下载解析接口"}
        return "uu_download_result|%s|%s" % (request_id, json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------
    # 调试工具
    # ------------------------------------------------------------------
    def _cmd_scan_vst_plugins(self, args, raw):
        self.bus.push("vst_plugins_result|running")
        return vst.scan_message()

    def _cmd_delete_vst_plugin(self, args, raw):
        return vst.delete_one("|".join(args))

    def _cmd_open_vst_plugin_location(self, args, raw):
        return vst.open_location("|".join(args))

    def _cmd_cleanup_vst_plugins(self, args, raw):
        vst.cleanup_plugins(self.bus)
        return ""

    def _cmd_cleanup_waves(self, args, raw):
        vst.cleanup_waves(self.bus)
        return ""

    def _cmd_one_click_optimize(self, args, raw):
        options = [item for item in (args[0].split(",") if args else []) if item]
        from . import optimize
        return optimize.run(options, self.bus)

    def _cmd_disable_todesk_devices(self, args, raw):
        self.bus.push("disable_todesk_devices_result|running")
        disabled = 0
        failed = 0
        for device in audio.list_devices():
            if "todesk" not in device["name"].lower():
                continue
            if device["state"] != 1:
                continue
            if audio.set_enabled(device["id"], False).endswith("success"):
                disabled += 1
            else:
                failed += 1
        return "disable_todesk_devices_result|%s|disabled=%d|failed=%d" % (
            "success" if failed == 0 else "failed", disabled, failed)

    # ---------------- 备份 ----------------
    def _cmd_list_backup_channel_candidates(self, args, raw):
        return backup.channel_candidates()

    def _cmd_list_backup_channel_items(self, args, raw):
        return backup.list_channel_snapshots("items")

    def _cmd_list_backup_channel_snapshots(self, args, raw):
        return backup.list_channel_snapshots()

    def _cmd_backup_selected_channels(self, args, raw):
        ids = [item for item in (args[0].split(",") if args else []) if item]
        return backup.backup_channels(ids)

    def _cmd_restore_backup_channel(self, args, raw):
        return backup.restore_channels([args[0]] if args else [])

    def _cmd_restore_backup_channels(self, args, raw):
        ids = [item for item in (args[0].split(",") if args else []) if item]
        return backup.restore_channels(ids)

    def _cmd_delete_backup_channels(self, args, raw):
        ids = [item for item in (args[0].split(",") if args else []) if item]
        return backup.delete_channels(ids)

    def _cmd_list_backup_effect_candidates(self, args, raw):
        return backup.effect_candidates()

    def _cmd_list_backup_effects(self, args, raw):
        return backup.list_effect_snapshots()

    def _cmd_backup_selected_effects(self, args, raw):
        items = [urllib.parse.unquote(item) for item in (args[0].split(",") if args else []) if item]
        return backup.backup_effects(items)

    def _cmd_restore_backup_effects(self, args, raw):
        ids = [item for item in (args[0].split(",") if args else []) if item]
        return backup.restore_effects(ids)

    def _cmd_delete_backup_effects(self, args, raw):
        ids = [item for item in (args[0].split(",") if args else []) if item]
        return backup.delete_effects(ids)

    # ------------------------------------------------------------------
    # Studio One 工具
    # ------------------------------------------------------------------
    def _cmd_studio_hook_refresh(self, args, raw):
        return studio.processes_message()

    def _cmd_studio_hook_inject(self, args, raw):
        self.bus.push("studio_hook_progress|正在连接目标进程...")
        return studio.hook_result("inject")

    def _cmd_studio_hook_extract(self, args, raw):
        self.bus.push("studio_hook_progress|正在提取当前工程...")
        return studio.hook_result("extract")

    def _cmd_studio_hook_open_output(self, args, raw):
        try:
            os.startfile(studio.output_dir())
        except Exception:
            pass
        return ""

    def _cmd_studio_file_decrypt_start(self, args, raw):
        path = "|".join(args)
        return studio.decrypt_start(path, self.bus)

    def _cmd_studio_downgrade_start(self, args, raw):
        path = args[0] if args else ""
        version = args[1] if len(args) > 1 else "3.5.4.45392"
        return studio.downgrade_start(path, version, self.bus)

    def _cmd_studio_file_drop_path(self, args, raw):
        return "studio_file_drop|" + "|".join(args)

    def _select_studio_file(self, kind: str) -> str:
        if not self.window:
            return ""
        try:
            import webview
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Studio One 工程 (*.song)", "所有文件 (*.*)"),
            )
            if result:
                path = result[0] if isinstance(result, (list, tuple)) else result
                return "studio_file_selected|%s|%s" % (kind, path)
        except Exception as exc:
            log("工程处理", "选择文件失败：%s" % exc, "错误")
        return ""

    def _cmd_studio_file_decrypt_select(self, args, raw):
        return self._select_studio_file("decrypt")

    def _cmd_studio_downgrade_select(self, args, raw):
        return self._select_studio_file("downgrade")
