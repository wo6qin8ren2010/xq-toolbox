# -*- coding: utf-8 -*-
"""联想软件商店（lestore.lenovo.com）接口封装。

原版的“软件下载”页由 C++ 侧调用联想商店网关，请求体是 AES-128-CBC 加密的
``{"data": "<base64>"}``。密钥与 IV 都取自联想商店网页版前端脚本（同一串 16 字节，
Key 与 IV 相同），因此可以按同样的方式直接请求，不需要任何私有签名服务。

对外提供两个能力：
    1. search(keyword)                  -> 搜索软件，返回网关原始 JSON（status/data/apps）
    2. resolve_download(soft_id, biz)   -> 解析官方安装包直链，返回 status/data/downloadUrls
"""

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://lestore.lenovo.com"
SEARCH_PATH = "/api/webstorecontents/search/contents"
DOWNLOAD_PATH = "/api/webstorecontents/download/getDownloadUrl"

# 联想商店网页版前端使用的 AES 密钥（同时作为 IV）
_AES_KEY = "65023EC4BA7420BB"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _aes_encrypt_b64(text: str) -> str:
    """用 .NET 的 AES-128-CBC(PKCS7) 加密，返回 base64。"""
    import clr  # noqa: F401  (pythonnet 随 pycaw 一起加载)

    from System.Security.Cryptography import Aes, CipherMode, PaddingMode
    from System.Text import Encoding
    from System import Convert

    encoding = Encoding.UTF8
    key = encoding.GetBytes(_AES_KEY)
    aes = Aes.Create()
    aes.Mode = CipherMode.CBC
    aes.Padding = PaddingMode.PKCS7
    aes.Key = key
    aes.IV = key
    encryptor = aes.CreateEncryptor()
    plain = encoding.GetBytes(text)
    cipher = encryptor.TransformFinalBlock(plain, 0, plain.Length)
    return str(Convert.ToBase64String(cipher))


def _post(path: str, payload: dict, timeout: int = 20) -> dict:
    body = json.dumps({"data": _aes_encrypt_b64(json.dumps(payload, ensure_ascii=False))}, ensure_ascii=False)
    request = urllib.request.Request(
        BASE + path,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE,
            "Referer": BASE + "/",
            "User-Agent": _UA,
        },
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        text = response.read().decode("utf-8", "replace")
    return json.loads(text)


def _fail(message: str) -> dict:
    return {"status": 1, "message": message, "data": None}


def search(keyword: str) -> dict:
    keyword = (keyword or "").strip()
    if not keyword:
        return _fail("搜索关键词不能为空")
    try:
        data = _post(SEARCH_PATH, {"searchKey": keyword})
    except urllib.error.HTTPError as exc:
        return _fail("联想商店返回 HTTP %s" % exc.code)
    except Exception as exc:  # 网络/解析异常统一兜底
        return _fail("无法连接联想软件下载服务：%s" % exc)
    if not isinstance(data, dict):
        return _fail("联想商店返回数据格式异常")
    return data


def resolve_download(soft_id: str, biz_info: str) -> dict:
    soft_id = (soft_id or "").strip()
    if not soft_id:
        return _fail("缺少软件 ID")
    payload = {
        "bizInfo": biz_info or "",
        "bizType": "1",
        "product": "3",
        "softId": soft_id,
        "type": "0",
    }
    try:
        data = _post(DOWNLOAD_PATH, payload)
    except urllib.error.HTTPError as exc:
        return _fail("联想商店返回 HTTP %s" % exc.code)
    except Exception as exc:
        return _fail("下载链接解析失败：%s" % exc)
    if not isinstance(data, dict):
        return _fail("联想商店返回数据格式异常")

    urls = ((data.get("data") or {}).get("downloadUrls") or []) if isinstance(data.get("data"), dict) else []
    if data.get("status") == 0 and not urls:
        return _fail("未找到可用的下载链接")
    return data
