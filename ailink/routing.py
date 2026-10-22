"""Per-upstream observations and staggered CONNECT races, before TLS payloads."""
from __future__ import annotations
import queue
import threading
import time

PRESETS = {
    "speed": dict(attempts=2, connect_timeout=3, retry_budget=10, keepalive_idle=30,
                  keepalive_interval=10, route_strategy="race", race_delay_ms=100),
    "balanced": dict(attempts=4, connect_timeout=5, retry_budget=25, keepalive_idle=60,
                     keepalive_interval=15, route_strategy="adaptive", race_delay_ms=150),
    "resilient": dict(attempts=6, connect_timeout=6, retry_budget=45, keepalive_idle=30,
                      keepalive_interval=10, route_strategy="adaptive", race_delay_ms=200),
}


class RouteHealth:
    def __init__(self):
        self.lock = threading.Lock()
        self.routes = {}

    @staticmethod
    def key(upstream):
        return f"{upstream['host']}:{upstream['port']}"

    def record(self, upstream, success, milliseconds):
        with self.lock:
            item = self.routes.setdefault(self.key(upstream), dict(attempts=0, succeeded=0, failed=0,
                                         streak=0, ewma_ms=None, cooldown_until=0, updated=0))
            item["attempts"] += 1
            item["updated"] = time.time()
            if success:
                item["succeeded"] += 1
                item["streak"] = 0
                item["cooldown_until"] = 0
                item["ewma_ms"] = round(milliseconds if item["ewma_ms"] is None else
                                        item["ewma_ms"]*.7 + milliseconds*.3, 2)
            else:
                item["failed"] += 1
                item["streak"] += 1
                if item["streak"] >= 2:
                    item["cooldown_until"] = time.time() + min(30, 2**min(item["streak"], 5))

    def ordered(self, upstreams):
        now = time.time()
        with self.lock:
            def score(pair):
                index, upstream = pair
                item = self.routes.get(self.key(upstream), {})
                fresh = now-item.get("updated", 0) < 300
                probed = now-item.get('probe_at',0)<300
                return (item.get("cooldown_until", 0) > now,
                        probed and item.get('probe_succeeded',0)==0,
                        item.get("streak", 0) if fresh else 0,
                        item.get('probe_ms') if probed and item.get('probe_ms') is not None else 1000,
                        item.get("ewma_ms") if fresh and item.get("ewma_ms") is not None else 1000,
                        index)
            return [u for _,u in sorted(enumerate(upstreams), key=score)]

    def benchmark(self, upstream, summary):
        with self.lock:
            item = self.routes.setdefault(self.key(upstream), dict(attempts=0,succeeded=0,failed=0,streak=0,ewma_ms=None,cooldown_until=0,updated=0))
            item.update(probe_ms=summary['p50_ms'],probe_succeeded=summary['succeeded'],probe_at=time.time())

    def snapshot(self, upstreams):
        now = time.time()
        ordered = self.ordered(upstreams)
        with self.lock:
            return [{"key": self.key(u), "host": u["host"], "port": u["port"],
                     **self.routes.get(self.key(u), dict(attempts=0, succeeded=0, failed=0, ewma_ms=None, updated=0)),
                     "cooldown_seconds": max(0, round(self.routes.get(self.key(u), {}).get("cooldown_until", 0)-now)),
                     "rank": ordered.index(u)+1} for u in upstreams]


def race_connect(upstreams, authority, config, stop, deadline, connect_fn, health):
    """Return a winner immediately. Every late loser closes its own socket."""
    from .transport import UpstreamFailure
    completed = queue.Queue()
    cancelled = threading.Event()
    ownership = threading.Lock()
    sockets = set()
    def worker(index, upstream):
        if cancelled.wait(index*config.get("race_delay_ms", 150)/1000) or stop.is_set():
            completed.put((None, upstream, UpstreamFailure("relay_stopped")))
            return
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            completed.put((None, upstream, UpstreamFailure("retry_budget_exhausted")))
            return
        started = time.monotonic()
        try:
            connection = connect_fn(upstream, authority, min(config["connect_timeout"], remaining), config)
            if health:
                health.record(upstream, True, (time.monotonic()-started)*1000)
            with ownership:
                if cancelled.is_set() or stop.is_set():
                    connection.close()
                else:
                    sockets.add(connection)
                    completed.put((connection, upstream, None))
        except (OSError, ValueError) as error:
            if health:
                health.record(upstream, False, (time.monotonic()-started)*1000)
            completed.put((None, upstream, error))
    for index, upstream in enumerate(upstreams):
        threading.Thread(target=worker, args=(index, upstream), daemon=True, name="connect-race").start()
    winner = None
    last = UpstreamFailure("upstream_unavailable")
    try:
        received = 0
        while received < len(upstreams):
            if stop.is_set():
                raise UpstreamFailure("relay_stopped")
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise UpstreamFailure("retry_budget_exhausted")
            try:
                connection, upstream, error = completed.get(timeout=min(.05, remaining))
            except queue.Empty:
                continue
            received += 1
            if connection:
                winner = connection
                return connection, upstream
            last = error
            if isinstance(error, UpstreamFailure) and error.status in (400, 401, 403, 407):
                raise error
        raise last
    finally:
        with ownership:
            cancelled.set()
            for connection in sockets:
                if connection is not winner:
                    connection.close()
