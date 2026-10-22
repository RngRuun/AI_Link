"""Native controller: background service events enter the Tk thread through a queue."""
from __future__ import annotations
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from .desktop_view import DesktopView, GREEN, MUTED
OUTCOMES = {"reachable": "HTTP 可达", "restricted": "访问受限", "method_unsupported": "方法不支持", "failed": "连接失败", "tls_error": "证书失败", "interrupted": "中途断开", "service_error": "服务错误", "closed": "服务关连接", "cancelled": "已取消"}
ROUTES = {"upstream": "原代理", "relay": "优化中继", "direct": "未指定应用代理"}

class DesktopUI(DesktopView):
    def __init__(self, root, service, on_closed):
        self.root, self.service, self.on_closed = root, service, on_closed
        self.state = None
        self.busy = False
        self.closing = False
        self.last_error = None
        self.stop = threading.Event()
        self.messages = queue.Queue()
        self.setup_view(root)
        self.hydrate(service.store.value)
        quota_config=service.quota.snapshot()["config"]
        self.quota_mode.set(quota_config["mode"])
        self.quota_threshold.set(str(quota_config["thresholds"][quota_config["mode"]]))
        self.quota_bucket.set(quota_config["bucket"])
        self.quota_auto.set(quota_config["auto_resume"])
        self.show("overview")
        threading.Thread(target=self.poll, name="desktop-status", daemon=True).start()
        self.root.after(100, self.drain)

    def hydrate(self, config):
        self.fields["primary"].set(str(config["upstreams"][0]["port"]))
        self.fields["backup"].set(str(config["upstreams"][1]["port"]) if len(config["upstreams"]) > 1 else "")
        for key in ("attempts", "connect_timeout", "retry_budget", "keepalive_idle", "keepalive_interval"):
            self.fields[key].set(str(config[key]))
        self.terminal.set(config["terminal_environment"])

    def poll(self):
        while not self.stop.is_set():
            try:
                self.messages.put(("state", self.service.status()))
            except Exception as error:
                self.messages.put(("poll_error", str(error)))
            self.stop.wait(1)

    def drain(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "state":
                    self.state = value
                    self.render(value)
                elif kind == "done":
                    self.busy = False
                    self.message.config(text=value, fg=GREEN)
                elif kind in ("error", "poll_error"):
                    if kind == "error":
                        self.busy = False
                    self.message.config(text=value, fg="#a34e3d")
                    if self.closing and kind == "error":
                        self.closing = False
                        self.stop.clear()
                        threading.Thread(target=self.poll, daemon=True).start()
                elif kind == "closed":
                    self.on_closed()
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        except Exception as error:
            self.callback_error(type(error), error, error.__traceback__)
        self.root.after(100, self.drain)

    def work(self, action, success):
        if self.busy or self.closing:
            return
        self.busy = True
        self.message.config(text="正在处理，请稍候…", fg=MUTED)
        if self.state:
            self.render(self.state)
        def run():
            try:
                if self.closing:
                    raise RuntimeError("应用正在退出，操作已取消。")
                action()
                self.messages.put(("done", success))
            except Exception as error:
                self.messages.put(("error", str(error)))
        threading.Thread(target=run, daemon=True).start()

    def toggle(self):
        if not self.state:
            return
        if self.state["system"]["owned"]:
            self.work(self.service.disable, "优化已关闭，原系统代理已恢复。")
        else:
            self.work(self.service.enable, "正在验证连接，通过后自动接管系统代理。")

    def save_config(self):
        try:
            config = {key: int(self.fields[key].get()) for key in ("attempts", "connect_timeout", "retry_budget", "keepalive_idle", "keepalive_interval")}
            config["upstreams"] = [{"host": "127.0.0.1", "port": int(self.fields["primary"].get())}]
            if self.fields["backup"].get().strip():
                config["upstreams"].append({"host": "127.0.0.1", "port": int(self.fields["backup"].get())})
            config["terminal_environment"] = self.terminal.get()
        except ValueError:
            self.message.config(text="请在端口和策略参数中输入整数。", fg="#a34e3d")
            return
        self.work(lambda: self.service.configure(config), "传输策略已保存。")

    def quick(self):
        self.show("diagnostics")
        self.run_diagnostics(quick=True)

    def run_diagnostics(self, quick=False):
        providers = [key for key, var in self.provider_vars.items() if var.get()]
        duration = {"快速检测": 0, "30 秒持续检测": 30, "60 秒持续检测": 60}[self.duration.get()]
        if quick:
            providers, duration = ["codex", "gemini"], 0
        def run():
            with self.service.mutation_lock:
                if self.service.shutdown_event.is_set():
                    raise RuntimeError("应用正在退出，检测已取消。")
                self.service.relay.start()
            self.service.diagnostics.start(providers, ["upstream", "relay"], duration)
        self.work(run, "连接诊断已开始。")

    def replace_rows(self, tree, rows):
        signature = tuple(tuple(str(v) for v in row) for row in rows)
        if getattr(tree, "_signature", None) == signature:
            return
        selection = tree.selection()
        vertical, horizontal = tree.yview(), tree.xview()
        tree._signature = signature
        tree.delete(*tree.get_children())
        for index, row in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=row)
        valid = [item for item in selection if tree.exists(item)]
        if valid:
            tree.selection_set(valid)
        if vertical:
            tree.yview_moveto(vertical[0])
        if horizontal:
            tree.xview_moveto(horizontal[0])

    def render(self, state):
        self.render_quota(state.get("quota"))
        owned = state["system"]["owned"]
        active = owned and state["system"]["enabled"] and state["system"]["server"] == "127.0.0.1:18790"
        pending = state["operation"]["running"]
        self.optimization_state.config(text="网络优化已开启" if active else "正在验证连接…" if pending else "优化待开启")
        self.toggle_button.config(text="关闭并恢复原代理" if owned else "开启网络优化",
                                  state="disabled" if self.busy or pending or self.closing else "normal")
        proxy = state["system"]["server"] if state["system"]["enabled"] else "未开启系统代理"
        upstream = state["config"]["upstreams"][0]["port"]
        self.path_label.config(text=f"当前系统代理：{proxy}    →    本地上游：127.0.0.1:{upstream}")
        counters = state["telemetry"]["counters"]
        for key in ("active", "connected", "retries"):
            count=counters[key]
            self.metric_values[key].config(text=f"{count/1000000:.1f}M" if count>=1000000 else f"{count/1000:.1f}k" if count>=10000 else str(count))
        transferred = counters["uploaded"] + counters["downloaded"]
        self.metric_values["bytes"].config(text=f"{transferred/1073741824:.1f} GB" if transferred>=1073741824 else f"{transferred/1048576:.1f} MB" if transferred >= 1048576 else f"{transferred/1024:.1f} KB")
        rows = []
        for key, provider in state["providers"].items():
            last = next((sample for sample in reversed(state["telemetry"]["history"]) if sample["provider"] == key), None)
            rows.append((provider["name"], OUTCOMES.get(last["outcome"], last["outcome"]) if last else "待检测",
                         f"{last['latency_ms']} ms" if last and last.get("latency_ms") is not None else "—", ROUTES.get(last["route"], "—") if last else "—"))
        self.replace_rows(self.overview_tree, rows)
        self.save_button.config(state="disabled" if owned or self.busy or pending or self.closing else "normal")
        self.export_button.configure(state="disabled" if self.closing else "normal")
        locked = owned or self.busy or pending or self.closing
        for entry in self.field_entries:
            entry.configure(state="disabled" if locked else "normal")
        self.terminal_check.configure(state="disabled" if locked else "normal")
        job = state["diagnostics"]
        running = bool(job and job["running"])
        self.run_button.config(state="disabled" if running or self.busy or self.closing else "normal")
        self.quick_button.configure(state="disabled" if running or self.busy or self.closing else "normal")
        self.duration_picker.configure(state="disabled" if running or self.busy or self.closing else "readonly")
        for checkbox in self.provider_checks:
            checkbox.configure(state="disabled" if running or self.busy or self.closing else "normal")
        self.cancel_button.config(state="normal" if running and not self.closing else "disabled")
        if job:
            self.progress_label.config(text=f"{'检测进行中' if running else '检测已结束'} · {len(job['results'])} / {job['total']} 项结果")
            rows = [(state["providers"].get(r["provider"], {}).get("name", r["provider"]), ROUTES.get(r["route"], r["route"]),
                     OUTCOMES.get(r["outcome"], r["outcome"]), r.get("status") or "—",
                     str(r.get("latency_ms") or "—") + " ms", str(r.get("held_seconds", 0)) + " s") for r in job["results"]]
            self.replace_rows(self.results, rows)
        events = [(time.strftime("%H:%M:%S", time.localtime(e["time"])), e["level"], e["message"]) for e in state["telemetry"]["events"]]
        self.replace_rows(self.events, events)
        kernel = state["kernel"]
        if kernel.get("available"):
            self.kernel_status.config(text=f"应用状态：{'正在运行，配置写入已锁定' if kernel['running'] else '已退出，可安全保存'}\n\n保活间隔：{kernel['keepalive']} 秒    切换策略时主动断开：{'开启' if kernel['close_connections'] else '关闭'}")
        else:
            self.kernel_status.config(text="未识别到兼容的 FlClash 配置。")
        writable = kernel.get("available") and not kernel.get("running") and not self.busy and not self.closing
        self.kernel_apply.config(state="normal" if writable else "disabled")
        self.kernel_restore.config(state="normal" if writable else "disabled")
        error = state["operation"].get("error")
        if error and error != self.last_error:
            self.message.config(text=error, fg="#a34e3d")
        self.last_error = error
        if not self.closing:
            self.statusbar.config(text=f"{'优化已开启' if active else '本机运行'}  ·  最小化继续运行，关闭窗口恢复原代理。  ·  运行 {state['telemetry']['uptime']//60} 分钟")

    def result_detail(self, _=None):
        selection = self.results.selection()
        if selection and self.state and self.state["diagnostics"]:
            index = int(selection[0])
            results = self.state["diagnostics"]["results"]
            if index < len(results):
                self.result_note.config(text=results[index]["note"])

    def export(self):
        if not self.state:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="导出诊断报告", defaultextension=".json",
                                          initialfile="AI-Link-诊断报告.json", filetypes=[("JSON 报告", "*.json")])
        if path:
            from pathlib import Path
            try:
                report = {key: self.state[key] for key in ("version", "config", "telemetry", "diagnostics", "kernel")}
                Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.message.config(text="诊断报告已导出，不含对话、账号或代理恢复备份。", fg=GREEN)
            except OSError as error:
                self.message.config(text=str(error), fg="#a34e3d")

    def callback_error(self, exc_type, error, traceback):
        self.service.telemetry.event("error", "ui_callback_failed", "界面状态更新失败；交互仍可继续。")
        self.message.config(text="界面操作失败：" + str(error)[:200], fg="#a34e3d")

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.stop.set()
        self.statusbar.config(text="正在恢复原代理并退出，请稍候…")
        self.toggle_button.config(state="disabled")
        def finish():
            try:
                self.service.shutdown()
                self.messages.put(("closed", None))
            except Exception as error:
                self.messages.put(("error", "恢复失败，应用保持运行：" + str(error)))
        threading.Thread(target=finish, daemon=True).start()
