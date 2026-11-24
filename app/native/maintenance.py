"""日常维护：快速清理 / 各项占用 / 软件卸载（对齐 PXOS TOOLBOX 的「日常维护」栏）。

所有动作都在本机执行，删除类操作只处理明确列出的缓存目录，
遇到正在占用的文件自动跳过，并把结果写进 卸载日志.txt / ToolsLog.log。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None

from .paths import data_dir, log

USER = os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA") or os.path.join(USER, "AppData", "Local")
ROAMING = os.environ.get("APPDATA") or os.path.join(USER, "AppData", "Roaming")
PROGRAMDATA = os.environ.get("ProgramData") or r"C:\ProgramData"
WINDIR = os.environ.get("WINDIR") or r"C:\Windows"
SYSTEMDRIVE = os.environ.get("SystemDrive") or "C:"

_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# Win32 辅助
# ----------------------------------------------------------------------------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


def recycle_info() -> tuple:
    """返回 (总字节, 文件数)，跨所有盘符统计回收站。"""
    total = 0
    count = 0
    try:
        kernel32 = ctypes.windll.kernel32
        mask = kernel32.GetLogicalDrives()
        for i in range(26):
            if not (mask >> i) & 1:
                continue
            root = "%s:\\" % chr(65 + i)
            info = SHQUERYRBINFO()
            info.cbSize = ctypes.sizeof(info)
            try:
                rc = ctypes.windll.shell32.SHQueryRecycleBinW(ctypes.c_wchar_p(root), ctypes.byref(info))
                if rc == 0:
                    total += int(info.i64Size or 0)
                    count += int(info.i64NumItems or 0)
            except Exception:
                continue
    except Exception:
        pass
    return total, count


def empty_recycle_bin() -> int:
    before, _ = recycle_info()
    try:
        # NOCONFIRMATION(1) | NOPROGRESSUI(2) | NOSOUND(4)
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
    except Exception as exc:
        log("日常维护", "清空回收站失败：%s" % exc, "警告")
        return 0
    after, _ = recycle_info()
    return max(0, before - after)


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_size_t),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_ulong),
        ("ProcessCount", ctypes.c_ulong),
        ("ThreadCount", ctypes.c_ulong),
    ]


def memory_stats() -> dict:
    ms = MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(ms)
    ok = False
    try:
        ok = bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)))
    except Exception:
        ok = False
    pi = PERFORMANCE_INFORMATION()
    pi.cb = ctypes.sizeof(pi)
    perf_ok = False
    try:
        perf_ok = bool(ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb))
    except Exception:
        perf_ok = False

    total = int(ms.ullTotalPhys) if ok else (psutil.virtual_memory().total if psutil else 0)
    avail = int(ms.ullAvailPhys) if ok else (psutil.virtual_memory().available if psutil else 0)
    page = int(pi.PageSize or 4096) if perf_ok else 4096
    cache = int(pi.SystemCache) * page if perf_ok else 0
    commit_total = int(pi.CommitTotal) * page if perf_ok else 0
    commit_limit = int(pi.CommitLimit) * page if perf_ok else 0
    used = max(0, total - avail)
    # 展示口径对齐 PX：已用 + 缓存 + 可用 = 总计
    display_free = max(0, total - used - cache)
    return {
        "total": total,
        "used": used,
        "cached": cache,
        "available": display_free,
        "availRaw": avail,
        "commitTotal": commit_total,
        "commitLimit": commit_limit,
        "percent": round(used * 100.0 / total, 1) if total else 0,
        "admin": is_admin(),
    }


def memory_clean() -> dict:
    before = memory_stats()["availRaw"]
    cleared = 0
    failed = 0
    if psutil is not None:
        for pid in psutil.pids():
            try:
                proc = ctypes.windll.kernel32.OpenProcess(0x0100 | 0x0400, False, int(pid))  # QUERY_LIMITED|SET_QUOTA
                if not proc:
                    failed += 1
                    continue
                try:
                    if ctypes.windll.kernel32.K32EmptyWorkingSet(proc):
                        cleared += 1
                    else:
                        failed += 1
                finally:
                    ctypes.windll.kernel32.CloseHandle(proc)
            except Exception:
                failed += 1
    # 尝试清 Standby 待机列表（需要管理员；失败忽略）
    standby_ok = _clear_standby()
    time.sleep(0.6)
    after = memory_stats()["availRaw"]
    freed = max(0, after - before)
    log("日常维护", "一键清理：整理工作集 %d 个进程，待机列表=%s，释放 %.1f MB" % (
        cleared, "已清" if standby_ok else "未清", freed / 1048576.0))
    return {"cleared": cleared, "failed": failed, "standby": standby_ok, "freed": freed,
            "before": before, "after": after}


def _enable_privilege(name: str) -> bool:
    try:
        adv = ctypes.WinDLL("advapi32")
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
        adv.OpenProcessToken.restype = ctypes.c_int
        adv.LookupPrivilegeValueW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_longlong)]
        adv.LookupPrivilegeValueW.restype = ctypes.c_int
        adv.AdjustTokenPrivileges.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                              ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        adv.AdjustTokenPrivileges.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        token = ctypes.c_void_p()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
            return False
        luid = ctypes.c_longlong(0)
        if not adv.LookupPrivilegeValueW(None, ctypes.c_wchar_p(name), ctypes.byref(luid)):
            k32.CloseHandle(token)
            return False

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _pack_ = 4
            _fields_ = [("Luid", ctypes.c_longlong), ("Attributes", ctypes.c_ulong)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _pack_ = 4
            _fields_ = [("PrivilegeCount", ctypes.c_ulong), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid.value
        tp.Privileges[0].Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED
        ok = adv.AdjustTokenPrivileges(token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None)
        err = ctypes.WinDLL("kernel32").GetLastError()
        k32.CloseHandle(token)
        return bool(ok) and err == 0
    except Exception:
        return False


def _clear_standby() -> bool:
    """NtSetSystemInformation(SystemMemoryListInformation, MemoryPurgeStandbyList=4)。"""
    if not _enable_privilege("SeProfileSingleProcessPrivilege"):
        return False
    try:
        ntdll = ctypes.WinDLL("ntdll")
        command = ctypes.c_ulong(4)
        status = ntdll.NtSetSystemInformation(80, ctypes.byref(command), ctypes.sizeof(command))
        return status == 0
    except Exception:
        return False


def disks() -> list:
    items = []
    if psutil is None:
        return items
    try:
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in (part.opts or "") or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            mount = part.mountpoint.rstrip("\\")
            if not mount:
                continue
            items.append({
                "letter": mount[0].upper() if len(mount) >= 2 and mount[1] == ":" else mount[:1].upper(),
                "mount": part.mountpoint,
                "label": _volume_label(part.mountpoint),
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": round(usage.percent, 1),
            })
    except Exception as exc:
        log("日常维护", "读取磁盘失败：%s" % exc, "警告")
    return items


def _volume_label(mount: str) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        fs = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(mount), buf, 256, None, None, None, fs, 64)
        if ok and buf.value:
            return buf.value
    except Exception:
        pass
    return "本地磁盘"


# ----------------------------------------------------------------------------
# 清理项
# ----------------------------------------------------------------------------
def _item(cid, name, desc, paths=None, kind="files", admin=False, default=False, note=""):
    return {"id": cid, "name": name, "desc": desc, "paths": paths or [],
            "kind": kind, "admin": admin, "default": default, "note": note}


def clean_items() -> list:
    items = [
        _item("user_temp", "用户临时文件",
              "清理 %TEMP% 目录，软件解压、安装、运行产生的临时文件。正在占用的文件会自动跳过。",
              [(os.path.join(LOCAL, "Temp"), "*")], default=True),
        _item("win_temp", "Windows 临时文件",
              "C:\\Windows\\Temp，系统服务和更新产生的临时文件，安全清理。",
              [(os.path.join(WINDIR, "Temp"), "*")], default=True),
        _item("recycle", "清空回收站", "永久删除回收站里已删除的文件，释放磁盘空间（不可恢复，默认不勾选）。", kind="recycle", default=False),
        _item("thumbs", "缩略图缓存",
              "资源管理器图片、视频预览小图缓存。清理后首次打开图片文件夹会重建，不丢文件。",
              [(os.path.join(LOCAL, r"Microsoft\Windows\Explorer"), "thumbcache_*.db")], default=True),
        _item("iconcache", "图标缓存",
              "桌面 / 文件图标缓存数据库。图标变白或错乱时使用，清理后系统自动重建。",
              [(os.path.join(LOCAL, r"Microsoft\Windows\Explorer"), "iconcache_*.db"),
               (LOCAL, "IconCache.db")]),
        _item("prefetch", "Prefetch 预读取",
              "Windows 启动预读取记录。清了会让软件首次启动慢一会儿，不建议天天清。",
              [(os.path.join(WINDIR, "Prefetch"), "*")], admin=True),
        _item("dumps", "崩溃转储",
              "程序、系统蓝屏崩溃产生的内存转储，体积大，普通用户不需要。",
              [(os.path.join(LOCAL, "CrashDumps"), "*"),
               (os.path.join(WINDIR, "Minidump"), "*"),
               (WINDIR, "MEMORY.DMP")], admin=True),
        _item("wer", "错误报告队列",
              "程序闪退、报错产生的 Windows 错误上报日志，可安全清理。",
              [(os.path.join(LOCAL, r"Microsoft\Windows\WER"), "*"),
               (os.path.join(PROGRAMDATA, r"Microsoft\Windows\WER"), "*")], admin=True),
        _item("delivery", "传递优化缓存",
              "Windows 更新的 P2P 分发缓存，单机环境可以清掉，后续更新会重新下载。",
              [(os.path.join(WINDIR, r"ServiceProfiles\LocalService\AppData\Local\Microsoft\Windows\DeliveryOptimization"), "*")],
              admin=True),
        _item("wupdate", "更新下载缓存",
              "Windows Update 已安装完的更新安装包缓存，清理不影响已打的补丁。",
              [(os.path.join(WINDIR, r"SoftwareDistribution\Download"), "*")], admin=True),
        _item("recent", "最近使用记录",
              "开始菜单、资源管理器里的最近访问列表（不删文件本身）。",
              [(os.path.join(ROAMING, r"Microsoft\Windows\Recent"), "*")]),
        _item("syslog", "系统日志",
              "C:\\Windows\\System32\\winevt\\Logs 下的事件日志文件。只清文件，不碰其它系统数据。",
              [(os.path.join(WINDIR, r"System32\winevt\Logs"), "*.evtx")], admin=True, note="删除后事件查看器历史清空"),
        _item("cache", "缓存文件",
              "浏览器缓存、DirectX / NVIDIA 着色器缓存、字体缓存、Internet 缓存。不删账号、书签和下载文件。",
              [(os.path.join(LOCAL, "D3DSCache"), "*"),
               (os.path.join(LOCAL, r"NVIDIA\DXCache"), "*"),
               (os.path.join(LOCAL, r"NVIDIA\GLCache"), "*"),
               (os.path.join(LOCAL, r"NVIDIA\OptixCache"), "*"),
               (os.path.join(LOCAL, r"Microsoft\Windows\INetCache"), "*"),
               (os.path.join(LOCAL, "FontCache"), "FontCache*.dat"),
               (os.path.join(LOCAL, r"Google\Chrome\User Data\Default\Cache"), "*"),
               (os.path.join(LOCAL, r"Google\Chrome\User Data\Default\Code Cache"), "*"),
               (os.path.join(LOCAL, r"Google\Chrome\User Data\Default\GPUCache"), "*"),
               (os.path.join(LOCAL, r"Microsoft\Edge\User Data\Default\Cache"), "*"),
               (os.path.join(LOCAL, r"Microsoft\Edge\User Data\Default\Code Cache"), "*"),
               (os.path.join(LOCAL, r"Microsoft\Edge\User Data\Default\GPUCache"), "*")],
              default=True),
        _item("dns", "刷新 DNS",
              "清空系统 DNS 解析缓存，解决网站打不开、域名异常。纯网络操作，不删文件。",
              kind="dns", default=True),
    ]
    return items


def _expand(folder, pattern):
    """把 (目录, 通配/文件名) 展开成待统计、待删除的路径列表。"""
    import fnmatch
    out = []
    try:
        if os.path.isfile(folder) and pattern == "*":
            return [folder]
        if not os.path.isdir(folder):
            return out
        for entry in os.scandir(folder):
            if pattern == "*" or fnmatch.fnmatch(entry.name.lower(), str(pattern).lower()):
                out.append(entry.path)
    except Exception:
        pass
    return out


def _du(path, depth=0):
    size = 0
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        if depth > 12:
            return 0
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        size += _du(entry.path, depth + 1)
                    else:
                        size += entry.stat(follow_symlinks=False).st_size
                except Exception:
                    continue
    except Exception:
        pass
    return size


def scan(ids) -> dict:
    ids = set(ids or [])
    items = {it["id"]: it for it in clean_items()}
    result = {}
    for cid in ids:
        item = items.get(cid)
        if not item:
            continue
        if item["kind"] == "recycle":
            size, count = recycle_info()
            result[cid] = {"size": size, "count": count}
        elif item["kind"] == "dns":
            result[cid] = {"size": 0, "count": 0}
        else:
            total = 0
            count = 0
            for path, pattern in item["paths"]:
                for target in _expand(path, pattern):
                    try:
                        if os.path.isdir(target):
                            total += _du(target)
                            count += 1
                        else:
                            total += os.path.getsize(target)
                            count += 1
                    except Exception:
                        continue
            result[cid] = {"size": total, "count": count}
    return result


def _delete_target(path) -> tuple:
    """删除文件/目录内容，返回 (释放字节, 跳过数量)。"""
    freed = 0
    skipped = 0
    try:
        if os.path.isfile(path):
            size = os.path.getsize(path)
            os.remove(path)
            return size, 0
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    fp = os.path.join(root, name)
                    try:
                        size = os.path.getsize(fp)
                        os.remove(fp)
                        freed += size
                    except Exception:
                        skipped += 1
                for name in dirs:
                    dp = os.path.join(root, name)
                    try:
                        os.rmdir(dp)
                    except Exception:
                        pass
    except Exception:
        skipped += 1
    return freed, skipped


def clean(ids) -> dict:
    ids = set(ids or [])
    items = {it["id"]: it for it in clean_items()}
    result = {"freed": 0, "skipped": 0, "details": [], "messages": []}
    for cid in ids:
        item = items.get(cid)
        if not item:
            continue
        before = 0
        skipped = 0
        if item["kind"] == "recycle":
            before = empty_recycle_bin()
            result["messages"].append("回收站已清空 %.1f MB" % (before / 1048576.0) if before else "回收站本已为空")
        elif item["kind"] == "dns":
            try:
                flags = 0x08000000 if sys.platform == "win32" else 0
                subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=flags)
                result["messages"].append("DNS 缓存已刷新")
            except Exception as exc:
                result["messages"].append("刷新 DNS 失败：%s" % exc)
        else:
            for path, pattern in item["paths"]:
                for target in _expand(path, pattern):
                    freed, skip = _delete_target(target)
                    before += freed
                    skipped += skip
        result["freed"] += before
        result["skipped"] += skipped
        result["details"].append({"id": cid, "name": item["name"], "freed": before, "skipped": skipped})
        log("日常维护", "清理项 %s：释放 %.1f MB，跳过 %d 个占用文件" % (item["name"], before / 1048576.0, skipped))
    return result


# ----------------------------------------------------------------------------
# 软件卸载
# ----------------------------------------------------------------------------
_UNINSTALL_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

_HIVES = [
    ("HKLM64", "HKLM", 0x0100),   # KEY_WOW64_64KEY
    ("HKLM32", "HKLM", 0x0200),   # KEY_WOW64_32KEY（同一路径在 32 位视图里就是 WOW6432Node）
    ("HKCU", "HKCU", 0x0000),
]

_APP_CACHE = {"items": [], "at": 0.0}
_APP_LOCK = threading.Lock()


def _wow(access: int) -> int:
    return access & (0x0100 | 0x0200)


def _open_uninstall_root(tag: str):
    import winreg
    wow = {"HKLM64": winreg.KEY_WOW64_64KEY, "HKLM32": winreg.KEY_WOW64_32KEY}.get(tag, 0)
    root = winreg.HKEY_LOCAL_MACHINE if tag.startswith("HKLM") else winreg.HKEY_CURRENT_USER
    return winreg.OpenKey(root, _UNINSTALL_PATH, 0, winreg.KEY_READ | wow)


def apps(force=False) -> list:
    import winreg
    with _APP_LOCK:
        if not force and _APP_CACHE["items"] and time.time() - _APP_CACHE["at"] < 30:
            return list(_APP_CACHE["items"])
        found = []
        for tag, root_name, access in _HIVES:
            try:
                key = _open_uninstall_root(tag)
            except Exception:
                continue
            try:
                count = winreg.QueryInfoKey(key)[0]
            except Exception:
                count = 0
            for index in range(count):
                try:
                    sub_name = winreg.EnumKey(key, index)
                except Exception:
                    continue
                try:
                    sub = winreg.OpenKey(key, sub_name, 0, winreg.KEY_READ | _wow(access))
                except Exception:
                    continue
                try:
                    def get(name, default=""):
                        try:
                            return str(winreg.QueryValueEx(sub, name)[0])
                        except Exception:
                            return default
                    display = get("DisplayName").strip()
                    if not display:
                        continue
                    if get("SystemComponent") in ("1", "0x1"):
                        is_system = True
                    else:
                        is_system = False
                    try:
                        estimated = int(get("EstimatedSize") or 0) * 1024
                    except Exception:
                        estimated = 0
                    app_id = hashlib.md5(("%s|%s" % (tag, sub_name)).encode("utf-8")).hexdigest()[:16]
                    found.append({
                        "id": app_id,
                        "name": display,
                        "version": get("DisplayVersion"),
                        "publisher": get("Publisher"),
                        "size": estimated,
                        "location": get("InstallLocation"),
                        "icon": get("DisplayIcon"),
                        "uninstall": get("UninstallString"),
                        "quiet": get("QuietUninstallString"),
                        "system": is_system,
                        "hive": tag,
                        "key": sub_name,
                    })
                finally:
                    try:
                        sub.Close()
                    except Exception:
                        pass
            try:
                key.Close()
            except Exception:
                pass
        # 同 id 去重，优先保留有 UninstallString 的
        merged = {}
        for item in found:
            key = item["id"]
            old = merged.get(key)
            if old is None or (not old.get("uninstall") and item.get("uninstall")):
                merged[key] = item
        result = sorted(merged.values(), key=lambda x: x["name"].lower())
        _APP_CACHE["items"] = result
        _APP_CACHE["at"] = time.time()
        log("日常维护", "扫描已安装应用：%d 条" % len(result))
        return list(result)


def _log_path() -> str:
    return os.path.join(data_dir(), "卸载日志.txt")


def append_uninstall_log(text: str) -> None:
    try:
        with _LOCK:
            with open(_log_path(), "a", encoding="utf-8") as fh:
                fh.write(text.rstrip() + "\n")
    except Exception:
        pass


def uninstall_log_text() -> str:
    try:
        with open(_log_path(), "r", encoding="utf-8") as fh:
            return fh.read()[-6000:]
    except Exception:
        return "还没有卸载记录。"


def _find(app_id):
    for item in apps():
        if item["id"] == app_id:
            return item
    apps(force=True)
    for item in apps():
        if item["id"] == app_id:
            return item
    return None


def _kill_related(location: str) -> int:
    if not location or psutil is None:
        return 0
    killed = 0
    norm = os.path.normcase(os.path.abspath(location))
    for proc in psutil.process_iter(["pid", "exe", "name"]):
        try:
            exe = proc.info.get("exe") or ""
            if exe and os.path.normcase(os.path.abspath(exe)).startswith(norm):
                proc.kill()
                killed += 1
        except Exception:
            continue
    return killed


def _remove_registry(item) -> bool:
    import winreg
    tag = item.get("hive") or "HKCU"
    root = winreg.HKEY_LOCAL_MACHINE if tag.startswith("HKLM") else winreg.HKEY_CURRENT_USER
    wow = winreg.KEY_WOW64_32KEY if tag == "HKLM32" else (winreg.KEY_WOW64_64KEY if tag == "HKLM64" else 0)
    try:
        with winreg.OpenKey(root, _UNINSTALL_PATH, 0, winreg.KEY_ALL_ACCESS | wow) as key:
            winreg.DeleteKey(key, item["key"])
        return True
    except Exception as exc:
        log("日常维护", "删除卸载注册表失败：%s" % exc, "警告")
        return False


SYSTEM_ROOTS = [os.path.normcase(os.path.abspath(p)) for p in
                (WINDIR, os.path.join(WINDIR, "System32"), os.path.join(PROGRAMDATA, "Microsoft"))]


def _safe_remove_dir(location: str) -> tuple:
    """删除安装目录（只允许非系统关键目录）。"""
    if not location:
        return 0, "无安装目录"
    path = os.path.abspath(location)
    norm = os.path.normcase(path)
    if not os.path.isdir(path):
        return 0, "目录不存在"
    for root in SYSTEM_ROOTS:
        if norm == root or norm.startswith(root + os.sep):
            return 0, "系统目录，跳过"
    if norm.count(os.sep) <= 2:
        return 0, "层级过浅，跳过"
    freed, skipped = _delete_target(path)
    try:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
    return freed, "已删除" if not os.path.isdir(path) else "部分残留"


def uninstall(app_id: str, mode: str = "normal") -> dict:
    item = _find(app_id)
    if not item:
        return {"ok": False, "message": "找不到这条应用，可能已经卸载了"}

    name = item["name"]
    cmd = item.get("quiet") if mode in ("deep", "force") and item.get("quiet") else item.get("uninstall")
    if not cmd:
        return {"ok": False, "message": "这个应用没有卸载命令，只能用自带卸载程序或 BCU 手工处理"}

    killed = 0
    if mode == "force":
        killed = _kill_related(item.get("location", ""))

    append_uninstall_log("[%s] %s 模式=%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), name, mode))
    log("日常维护", "开始卸载 %s（%s），命令：%s" % (name, mode, cmd))

    waited = None
    try:
        proc = subprocess.Popen(cmd, cwd=(item.get("location") or None), shell=False)
        if mode in ("deep", "force"):
            try:
                waited = proc.wait(timeout=900)
            except Exception:
                waited = None
    except Exception as exc:
        append_uninstall_log("启动卸载程序失败：%s" % exc)
        return {"ok": False, "message": "启动卸载程序失败：%s" % exc}

    result = {"ok": True, "name": name, "mode": mode, "killed": killed, "exitCode": waited,
              "removedKey": False, "removedDir": "", "freed": 0}
    if mode in ("deep", "force"):
        time.sleep(1.2)
        result["removedKey"] = _remove_registry(item)
        freed, note = _safe_remove_dir(item.get("location") or "")
        result["removedDir"] = note
        result["freed"] = freed
        result["message"] = "卸载完成" if waited == 0 else "卸载程序已结束（退出码 %s）" % waited
        append_uninstall_log("  收尾：注册表=%s，目录=%s，释放 %.1f MB，结束进程 %d 个" % (
            "已删" if result["removedKey"] else "未删", note, freed / 1048576.0, killed))
    else:
        result["message"] = "已启动卸载程序，按它自己的界面完成卸载即可"
    apps(force=True)
    return result
