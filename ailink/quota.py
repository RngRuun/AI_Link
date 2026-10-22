"""Quota observation and opt-in, per-thread cancellation/continuation state machine."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import queue
import threading
import time
from .codex_rpc import CodexRPC
from .config import atomic_json
from .personal_usage import PersonalUsage
from .conversations import ConversationControls
import uuid

PRESETS = {"plus": 15, "pro": 10}  # Editable reserve preferences, never claims about plan capacity.
CONTINUE = "请继续本对话中尚未完成的任务。先核对已经完成的工作，避免重复执行；如果任务实际上已经全部完成，不要再开展额外工作。"


def quota_windows(payload, bucket="codex"):
    buckets = payload.get("rateLimitsByLimitId")
    value = buckets.get(bucket) if isinstance(buckets, dict) and buckets else payload.get("rateLimits")
    if not isinstance(value, dict) or value.get("limitId", "codex") != bucket:
        return [], None
    windows = []
    for key in ("primary", "secondary"):
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            return [], value.get("planType")
        used = raw.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            return [], value.get("planType")
        windows.append({"key": key, "remaining": 100-used, "reset": raw.get("resetsAt"),
                        "minutes": raw.get("windowDurationMins")})
    return windows, value.get("planType")


class QuotaGuard(ConversationControls):
    def __init__(self, state_dir, telemetry, rpc_factory=CodexRPC):
        self.path = Path(state_dir) / "quota-guard.json"
        self.telemetry, self.rpc_factory = telemetry, rpc_factory
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.closed = threading.Event()
        self.wake = threading.Event()
        self.rpc = None
        self.shared=False
        self.data = {"mode": "plus", "thresholds": {p:{"primary":v,"secondary":v} for p,v in PRESETS.items()}, "auto_resume": False,
                     "resume_target":"",
                     "bucket": "codex", "threads": {},
                     "caps": {p: {"enabled": False, "primary": 100000, "secondary": 1000000} for p in PRESETS},
                     "active_profile": "me", "profiles": {}}
        if self.path.exists():
            try:
                old = json.loads(self.path.read_text(encoding="utf-8"))
                thresholds = old.get("thresholds", {})
                migrated = {p:self.validate_thresholds(thresholds.get(p)) for p in PRESETS}
                if old.get("mode") in PRESETS:
                    self.data["mode"], self.data["thresholds"] = old["mode"], migrated
                    self.data["auto_resume"] = old.get("auto_resume") is True
                    self.data['resume_target']=old.get('resume_target','') if isinstance(old.get('resume_target',''),str) else ''
                    for plan in PRESETS:
                        try:
                            self.data["caps"][plan] = self.validate_caps(old.get("caps", {}).get(plan))
                        except ValueError:
                            pass
                    if isinstance(old.get("bucket"), str) and len(old["bucket"]) <= 128:
                        self.data["bucket"] = old["bucket"]
                    if isinstance(old.get("account_fingerprint"), str):
                        self.data["account_fingerprint"] = old["account_fingerprint"]
                    records = old.get("threads", {})
                    if isinstance(records, dict):
                        for key, record in list(records.items())[:100]:
                            if isinstance(key, str) and isinstance(record, dict) and record.get("state") in ("watching", "interrupt_pending", "paused", "paused_idle", "resume_pending"):
                                self.data["threads"][key] = record
            except (ValueError, OSError, AttributeError, TypeError):
                pass
        self.enabled = False  # Restart always requires deliberate re-arming.
        self.data["profiles"] = {"me": {"name": "我的用量", "caps": copy.deepcopy(self.data["caps"])}}
        try:
            profiles = old.get("profiles", {}) if self.path.exists() else {}
            for profile_id, profile in list(profiles.items())[:10]:
                if isinstance(profile, dict) and isinstance(profile.get("name"), str) and len(profile["name"]) <= 24:
                    self.data["profiles"][profile_id] = {"name": profile["name"], "caps": {p:self.validate_caps(profile["caps"][p]) for p in PRESETS}}
            selected_profile = old.get("active_profile", "me") if self.path.exists() else "me"
            self.data["active_profile"] = selected_profile if selected_profile in self.data["profiles"] else "me"
        except (ValueError, AttributeError, TypeError, KeyError, UnboundLocalError):
            pass
        self.data["caps"] = self.data["profiles"][self.data["active_profile"]]["caps"]
        self.status_text = "未连接 Codex"
        self.error = None
        self.windows = []
        self.plan = None
        self.updated = 0
        self.choices = []
        self.outputs = {}
        self.buckets = ["codex"]
        self.account_key = None
        self.worker = None
        self.personal = PersonalUsage(state_dir)
        self.runtime = {}
        self.source = None
        self.catalog={'items':[],'cursor':None,'search':'','updated':0}
        self.catalog_ids=set()
        self.last_attach={'attached':[],'failed':[]}

    def source_context(self):
        with self.lock:return self.data['active_profile'],self.data['mode'],self.data.get('account_fingerprint','local-session')

    def source_credit(self, context, thread_id, total, previous, at):
        profile,plan,scope=context
        with self.lock:
            key=(scope,thread_id);live=self.personal.baselines.get(key)
            self.personal.baselines[key]=previous
            try:return self.personal.observe(profile,plan,scope,{'threadId':thread_id,'tokenUsage':{'total':{'totalTokens':total}}},at,persist=False)
            finally:self.personal.baselines[key]=live

    def source_flush(self):
        with self.lock:self.personal.save()
        self.wake.set()

    def _save(self):
        atomic_json(self.path, self.data)

    def snapshot(self):
        with self.lock:
            self._drain_usage()
            source=self.source.snapshot() if self.source else None
            personal=self.personal.snapshot(self.data['active_profile'])
            personal['incomplete']=personal['incomplete'] or bool(source and source['enabled'] and source.get('error'))
            return copy.deepcopy(dict(config=self.data, enabled=self.enabled, connected=self.rpc is not None,
                                      windows=self.windows, plan=self.plan, updated=self.updated, error=self.error,
                                      message=self.status_text, choices=self.choices, buckets=self.buckets, outputs=self.outputs,
                                      personal=personal,source=source,catalog=self.catalog,last_attach=self.last_attach,
                                      continuation_rules={k:self.continuation(k,r) for k,r in self.data['threads'].items()},
                                      turn_states={k:v.get('status','unknown') for k,v in self.runtime.items()},
                                      control={'scope':'shared' if getattr(self,'shared',False) else 'managed','selected':sum(bool(r.get('selected')) and k in {c['id'] for c in self.choices} for k,r in self.data['threads'].items())}))

    def connect(self, shared=False, endpoint='', token_file=None):
        with self.lock:
            if self.closed.is_set():
                raise RuntimeError("应用正在退出，不能连接 Codex。")
            if self.rpc:
                return
            self.choices, self.outputs = [], {}
            self.runtime = {}
            self.catalog={'items':[],'cursor':None,'search':'','updated':0};self.catalog_ids=set();self.last_attach={'attached':[],'failed':[]}
            rpc = self.rpc_factory(shared=shared,endpoint=endpoint,token_file=token_file) if endpoint or token_file else self.rpc_factory(shared=shared)
            rpc.wake = self.wake
            rpc.connect()
            try:
                if self.closed.is_set():
                    raise RuntimeError("应用正在退出，连接已取消。")
                account = rpc.call("account/read", {"refreshToken": False}).get("account") or {}
                if account.get("type") != "chatgpt":
                    raise RuntimeError("额度守护需要 Codex 已登录的 ChatGPT 账户，API Key 登录不提供套餐额度。")
                # Account identity is compared in memory only; never write email or tokens to the journal.
                self.account_key = account.get("email")
                fingerprint = hashlib.sha256(str(self.account_key).encode()).hexdigest()
                previous = self.data.get("account_fingerprint")
                if previous and previous != fingerprint:
                    self.data["threads"] = {}
                self.data["account_fingerprint"] = fingerprint
                self._save()
                self.plan = account.get("planType")
                self.rpc = rpc
                self.shared = shared
                self.status_text = "已连接共享 Codex 服务" if shared else "已连接本地历史与托管 Codex"
                if not shared:
                    for thread_id, record in list(self.data["threads"].items())[:100]:
                        if record.get("owned") or record.get('attached'):
                            try:
                                params={"threadId":thread_id}
                                if record.get('owned'):params.update(sandbox='read-only',approvalPolicy='untrusted')
                                thread = rpc.call("thread/resume", params)["thread"]
                                self.choices.append({"id": thread_id, "name": thread.get("name") or thread.get("preview") or thread_id})
                                if thread.get('turns'):self.runtime[thread_id]=thread['turns'][-1]
                                self.personal.prime(fingerprint, thread_id)
                            except Exception as error:
                                # Blank threads have no rollout in this Codex version. Recreate only
                                # an unused preparation, never an accepted or uncertain model request.
                                if (record.get('prepared') and not record.get('start_pending') and
                                    not record.get('turn_id') and record.get('state')=='watching' and
                                    'no rollout found' in str(error).lower() and
                                    isinstance(record.get('cwd'),str) and Path(record['cwd']).is_dir()):
                                    replacement=rpc.call('thread/start',{'cwd':record['cwd'],'sandbox':'read-only','approvalPolicy':'untrusted'})['thread']['id']
                                    self.data['threads'][replacement]=record
                                    self.data['threads'].pop(thread_id)
                                    if self.data.get('resume_target')==thread_id:self.data['resume_target']=replacement
                                    self.personal.prime(fingerprint,replacement,empty=True)
                                    self._save()
                                    self.choices.append({'id':replacement,'name':'待发送任务 · '+Path(record['cwd']).name})
                                else:self.error = "部分托管对话未能重新加载，请在 Codex 中核实。"
                self.refresh()
            except Exception:
                self.rpc = None
                rpc.close()
                raise
            self.stop = threading.Event()
            self.worker = threading.Thread(target=self._loop, daemon=True, name="quota-monitor")
            self.worker.start()

    @staticmethod
    def validate_thresholds(value):
        if type(value) is int:
            value={"primary":value,"secondary":value}  # Preserve both limits for legacy settings/clients.
        if (not isinstance(value,dict) or set(value)!={"primary","secondary"} or
            any(type(value.get(k)) is not int or not 1<=value[k]<=95 for k in ("primary","secondary"))):
            raise ValueError("短期、长期剩余额度阈值都必须为 1–95 的整数。")
        return dict(value)

    def reached_windows(self):
        thresholds=self.data["thresholds"][self.data["mode"]]
        return [w["key"] for w in self.windows if w["remaining"]<=thresholds[w["key"]]]

    @staticmethod
    def validate_caps(value):
        if not isinstance(value, dict) or set(value) != {"enabled", "primary", "secondary"} or type(value.get("enabled")) is not bool:
            raise ValueError("个人上限需要开关及近 5 小时、近 7 天两项 token 上限。")
        if any(type(value.get(k)) is not int or not 1 <= value[k] <= 1000000000 for k in ("primary", "secondary")):
            raise ValueError("个人 token 上限必须为 1–1,000,000,000 的整数。")
        return dict(value)

    def configure(self, mode, threshold, auto_resume, bucket="codex", caps=None, resume_target=None):
        if mode not in PRESETS:
            raise ValueError("请选择 Plus / Pro 模式。")
        threshold=self.validate_thresholds(threshold)
        with self.lock:
            caps = self.validate_caps(caps) if caps is not None else None
            if self.enabled:
                raise RuntimeError("请先关闭额度守护再修改设置。")
            if bucket not in self.buckets:
                raise ValueError("请选择服务端返回的额度池。")
            if resume_target is not None:
                if not isinstance(resume_target,str) or len(resume_target)>128 or (resume_target and resume_target not in {c['id'] for c in self.choices}):raise ValueError('请选择当前连接可控制的续聊目标。')
            self.data.update(mode=mode, auto_resume=bool(auto_resume), bucket=bucket)
            if resume_target is not None:
                self.data['resume_target']=resume_target
                for thread_id,record in self.data['threads'].items():
                    record['continuation']={**self.continuation(thread_id,record),'enabled':thread_id==resume_target}
            self.data["thresholds"][mode] = threshold
            if caps is not None:
                self.data["caps"][mode] = caps
            self._save()
            self.windows, self.updated = [], 0
        self.wake.set()

    def arm(self, enabled):
        with self.lock:
            if enabled and (not self.rpc or not self.windows or time.time()-self.updated > 65):
                raise RuntimeError("请先连接并成功刷新额度，再开启守护。")
            if enabled and {w['key'] for w in self.windows}!={'primary','secondary'}:
                raise RuntimeError("短期或长期额度数据缺失，无法同时执行两项限制，请刷新额度后再开启守护。")
            if enabled and (not self.plan or self.plan.lower() != self.data["mode"]):
                raise RuntimeError("所选模式与服务端账户计划不符，请切换 Plus / Pro 模式。")
            targets=[c['id'] for c in self.choices if self.data['threads'].get(c['id'],{}).get('selected')]
            if enabled and not targets:raise RuntimeError('没有已接管并选中守护的对话；读取额度不等于能够控制其他 Codex 窗口。请先选择可控目标。')
            if enabled and self.data['auto_resume']:
                if not any(self.continuation(k,self.data['threads'][k])['enabled'] for k in targets):raise RuntimeError('请为至少一个已选中目标开启续聊规则，或关闭自动续聊。')
            if enabled and self.shared:
                for thread_id in targets:
                    thread=self.rpc.call('thread/resume',{'threadId':thread_id})['thread']
                    if thread.get('turns'):self.runtime[thread_id]=thread['turns'][-1]
            self.enabled = bool(enabled)
            if not enabled:
                for record in self.data['threads'].values():
                    if record.get('state')!='resume_pending':record['state']='watching'
                self._save()
            self.status_text = f"额度守护已开启 · {len(targets)} 个可控目标" if enabled else "额度守护已关闭；不会自动续聊"
            if enabled:self._evaluate()
        self.wake.set()

    def refresh(self):
        with self.lock:
            if not self.rpc:
                raise RuntimeError("请先连接 Codex。")
            self._drain_usage()
            self._evaluate()  # Personal limits remain enforceable when account quota reads fail.
            self.windows, self.updated = [], 0
            account = self.rpc.call("account/read", {"refreshToken": False}).get("account") or {}
            if account.get("type") != "chatgpt" or account.get("email") != self.account_key:
                self.enabled = False
                raise RuntimeError("Codex 登录账户发生变化；守护已关闭，请重新连接。")
            self.plan = account.get("planType")
            payload = self.rpc.call("account/rateLimits/read")
            self.buckets = list(payload.get("rateLimitsByLimitId") or {"codex": {}})
            self.windows, bucket_plan = quota_windows(payload, self.data["bucket"])
            self.plan = bucket_plan or self.plan
            self.updated = time.time()
            self.error = None
            if not self.windows:
                self.error = "额度数据缺失；自动续聊已暂停。"
            if self.shared:
                loaded = self.rpc.call("thread/loaded/list", {"limit": 100}).get("data", [])
                names = {choice["id"]: choice["name"] for choice in self.choices}
                self.choices = [{"id": thread_id, "name": names.get(thread_id, thread_id)} for thread_id in loaded[:100]]
            self._evaluate()

    def select(self, thread_id, selected):
        with self.lock:
            if thread_id not in {c["id"] for c in self.choices}:
                raise ValueError("只能守护当前连接可控制的对话。")
            if selected and self.shared:
                result = self.rpc.call("thread/resume", {"threadId":thread_id})
                turns = result.get("thread",{}).get("turns",[])
                if turns:
                    self.runtime[thread_id] = turns[-1]
            record = self.data["threads"].setdefault(thread_id, {"state": "watching"})
            record["selected"] = bool(selected)
            if not selected and record.get('state')!='resume_pending':record['state']='watching'
            self._save()
        self.wake.set()

    def prepare_chat(self, cwd):
        with self.lock:
            if not self.rpc or self.shared:raise RuntimeError('请先连接 AI Link 托管模式。')
            if not isinstance(cwd,str) or not Path(cwd).is_dir():raise ValueError('请选择存在的工作区文件夹。')
            if len(self.data['threads'])>=100:raise RuntimeError('已达到 100 个受保护对话上限。')
            directory=str(Path(cwd).resolve())
            result=self.rpc.call('thread/start',{'cwd':directory,'sandbox':'read-only','approvalPolicy':'untrusted'})
            thread_id=result['thread']['id']
            self.data['threads'][thread_id]={'selected':True,'state':'watching','owned':True,'prepared':True,'cwd':directory,'personal_profile':self.data['active_profile'],'continuation':{'enabled':True,'prompt':''}}
            self.choices.append({'id':thread_id,'name':'待发送任务 · '+Path(cwd).name})
            if not self.data.get('resume_target'):self.data['resume_target']=thread_id
            self.personal.prime(self.data.get('account_fingerprint'),thread_id,empty=True)
            self._save();return {'threadId':thread_id}

    def start_chat(self, prompt, cwd, prepared=None):
        with self.lock:
            self._drain_usage()
            if not self.rpc or self.shared:
                raise RuntimeError("请先连接 AI Link 托管模式。")
            if not prompt.strip() or len(prompt) > 20000 or not Path(cwd).is_dir():
                raise ValueError("请输入任务，并选择存在的工作目录。")
            if not self.enabled or not self.windows or time.time()-self.updated > 65:
                raise RuntimeError("请先刷新额度并开启守护，再创建受保护对话。")
            if not prepared and len(self.data["threads"]) >= 100:
                raise RuntimeError("已达到 100 个受保护对话的本地上限。请在完整 Codex 客户端管理后续任务。")
            if not self.plan or self.plan.lower() != self.data['mode'] or {w['key'] for w in self.windows}!={'primary','secondary'}:
                raise RuntimeError("当前套餐或短期、长期额度不完整，请刷新并核实守护状态。")
            if self.reached_windows() or self.cap_reached():
                raise RuntimeError("当前额度已触及保留阈值或个人消耗上限，不能开始新任务。")
            if prepared:
                record=self.data['threads'].get(prepared,{})
                if not record.get('prepared') or not record.get('owned') or not record.get('selected') or prepared not in {c['id'] for c in self.choices} or record.get('cwd')!=str(Path(cwd).resolve()):raise ValueError('准备的对话与当前工作区不一致，请核实目标。')
                thread_id=prepared;record['prepared']=False
                next(c for c in self.choices if c['id']==thread_id)['name']=prompt[:80]
            else:
                result = self.rpc.call("thread/start", {"cwd": str(Path(cwd).resolve()), "sandbox": "read-only", "approvalPolicy": "untrusted"})
                thread_id = result["thread"]["id"]
                self.choices.append({"id": thread_id, "name": prompt[:80]})
                self.data["threads"][thread_id] = {"selected": True, "state": "watching", "owned": True,'continuation':{'enabled':True,'prompt':''}}
            self.data["threads"][thread_id]["personal_profile"] = self.data["active_profile"]
            self.data['threads'][thread_id]['start_pending']=True
            self.personal.prime(self.data.get("account_fingerprint"), thread_id, empty=True)
            self._save()
            try:
                result = self.rpc.call("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]})
            except Exception:
                self.data['threads'][thread_id]['control_error']='任务发送结果未确认，未自动重发；请核实该对话状态。'
                self._save()
                raise
            self.data['threads'][thread_id].pop('start_pending',None)
            self.data['threads'][thread_id].pop('control_error',None)
            self._save()
            if result.get("turn"):
                self.runtime[thread_id] = {**result["turn"], "status":"pendingStart"}
            return thread_id

    def _thread(self, thread_id):
        self._drain_control()
        if thread_id in self.runtime:
            thread = self.rpc.call("thread/read", {"threadId":thread_id})["thread"]
            self._drain_control()
            latest = dict(self.runtime[thread_id])
            runtime_status = thread.get("status",{}).get("type") if isinstance(thread.get("status"),dict) else None
            if ((latest.get("status") == "interrupted" and runtime_status != "idle") or
                (latest.get("status") == "inProgress" and runtime_status == "idle")):
                latest["status"] = "unknown"  # Wait for matching events; never infer an interrupted turn.
            thread["turns"] = [latest]
            return thread
        try:
            return self.rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
        except RuntimeError as error:
            if not any(term in str(error) for term in ("includeTurns", "paginated", "list_turns")):
                raise
            thread = self.rpc.call("thread/read", {"threadId": thread_id})["thread"]
            try:
                turns = self.rpc.call("thread/turns/list", {"threadId":thread_id,"limit":2,"sortDirection":"desc","itemsView":"summary"})
            except RuntimeError:
                if thread.get('status',{}).get('type')=='idle':thread['turns']=[];return thread
                raise RuntimeError("此 Codex 版本不提供回合历史；未取得活动回合 ID，无法确认停止。") from None
            thread["turns"] = list(reversed(turns.get("data",[])))
            return thread

    def cap_reached(self):
        cap = self.data["caps"][self.data["mode"]]
        usage = self.personal.snapshot(self.data["active_profile"])["plans"][self.data["mode"]]
        source=self.source.snapshot() if self.source else None
        unavailable=bool(source and source['enabled'] and source.get('error'))
        return cap["enabled"] and (self.personal.incomplete or unavailable or any(usage[k]["total"] >= cap[k] for k in ("primary", "secondary")))

    def _drain_usage(self):
        self._drain_control()
        events = getattr(self.rpc, "usage_events", None)
        if not isinstance(events, queue.Queue):
            return
        if getattr(self.rpc, "usage_overflow", False):
            self.personal.incomplete = True
        while True:
            try:
                params = events.get_nowait()
            except queue.Empty:
                break
            if not isinstance(params,dict) or not isinstance(params.get("threadId"),str):
                continue
            record = self.data["threads"].get(params.get("threadId"), {})
            profile = record.get("personal_profile")
            if profile in self.data["profiles"]:
                self.personal.observe(profile, self.plan, self.data.get("account_fingerprint"), params)

    def _drain_control(self):
        events = getattr(self.rpc,"control_events",None)
        if not isinstance(events,queue.Queue):
            return
        while True:
            try:
                params = events.get_nowait()
            except queue.Empty:
                break
            if isinstance(params,dict) and isinstance(params.get("threadId"),str) and isinstance(params.get("turn"),dict):
                self.runtime[params["threadId"]] = params["turn"]
                record=self.data['threads'].get(params['threadId'],{})
                if record.pop('start_pending',None):
                    record.pop('control_error',None);self._save()

    def profile(self, profile_id=None, name=None):
        with self.lock:
            if self.enabled:
                raise RuntimeError("请先关闭守护再切换个人统计档案。")
            if name is not None:
                if not isinstance(name, str) or not name.strip() or len(name.strip()) > 24 or len(self.data["profiles"]) >= 10:
                    raise ValueError("档案名称应为 1–24 字，最多 10 个档案。")
                profile_id = uuid.uuid4().hex
                self.data["profiles"][profile_id] = {"name": name.strip(), "caps": {p: {"enabled": False, "primary": 100000, "secondary": 1000000} for p in PRESETS}}
            if profile_id not in self.data["profiles"]:
                raise ValueError("请选择存在的个人统计档案。")
            self._drain_usage()
            self.data["active_profile"] = profile_id
            self.data["caps"] = self.data["profiles"][profile_id]["caps"]
            self._save()

    def attribute(self, thread_id, personal):
        with self.lock:
            if thread_id not in {t["id"] for t in self.choices}:
                raise ValueError("对话不在当前可观测范围。")
            self._drain_usage()
            record = self.data["threads"].setdefault(thread_id, {"state": "watching", "selected": False})
            if personal:
                if self.shared:
                    self.rpc.call("thread/resume", {"threadId": thread_id})  # Subscribe to future events only.
                record["personal_profile"] = self.data["active_profile"]
                self.personal.prime(self.data.get("account_fingerprint"), thread_id)
            else:
                record.pop("personal_profile", None)
            self._save()

    def import_personal(self, paths):
        with self.lock:
            if self.enabled:
                raise RuntimeError("请先关闭守护，再导入个人记录。")
            scope = self.data.get("account_fingerprint", "local-session")
            return self.personal.import_files(paths,self.data["active_profile"],self.data["mode"],scope)

    def _evaluate(self):
        if self.closed.is_set() or not self.enabled:
            return
        if not self.plan or self.plan.lower() != self.data["mode"]:
            self.enabled = False
            self.error = "账户计划未知或与模式不符；守护已关闭。"
            return
        personal = self.cap_reached()
        fresh = bool(self.windows) and time.time()-self.updated <= 65
        triggered=self.reached_windows() if fresh else []
        reserve = bool(triggered)
        if not fresh and not personal:
            return
        full = fresh and {w['key'] for w in self.windows}=={'primary','secondary'} and not personal and not (getattr(self.rpc,"control_overflow",False) is True) and all(w["remaining"] == 100 for w in self.windows)
        controllable = {choice["id"] for choice in self.choices}
        control_errors=[]
        for thread_id, record in self.data["threads"].items():
            if not record.get("selected") or thread_id not in controllable:
                continue
            if self.closed.is_set():
                return
            mine = record.get("personal_profile") == self.data["active_profile"]
            if record.get("stop_reason") == "personal_cap" and not mine:
                continue
            low = reserve or (personal and mine)
            try:
                thread = self._thread(thread_id)
            except (RuntimeError, OSError) as error:
                self.error = str(error)[:250]
                record['control_error']=self.error
                continue
            turns = thread.get("turns", [])
            messages = [item.get("text", "") for turn in turns[-2:] for item in turn.get("items", []) if item.get("type") == "agentMessage"]
            self.outputs[thread_id] = "\n\n".join(messages)[-12000:] or "Codex 正在处理，或尚无可展示的回复。"
            latest = turns[-1] if turns else {}
            runtime_status = thread.get('status',{}).get('type') if isinstance(thread.get('status'),dict) else None
            running = latest.get('status') in ('inProgress','pendingStart') or runtime_status in ('active','busy')
            idle = latest.get('status') in ('completed','failed','interrupted') or runtime_status=='idle'
            if thread.get('name') or thread.get('preview'):
                next(choice for choice in self.choices if choice['id']==thread_id)['name']=thread.get('name') or thread['preview']
            state=record['state']
            if latest.get('status')=='completed':
                if state!='watching' and not (state=='resume_pending' and latest.get('id')==record.get('turn_id')):
                    record['state']='watching';record.pop('control_error',None);self._save()
                continue
            if low and state=='watching':
                if not running and not idle:
                    record['control_error']='尚未取得可确认的回合状态；未宣称停止。';continue
                record.update(turn_id=latest.get('id'),windows=[w['key'] for w in self.windows],trigger_windows=triggered,stop_reason='personal_cap' if personal and mine else 'reserve',stopped_at=time.time())
                if running:
                    if not latest.get('id') or latest.get('status')!='inProgress':
                        record['control_error']='等待当前活动回合 ID，尚不能确认停止。';continue
                    record.update(state='interrupt_pending',interrupt_attempts=1,interrupt_requested_at=time.time());record.pop('control_error',None);self._save()
                    try:self.rpc.call('turn/interrupt',{'threadId':thread_id,'turnId':latest['id']})
                    except (RuntimeError,OSError) as error:
                        record['control_error']=str(error)[:250];control_errors.append(error);continue
                    self.status_text='已请求停止目标回合，等待 Codex 确认'
                    self.telemetry.event('info','quota_interrupt_requested',self.status_text)
                else:
                    if not latest.get('id') or latest.get('status') not in ('interrupted','failed'):
                        continue
                    record['state']='paused_idle';record.pop('control_error',None);self._save()
                    self.status_text='目标当前空闲，已进入阈值停止状态，等待满额'
            elif state in ('interrupt_pending','paused','paused_idle'):
                if latest.get('id')!=record.get('turn_id'):
                    record['state']='watching';self._save();self.wake.set();continue
                if state=='interrupt_pending':
                    if latest.get('status')=='interrupted':record['state']='paused';self._save()
                    elif idle:record['state']='paused_idle';self._save()
                    else:
                        if latest.get('status')=='inProgress' and time.time()-record.get('interrupt_requested_at',0)>=5:
                            if record.get('interrupt_attempts',1)<3:
                                record['interrupt_attempts']=record.get('interrupt_attempts',1)+1;record['interrupt_requested_at']=time.time();self._save()
                                try:self.rpc.call('turn/interrupt',{'threadId':thread_id,'turnId':latest['id']})
                                except (RuntimeError,OSError) as error:
                                    record['control_error']=str(error)[:250];control_errors.append(error);continue
                            else:record['control_error']='已向同一活动回合请求停止 3 次，仍未确认中断。请在原 Codex 客户端核实。'
                        continue
                if running:continue
                known=set(record.get('windows',[])) <= {w['key'] for w in self.windows}
                rule=self.continuation(thread_id,record)
                if full and known and self.data['auto_resume'] and rule['enabled']:
                    if not record.get('turn_id'):
                        record['control_error']='此版本未提供空闲回合身份，无法验证是否有新用户活动；暂不自动续聊。';continue
                    record['state']='resume_pending';self._save()
                    try:current_thread=self._thread(thread_id)
                    except (RuntimeError,OSError) as error:
                        record['control_error']=str(error)[:250];control_errors.append(error);continue
                    current=current_thread.get('turns',[])
                    if not current or current[-1].get('id')!=record['turn_id'] or current[-1].get('status') not in ('interrupted','failed'):
                        record['state']='watching';self._save();continue
                    try:result=self.rpc.call('turn/start',{'threadId':thread_id,'input':[{'type':'text','text':rule['prompt'] or CONTINUE}]})
                    except (RuntimeError,OSError) as error:
                        record['control_error']=str(error)[:250];control_errors.append(error);continue
                    if result.get('turn'):self.runtime[thread_id]={**result['turn'],'status':'pendingStart'}
                    record['state']='watching';record.pop('control_error',None);self._save()
                    self.status_text='额度已确认充满，已按该对话的规则提交一次继续任务'
                    self.telemetry.event('success','quota_resumed',self.status_text)
            elif state=='resume_pending' and latest.get('id') and latest['id']!=record.get('turn_id'):
                # A new observed turn resolves the unknown submission; never replay it.
                record['state']='watching';self._save();self.wake.set()
        # One uncertain target must not prevent other selected conversations from
        # receiving their own protection. Keep each target's journal unchanged.
        if control_errors:raise control_errors[0]

    def _loop(self):
        stop = self.stop
        delay = 30
        while not stop.is_set():
            self.wake.wait(delay)
            self.wake.clear()
            if stop.is_set():
                break
            # Coalesce streaming notifications; never hammer the quota endpoint.
            if stop.wait(max(0, 5-(time.time()-self.updated))):
                break
            try:
                self.refresh()
                delay = 30
            except Exception as error:
                with self.lock:
                    self.error = str(error)[:250]
                    self.windows = []
                delay = min(120, delay*2)

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.lock:
            self.enabled = False
            if self.rpc:
                self.rpc.close()
                self.rpc = None
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)

    def shutdown(self):
        self.closed.set()
        self.close()
