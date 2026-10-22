"""Shared application service, independent of any UI or HTTP server."""
from __future__ import annotations
import secrets
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from . import __version__
from .config import ConfigStore
from .diagnostics import Diagnostics, PROVIDERS, probe
from .events import Telemetry
from .kernel import FlClashAdapter
from .system_proxy import SystemProxy
from .transport import Relay
from .quota import QuotaGuard
from .team_usage import TeamUsage
from .personal_source import PersonalSource
from .network_measure import NetworkMeasure
from .routing import PRESETS
from .storage_cleanup import StorageCleanup

class Application:
    def __init__(self, directory, state_dir, system=None, relay_port=18790):
        self.directory, self.state_dir = Path(directory), Path(state_dir)
        self.store = ConfigStore(self.state_dir)
        self.telemetry = Telemetry()
        self.quota = QuotaGuard(self.state_dir, self.telemetry)
        self.quota.source = PersonalSource(self.state_dir,self.quota.source_context,self.quota.source_credit,self.quota.source_flush)
        self.system = system or SystemProxy(self.state_dir, port=relay_port)
        self.relay = Relay(lambda: self.store.value, self.telemetry, relay_port)
        self.diagnostics = Diagnostics(self.telemetry, lambda: self.store.value, self.relay)
        self.measure = NetworkMeasure(lambda: self.store.value, self.relay)
        self.team = TeamUsage(self.state_dir)
        self.storage = StorageCleanup(self.state_dir,self.quota)
        self.kernel = FlClashAdapter(self.state_dir)
        self.token = secrets.token_urlsafe(32)
        self.mutation_lock = threading.RLock()
        self.job_lock = threading.Lock()
        self.operation = {"running": False, "message": "", "error": None}
        self.enable_cancel = threading.Event()
        self.shutdown_event = threading.Event()
        self.kernel_cache = self.kernel.status()
        self.last_kernel_refresh = time.monotonic()
        self.telemetry.event("info", "app_started", "控制面板已就绪；优化尚未接管系统代理。")
    def start_observers(self):
        self.quota.source.start()

    def status(self):
        if time.monotonic() - self.last_kernel_refresh > 10:
            self.kernel_cache = self.kernel.status()
            self.last_kernel_refresh = time.monotonic()
        with self.job_lock:
            operation = dict(self.operation)
        return dict(version=__version__, config=self.store.value, relay=bool(self.relay.server),
                    relay_port=self.relay.port, system=self.system.current(), telemetry=self.telemetry.snapshot(),
                    diagnostics=self.diagnostics.snapshot(), operation=operation, kernel=self.kernel_cache,
                    providers=PROVIDERS, quota=self.quota.snapshot(), team=self.team.snapshot(), storage=self.storage.snapshot(),
                    network=dict(routes=self.relay.health.snapshot(self.store.value['upstreams']),
                                 measure=self.measure.snapshot(),
                                 preset=next((name for name,fields in PRESETS.items() if all(self.store.value[k]==v for k,v in fields.items())),None)))

    def preset(self, name):
        if not isinstance(name,str) or name not in PRESETS:
            raise ValueError("请选择速度优先、均衡或波动恢复策略。")
        with self.mutation_lock:
            if self.shutdown_event.is_set() or (self.measure.snapshot() or {}).get('running'):
                raise RuntimeError("请等待路径评估结束再切换策略。")
            result = self.store.save({**self.store.value,**PRESETS[name]})
            self.measure.invalidate()
            self.telemetry.event('info','network_preset_saved','传输策略已更新，将用于之后建立的连接。')
            return result

    def measure_paths(self):
        with self.mutation_lock:
            if self.shutdown_event.is_set():
                raise RuntimeError("应用正在退出。")
            self.relay.start()
            return self.measure.start()

    def enable(self):
        with self.job_lock:
            if self.shutdown_event.is_set():
                raise RuntimeError("应用正在退出，不能重新启用网络优化。")
            if self.operation["running"]:
                raise RuntimeError("正在处理另一项操作。")
            if self.system.current()["owned"]:
                raise RuntimeError("系统代理已接管。")
            self.operation = {"running": True, "message": "验证上游代理与 TLS 连接…", "error": None}
            self.enable_cancel.clear()
        threading.Thread(target=self._enable, name="ai-link-enable", daemon=True).start()

    def _enable(self):
        try:
            with self.mutation_lock:
                if self.enable_cancel.is_set() or self.shutdown_event.is_set():
                    raise RuntimeError("已取消启用，原系统代理保持不变。")
                self.relay.start()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda p: probe(p, "relay", self.store.value, self.relay.port), ["codex", "gemini"]))
                if not any(r["reachable"] for r in results):
                    raise RuntimeError("两项 TLS 验证均失败，已保留原系统代理。请检查上游端口或 VPN。")
                for result in results:
                    self.telemetry.sample(result["provider"], result)
                if self.enable_cancel.is_set() or self.shutdown_event.is_set():
                    raise RuntimeError("已取消启用，原系统代理保持不变。")
                self.system.acquire(terminal=self.store.value["terminal_environment"])
                self.telemetry.event("success", "system_proxy_acquired", "系统代理已接入本地中继。已有连接不会被迁移。")
            with self.job_lock:
                self.operation = {"running": False, "message": "优化已启用", "error": None}
        except Exception as error:
            with self.job_lock:
                self.operation = {"running": False, "message": "未接管系统代理", "error": str(error)[:250]}
            self.telemetry.event("warning", "enable_failed", "未完成接管；请查看操作结果与连接诊断。")

    def disable(self):
        self.enable_cancel.set()
        with self.mutation_lock:
            restored = self.system.restore()  # Restore first; never leave Windows pointing at a stopped listener.
            self.diagnostics.stop.set()
            self.measure.cancel()
            self.relay.stop()
            self.telemetry.event("success", "system_proxy_restored", "已恢复本工具接管前的代理；用户后来修改的设置予以保留。")
            return restored

    def configure(self, value):
        with self.mutation_lock:
            if self.shutdown_event.is_set():
                raise RuntimeError("应用正在退出，不能修改策略。")
            with self.job_lock:
                if self.operation["running"]:
                    raise RuntimeError("连接验证进行中，请稍后再修改策略。")
            if self.system.current()["owned"]:
                raise RuntimeError("请先关闭系统代理接管，再修改连接策略。")
            if (self.measure.snapshot() or {}).get('running'):
                raise RuntimeError("请先取消路径评估，再修改上游配置。")
            result = self.store.save(value)
            self.measure.invalidate()
            self.telemetry.event("info", "configuration_saved", "连接策略已保存；下一条新连接使用新策略。")
            return result

    def shutdown(self):
        self.shutdown_event.set()
        self.quota.source.shutdown()
        self.quota.shutdown()
        return self.disable()
