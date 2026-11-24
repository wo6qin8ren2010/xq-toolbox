# -*- coding: utf-8 -*-
"""自建库后台（本地网页版）。

用途：给自己维护一份“项目档案 + 软件清单”，在浏览器里增删改，数据存本地 JSON，
软件本体通过同一个数据文件读取并展示（侧栏“我的软件库”）。

启动：
    python admin/server.py                # 只允许本机访问，自动打开浏览器
    python admin/server.py --host 0.0.0.0 # 允许局域网访问（写入需要口令）

数据文件：%LOCALAPPDATA%\\MixerToolsRebuild\\自建库.json
口令文件：%LOCALAPPDATA%\\MixerToolsRebuild\\后台口令.txt
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MixerToolsRebuild")
DATA_FILE = os.path.join(DATA_DIR, "自建库.json")
TOKEN_FILE = os.path.join(DATA_DIR, "后台口令.txt")
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

_LOCK = threading.Lock()

EMPTY_DATA = {"version": 1, "software": [], "projects": []}


# ----------------------------------------------------------------------------
# 数据读写
# ----------------------------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:10]


def _normalize(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}
    software = data.get("software") if isinstance(data.get("software"), list) else []
    projects = data.get("projects") if isinstance(data.get("projects"), list) else []
    for item in software:
        item.setdefault("id", _new_id("s"))
        item.setdefault("name", "未命名软件")
        item.setdefault("category", "未分类")
        item.setdefault("icon", "")
        item.setdefault("version", "")
        item.setdefault("size", "")
        item.setdefault("intro", "")
        item.setdefault("url", "")
        item.setdefault("note", "")
        item.setdefault("createdAt", _now())
        item.setdefault("updatedAt", item["createdAt"])
    for project in projects:
        project.setdefault("id", _new_id("p"))
        project.setdefault("name", "未命名项目")
        project.setdefault("icon", "")
        project.setdefault("intro", "")
        project.setdefault("note", "")
        project.setdefault("tags", [])
        software_ids = project.get("softwareIds")
        project["softwareIds"] = [sid for sid in software_ids if isinstance(sid, str)] if isinstance(software_ids, list) else []
        links = project.get("links")
        project["links"] = links if isinstance(links, list) else []
        for link in project["links"]:
            link.setdefault("id", _new_id("l"))
            link.setdefault("name", "未命名链接")
            link.setdefault("url", "")
            link.setdefault("note", "")
        project.setdefault("createdAt", _now())
        project.setdefault("updatedAt", project["createdAt"])
    return {"version": 1, "software": software, "projects": projects}


def load_data() -> dict:
    with _LOCK:
        if not os.path.isfile(DATA_FILE):
            return json.loads(json.dumps(EMPTY_DATA))
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                return _normalize(json.load(fh))
        except Exception:
            # 损坏文件保留一份 .bad 再退回空库，避免直接丢数据
            try:
                shutil.copy2(DATA_FILE, DATA_FILE + ".bad")
            except Exception:
                pass
            return json.loads(json.dumps(EMPTY_DATA))


def save_data(data: dict) -> None:
    with _LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_normalize(data), fh, ensure_ascii=False, indent=2)
        if os.path.isfile(DATA_FILE):
            try:
                shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
            except Exception:
                pass
        os.replace(tmp, DATA_FILE)


def get_token() -> str:
    if os.path.isfile(TOKEN_FILE):
        try:
            token = open(TOKEN_FILE, "r", encoding="utf-8").read().strip()
            if token:
                return token
        except Exception:
            pass
    token = secrets.token_urlsafe(12)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "MixerToolsAdmin/1.0"
    token = ""

    # -- 基础输出 ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _error(self, code: int, message: str):
        self._json({"ok": False, "error": message}, code)

    def log_message(self, fmt, *args):  # 安静一点，只在控制台留一行
        print("[后台] %s %s" % (self.address_string(), fmt % args))

    # -- 鉴权 --------------------------------------------------------------
    def _token_from_request(self) -> str:
        query = parse_qs(urlparse(self.path).query)
        if "token" in query and query["token"]:
            return query["token"][0]
        return self.headers.get("X-Admin-Token", "")

    def _require_token(self) -> bool:
        if self._token_from_request() != self.token:
            self._error(401, "口令不正确，请在页面右上角填入后台口令")
            return False
        return True

    # -- 路由 --------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_ui()
        if path == "/api/library":
            if not self._require_token():
                return
            return self._json({"ok": True, "data": load_data()})
        if path == "/api/health":
            return self._json({"ok": True, "dataFile": DATA_FILE, "time": _now()})
        if path == "/api/export":
            if not self._require_token():
                return
            body = json.dumps(load_data(), ensure_ascii=False, indent=2).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="zijiaku.json"'})
        return self._error(404, "未知路径")

    def do_POST(self):
        if not self._require_token():
            return
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            return
        parts = [unquote(p) for p in path.strip("/").split("/")]

        if parts[:2] == ["api", "software"] and len(parts) == 2:
            return self._create_software(payload)
        if parts[:2] == ["api", "projects"] and len(parts) == 2:
            return self._create_project(payload)
        if len(parts) == 4 and parts[1] == "projects" and parts[3] == "links":
            return self._add_link(parts[2], payload)
        if len(parts) == 4 and parts[1] == "projects" and parts[3] == "software":
            return self._attach_software(parts[2], payload)
        if len(parts) == 4 and parts[1] == "software" and parts[3] == "probe":
            return self._probe(parts[2])
        if parts[:2] == ["api", "import"]:
            return self._import(payload)
        return self._error(404, "未知路径")

    def do_PUT(self):
        if not self._require_token():
            return
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            return
        parts = [unquote(p) for p in path.strip("/").split("/")]
        if len(parts) == 3 and parts[1] == "software":
            return self._update_software(parts[2], payload)
        if len(parts) == 3 and parts[1] == "projects":
            return self._update_project(parts[2], payload)
        if len(parts) == 5 and parts[1] == "projects" and parts[3] == "links":
            return self._update_link(parts[2], parts[4], payload)
        return self._error(404, "未知路径")

    def do_DELETE(self):
        if not self._require_token():
            return
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/")]
        if len(parts) == 3 and parts[1] == "software":
            return self._delete_software(parts[2])
        if len(parts) == 3 and parts[1] == "projects":
            return self._delete_project(parts[2])
        if len(parts) == 5 and parts[1] == "projects" and parts[3] == "links":
            return self._delete_link(parts[2], parts[4])
        if len(parts) == 5 and parts[1] == "projects" and parts[3] == "software":
            return self._detach_software(parts[2], parts[4])
        return self._error(404, "未知路径")

    # -- 工具 --------------------------------------------------------------
    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json({"ok": True, "data": None})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            self._error(400, "请求体不是合法 JSON")
            return None

    def _serve_ui(self):
        index = os.path.join(UI_DIR, "index.html")
        if not os.path.isfile(index):
            return self._error(500, "后台页面缺失：%s" % index)
        with open(index, "rb") as fh:
            body = fh.read()
        self._send(200, body, "text/html; charset=utf-8")

    # -- 业务：软件 --------------------------------------------------------
    def _clean_software(self, payload: dict) -> dict:
        def text(key, limit=200):
            return str(payload.get(key, "") or "").strip()[:limit]

        return {
            "name": text("name", 80),
            "category": text("category", 40) or "未分类",
            "icon": text("icon", 120000),
            "version": text("version", 40),
            "size": text("size", 40),
            "intro": text("intro", 500),
            "url": text("url", 800),
            "note": text("note", 800),
        }

    def _validate_software(self, data: dict) -> str | None:
        if not data["name"]:
            return "软件名称必填"
        if data["url"] and not (data["url"].startswith("http://") or data["url"].startswith("https://")):
            return "下载地址必须是 http(s) 链接"
        return None

    def _create_software(self, payload: dict):
        data = self._clean_software(payload)
        problem = self._validate_software(data)
        if problem:
            return self._error(400, problem)
        store = load_data()
        item = dict(data, id=_new_id("s"), createdAt=_now(), updatedAt=_now())
        store["software"].append(item)
        save_data(store)
        return self._json({"ok": True, "data": item})

    def _update_software(self, sid: str, payload: dict):
        store = load_data()
        target = next((s for s in store["software"] if s["id"] == sid), None)
        if not target:
            return self._error(404, "软件不存在")
        data = self._clean_software(payload)
        problem = self._validate_software(data)
        if problem:
            return self._error(400, problem)
        target.update(data)
        target["updatedAt"] = _now()
        save_data(store)
        return self._json({"ok": True, "data": target})

    def _delete_software(self, sid: str):
        store = load_data()
        before = len(store["software"])
        store["software"] = [s for s in store["software"] if s["id"] != sid]
        if len(store["software"]) == before:
            return self._error(404, "软件不存在")
        for project in store["projects"]:
            project["softwareIds"] = [x for x in project["softwareIds"] if x != sid]
        save_data(store)
        return self._json({"ok": True})

    def _probe(self, sid: str):
        """HEAD 探测下载地址是否可用，并尽量拿到体积。"""
        import urllib.request
        store = load_data()
        target = next((s for s in store["software"] if s["id"] == sid), None)
        if not target:
            return self._error(404, "软件不存在")
        url = target.get("url") or ""
        if not url:
            return self._json({"ok": False, "error": "该软件没有下载地址"})
        try:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MixerToolsAdmin/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                size = int(response.headers.get("Content-Length") or 0)
                ctype = response.headers.get("Content-Type") or ""
            human = ""
            if size:
                human = "%.2f MB" % (size / 1024.0 / 1024.0) if size > 1024 * 1024 else "%.0f KB" % (size / 1024.0)
            return self._json({"ok": True, "data": {"status": 200, "bytes": size, "size": human, "contentType": ctype}})
        except Exception as exc:
            return self._json({"ok": False, "error": "探测失败：%s" % exc})

    # -- 业务：项目 --------------------------------------------------------
    def _clean_project(self, payload: dict) -> dict:
        def text(key, limit=500):
            return str(payload.get(key, "") or "").strip()[:limit]

        tags = payload.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
        elif isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tags = []
        return {
            "name": text("name", 80),
            "icon": text("icon", 120000),
            "intro": text("intro", 500),
            "note": text("note", 800),
            "tags": tags,
        }

    def _create_project(self, payload: dict):
        data = self._clean_project(payload)
        if not data["name"]:
            return self._error(400, "项目名称必填")
        store = load_data()
        project = dict(data, id=_new_id("p"), softwareIds=[], links=[], createdAt=_now(), updatedAt=_now())
        store["projects"].append(project)
        save_data(store)
        return self._json({"ok": True, "data": project})

    def _update_project(self, pid: str, payload: dict):
        store = load_data()
        project = next((p for p in store["projects"] if p["id"] == pid), None)
        if not project:
            return self._error(404, "项目不存在")
        data = self._clean_project(payload)
        if not data["name"]:
            return self._error(400, "项目名称必填")
        project.update(data)
        project["updatedAt"] = _now()
        save_data(store)
        return self._json({"ok": True, "data": project})

    def _delete_project(self, pid: str):
        store = load_data()
        before = len(store["projects"])
        store["projects"] = [p for p in store["projects"] if p["id"] != pid]
        if len(store["projects"]) == before:
            return self._error(404, "项目不存在")
        save_data(store)
        return self._json({"ok": True})

    def _add_link(self, pid: str, payload: dict):
        store = load_data()
        project = next((p for p in store["projects"] if p["id"] == pid), None)
        if not project:
            return self._error(404, "项目不存在")
        name = str(payload.get("name", "") or "").strip()[:80]
        url = str(payload.get("url", "") or "").strip()[:800]
        if not name:
            return self._error(400, "链接名称必填")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            return self._error(400, "链接必须是 http(s) 地址")
        link = {"id": _new_id("l"), "name": name, "url": url, "note": str(payload.get("note", "") or "").strip()[:500]}
        project["links"].append(link)
        project["updatedAt"] = _now()
        save_data(store)
        return self._json({"ok": True, "data": link})

    def _update_link(self, pid: str, lid: str, payload: dict):
        store = load_data()
        project = next((p for p in store["projects"] if p["id"] == pid), None)
        if not project:
            return self._error(404, "项目不存在")
        link = next((x for x in project["links"] if x["id"] == lid), None)
        if not link:
            return self._error(404, "链接不存在")
        for key in ("name", "url", "note"):
            if key in payload:
                link[key] = str(payload.get(key, "") or "").strip()[:800]
        project["updatedAt"] = _now()
        save_data(store)
        return self._json({"ok": True, "data": link})

    def _delete_link(self, pid: str, lid: str):
        store = load_data()
        project = next((p for p in store["projects"] if p["id"] == pid), None)
        if not project:
            return self._error(404, "项目不存在")
        project["links"] = [x for x in project["links"] if x["id"] != lid]
        project["updatedAt"] = _now()
        save_data(store)
        return self._json({"ok": True})

    def _attach_software(self, pid: str, payload: dict):
        sid = str(payload.get("softwareId", "") or "").strip()
        store = load_data()
        project = next((p for p in store["projects"] if p["id"] == pid), None)
        if not project:
            return self._error(404, "项目不存在")
        if not any(s["id"] == sid for s in store["software"]):
            return self._error(404, "软件不存在")
        if sid not in project["softwareIds"]:
            project["softwareIds"].append(sid)
            project["updatedAt"] = _now()
            save_data(store)
        return self._json({"ok": True})

    def _detach_software(self, pid: str, sid: str):
        store = load_data()
        project = next((p for p in store["projects"] if p["id"] == pid), None)
        if not project:
            return self._error(404, "项目不存在")
        project["softwareIds"] = [x for x in project["softwareIds"] if x != sid]
        project["updatedAt"] = _now()
        save_data(store)
        return self._json({"ok": True})

    # -- 业务：导入 --------------------------------------------------------
    def _import(self, payload: dict):
        incoming = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(incoming, dict):
            return self._error(400, "导入内容缺失")
        mode = str(payload.get("mode", "merge") or "merge")
        incoming = _normalize(incoming)
        if mode == "replace":
            save_data(incoming)
            return self._json({"ok": True, "data": {"mode": "replace", "software": len(incoming["software"]), "projects": len(incoming["projects"])}})

        store = load_data()
        existing_ids = {s["id"] for s in store["software"]}
        for item in incoming["software"]:
            if item["id"] in existing_ids:
                item["id"] = _new_id("s")
            store["software"].append(item)
        existing_pids = {p["id"] for p in store["projects"]}
        for project in incoming["projects"]:
            if project["id"] in existing_pids:
                project["id"] = _new_id("p")
            store["projects"].append(project)
        save_data(store)
        return self._json({"ok": True, "data": {"mode": "merge", "software": len(store["software"]), "projects": len(store["projects"])}})


def _lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    if sys.stdout is None or sys.stderr is None:  # 无控制台（打包 exe --admin）
        import io
        devnull = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")
        sys.stdout = sys.stdout or devnull
        sys.stderr = sys.stderr or devnull

    parser = argparse.ArgumentParser(description="小庆工具箱 - 自建库后台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；局域网访问用 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(DATA_FILE):
        save_data(json.loads(json.dumps(EMPTY_DATA)))
    Handler.token = get_token()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    local_url = "http://127.0.0.1:%d/?token=%s" % (args.port, Handler.token)
    print("=" * 64)
    print("自建库后台已启动")
    print("  数据文件：%s" % DATA_FILE)
    print("  后台口令：%s（同 %s）" % (Handler.token, TOKEN_FILE))
    print("  本机地址：%s" % local_url)
    if args.host not in ("127.0.0.1", "localhost"):
        print("  局域网地址：http://%s:%d/?token=%s" % (_lan_ip(), args.port, Handler.token))
    print("  关闭窗口或按 Ctrl+C 停止")
    print("=" * 64)
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n后台已停止")


if __name__ == "__main__":
    main()
