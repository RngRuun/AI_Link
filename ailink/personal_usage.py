"""Personal observations from attributed thread token events, never account % deltas."""
import json
import time
import hashlib
import copy
from datetime import datetime
from pathlib import Path
from .config import atomic_json
from .import_paths import collect_jsonl


class PersonalUsage:
    def __init__(self, directory):
        self.path = Path(directory) / "personal-usage.json"
        self.entries = []
        self.baselines = {}
        self.incomplete = False
        self.credited = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.entries = [e for e in data.get("entries", [])[-50000:] if isinstance(e, dict)
                                and type(e.get("tokens")) is int and e["tokens"] >= 0
                                and type(e.get("at")) in (int, float) and e["at"] > time.time()-8*86400
                                and e.get("plan") in ("plus", "pro") and isinstance(e.get("profile"), str)]
                self.incomplete = data.get("incomplete") is True
                if isinstance(data.get("credited"), dict):
                    self.credited = {k:v for k,v in list(data["credited"].items())[-5000:] if isinstance(v, list)}
        except (OSError, ValueError, TypeError):
            self.incomplete = self.path.exists()

    def save(self):
        atomic_json(self.path, {"entries": self.entries, "incomplete": self.incomplete, "credited": self.credited})

    def prime(self, scope, thread_id, empty=False):
        # Only a freshly created thread has a proven zero cumulative baseline.
        self.baselines[(scope, thread_id)] = 0 if empty else None

    def observe(self, profile, plan, scope, params, now=None, persist=True):
        if plan not in ("plus", "pro") or not profile:
            return 0
        if not isinstance(params, dict) or not isinstance(params.get("tokenUsage"), dict) or not isinstance(params["tokenUsage"].get("total"),dict):
            return 0
        total = params["tokenUsage"]["total"].get("totalTokens")
        thread_id = params.get("threadId")
        if type(total) is not int or total < 0 or total > 10**15 or not isinstance(thread_id, str):
            return 0
        key = (scope, thread_id)
        previous = self.baselines.get(key)
        if previous is not None and total < previous:
            return 0  # Ignore delayed counters; never turn a replay into new consumption.
        self.baselines[key] = total
        if previous is None:
            return 0  # First observation establishes a baseline, not retrospective ownership.
        identity = hashlib.sha256(thread_id.encode()).hexdigest()
        ranges = self.credited.get(identity, [])
        delta = total-previous-sum(max(0, min(total, end)-max(previous, start)) for start,end in ranges)
        if delta <= 0:
            return 0
        now = time.time() if now is None else now
        if now < time.time()-8*86400 or now > time.time()+60:
            return 0
        merged = []
        for start,end in sorted([*ranges, [previous,total]]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1],end)
            else:
                merged.append([start,end])
        self.credited[identity] = merged
        self.entries = [e for e in self.entries if time.time()-8*86400 <= e["at"] <= time.time()+60]
        bucket = int(now//60)
        last = self.entries[-1] if self.entries else None
        if last and last.get("bucket") == bucket and last["profile"] == profile and last["plan"] == plan:
            last["tokens"] += delta
            last["at"] = now  # Conservative expiry: never free a rolling budget early.
        else:
            self.entries.append({"at": now, "bucket": bucket, "profile": profile, "plan": plan, "tokens": delta})
        if len(self.entries) > 50000:
            self.entries = self.entries[-50000:]
            self.incomplete = True
        if persist:
            self.save()
        return delta

    def import_files(self, paths, profile, plan, scope):
        discovered = collect_jsonl(paths)
        original = self.entries, self.baselines, self.credited, self.incomplete
        self.entries, self.baselines, self.credited = copy.deepcopy((self.entries, self.baselines, self.credited))
        try:
            total = sum(self.import_file(p, profile, plan, scope, persist=False) for p in discovered['paths'])
            self.save()
        except BaseException as error:
            self.entries, self.baselines, self.credited, self.incomplete = original
            if isinstance(error,(UnicodeError,OSError)):raise ValueError('部分 JSONL 无法读取，请使用原始 UTF-8 会话记录；本批未写入。') from error
            raise
        return {key:discovered[key] for key in ('files','bytes','skipped_links')} | {'tokens':total}

    def import_file(self, path, profile, plan, scope, persist=True):
        path = Path(path)
        if not path.is_file() or path.suffix.lower() != ".jsonl" or path.stat().st_size > 64*1024*1024:
            raise ValueError("请选择不超过 64 MB 的 Codex JSONL 对话记录。")
        thread_id, previous, imported = None, 0, 0
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    payload = record.get("payload", {})
                    if record.get("type") == "session_meta":
                        thread_id = payload.get("id")
                        if payload.get("forked_from_id"):
                            previous = None
                    if not thread_id or record.get("type") != "event_msg" or payload.get("type") != "token_count":
                        continue
                    total = (payload.get("info") or {}).get("total_token_usage", {}).get("total_tokens")
                    at = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
                    if type(total) is not int or total < 0:
                        continue
                    # Import has an independent cursor. Credited intervals deduplicate live observations.
                    live = self.baselines.get((scope,thread_id))
                    self.baselines[(scope,thread_id)] = previous
                    imported += self.observe(profile,plan,scope,{"threadId":thread_id,"tokenUsage":{"total":{"totalTokens":total}}},at,persist=False)
                    previous = max(previous or 0,total)
                    self.baselines[(scope,thread_id)] = live
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        if persist:
            self.save()
        return imported

    def snapshot(self, profile, now=None):
        now = time.time() if now is None else now
        result = {}
        for plan in ("plus", "pro"):
            result[plan] = {}
            for key, seconds in (("primary", 5*3600), ("secondary", 7*86400)):
                entries = [e for e in self.entries if e["profile"] == profile and e["plan"] == plan
                           and now-seconds < e["at"] <= now]
                total = sum(e["tokens"] for e in entries)
                # Aggregate only observed entries into <=240 display points; preserve the full sum.
                groups = {}
                step = seconds/239
                for e in entries:
                    group = int((e["at"]-(now-seconds))/step)
                    p = groups.setdefault(group, {"at": e["at"], "tokens": 0})
                    p["at"] = max(p["at"], e["at"])
                    p["tokens"] += e["tokens"]
                result[plan][key] = {"total": total, "points": sorted(groups.values(), key=lambda p:p["at"]), "seconds": seconds}
        return {"profile": profile, "plans": result, "incomplete": self.incomplete,
                "basis": "attributed_thread_token_events", "updated": now}
