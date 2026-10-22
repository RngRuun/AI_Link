"""Credential-free probes distinguish transport reachability from service access."""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .transport import UpstreamFailure, connect_once

PROVIDERS = {
    "codex": {"name": "Codex / ChatGPT", "host": "chatgpt.com", "path": "/backend-api/codex/responses", "color": "green"},
    "gemini": {"name": "Gemini", "host": "gemini.google.com", "path": "/", "color": "blue"},
    "openai-api": {"name": "OpenAI API", "host": "api.openai.com", "path": "/v1/models", "color": "violet"},
    "google-api": {"name": "Gemini API", "host": "generativelanguage.googleapis.com", "path": "/v1beta/models", "color": "amber"},
}


def probe(provider_id, route, config, relay_port=18790, stop=None, duration=0):
    provider = PROVIDERS[provider_id]
    started = time.monotonic()
    sock = None
    result = dict(provider=provider_id, route=route, reachable=False, status=None,
                  latency_ms=None, tls_ms=None, samples=[], outcome="failed", note="", held_seconds=0,
                  stage="tcp", error_code=None)
    try:
        if stop and stop.is_set():
            result.update(outcome="cancelled", note="检测已取消。")
            return result
        host = provider["host"]
        timeout = min(config["connect_timeout"] + 3, 15)
        if route == "direct":
            sock = socket.create_connection((host, 443), timeout)
        else:
            result["stage"] = "proxy_connect"
            upstream = {"host": "127.0.0.1", "port": relay_port} if route == "relay" else config["upstreams"][0]
            sock = connect_once(upstream, host + ":443", timeout, config)
        context = ssl.create_default_context()
        result["stage"] = "tls"
        sock = context.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
        result["tls_ms"] = round((time.monotonic() - started) * 1000)
        result["reachable"] = True
        result["stage"] = "http"
        held_start = time.monotonic()
        rounds = 1 + duration // 5
        for index in range(rounds):
            if stop and stop.is_set():
                result.update(outcome="cancelled", note="检测已取消。")
                break
            sample_start = time.monotonic()
            # HEAD avoids downloading content, creating model requests, or sending login cookies.
            sock.sendall(f"HEAD {provider['path']} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: AI-Link-Diagnostics/0.1\r\nConnection: keep-alive\r\n\r\n".encode("ascii"))
            response = http.client.HTTPResponse(sock, method="HEAD")
            response.begin()
            status, will_close = response.status, response.will_close
            response.read()
            response.close()
            result["status"] = status
            result["samples"].append(round((time.monotonic() - sample_start) * 1000))
            if status == 405:
                result.update(outcome="method_unsupported", note="该端点不支持 HEAD；TLS 与 HTTP 可达，未验证生成请求。")
            elif status in (401, 403, 429):
                result.update(outcome="restricted", note="TLS 与 HTTP 已到达服务；认证、策略、请求方法或限流限制，未验证 AI 功能。")
            elif status >= 500:
                result.update(outcome="service_error", note="已到达服务，但服务返回错误。")
            elif status >= 400:
                result.update(outcome="restricted", note="已到达 HTTP 服务；该诊断路径不可用，未验证 AI 功能。")
            else:
                result.update(outcome="reachable", note="TLS 与 HTTP 可达；未验证登录、模型权限或生成能力。")
            if index < rounds - 1:
                if will_close:
                    result.update(outcome="closed", note="服务主动关闭 HTTP 连接；不能据此认定 VPN 丢线。")
                    break
                if stop and stop.wait(5):
                    result.update(outcome="cancelled", note="检测已取消。")
                    break
                if not stop:
                    time.sleep(5)
        result["held_seconds"] = round(time.monotonic() - held_start, 1)
        result["latency_ms"] = result["tls_ms"] + result["samples"][0] if result["samples"] else result["tls_ms"]
    except ssl.SSLCertVerificationError:
        result.update(outcome="tls_error", error_code="certificate_verification", note="证书校验失败；未降低 TLS 校验标准。")
    except (OSError, ValueError, http.client.HTTPException) as error:
        if isinstance(error, (TimeoutError, socket.timeout)):
            code = "timeout"
        elif isinstance(error, ConnectionRefusedError):
            code = "connection_refused"
        elif isinstance(error, socket.gaierror):
            code = "dns_resolution"
        elif isinstance(error, UpstreamFailure):
            code = error.code
        else:
            code = "connection_or_protocol"
        result["error_code"] = code
        stage_label = {"tcp": "TCP 建连", "proxy_connect": "代理隧道", "tls": "TLS 握手", "http": "HTTP 响应"}[result["stage"]]
        result.update(outcome="interrupted" if result["reachable"] else "failed",
                      note=f"{stage_label}阶段失败（{code}）。" + ("已建连接在检测期间中断。" if result["reachable"] else ""))
    finally:
        if sock:
            sock.close()
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


class Diagnostics:
    def __init__(self, telemetry, get_config, relay):
        self.telemetry, self.get_config, self.relay = telemetry, get_config, relay
        self.lock = threading.Lock()
        self.current = None
        self.stop = threading.Event()

    def start(self, providers, routes, duration):
        if not isinstance(providers, list) or not providers or len(providers) > 4 or any(not isinstance(p, str) or p not in PROVIDERS for p in providers):
            raise ValueError("请选择有效的 AI 服务。")
        if not isinstance(routes, list) or not routes or len(routes) > 3 or any(not isinstance(r, str) or r not in ("direct", "upstream", "relay") for r in routes):
            raise ValueError("请选择有效的检测路径。")
        if type(duration) is not int or duration not in (0, 30, 60):
            raise ValueError("检测时长必须是快速检测、30 秒或 60 秒。")
        if "relay" in routes and not self.relay.server:
            raise ValueError("请先启动本地中继，再检测优化路径。")
        with self.lock:
            if self.current and self.current["running"]:
                raise RuntimeError("已有检测正在进行。")
            self.stop.clear()
            self.current = dict(id=uuid.uuid4().hex, running=True, started=time.time(),
                                duration=duration, total=len(set(providers)) * len(set(routes)), results=[])
        threading.Thread(target=self._run, args=(list(dict.fromkeys(providers)), list(dict.fromkeys(routes)), duration),
                         name="ai-link-diagnostics", daemon=True).start()
        return self.snapshot()

    def _run(self, providers, routes, duration):
        from concurrent.futures import as_completed
        config = self.get_config()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(probe, p, r, config, self.relay.port, self.stop, duration) for p in providers for r in routes]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    result = {"outcome": "failed", "note": "检测工作线程异常。", "provider": "unknown", "route": "unknown"}
                with self.lock:
                    self.current["results"].append(result)
                self.telemetry.sample(result["provider"], result)
        with self.lock:
            self.current["running"] = False
            self.current["finished"] = time.time()
        self.telemetry.event("info", "diagnostics_finished", "连接诊断已完成；结果仅代表检测期间的状态。")

    def snapshot(self):
        import copy
        with self.lock:
            return copy.deepcopy(self.current)
