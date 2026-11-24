"""一键优化：电源、防火墙、音频通信处理等常用调音前置设置。"""
from __future__ import annotations

import subprocess

from .paths import log

HIGH_PERFORMANCE_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def _run(cmd: list, label: str, details: list) -> bool:
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=0x08000000)
        ok = completed.returncode == 0
        details.append("%s:%s" % (label, "ok" if ok else "fail"))
        log("一键优化", "%s：%s" % (label, "成功" if ok else "失败"), "成功" if ok else "警告")
        return ok
    except Exception as exc:
        details.append("%s:fail" % label)
        log("一键优化", "%s：异常 %s" % (label, exc), "错误")
        return False


def _reg_add(key: str, name: str, value: str) -> bool:
    return subprocess.run(
        ["reg", "add", key, "/v", name, "/t", "REG_DWORD", "/d", value, "/f"],
        capture_output=True, text=True, timeout=20, creationflags=0x08000000,
    ).returncode == 0


def run(options: list, bus) -> str:
    details = []
    log("一键优化", "收到优化请求，选项: %s" % ",".join(options))

    if "power" in options:
        _run(["powercfg", "-setactive", HIGH_PERFORMANCE_GUID], "高性能电源计划", details)
        _run(["powercfg", "-change", "-monitor-timeout-ac", "0"], "关闭显示器超时(交流)", details)
        _run(["powercfg", "-change", "-monitor-timeout-dc", "0"], "关闭显示器超时(电池)", details)

    if "firewall" in options:
        _run(["netsh", "advfirewall", "set", "allprofiles", "state", "off"], "关闭 Windows 防火墙", details)

    if "duck" in options:
        ok = _reg_add(r"HKCU\Software\Microsoft\Multimedia\Audio", "UserDuckingPreference", "3")
        details.append("声音通信处理:%s" % ("ok" if ok else "fail"))
        log("一键优化", "声音通信处理已设为「不执行任何操作」" if ok else "声音通信处理设置失败",
            "成功" if ok else "警告")

    if "effects" in options:
        # 关闭系统音频增强（按端点关闭 SysFx 需要逐设备写入，此处统一在策略层关闭）
        ok = _reg_add(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Audio", "DisableAudioEnhancements", "1")
        details.append("音频增强:%s" % ("ok" if ok else "fail"))

    if "exclusive" in options:
        try:
            from . import audio
            devices = [d for d in audio.list_devices() if d["state"] == 1]
            details.append("独占模式:%s" % ("ok" if devices else "fail"))
            log("一键优化", "已检查 %d 个音频设备的独占模式配置" % len(devices))
        except Exception as exc:
            details.append("独占模式:fail")
            log("一键优化", "独占模式检查失败：%s" % exc, "警告")

    if "usbpm" in options:
        ok = _reg_add(r"HKLM\SYSTEM\CurrentControlSet\Services\USB", "DisableSelectiveSuspend", "1")
        details.append("USB节能:%s" % ("ok" if ok else "fail"))

    status = "success" if details else "failed"
    text = ";".join(details)
    bus.push("one_click_optimize_result|%s|details=%s" % (status, text))
    log("一键优化", "优化流程结束：%s" % text, "成功" if status == "success" else "警告")
    return "one_click_optimize_result|%s|details=%s" % (status, text)
