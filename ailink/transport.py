"""Opaque CONNECT relay. Retries end before any client TLS bytes are forwarded."""
from __future__ import annotations

import random
import select
import socket
import socketserver
import threading
import time
from contextlib import suppress

HEADER_LIMIT = 32768


class UpstreamFailure(OSError):
    def __init__(self, code="upstream_unavailable", status=None):
        super().__init__(code)
        self.code, self.status = code, status


def read_header(sock: socket.socket, deadline=None) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("header deadline exceeded")
            sock.settimeout(remaining)
        chunk = sock.recv(min(4096, HEADER_LIMIT + 1 - len(data)))
        if not chunk:
            raise UpstreamFailure("unexpected_eof")
        data.extend(chunk)
        if len(data) > HEADER_LIMIT:
            raise UpstreamFailure("header_too_large")
    header, tail = bytes(data).split(b"\r\n\r\n", 1)
    return header + b"\r\n\r\n", tail


def tune_socket(sock: socket.socket, config: dict):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    with suppress(OSError, AttributeError):
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
            sock.ioctl(socket.SIO_KEEPALIVE_VALS,
                       (1, config["keepalive_idle"] * 1000, config["keepalive_interval"] * 1000))
        else:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, config["keepalive_idle"])
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, config["keepalive_interval"])


def connect_once(upstream: dict, authority: str, timeout: float, config: dict) -> socket.socket:
    deadline = time.monotonic() + timeout
    sock = socket.create_connection((upstream["host"], upstream["port"]), timeout=timeout)
    try:
        tune_socket(sock, config)
        sock.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\nProxy-Connection: Keep-Alive\r\n\r\n".encode("ascii"))
        header, tail = read_header(sock, deadline)
        parts = header.split(b"\r\n", 1)[0].split()
        if len(parts) < 2 or parts[0] not in (b"HTTP/1.0", b"HTTP/1.1") or not parts[1].isdigit():
            raise UpstreamFailure("invalid_proxy_response")
        status = int(parts[1])
        if status != 200:
            raise UpstreamFailure("proxy_rejected", status)
        if tail:
            raise UpstreamFailure("unexpected_upstream_data")
        return sock
    except BaseException:
        sock.close()
        raise


def dial(upstreams, authority, config, stop, telemetry, connect_fn=connect_once, health=None):
    deadline = time.monotonic() + config["retry_budget"]
    last_error = UpstreamFailure()
    for attempt in range(config["attempts"]):
        if stop.is_set():
            raise UpstreamFailure("relay_stopped")
        if attempt:
            telemetry.add(retries=1)
            delay = min(0.5 * 2 ** (attempt - 1) + random.uniform(0, 0.2), 4)
            if stop.wait(min(delay, max(0, deadline - time.monotonic()))):
                raise UpstreamFailure("relay_stopped")
        strategy = config.get("route_strategy", "ordered")
        ordered = health.ordered(upstreams) if health and strategy != "ordered" else upstreams
        if strategy == "race" and len(ordered) > 1:
            from .routing import race_connect
            try:
                connection, upstream = race_connect(ordered, authority, config, stop, deadline, connect_fn, health)
                telemetry.add(raced=1, **({"fallback": 1} if upstream != upstreams[0] else {}))
                return connection
            except (OSError, ValueError) as error:
                last_error = error
                if isinstance(error, UpstreamFailure) and error.status in (400, 401, 403, 407):
                    raise
                continue
        for index, upstream in enumerate(ordered):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UpstreamFailure("retry_budget_exhausted") from last_error
            try:
                started = time.monotonic()
                connection = connect_fn(upstream, authority, min(config["connect_timeout"], remaining), config)
                if health:
                    health.record(upstream, True, (time.monotonic()-started)*1000)
                if attempt or index:
                    telemetry.event("success", "connection_recovered", "重试或备用上游成功建立了新隧道。")
                if upstream != upstreams[0]:
                    telemetry.add(fallback=1)
                return connection
            except (OSError, ValueError) as error:
                if health:
                    health.record(upstream, False, (time.monotonic()-started)*1000)
                last_error = error
                if isinstance(error, UpstreamFailure) and error.status in (400, 401, 403, 407):
                    # Authentication/policy failures should never be hammered with retries.
                    raise
    raise UpstreamFailure("upstream_unavailable") from last_error


def validate_authority(raw: str) -> str:
    from urllib.parse import urlsplit
    if len(raw) > 300 or any(c in raw for c in "\r\n/@?#"):
        raise ValueError("invalid authority")
    parsed = urlsplit("//" + raw)
    if not parsed.hostname or parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError("invalid authority")
    raw.encode("ascii")
    return raw


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        owner = self.server.owner
        if not owner.slots.acquire(blocking=False):
            owner.telemetry.add(rejected=1)
            with suppress(OSError):
                self.request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            return
        remote = None
        tunnel_started = False
        started = time.monotonic()
        owner.telemetry.add(accepted=1, active=1)
        owner.track(self.request, True)
        try:
            config = owner.get_config()
            self.request.settimeout(15)
            tune_socket(self.request, config)
            header, initial = read_header(self.request, time.monotonic() + 15)
            first = header.split(b"\r\n", 1)[0].decode("ascii")
            method, target, version = first.split(" ")
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("unsupported version")
            if method == "CONNECT":
                authority = validate_authority(target)
                remote = dial(config["upstreams"], authority, config, owner.stop_event, owner.telemetry, health=owner.health)
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                tunnel_started = True
                if initial:
                    remote.sendall(initial)
                    owner.telemetry.add(uploaded=len(initial))
            else:
                # HTTP requests may contain non-idempotent bodies: connect once and never replay.
                from urllib.parse import urlsplit
                uri = urlsplit(target)
                if uri.scheme != "http" or not uri.hostname or uri.username or uri.password or uri.fragment:
                    raise ValueError("absolute HTTP URL required")
                upstream = (owner.health.ordered(config["upstreams"]) if config.get("route_strategy") != "ordered" else config["upstreams"])[0]
                remote = socket.create_connection((upstream["host"], upstream["port"]), config["connect_timeout"])
                tune_socket(remote, config)
                remote.sendall(header + initial)
                tunnel_started = True
                owner.telemetry.add(uploaded=len(header) + len(initial))
            owner.track(remote, True)
            owner.telemetry.add(connected=1)
            owner.telemetry.connection((time.monotonic()-started)*1000)
            self.request.settimeout(None)
            remote.settimeout(None)
            self.pump(self.request, remote, owner)
        except (OSError, ValueError, UnicodeError):
            if owner.stop_event.is_set():
                return
            owner.telemetry.add(failed=1, **{"interrupted" if tunnel_started else "connect_failed": 1})
            owner.telemetry.event("warning", "tunnel_interrupted" if tunnel_started else "connect_failed",
                                  "隧道中断；由客户端重新建立连接。" if tunnel_started else "新连接未能建立；未重放应用请求。")
            if not tunnel_started:
                with suppress(OSError):
                    self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
        finally:
            owner.track(self.request, False)
            if remote:
                owner.track(remote, False)
                remote.close()
            owner.telemetry.add(active=-1)
            owner.slots.release()

    @staticmethod
    def pump(client, upstream, owner):
        peers = {client: upstream, upstream: client}
        buffers = {client: bytearray(), upstream: bytearray()}
        reading, eof, half_closed = set(peers), set(), set()
        for sock in peers:
            sock.setblocking(False)
        while (reading or any(buffers.values())) and not owner.stop_event.is_set():
            readers = [s for s in reading if len(buffers[peers[s]]) < 512*1024]
            writers = [s for s in peers if buffers[s]]
            readable, writable, _ = select.select(readers, writers, [], .5)
            for source in readable:
                try:
                    data = source.recv(min(65536, 512*1024-len(buffers[peers[source]])))
                except (BlockingIOError, InterruptedError):
                    continue
                if data:
                    buffers[peers[source]].extend(data)
                else:
                    reading.remove(source)
                    eof.add(source)
            for destination in writable:
                try:
                    sent = destination.send(buffers[destination])
                except (BlockingIOError, InterruptedError):
                    continue
                if sent == 0:
                    raise ConnectionError("socket closed during send")
                del buffers[destination][:sent]
                owner.telemetry.add(**{"uploaded" if destination is upstream else "downloaded": sent})
            # Flush queued bytes before propagating EOF; preserve bidirectional half-closes.
            for source in eof:
                destination = peers[source]
                if not buffers[destination] and destination not in half_closed:
                    with suppress(OSError):
                        destination.shutdown(socket.SHUT_WR)
                    half_closed.add(destination)


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False


class Relay:
    def __init__(self, get_config, telemetry, port=18790):
        self.get_config, self.telemetry, self.port = get_config, telemetry, port
        self.server = None
        self.stop_event = threading.Event()
        self.slots = threading.BoundedSemaphore(128)
        self.connections = set()
        self.lock = threading.Lock()
        from .routing import RouteHealth
        self.health = RouteHealth()

    def track(self, connection, add):
        with self.lock:
            if add:
                self.connections.add(connection)
            else:
                self.connections.discard(connection)

    def start(self):
        if self.server:
            return
        self.stop_event.clear()
        self.server = RelayServer(("127.0.0.1", self.port), RelayHandler)
        self.port = self.server.server_address[1]
        self.server.owner = self
        threading.Thread(target=self.server.serve_forever, name="ai-link-relay", daemon=True).start()
        self.telemetry.event("success", "relay_started", "本地连接中继已启动。")

    def stop(self):
        if not self.server:
            return
        self.stop_event.set()
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        with self.lock:
            for connection in list(self.connections):
                with suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
                    connection.close()
        self.telemetry.event("info", "relay_stopped", "中继已停止。")
