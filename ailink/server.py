"""Loopback-only control API and static application; no external dependencies."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .config import ConfigStore, ValidationError
from .diagnostics import Diagnostics, PROVIDERS, probe
from .events import Telemetry
from .kernel import FlClashAdapter
from .system_proxy import SystemProxy
from .transport import Relay


from .service import Application


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 16

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address


class Handler(BaseHTTPRequestHandler):
    server_version = "AI-Link"

    @property
    def app(self):
        return self.server.app

    def log_message(self, *_):
        pass

    def valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {"error": "拒绝未知 Host。"})
        if self.path == "/api/bootstrap":
            return self.respond(200, {"token": self.app.token, "version": __version__})
        if self.path == "/api/status":
            return self.respond(200, self.app.status())
        if self.path == "/api/report":
            state = self.app.status()
            return self.respond(200, {"version": __version__, "exported_at": time.time(),
                                      "config": state["config"], "telemetry": state["telemetry"],
                                      "diagnostics": state["diagnostics"], "kernel": state["kernel"],
                                      "privacy": "不含账号、请求内容、订阅信息或代理设置备份。"})
        routes = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if self.path not in routes:
            return self.respond(404, {"error": "资源不存在。"})
        path = self.app.directory / "static" / routes[self.path]
        return self.respond(200, path.read_bytes(), mimetypes.guess_type(str(path))[0] + "; charset=utf-8")

    def do_POST(self):
        app = self.app
        origin = self.headers.get("Origin")
        if not self.valid_host() or (origin and origin != "http://" + self.headers["Host"]):
            return self.respond(403, {"error": "仅允许本机同源操作。"})
        if not secrets.compare_digest(self.headers.get("X-AI-Link-Token", ""), self.app.token):
            return self.respond(403, {"error": "会话已失效，请刷新面板。"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.respond(415, {"error": "需要 JSON 请求。"})
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("不支持分块控制请求。")
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 16384:
                return self.respond(413, {"error": "请求过大。"})
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("请求必须是 JSON 对象。")
            if self.path not in ("/api/config", "/api/diagnostics") and body:
                raise ValueError("此操作不接受额外参数。")
            app = self.app
            if self.path == "/api/config":
                result = app.configure(body)
            elif self.path == "/api/enable":
                app.enable()
                result = {"accepted": True}
            elif self.path == "/api/disable":
                result = app.disable()
            elif self.path == "/api/relay/start":
                with app.mutation_lock:
                    app.relay.start()
                result = {"started": True}
            elif self.path == "/api/diagnostics":
                if set(body) != {"providers", "routes", "duration"}:
                    raise ValueError("诊断字段不完整。")
                result = app.diagnostics.start(body["providers"], body["routes"], body["duration"])
            elif self.path == "/api/diagnostics/cancel":
                app.diagnostics.stop.set()
                result = {"cancelled": True}
            elif self.path in ("/api/kernel/apply", "/api/kernel/restore"):
                with app.mutation_lock:
                    result = app.kernel.apply() if self.path.endswith("apply") else app.kernel.restore()
                    app.kernel_cache = app.kernel.status()
                    app.last_kernel_refresh = time.monotonic()
            else:
                return self.respond(404, {"error": "接口不存在。"})
            return self.respond(200, result)
        except (ValueError, ValidationError) as error:
            return self.respond(400, {"error": str(error)[:250]})
        except RuntimeError as error:
            return self.respond(409, {"error": str(error)[:250]})
        except OSError:
            app.telemetry.event("error", "operation_failed", "本机文件、端口或系统设置操作失败。")
            return self.respond(500, {"error": "本机操作失败。原设置可能仍在使用，请检查端口占用、文件权限或使用紧急恢复。"})


def make_server(app, port=18791):
    server = ControlServer(("127.0.0.1", port), Handler)
    server.app = app
    return server
