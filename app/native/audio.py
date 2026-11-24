"""音频设备 / 应用混音器：基于 Windows Core Audio（pycaw）。"""
from __future__ import annotations

import json
import subprocess
import threading
import time

from .paths import log

_lock = threading.RLock()
_lock_state = {"enabled": False, "target": 100, "thread": None}

_STATE_MAP = {"Active": 1, "Disabled": 2, "NotPresent": 4, "Unplugged": 8}


def _com():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


def _state_code(state) -> int:
    name = getattr(state, "name", None)
    if name in _STATE_MAP:
        return _STATE_MAP[name]
    try:
        return int(state)
    except Exception:
        return 4


def _flow_is_capture(device_id: str) -> bool:
    try:
        from pycaw.pycaw import AudioUtilities
        return int(AudioUtilities.GetEndpointDataFlow(device_id, 1)) == 1
    except Exception:
        return False


def _default_device(flow: int):
    """flow: 0=播放, 1=录制；返回 AudioDevice 包装对象。"""
    _com()
    try:
        from pycaw.pycaw import AudioUtilities
        from pycaw.constants import ERole
        enumerator = AudioUtilities.GetDeviceEnumerator()
        device = enumerator.GetDefaultAudioEndpoint(int(flow), 0)
        return AudioUtilities.CreateDevice(device)
    except Exception as exc:
        log("音频设备", "读取默认设备失败：%s" % exc, "警告")
        return None


def list_devices() -> list:
    _com()
    from pycaw.pycaw import AudioUtilities

    with _lock:
        default_render = _default_device(0)
        default_render_id = default_render.id if default_render is not None else ""
        default_capture = _default_device(1)
        default_capture_id = default_capture.id if default_capture is not None else ""

        items = []
        for device in AudioUtilities.GetAllDevices():
            try:
                state = _state_code(device.state)
                capture = _flow_is_capture(device.id)
                default = device.id == (default_capture_id if capture else default_render_id)
                items.append({
                    "id": device.id,
                    "name": device.FriendlyName or "未命名设备",
                    "capture": capture,
                    "state": state,
                    "enabled": state == 1,
                    "default": bool(default),
                })
            except Exception as exc:
                log("音频设备", "读取设备失败：%s" % exc, "警告")
        items.sort(key=lambda x: (x["capture"], 0 if x["state"] == 1 else 1, x["name"]))
        return items


def devices_message() -> str:
    return "audio_devices_result|" + json.dumps(list_devices(), ensure_ascii=False)


def set_default(kind: str, device_id: str) -> str:
    _com()
    try:
        from pycaw.pycaw import AudioUtilities
        from pycaw.constants import ERole

        AudioUtilities.SetDefaultDevice(device_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
        log("音频设备", "已切换默认%s设备：%s" % ("录制" if kind == "capture" else "播放", device_id))
        return "audio_set_default_result|success"
    except Exception as exc:
        log("音频设备", "切换默认设备失败：%s" % exc, "错误")
        return "audio_set_default_result|failed"


def list_sessions() -> list:
    _com()
    from pycaw.pycaw import AudioUtilities

    result = []
    seen = set()
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as exc:
        log("应用混音器", "枚举会话失败：%s" % exc, "警告")
        return result

    for session in sessions:
        try:
            volume = session.SimpleAudioVolume.GetMasterVolume() if session.SimpleAudioVolume else 0.0
        except Exception:
            continue
        process = getattr(session, "Process", None)
        if process is not None:
            try:
                name = process.name()
                pid = int(process.pid)
            except Exception:
                continue
            key = (name, pid)
        else:
            name = "系统声音"
            pid = 0
            key = (name, 0)
        if key in seen:
            continue
        seen.add(key)
        result.append({"pid": pid, "name": name, "volume": round(volume * 100.0, 2)})
    # 系统声音置顶，其余按名称排序，和原版展示顺序一致
    result.sort(key=lambda x: (0 if x["pid"] == 0 else 1, x["name"].lower()))
    return result


def sessions_message() -> str:
    return "audio_sessions_result|" + json.dumps(list_sessions(), ensure_ascii=False)


def set_session_volume(pid: int, value: float) -> None:
    _com()
    from pycaw.pycaw import AudioUtilities

    for session in AudioUtilities.GetAllSessions():
        process = getattr(session, "Process", None)
        try:
            session_pid = int(process.pid) if process is not None else 0
        except Exception:
            continue
        if session_pid == pid:
            try:
                session.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, value / 100.0)), None)
            except Exception as exc:
                log("应用混音器", "设置音量失败：%s" % exc, "警告")


def _mic():
    return _default_device(1)


def mic_volume() -> int:
    _com()
    try:
        device = _mic()
        return int(round(device.volume_percent)) if device is not None else 0
    except Exception:
        return 0


def capture_status() -> str:
    actual = mic_volume()
    enabled = 1 if _lock_state["enabled"] else 0
    return "audio_capture_lock_status|success|%d|%d|%d|1" % (enabled, _lock_state["target"], actual)


def _lock_worker(target: int) -> None:
    _com()
    while _lock_state["enabled"] and _lock_state["target"] == target:
        try:
            device = _mic()
            if device is not None:
                current = abs(int(round(device.volume_percent)) - target)
                if current >= 2:
                    device.EndpointVolume.SetMasterVolumeLevelScalar(min(1.0, max(0.0, target / 100.0)), None)
        except Exception as exc:
            log("音频锁定", "锁定内录音量失败：%s" % exc, "警告")
            break
        time.sleep(1.0)


def set_capture_lock(enabled: bool, value: int) -> str:
    _com()
    _lock_state["target"] = max(0, min(100, int(value)))
    _lock_state["enabled"] = bool(enabled)
    if enabled:
        try:
            device = _mic()
            if device is not None:
                device.EndpointVolume.SetMasterVolumeLevelScalar(_lock_state["target"] / 100.0, None)
        except Exception as exc:
            log("音频锁定", "设置内录音量失败：%s" % exc, "错误")
            _lock_state["enabled"] = False
            return "audio_capture_lock_result|failed"
        thread = threading.Thread(target=_lock_worker, args=(_lock_state["target"],), daemon=True)
        _lock_state["thread"] = thread
        thread.start()
        log("音频锁定", "已锁定默认内录音量到 %d%%" % _lock_state["target"])
        return "audio_capture_lock_result|on"
    log("音频锁定", "已解除默认内录音量锁定")
    return "audio_capture_lock_result|off"


def set_capture_volume(value: int) -> str:
    _com()
    try:
        device = _mic()
        if device is None:
            return "audio_capture_volume_result|%d|failed" % value
        device.EndpointVolume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, int(value) / 100.0)), None)
        if _lock_state["enabled"]:
            _lock_state["target"] = int(value)
        return "audio_capture_volume_result|%d|success" % value
    except Exception as exc:
        log("音频锁定", "设置内录音量失败：%s" % exc, "错误")
        return "audio_capture_volume_result|%d|failed" % value


def _ps_instance_id(device_id: str) -> str:
    _com()
    from pycaw.pycaw import AudioUtilities
    for device in AudioUtilities.GetAllDevices():
        if device.id != device_id:
            continue
        for key, value in (device.properties or {}).items():
            if key.startswith("{9C119480-DDC2-4954-A150-5BD240D454AD}") and str(value).startswith("SWD\\MMDEVAPI"):
                return str(value)
    return ""


def set_enabled(device_id: str, enabled: bool) -> str:
    instance = _ps_instance_id(device_id)
    if not instance:
        return "audio_set_enabled_result|failed"
    verb = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "%s -InstanceId '%s' -Confirm:$false" % (verb, instance)],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000,
        )
        if completed.returncode == 0 and not completed.stderr.strip():
            log("音频设备", "%s设备：%s" % ("启用" if enabled else "禁用", device_id))
            return "audio_set_enabled_result|success"
        log("音频设备", "设备状态修改失败：%s" % (completed.stderr.strip()[:200] or completed.stdout.strip()[:200]), "错误")
    except Exception as exc:
        log("音频设备", "设备状态修改异常：%s" % exc, "错误")
    return "audio_set_enabled_result|failed"


def set_sample_rate(rate: str) -> str:
    """改所有活动端点的默认格式采样率（直接写 Core Audio 端点格式属性）。"""
    try:
        target = int(rate)
    except Exception:
        return "audio_set_sample_rate_result|failed"
    if target not in (44100, 48000, 88200, 96000, 192000, 176400, 22050, 32000):
        return "audio_set_sample_rate_result|%s|failed" % rate
    _com()
    lock = _lock
    with lock:
        try:
            from . import audio_format
            ok, fail, details = audio_format.apply_rate(target, logger=log)
        except Exception as exc:
            log("采样率", "批量改写采样率异常：%s" % exc, "错误")
            return "audio_set_sample_rate_result|%s|failed" % rate
    log("采样率", "共 %d 个端点成功、%d 个失败（目标 %d Hz）" % (ok, fail, target))
    return "audio_set_sample_rate_result|%d|%s" % (target, "success" if ok else "failed")
