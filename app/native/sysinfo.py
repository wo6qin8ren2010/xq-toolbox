"""系统概述：CPU / 内存 / 磁盘 / 显卡 / 温度 / 系统信息采集。"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import time

import psutil

_cache = {"at": 0.0, "gpu": (0.0, 0.0, 0.0, ""), "disk": (0.0, 0.0), "tempAt": 0.0, "cpuTemp": None}
_lock = threading.Lock()


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


def snapshot() -> dict:
    now = time.time()
    with _lock:
        if now - _cache["at"] > 1.5:
            _cache["gpu"] = _nvidia()
            try:
                disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
                _cache["disk"] = (disk.percent, disk.total / (1024 ** 3))
            except Exception:
                _cache["disk"] = (0.0, 0.0)
            _cache["at"] = now
        if now - _cache["tempAt"] > 15:
            _cache["cpuTemp"] = _acpi_cpu_temp()
            _cache["tempAt"] = now
        gpu_util, gpu_mem, gpu_temp, gpu_name = _cache["gpu"]
        cpu_temp = _cache["cpuTemp"]
        disk_pct, _disk_total = _cache["disk"]

    vm = psutil.virtual_memory()
    try:
        freq = psutil.cpu_freq()
        cpu_freq_mhz = float(freq.current) if freq and freq.current else 0.0
    except Exception:
        cpu_freq_mhz = 0.0

    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.percent,
        "disk": disk_pct,
        "gpuUtilization": gpu_util,
        "gpuMemory": gpu_mem,
        "cpuTemp": cpu_temp,
        "gpuTemp": gpu_temp if gpu_temp > 0 else None,
        "os": _os_name(),
        "processor": _processor_name(),
        "cpuFrequencyMHz": cpu_freq_mhz,
        "totalRamGb": vm.total / (1024 ** 3),
        "gpuName": gpu_name or "未知",
    }


def as_message() -> str:
    return "system_overview|" + json.dumps(snapshot(), ensure_ascii=False)


def monitor_message() -> str:
    """侧栏实时监控条：只要最精简的字段。"""
    data = snapshot()
    payload = {
        "cpu": round(float(data.get("cpu") or 0.0), 1),
        "ram": round(float(data.get("ram") or 0.0), 1),
        "gpu": round(float(data.get("gpuUtilization") or 0.0), 1),
        "vram": round(float(data.get("gpuMemory") or 0.0), 1),
        "cpuTemp": data.get("cpuTemp"),
        "gpuTemp": data.get("gpuTemp"),
        "gpuName": data.get("gpuName") or "",
    }
    return "monitor|" + json.dumps(payload, ensure_ascii=False)
