"""Bounded operational telemetry. Never records payloads, URLs or headers."""
from __future__ import annotations

import threading
import time
import math
import statistics
from collections import deque


class Telemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque(maxlen=150)
        self.history = deque(maxlen=60)
        self.counters = dict(active=0, accepted=0, connected=0, failed=0, retries=0,
                             fallback=0, uploaded=0, downloaded=0, raced=0, rejected=0, connect_failed=0, interrupted=0)
        self.started = time.time()
        self.series = deque(maxlen=180)
        self.connect_times = deque(maxlen=250)
        self.last_tick = time.monotonic()
        self.last_bytes = (0, 0)

    def event(self, level: str, code: str, message: str):
        with self.lock:
            self.events.appendleft(dict(time=time.time(), level=level, code=code, message=message))

    def add(self, **values):
        with self.lock:
            for key, value in values.items():
                self.counters[key] += value

    def sample(self, provider: str, result: dict):
        with self.lock:
            self.history.append({**result, "time": time.time(), "provider": provider})

    def connection(self, milliseconds):
        with self.lock:
            self.connect_times.append(round(milliseconds, 2))

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now-self.last_tick
            if elapsed >= 1:
                up, down = self.counters["uploaded"], self.counters["downloaded"]
                self.series.append(dict(at=time.time(), up_bps=max(0,(up-self.last_bytes[0])/elapsed),
                                        down_bps=max(0,(down-self.last_bytes[1])/elapsed), active=self.counters["active"]))
                self.last_tick, self.last_bytes = now, (up,down)
            times = sorted(self.connect_times)
            return dict(counters=dict(self.counters), events=list(self.events),
                        history=list(self.history), uptime=int(time.time() - self.started), series=list(self.series),
                        connections=dict(samples=len(times), p50_ms=statistics.median(times) if times else None,
                                         p95_ms=times[max(0,math.ceil(len(times)*.95)-1)] if times else None))
