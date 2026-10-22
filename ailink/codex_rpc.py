"""Codex's supported newline-delimited app-server protocol. No token-file parsing."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import socket
from urllib.parse import urlsplit


def local_endpoint(value):
    if not isinstance(value,str) or len(value)>200:
        raise ValueError('共享地址必须是本机 ws://127.0.0.1:端口。')
    try:
        parsed=urlsplit(value)
        if (parsed.scheme!='ws' or parsed.hostname not in ('127.0.0.1','::1') or
            not parsed.port or not 1<=parsed.port<=65535 or parsed.username or parsed.password or
            parsed.path not in ('','/') or parsed.query or parsed.fragment):raise ValueError()
    except ValueError:raise ValueError('仅接受本机回环 WebSocket 地址，不允许外部地址、账号或查询参数。') from None
    return value


def find_codex():
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    candidates = list(base.glob("*/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    raise RuntimeError("未找到 Codex CLI。请先安装 Codex 并登录 ChatGPT。")


class CodexRPC:
    def __init__(self, shared=False, endpoint='', token_file=None):
        self.shared = shared
        self.endpoint=local_endpoint(endpoint) if endpoint else ''
        if self.endpoint and not shared:raise ValueError('共享地址仅适用于共享连接模式。')
        self.token_file=token_file
        self.socket=None
        self.pending = {}
        self.lock = threading.Lock()
        self.sequence = 0
        self.wake = threading.Event()
        self.usage_events = queue.Queue(maxsize=4096)
        self.usage_overflow = False
        self.control_events = queue.Queue(maxsize=4096)
        self.control_overflow = False
        self.process = None

    def connect(self):
        if self.endpoint:
            import websocket
            headers=[]
            if self.token_file:
                path=Path(self.token_file)
                if not path.is_absolute() or not path.is_file() or path.stat().st_size>16384:raise ValueError('连接凭据文件无效或过大。')
                token=path.read_text('utf-8').strip()
                if not token or any(c.isspace() for c in token) or not token.isascii():raise ValueError('连接凭据必须是单行 ASCII token。')
                headers=['Authorization: Bearer '+token]
            # Connect a literal loopback address directly, without VPN/proxy routing or redirects.
            address=urlsplit(self.endpoint)
            raw=socket.create_connection((address.hostname,address.port),timeout=5)
            try:self.socket=websocket.create_connection(self.endpoint,timeout=5,header=headers,suppress_origin=True,socket=raw,redirect_limit=0)
            except Exception:
                raw.close();raise RuntimeError('无法连接本机共享服务，请检查地址、服务状态和连接凭据。') from None
            self.socket.settimeout(None)
        else:
            args = [find_codex(), "app-server"]
            args += ["proxy"] if self.shared else ["--listen", "stdio://"]
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, daemon=True, name="codex-rpc-reader").start()
        try:
            self.call("initialize", {"clientInfo": {"name": "ai_link", "title": "AI Link", "version": "0.13.0"}})
            self._write({"method": "initialized"})
        except Exception as error:
            self.close()
            if self.shared:raise RuntimeError('未连接到现有 Codex 进程的共享控制接口；当前桌面对话不可控制。可使用 AI Link 托管任务，或先配置该进程的控制接入。') from error
            raise

    def _write(self, message):
        with self.lock:
            if self.socket:
                self.socket.send(json.dumps(message,ensure_ascii=False));return
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("Codex 连接已关闭。")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def call(self, method, params=None, timeout=25):
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            response = self.pending[request_id] = queue.Queue(maxsize=1)
        try:
            self._write({"id": request_id, "method": method, "params": params or {}})
            try:
                value = response.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError("Codex 请求超时；执行结果可能未知，请刷新状态。") from None
            if "error" in value:
                raise RuntimeError(str(value["error"].get("message", "Codex 请求失败"))[:250])
            return value.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def _read(self):
        try:
            def messages():
                if self.socket:
                    connection=self.socket
                    while True:
                        message=connection.recv()
                        if not message:return
                        yield message
                else:
                    yield from self.process.stdout
            for line in messages():
                if len(line)>16000000:raise RuntimeError('Codex 返回的单条记录过大，已停止读取。')
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if "method" in value:
                    # Never silently approve requests from an agent.
                    if "id" in value:
                        self._write({"id": value["id"], "error": {"code": -32000,
                                     "message": "AI Link cannot approve this request. Continue in a full Codex client."}})
                        self.wake.set()
                    else:
                        if value["method"] in ("turn/started", "turn/completed"):
                            try:
                                self.control_events.put_nowait(value.get("params", {}))
                            except queue.Full:
                                self.control_overflow = True
                        if value["method"] == "thread/tokenUsage/updated":
                            try:
                                self.usage_events.put_nowait(value.get("params", {}))
                            except queue.Full:
                                self.usage_overflow = True
                            self.wake.set()
                        if value["method"] in ("account/rateLimits/updated", "account/updated", "turn/started", "turn/completed"):
                            self.wake.set()
                else:
                    with self.lock:
                        target = self.pending.get(value.get("id"))
                    if target:
                        target.put_nowait(value)
        except Exception:
            pass
        finally:
            with self.lock:
                for target in self.pending.values():
                    try:
                        target.put_nowait({"error": {"message": "Codex 连接中断；请重新连接。"}})
                    except queue.Full:
                        pass
            self.wake.set()
            self.close()

    def close(self):
        connection,self.socket=self.socket,None
        if connection:
            try:connection.close(timeout=1)
            except Exception:pass
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
