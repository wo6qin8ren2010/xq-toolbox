"""音频端点格式（采样率）读写：直接改 Core Audio 端点的格式属性。

原理：Windows 把每个端点的格式存在属性存储里
  PKEY_AudioEngine_DeviceFormat (DeviceFormat)  —— 端点当前/默认格式
  PKEY_AudioEngine_OEMFormat    (OEMFormat)     —— 驱动厂商提供的格式（可能不存在）
把 WAVEFORMATEX(TENSIBLE) 里的 nSamplesPerSec 换掉、并按 nBlockAlign 重算
nAvgBytesPerSec，然后 SetValue + Commit，Windows 就会按新格式重新配置端点。
"""
from __future__ import annotations

import ctypes
import struct
from ctypes import POINTER, Structure, byref, c_ubyte, c_ulong, c_ushort, c_void_p, cast

from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

STGM_READ = 0x00000000
STGM_READWRITE = 0x00000002
VT_BLOB = 65

PKEY_DEVICE_FORMAT = GUID("{f19f064d-082c-4e27-bc73-6882a1bb8e4c}")
PKEY_OEM_FORMAT = GUID("{e4870e26-3cc5-4cd2-ba46-ca0a9a70ed04}")


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class _BLOB(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", POINTER(c_ubyte))]


class PROPVARIANT(Structure):  # x64 上共 24 字节，与系统 PROPVARIANT 等长
    _fields_ = [
        ("vt", c_ushort),
        ("r1", c_ushort),
        ("r2", c_ushort),
        ("r3", c_ushort),
        ("blob", _BLOB),
    ]


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "cProps")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], c_ulong, "iProp"),
                  (["out"], POINTER(PROPERTYKEY), "pkey")),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["out"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "SetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "propvar")),
        COMMETHOD([], HRESULT, "Commit"),
    )


def _key(guid, pid) -> PROPERTYKEY:
    k = PROPERTYKEY()
    k.fmtid = guid
    k.pid = pid
    return k


DEVICE_KEY = _key(PKEY_DEVICE_FORMAT, 0)
OEM_KEY = _key(PKEY_OEM_FORMAT, 3)


def _open(dev, mode):
    return dev.OpenPropertyStore(mode).QueryInterface(IPropertyStore)


def _read(store, pk):
    pv = store.GetValue(byref(pk))
    if pv.vt != VT_BLOB or not pv.blob.cbSize:
        return None
    size = pv.blob.cbSize
    buf = (c_ubyte * size).from_address(cast(pv.blob.pBlobData, c_void_p).value)
    data = bytes(buf)
    ctypes.windll.ole32.PropVariantClear(byref(pv))
    return data


def _pv(data):
    pv = PROPVARIANT()
    pv.vt = VT_BLOB
    buf = (c_ubyte * len(data))(*data)
    pv.blob.cbSize = len(data)
    pv.blob.pBlobData = cast(buf, POINTER(c_ubyte))
    pv._keep = buf  # 防止 GC 掉底层缓冲
    return pv


def _write(store, pk, data) -> None:
    pv = _pv(data)
    store.SetValue(byref(pk), byref(pv))
    store.Commit()


def parse(data):
    """返回 (格式名, 声道, 采样率, 位深)。"""
    tag, ch, rate, _bps, _align, bits, cbsize = struct.unpack_from("<HHIIHHH", data, 0)
    name = {1: "PCM", 3: "IEEE_FLOAT", 0xFFFE: "EXTENSIBLE"}.get(tag, str(tag))
    return name, ch, rate, bits


def change_rate(data, rate) -> bytes:
    """只改 nSamplesPerSec 与 nAvgBytesPerSec，其余字节（位深/声道/掩码/子格式）原样保留。"""
    tag, ch, _rate, _bps, align, bits, _cb = struct.unpack_from("<HHIIHHH", data, 0)
    if not align:
        raise ValueError("端点格式非法（nBlockAlign=0）")
    out = bytearray(data)
    struct.pack_into("<I", out, 4, rate)
    struct.pack_into("<I", out, 8, align * rate)
    return bytes(out)


def active_devices():
    devices = []
    for d in _all_devices():
        if _state_value(d) == 1:
            devices.append(d)
    return devices


def _all_devices():
    from pycaw.utils import AudioUtilities
    return AudioUtilities.GetAllDevices()


def _state_value(device) -> int:
    state = getattr(device, "state", 4)
    value = getattr(state, "value", state)
    try:
        return int(value)
    except Exception:
        return 4


def apply_rate(rate: int, logger=None):
    """对所有活动端点改成指定采样率，返回 (成功数, 失败数, 明细列表)。"""
    ok = fail = 0
    details = []
    for device in active_devices():
        name = device.FriendlyName or device.id
        try:
            rw = _open(device._dev, STGM_READWRITE)
            dev_fmt = _read(rw, DEVICE_KEY)
            oem_fmt = _read(rw, OEM_KEY)
            base = oem_fmt or dev_fmt
            if not base:
                raise ValueError("端点没有格式属性")
            old_rate = parse(base)[2]
            new_fmt = change_rate(base, rate)
            _write(rw, DEVICE_KEY, new_fmt)
            if oem_fmt:
                _write(rw, OEM_KEY, new_fmt)
            ro = _open(device._dev, STGM_READ)
            got = _read(ro, DEVICE_KEY)
            if not got or parse(got)[2] != int(rate):
                raise ValueError("回读校验失败（%s Hz）" % (parse(got)[2] if got else "空"))
            ok += 1
            details.append("%s: %d → %d Hz" % (name, old_rate, rate))
            if logger:
                logger("采样率", "%s：%d → %d Hz 已生效" % (name, old_rate, rate))
        except Exception as exc:
            fail += 1
            details.append("%s: 失败 %s" % (name, exc))
            if logger:
                logger("采样率", "%s 修改失败：%s" % (name, exc), "警告")
    return ok, fail, details
