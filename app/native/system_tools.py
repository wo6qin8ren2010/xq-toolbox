"""“系统命令”面板：把 48 个按钮映射到对应的 Windows 控制台/设置入口。"""
from __future__ import annotations

import os
import subprocess

from .paths import log

WINDIR = os.environ.get("WINDIR", r"C:\Windows")
SYSTEM32 = os.path.join(WINDIR, "System32")

COMMANDS = {
    "control": ["control.exe"],
    "settings": ["cmd", "/c", "start", "ms-settings:"],
    "info": ["msinfo32.exe"],
    "about": ["cmd", "/c", "start", "ms-settings:about"],
    "display": ["cmd", "/c", "start", "ms-settings:display"],
    "sound": ["cmd", "/c", "start", "ms-settings:sound"],
    "network": ["control.exe", "ncpa.cpl"],
    "bluetooth": ["cmd", "/c", "start", "ms-settings:bluetooth"],
    "devices": ["devmgmt.msc"],
    "disk": ["diskmgmt.msc"],
    "storage": ["cmd", "/c", "start", "ms-settings:storagesense"],
    "computer": ["compmgmt.msc"],
    "tasks": ["taskmgr.exe"],
    "resmon": ["resmon.exe"],
    "perfmon": ["perfmon.exe"],
    "reliability": ["perfmon.exe", "/rel"],
    "events": ["eventvwr.msc"],
    "services": ["services.msc"],
    "power": ["powercfg.cpl"],
    "power_options": ["cmd", "/c", "start", "ms-settings:powersleep"],
    "env": ["rundll32.exe", "sysdm.cpl,EditEnvironmentVariables"],
    "hosts": ["notepad.exe", os.path.join(SYSTEM32, "drivers", "etc", "hosts")],
    "registry": ["regedit.exe"],
    "group_policy": ["gpedit.msc"],
    "security_policy": ["secpol.msc"],
    "users": ["lusrmgr.msc"],
    "certificates": ["certmgr.msc"],
    "odbc": [os.path.join(SYSTEM32, "odbcad32.exe")],
    "command": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "terminal": ["wt.exe"],
    "cleanmgr": ["cleanmgr.exe"],
    "defrag": ["dfrgui.exe"],
    "memory_diag": ["MdSched.exe"],
    "restore": ["rstrui.exe"],
    "startup": ["cmd", "/c", "start", "ms-settings:startupapps"],
    "apps": ["cmd", "/c", "start", "ms-settings:appsfeatures"],
    "optional_features": ["cmd", "/c", "start", "ms-settings:optionalfeatures"],
    "recovery": ["cmd", "/c", "start", "ms-settings:recovery"],
    "security": ["cmd", "/c", "start", "windowsdefender:"],
    "personalization": ["cmd", "/c", "start", "ms-settings:personalization"],
    "privacy": ["cmd", "/c", "start", "ms-settings:privacy"],
    "datetime": ["cmd", "/c", "start", "ms-settings:dateandtime"],
    "clipboard": ["cmd", "/c", "start", "ms-settings:clipboard"],
}


def open_tool(tool_id: str) -> bool:
    if tool_id == "classic_context":
        return _classic_context()
    if tool_id == "restart_explorer":
        return _restart_explorer()
    if tool_id == "firewall":
        return _firewall_off()
    command = COMMANDS.get(tool_id)
    if not command:
        log("系统命令", "未知的系统命令：%s" % tool_id, "警告")
        return False
    try:
        subprocess.Popen(command, shell=False, creationflags=0x08000000)
        log("系统命令", "已打开：%s" % tool_id)
        return True
    except Exception as exc:
        log("系统命令", "打开失败：%s（%s）" % (tool_id, exc), "错误")
        return False


def _classic_context() -> bool:
    key = r"HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    try:
        subprocess.run(["reg", "add", key, "/ve", "/f"], capture_output=True, timeout=15, creationflags=0x08000000)
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, timeout=15, creationflags=0x08000000)
        subprocess.Popen(["explorer.exe"], creationflags=0x08000000)
        log("系统命令", "已切换为经典右键菜单并重启资源管理器", "成功")
        return True
    except Exception as exc:
        log("系统命令", "切换右键菜单失败：%s" % exc, "错误")
        return False


def _restart_explorer() -> bool:
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, timeout=15, creationflags=0x08000000)
        subprocess.Popen(["explorer.exe"], creationflags=0x08000000)
        log("系统命令", "资源管理器已重启", "成功")
        return True
    except Exception as exc:
        log("系统命令", "重启资源管理器失败：%s" % exc, "错误")
        return False


def _firewall_off() -> bool:
    try:
        completed = subprocess.run(
            ["netsh", "advfirewall", "set", "allprofiles", "state", "off"],
            capture_output=True, timeout=20, creationflags=0x08000000,
        )
        ok = completed.returncode == 0
        log("系统命令", "防火墙已关闭" if ok else "关闭防火墙失败", "成功" if ok else "错误")
        return ok
    except Exception as exc:
        log("系统命令", "关闭防火墙异常：%s" % exc, "错误")
        return False
