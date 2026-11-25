"""系统概述：CPU / 内存 / 磁盘 / 显卡 / 温度 / 系统信息采集。

采集分两类：
- 内存级读取（psutil、注册表）在请求线程里直接做，微秒级；
- 需要拉起子进程的（nvidia-smi、ACPI 测温）一律放后台线程，
  结果写缓存。请求路径永不等待子进程，也绝不持锁做 I/O。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import time

import psutil

_GPU_EVERY = 1.5
_TEMP_EVERY = 15.0
_IDLE_AFTER = 6.0

_cache = {"gpu": (0.0, 0.0, 0.0, ""), "cpuTemp": None}
_lock = threading.Lock()
_static = {}
_worker = None
_worker_lock = threading.Lock()
_last_want = 0.0


def _reg_value(path: str, name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except Exception:
        return ""


def _os_name() -> str:
    product = _reg_value(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName") or "Windows"
    build = _reg_value(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuildNumber")
    try:
        if build and int(build) >= 22000:
            product = product.replace("Windows 10", "Windows 11")
    except Exception:
        pass
    return "%s (%s)" % (product, build) if build else product


def _processor_name() -> str:
    name = _reg_value(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    name = (name or platform.processor() or "未知处理器").strip()
    return " ".join(name.split())


def _static_info() -> tuple:
    """机器型号信息运行期不会变，只读一次注册表。"""
    if not _static:
        _static["os"] = _os_name()
        _static["processor"] = _processor_name()
    return _static["os"], _static["processor"]


def _nvidia() -> tuple:
    """通过 nvidia-smi 读取显卡利用率、显存占用与显卡温度。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=0x08000000,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return (0.0, 0.0, 0.0, "")
        line = out.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        util = float(parts[0])
        used = float(parts[1])
        total = float(parts[2]) if len(parts) > 2 else 0.0
        temp = float(parts[3]) if len(parts) > 3 else 0.0
        name = parts[4] if len(parts) > 4 else ""
        mem_pct = (used / total * 100.0) if total else 0.0
        return (util, mem_pct, temp, name)
    except Exception:
        return (0.0, 0.0, 0.0, "")


def _acpi_cpu_temp() -> float | None:
    """ACPI 热区温度（部分主板不提供，读不到时返回 None）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$v=(Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
             "-ErrorAction SilentlyContinue | Measure-Object -Property CurrentTemperature -Maximum).Maximum;"
             " if ($v) { [string]$v }"],
            capture_output=True, text=True, timeout=6, creationflags=0x08000000,
        )
        text = (out.stdout or "").strip()
        if not text:
            return None
        raw = float(text)
        celsius = raw / 10.0 - 273.15
        if -20.0 <= celsius <= 120.0:
            return round(celsius, 1)
        return None
    except Exception:
        return None


def _loop() -> None:
    last_temp = 0.0
    while True:
        # 没人看的时候不拉子进程：界面切走或最小化后自动停采，回来 1 秒内补上
        if time.time() - _last_want > _IDLE_AFTER:
            time.sleep(1.0)
            continue
        gpu = _nvidia()
        with _lock:
            _cache["gpu"] = gpu
        now = time.time()
        if now - last_temp >= _TEMP_EVERY:
            temp = _acpi_cpu_temp()
            with _lock:
                _cache["cpuTemp"] = temp
            last_temp = time.time()
        time.sleep(_GPU_EVERY)


def prime() -> None:
    """启动时调用： warm 静态信息与 CPU 采样，并拉起后台采集线程。"""
    global _worker, _last_want
    with _worker_lock:
        if _worker is not None:
            return
        _last_want = time.time()
        _static_info()
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        _worker = threading.Thread(target=_loop, name="sysinfo", daemon=True)
        _worker.start()


def snapshot() -> dict:
    """只读缓存 + 内存级采样，任何情况下都不阻塞在子进程上。"""
    global _last_want
    _last_want = time.time()
    prime()
    with _lock:
        gpu_util, gpu_mem, gpu_temp, gpu_name = _cache["gpu"]
        cpu_temp = _cache["cpuTemp"]

    try:
        disk_pct = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").percent
    except Exception:
        disk_pct = 0.0

    vm = psutil.virtual_memory()
    try:
        freq = psutil.cpu_freq()
        cpu_freq_mhz = float(freq.current) if freq and freq.current else 0.0
    except Exception:
        cpu_freq_mhz = 0.0

    os_name, processor = _static_info()
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.percent,
        "disk": disk_pct,
        "gpuUtilization": gpu_util,
        "gpuMemory": gpu_mem,
        "cpuTemp": cpu_temp,
        "gpuTemp": gpu_temp if gpu_temp > 0 else None,
        "os": os_name,
        "processor": processor,
        "cpuFrequencyMHz": cpu_freq_mhz,
        "totalRamGb": vm.total / (1024 ** 3),
        "gpuName": gpu_name or "未知",
    }


def as_message() -> str:
    return "system_overview|" + json.dumps(snapshot(), ensure_ascii=False)