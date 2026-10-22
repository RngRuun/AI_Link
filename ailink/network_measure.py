"""Bounded HEAD comparisons of each configured upstream and the actual relay."""
from __future__ import annotations
import copy
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .diagnostics import probe


class NetworkMeasure:
    def __init__(self, get_config, relay):
        self.get_config, self.relay = get_config, relay
        self.lock = threading.Lock()
        self.current = None
        self.stop = threading.Event()

    def start(self):
        with self.lock:
            if self.current and self.current["running"]:
                raise ValueError("路径评估正在进行。")
            config = copy.deepcopy(self.get_config())
            self.stop = threading.Event()
            self.current = dict(running=True, started=time.time(), finished=None, results=[],
                                total=3*2*(len(config["upstreams"])+1), completed=0, cancelled=False,
                                strategy=config["route_strategy"], error=None)
            threading.Thread(target=self._run, args=(config, self.stop), daemon=True, name="path-measure").start()
        return self.snapshot()

    def cancel(self):
        self.stop.set()

    def invalidate(self):
        with self.lock:
            if self.current:
                self.current['stale'] = True

    def _run(self, config, stop):
        jobs = [(f"upstream-{index}", u) for index,u in enumerate(config["upstreams"])] + [("relay", None)]
        def run_one(key, upstream, provider, round_index):
            if stop.is_set():
                return None
            candidate = copy.deepcopy(config)
            if upstream:
                candidate["upstreams"] = [upstream]
            result = probe(provider, "upstream" if upstream else "relay", candidate, self.relay.port, stop)
            return {**result, "key": key, "port": upstream["port"] if upstream else self.relay.port, "round":round_index+1}
        try:
            # Pair each path in every round; do not compare samples collected in different sessions.
            with ThreadPoolExecutor(max_workers=4) as pool:
                for round_index in range(3):
                    if stop.is_set():
                        break
                    futures = [pool.submit(run_one,key,upstream,provider,round_index)
                               for provider in ("codex","gemini") for key,upstream in jobs]
                    for future in as_completed(futures):
                        result = future.result()
                        with self.lock:
                            self.current["completed"] += 1
                            if result:
                                self.current["results"].append(result)
        except Exception:
            with self.lock:
                self.current["error"] = "路径评估未完成，请重试。"
        finally:
            with self.lock:
                self.current.update(running=False, cancelled=stop.is_set(), finished=time.time())
            snapshot = self.snapshot()
            if not stop.is_set() and not snapshot['error'] and not snapshot.get('stale'):
                for summary in snapshot['summaries']:
                    if summary['key'].startswith('upstream-') and summary['attempts']==6:
                        self.relay.health.benchmark(config['upstreams'][int(summary['key'].split('-')[1])],summary)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.current)
        if result is None:
            return None
        groups = {}
        for sample in result["results"]:
            g = groups.setdefault(sample["key"], dict(key=sample["key"],port=sample["port"],attempts=0,
                                  succeeded=0, times=[], provider_times={}))
            g["attempts"] += 1
            valid = sample.get("reachable") and type(sample.get("status")) is int and sample.get("outcome") not in ("failed","interrupted","cancelled","tls_error")
            if valid:
                g["succeeded"] += 1
                ms = sample["latency_ms"]
                g["times"].append(ms)
                g["provider_times"].setdefault(sample["provider"], []).append(ms)
        summaries = []
        for g in groups.values():
            times = sorted(g.pop("times"))
            g.update(p50_ms=round(statistics.median(times),1) if times else None,
                     p95_ms=times[max(0,math.ceil(len(times)*.95)-1)] if times else None,
                     jitter_ms=round(statistics.pstdev(times),1) if len(times)>1 else None,
                     provider_medians={p:statistics.median(t) for p,t in g.pop("provider_times").items()})
            summaries.append(g)
        result["summaries"] = summaries
        # A partial/cancelled run must never produce a confident recommendation.
        candidates = [g for g in summaries if g["key"] != "relay" and g["attempts"] == 6 and g["succeeded"] == 6]
        result["best"] = min(candidates,key=lambda g:g["p50_ms"])["key"] if candidates and not result["running"] and not result["cancelled"] and not result["error"] and not result.get('stale') else None
        return result
