# -*- coding: utf-8 -*-
"""系统音乐组件：读取 Windows 媒体会话（SMTC），控制播放/暂停/上下曲，并按进度匹配歌词。

设计沿用 sysinfo 的原则：所有耗时操作（WinRT 异步调用、封面读取、歌词联网）
都在后台线程里做，前台 snapshot() 只读缓存，绝不阻塞界面。
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from .paths import log

_POLL = 1.0
_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}
_COVER_MAX = 220 * 1024

_lock = threading.Lock()
_state = {
    "active": False, "title": "", "artist": "", "album": "", "source": "",
    "status": "none", "position": 0.0, "duration": 0.0,
    "canPlay": False, "canPause": False, "canNext": False, "canPrev": False,
    "cover": "", "lyric": "", "lyricNext": "", "lyricState": "idle",
}
_worker = None
_worker_lock = threading.Lock()
_loop = None
_session_ref = {"session": None}

_lyric_lock = threading.Lock()
_lyric_cache = {}          # (title, artist) -> list[(sec, text)] 或 None
_lyric_key = {"key": None}
_lyric_busy = {"flag": False}


def _empty_state() -> dict:
    return {
        "active": False, "title": "", "artist": "", "album": "", "source": "",
        "status": "none", "position": 0.0, "duration": 0.0,
        "canPlay": False, "canPause": False, "canNext": False, "canPrev": False,
        "cover": "", "lyric": "", "lyricNext": "", "lyricState": "idle",
    }


def _seconds(value) -> float:
    try:
        if hasattr(value, "total_seconds"):
            return float(value.total_seconds())
        return float(value) / 1e7
    except Exception:
        return 0.0


# ----------------------------------------------------------------------
# 歌词：联网按曲名 + 歌手匹配，失败就留空，绝不影响播放信息
# ----------------------------------------------------------------------
_BAD_WORDS = ("原唱", "翻唱", "cover", "伴奏", "纯音乐", "dj", "remix", "live", "试听", "钢琴版", "女声版", "男声版")
_CREDIT_KEY = re.compile(
    r"(作词|作曲|编曲|制作|出品|监制|混音|母带|录音|版权|发行|统筹|和声|合声|吉他|贝斯|鼓手|弦乐|管乐|"
    r"词|曲|鼓|键盘|合成器|打击乐|op|sp|isrc|lyric|compos|writ|produc|arrang|mix|master|record|vocal)", re.I)
_CREDIT = re.compile(r"^\s*([^:：]{1,16})[:：]")
_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def _norm(text: str) -> str:
    text = re.sub(r"[\(（\[【].*?[\)）\]】]", "", str(text or ""))
    return re.sub(r"[\s\-_·.,!?'\"’]+", "", text).lower()


def _http_json(url: str, referer: str = ""):
    headers = dict(_UA)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _score(name: str, artists: str, title: str, artist: str) -> int:
    """打分选最接近的原唱版本，避免搜到翻唱/伴奏。"""
    want_t, want_a = _norm(title), _norm(artist)
    got_t, got_a = _norm(name), _norm(artists)
    score = 0
    if want_t and got_t == want_t:
        score += 6
    elif want_t and want_t in got_t:
        score += 2
    if want_a and got_a and (want_a in got_a or got_a in want_a):
        score += 4
    low = str(name).lower()
    for bad in _BAD_WORDS:
        if bad in low and bad not in str(title).lower():
            score -= 5
    return score


def _candidates(title: str, artist: str) -> list:
    """QQ 音乐为主（排序准），网易云兜底。任一来源失败都不影响另一个。"""
    query = urllib.parse.quote((title + " " + artist).strip())
    out = []
    try:
        data = _http_json(
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=%s&format=json&n=8&p=1" % query,
            "https://y.qq.com/")
        for song in ((data.get("data") or {}).get("song") or {}).get("list") or []:
            out.append({
                "src": "qq", "id": song.get("songmid"),
                "name": song.get("songname", ""),
                "artists": " ".join(a.get("name", "") for a in song.get("singer", [])),
            })
    except Exception as exc:
        log("音乐组件", "QQ 搜索失败：%s" % str(exc)[:70], "警告")
    try:
        data = _http_json("https://music.163.com/api/search/get?s=%s&type=1&limit=8" % query)
        for song in (data.get("result") or {}).get("songs") or []:
            out.append({
                "src": "163", "id": song.get("id"),
                "name": song.get("name", ""),
                "artists": " ".join(a.get("name", "") for a in song.get("artists", [])),
            })
    except Exception as exc:
        log("音乐组件", "网易云搜索失败：%s" % str(exc)[:70], "警告")
    for cand in out:
        cand["score"] = _score(cand["name"], cand["artists"], title, artist)
    out.sort(key=lambda c: -c["score"])
    return out


def _lyric_text(cand: dict) -> str:
    if cand["src"] == "qq":
        data = _http_json(
            "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid=%s&format=json&nobase64=1" % cand["id"],
            "https://y.qq.com/portal/player.html")
        text = data.get("lyric") or ""
        if text and not text.lstrip().startswith("["):
            text = base64.b64decode(text).decode("utf-8", "ignore")
        return text
    data = _http_json("https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1" % cand["id"])
    return (data.get("lrc") or {}).get("lyric", "")


def _parse_lrc(text: str, title: str = "") -> list:
    out = []
    head = (re.compile(r"^\s*" + re.escape(str(title)) + r"(?:\s*[\(（\[【][^\)）\]】]*[\)）\]】])*\s*[-—–]\s*", re.I)
            if title else None)
    for raw in str(text or "").splitlines():
        stamps = _TIME.findall(raw)
        if not stamps:
            continue
        body = _TIME.sub("", raw).strip()
        if not body or body == "//":
            continue
        label = _CREDIT.match(body)
        if label and _CREDIT_KEY.search(label.group(1)):
            continue
        if head is not None and head.match(body):
            continue
        for mm, ss, frac in stamps:
            frac = (frac or "0").ljust(3, "0")[:3]
            out.append((int(mm) * 60 + int(ss) + int(frac) / 1000.0, body))
    out.sort(key=lambda x: x[0])
    return out


def _fetch_lyrics(title: str, artist: str):
    key = (title, artist)
    lines = None
    try:
        for cand in _candidates(title, artist)[:3]:
            if cand["score"] < 6:
                break
            try:
                lines = _parse_lrc(_lyric_text(cand), title) or None
            except Exception:
                lines = None
            if lines:
                break
    except Exception as exc:
        log("音乐组件", "歌词获取失败（%s）：%s" % (title, str(exc)[:80]), "警告")
        lines = None
    with _lyric_lock:
        if len(_lyric_cache) > 40:
            _lyric_cache.clear()
        _lyric_cache[key] = lines
    return lines


def _lyrics_for(title: str, artist: str):
    """返回 (lines, state)。未取到就异步去取，本次先返回空。"""
    key = (title, artist)
    with _lyric_lock:
        if key in _lyric_cache:
            lines = _lyric_cache[key]
            return lines, ("ready" if lines else "none")
    if not _lyric_busy["flag"]:
        _lyric_busy["flag"] = True

        def run():
            try:
                _fetch_lyrics(title, artist)
            finally:
                _lyric_busy["flag"] = False

        threading.Thread(target=run, name="xq-lyric", daemon=True).start()
    return None, "loading"


def _line_at(lines: list, pos: float):
    if not lines:
        return "", ""
    idx = -1
    for i, (sec, _text) in enumerate(lines):
        if sec <= pos + 0.25:
            idx = i
        else:
            break
    if idx < 0:
        return "", lines[0][1]
    nxt = lines[idx + 1][1] if idx + 1 < len(lines) else ""
    return lines[idx][1], nxt


# ----------------------------------------------------------------------
# WinRT 会话读取
# ----------------------------------------------------------------------
async def _read_cover(props) -> str:
    try:
        ref = props.thumbnail
        if ref is None:
            return ""
        import winrt.system as _ws
        from winrt.windows.storage.streams import DataReader
        stream = await ref.open_read_async()
        size = int(stream.size)
        if size <= 0 or size > _COVER_MAX:
            return ""
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(size)
        # pywinrt 新版 read_bytes 只接受 winrt.system.Array / buffer，不再接受 int
        buf = _ws.Array(_ws.UInt8, size)
        reader.read_bytes(buf)
        raw = bytes(bytearray(buf))
        mime = "image/jpeg"
        if raw[:8].startswith(b"\x89PNG"):
            mime = "image/png"
        elif raw[:4] == b"RIFF":
            mime = "image/webp"
        return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))
    except Exception as exc:
        log("音乐组件", "读取专辑封面失败：%s" % str(exc)[:120], "警告")
        return ""


async def _tick():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )
    mgr = await Manager.request_async()
    cover_key, cover_val = None, ""
    cover_tries, cover_next = 0, 0.0
    while True:
        try:
            session = mgr.get_current_session()
            if session is None:
                sessions = mgr.get_sessions()
                session = sessions[0] if len(sessions) else None
            if session is None:
                with _lock:
                    _state.update(_empty_state())
                _session_ref["session"] = None
                cover_key, cover_val = None, ""
                cover_tries, cover_next = 0, 0.0
            else:
                _session_ref["session"] = session
                props = await session.try_get_media_properties_async()
                info = session.get_playback_info()
                line = session.get_timeline_properties()
                status = int(info.playback_status)
                pos = _seconds(line.position)
                dur = _seconds(line.end_time)
                if status == 4:
                    try:
                        import datetime
                        updated = line.last_updated_time
                        if updated is not None:
                            drift = (datetime.datetime.now(datetime.timezone.utc) - updated).total_seconds()
                            if 0 < drift < 30:
                                pos += drift
                    except Exception:
                        pass
                if dur > 0:
                    pos = max(0.0, min(pos, dur))
                title = props.title or ""
                artist = props.artist or ""
                key = (title, artist)
                if key != cover_key:
                    cover_key, cover_val, cover_tries, cover_next = key, "", 0, 0.0
                # 部分播放器先给标题、稍后才给缩略图，无封面时按间隔重试几次
                now_ts = time.time()
                if not cover_val and cover_tries < 8 and now_ts >= cover_next:
                    got = await _read_cover(props)
                    if got:
                        cover_val, cover_tries = got, 0
                    else:
                        cover_tries += 1
                        cover_next = now_ts + 3.0
                lines, lstate = _lyrics_for(title, artist) if title else (None, "idle")
                cur, nxt = _line_at(lines, pos)
                ctrl = info.controls
                with _lock:
                    _state.update({
                        "active": True,
                        "title": title or "未知曲目",
                        "artist": artist,
                        "album": props.album_title or "",
                        "source": session.source_app_user_model_id or "",
                        "status": {4: "playing", 5: "paused", 3: "stopped"}.get(status, "other"),
                        "position": round(pos, 2),
                        "duration": round(dur, 2),
                        "canPlay": bool(ctrl.is_play_enabled),
                        "canPause": bool(ctrl.is_pause_enabled),
                        "canNext": bool(ctrl.is_next_enabled),
                        "canPrev": bool(ctrl.is_previous_enabled),
                        "cover": cover_val,
                        "lyric": cur,
                        "lyricNext": nxt,
                        "lyricState": lstate,
                    })
        except Exception as exc:
            log("音乐组件", "读取系统媒体会话失败：%s" % str(exc)[:100], "警告")
            with _lock:
                _state.update(_empty_state())
        await asyncio.sleep(_POLL)


def _thread_main():
    global _loop
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_tick())
    except Exception as exc:
        log("音乐组件", "后台采集退出：%s" % str(exc)[:100], "警告")


def prime() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            return
        try:
            import winrt.windows.media.control  # noqa: F401
        except Exception as exc:
            log("音乐组件", "系统媒体接口不可用：%s" % str(exc)[:80], "警告")
            return
        _worker = threading.Thread(target=_thread_main, name="xq-music", daemon=True)
        _worker.start()


def snapshot() -> dict:
    prime()
    with _lock:
        return dict(_state)


def as_message() -> str:
    return "music|" + json.dumps(snapshot(), ensure_ascii=False)


def control(action: str) -> str:
    """把控制指令投到后台事件循环，立即返回，不阻塞界面。"""
    prime()
    session = _session_ref.get("session")
    loop = _loop
    if session is None or loop is None:
        return "music_control_result|%s|nosession" % action

    async def run():
        if action == "next":
            return await session.try_skip_next_async()
        if action == "prev":
            return await session.try_skip_previous_async()
        status = int(session.get_playback_info().playback_status)
        if status == 4:
            return await session.try_pause_async()
        return await session.try_play_async()

    try:
        asyncio.run_coroutine_threadsafe(run(), loop)
        log("音乐组件", "控制指令：%s" % action, "用户操作")
        return "music_control_result|%s|sent" % action
    except Exception as exc:
        return "music_control_result|%s|failed" % action