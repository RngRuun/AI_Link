"""Native quota workspace; violet distinguishes it from green network controls."""
import time
import tkinter as tk
from tkinter import ttk, filedialog

PURPLE = "#6b4dcc"


class QuotaView:
    def build_quota(self):
        page = self.pages["quota"]
        hero = tk.Frame(page, bg="#eee9fc", padx=self.s(22), pady=self.s(20))
        hero.pack(fill="x")
        self.label(hero, "CODEX  /  额度守护", 10, PURPLE, True).pack(anchor="w")
        self.label(hero, "让任务随额度恢复继续。", 21, "#30244d", True,
                   wraplength=self.s(700), anchor="w", justify="left").pack(fill="x", pady=self.s(10))
        self.note(hero, "低于阈值暂停 · 充满后可选续聊 · 每个对话独立授权").pack(fill="x")
        self.quota_status = self.note(hero, "尚未连接 Codex。登录信息由本机 Codex 管理。")
        self.quota_status.pack(fill="x", pady=(self.s(12), 0))
        connection = self.card(page)
        connection.pack(fill="x", pady=self.s(12))
        self.connection_mode = tk.StringVar(value="AI Link 托管")
        self.connection_picker = ttk.Combobox(connection, textvariable=self.connection_mode,
                    values=["AI Link 托管", "共享 Codex 进程"], state="readonly")
        self.connection_picker.pack(fill="x")
        row = tk.Frame(connection, bg="white")
        row.pack(fill="x", pady=(self.s(10), 0))
        self.connect_button = self.quota_button(row, "连接 Codex", self.quota_connect)
        self.connect_button.pack(side="left")
        self.quota_refresh_button = self.quota_button(row, "刷新额度", lambda: self.work(self.service.quota.refresh, "额度已刷新。"))
        self.quota_refresh_button.pack(side="left", padx=self.s(8))
        self.quota_disconnect_button = self.button(row, "断开", lambda: self.work(self.service.quota.close, "Codex 连接已关闭，守护已停止。"))
        self.quota_disconnect_button.pack(side="left")
        self.quota_account = self.note(connection, "账户计划 —  ·  最近更新 —")
        self.quota_account.pack(fill="x", pady=(self.s(12), 0))
        self.quota_metrics = tk.Frame(page, bg=page.cget("bg"))
        self.quota_metrics.pack(fill="x")
        self.quota_gauges = []
        for name in ("短期窗口", "长期窗口"):
            card = self.card(self.quota_metrics)
            self.label(card, name, 11, PURPLE, True).pack(anchor="w")
            value = self.label(card, "—", 30, "#30244d", True)
            value.pack(anchor="w", pady=self.s(5))
            bar = tk.Canvas(card, height=self.s(7), bg="#eee9fc", highlightthickness=0)
            bar.pack(fill="x")
            fill = bar.create_rectangle(0, 0, 0, self.s(7), fill=PURPLE, outline="")
            caption = self.note(card, "等待服务端数据")
            caption.pack(fill="x", pady=(self.s(10), 0))
            self.quota_gauges.append(dict(card=card, value=value, bar=bar, fill=fill, caption=caption, current=0, target=0))
            bar.bind("<Configure>", lambda _, g=self.quota_gauges[-1]: self.draw_gauge(g))
        settings = self.card(page)
        settings.pack(fill="x", pady=self.s(12))
        self.label(settings, "守护策略", 15, "#30244d", True).pack(anchor="w")
        self.note(settings, "任一窗口的剩余额度 ≤ 阈值时暂停。自动续聊要求已知窗口全部回到 100%。").pack(fill="x", pady=self.s(8))
        self.quota_mode = tk.StringVar(value="plus")
        self.quota_threshold = tk.StringVar(value="15")
        self.quota_long_threshold = tk.StringVar(value="15")
        self.quota_bucket = tk.StringVar(value="codex")
        self.quota_auto = tk.BooleanVar(value=False)
        self.quota_controls = []
        for title, variable, values in [("计划模式", self.quota_mode, ["plus", "pro"]), ("额度池", self.quota_bucket, ["codex"])]:
            self.label(settings, title, 10).pack(anchor="w", pady=(self.s(8),self.s(4)))
            widget = ttk.Combobox(settings, textvariable=variable, values=values, state="readonly")
            widget.pack(fill="x")
            self.quota_controls.append(widget)
        self.quota_controls[0].bind("<<ComboboxSelected>>", self.quota_mode_changed)
        self.label(settings, "短期阈值 · 剩余百分比（1–95）", 10).pack(anchor="w", pady=(self.s(8),self.s(4)))
        self.quota_threshold_entry = ttk.Entry(settings, textvariable=self.quota_threshold)
        self.quota_threshold_entry.pack(fill="x")
        self.label(settings,"长期阈值 · 剩余百分比（1–95）",10).pack(anchor="w",pady=(self.s(8),self.s(4)))
        self.quota_long_threshold_entry=ttk.Entry(settings,textvariable=self.quota_long_threshold)
        self.quota_long_threshold_entry.pack(fill="x")
        self.quota_auto_check = tk.Checkbutton(settings, text="额度充满后自动继续已暂停的对话", variable=self.quota_auto,
                     bg="white", fg=PURPLE, activebackground="white", anchor="w", wraplength=self.s(350))
        self.quota_auto_check.pack(fill="x", pady=self.s(10))
        actions = tk.Frame(settings,bg="white")
        actions.pack(fill="x")
        self.quota_save_button = self.quota_button(actions, "保存策略", self.quota_save)
        self.quota_save_button.pack(side="left")
        self.quota_arm_button = self.quota_button(actions, "开启守护", self.quota_toggle)
        self.quota_arm_button.pack(side="left", padx=self.s(8))
        self.note(settings, "Plus / Pro 分别保存本地保留阈值，不能改变账户套餐。阈值按服务端更新检测，无法保证精确到 token 停止。").pack(fill="x", pady=(self.s(10),0))
        self.label(page,"受保护对话",15,"#30244d",True).pack(anchor="w", pady=(self.s(6),self.s(8)))
        self.quota_tree = self.tree(page,["selected","name","status","id"],["守护","对话","状态","对话 ID"],[60,220,140,270],5)
        self.quota_tree.bind("<<TreeviewSelect>>", self.quota_thread_detail)
        self.quota_select_button = self.quota_button(page, "切换所选对话的守护", self.quota_select)
        self.quota_select_button.pack(anchor="w", pady=self.s(8))
        self.note(page,"共享模式只列出连接进程已加载的对话；托管模式只控制本应用创建的对话。关闭守护或取消勾选后，不会再自动续聊。重启应用后需手动重新开启守护。").pack(fill="x")
        task = self.card(page)
        task.pack(fill="x",pady=self.s(12))
        self.label(task,"托管对话 · 只读工作区",14,PURPLE,True).pack(anchor="w")
        self.note(task,"可运行只读分析任务。需要审批的操作会被拒绝，请在完整 Codex 客户端处理。关闭本应用会结束托管连接。只有共享模式可以守护对应进程中的现有对话。").pack(fill="x",pady=self.s(8))
        self.quota_cwd=tk.StringVar(value=str(self.service.directory))
        self.quota_cwd_entry=ttk.Entry(task,textvariable=self.quota_cwd)
        self.quota_cwd_entry.pack(fill="x")
        self.quota_browse_button=self.button(task,"选择工作目录",self.quota_choose_directory)
        self.quota_browse_button.pack(anchor="w",pady=self.s(6))
        self.quota_prompt=tk.Text(task,height=4,wrap="word",relief="flat",bg="#f6f3fc",fg="#30244d",padx=self.s(10),pady=self.s(10))
        self.quota_prompt.pack(fill="x")
        self.quota_start_button=self.quota_button(task,"发送并创建受保护对话",self.quota_start)
        self.quota_start_button.pack(anchor="w",pady=self.s(10))
        self.quota_detail=self.note(task,"选中对话后显示状态。")
        self.quota_detail.pack(fill="x")

    def quota_button(self,parent,text,command):
        button=self.button(parent,text,command,True)
        button.configure(bg=PURPLE,activebackground="#5136a4")
        return button

    def draw_gauge(self,gauge):
        gauge["bar"].coords(gauge["fill"],0,0,gauge["bar"].winfo_width()*gauge["current"]/100,self.s(7))

    def animate_gauges(self):
        moving=False
        for g in self.quota_gauges:
            delta=g["target"]-g["current"]
            g["current"]=g["target"] if abs(delta)<0.2 or self.reduce_motion.get() else g["current"]+delta*0.18
            moving |= abs(delta)>=0.2 and not self.reduce_motion.get()
            self.draw_gauge(g)
        self.gauge_timer=self.root.after(16,self.animate_gauges) if moving else None

    def quota_connect(self):
        shared=self.connection_mode.get()=="共享 Codex 进程"
        self.work(lambda:self.service.quota.connect(shared),"Codex 已连接。请检查计划和策略，再开启守护。")

    def quota_mode_changed(self,_=None):
        config=self.state["quota"]["config"] if self.state else {"thresholds":{"plus":{"primary":15,"secondary":15},"pro":{"primary":10,"secondary":10}}}
        self.quota_threshold.set(str(config["thresholds"][self.quota_mode.get()]["primary"]))
        self.quota_long_threshold.set(str(config["thresholds"][self.quota_mode.get()]["secondary"]))

    def quota_save(self):
        try:
            threshold={"primary":int(self.quota_threshold.get()),"secondary":int(self.quota_long_threshold.get())}
        except ValueError:
            self.message.configure(text="暂停阈值请输入 1–95 的整数。",fg="#a34e3d")
            return
        mode,auto,bucket=self.quota_mode.get(),self.quota_auto.get(),self.quota_bucket.get()
        self.work(lambda:self.service.quota.configure(mode,threshold,auto,bucket),"额度守护策略已保存。")

    def quota_toggle(self):
        if not self.state:return
        enabled=not self.state["quota"]["enabled"]
        self.work(lambda:self.service.quota.arm(enabled),"额度守护已开启。" if enabled else "额度守护已关闭。")

    def quota_choose_directory(self):
        path=filedialog.askdirectory(parent=self.root,title="选择托管任务的只读工作目录")
        if path:self.quota_cwd.set(path)

    def quota_select(self):
        selection=self.quota_tree.selection()
        if not selection:return
        if not self.state:return
        choices=self.state["quota"]
        index=int(selection[0])
        if index>=len(choices["choices"]):return
        thread_id=choices["choices"][index]["id"]
        selected=choices["config"]["threads"].get(thread_id,{}).get("selected",False)
        self.work(lambda:self.service.quota.select(thread_id,not selected),"对话守护范围已更新。")

    def quota_start(self):
        prompt,cwd=self.quota_prompt.get("1.0","end-1c"),self.quota_cwd.get()
        self.work(lambda:self.service.quota.start_chat(prompt,cwd),"受保护对话已创建。")

    def quota_thread_detail(self,_=None):
        if not self.state:return
        selection=self.quota_tree.selection()
        if not selection:return
        q=self.state.get("quota",{})
        index=int(selection[0])
        if index<len(q.get("choices",[])):
            t=q["choices"][index]
            self.quota_detail.configure(text=t["id"]+"\n"+q.get("outputs",{}).get(t["id"],"等待下一次状态刷新。"))

    def render_quota(self,q):
        if not q:return
        connected,enabled=q["connected"],q["enabled"]
        locked=enabled or self.busy or self.closing
        self.quota_status.configure(text=q.get("error") or q["message"],fg="#a34e3d" if q.get("error") else PURPLE)
        stale=not q["updated"] or time.time()-q["updated"]>65 or q.get("error")
        updated=time.strftime("%H:%M:%S",time.localtime(q["updated"])) if q["updated"] else "—"
        self.quota_account.configure(text=f"账户计划：{q.get('plan') or '未知'}  ·  最近更新：{updated}"+("  ·  数据不可用于自动续聊" if stale else "  ·  每 30 秒刷新，额度事件触发更新"))
        for index,g in enumerate(self.quota_gauges):
            w=next((w for w in q["windows"] if w["key"]==("primary" if index==0 else "secondary")),None)
            g["value"].configure(text=f"{w['remaining']:g}%" if w else "—",fg="#8c829e" if stale else "#30244d")
            g["target"]=w["remaining"] if w else 0
            reset=time.strftime("%m-%d %H:%M",time.localtime(w["reset"])) if w and isinstance(w.get("reset"),(float,int)) else "未知"
            g["caption"].configure(text=f"{w['minutes']} 分钟窗口 · 重置 {reset}" if w else "服务端未提供此窗口")
        if not getattr(self,"gauge_timer",None):self.animate_gauges()
        self.connect_button.configure(state="disabled" if connected or self.busy or self.closing else "normal")
        self.connection_picker.configure(state="disabled" if connected or self.busy or self.closing else "readonly")
        for b in (self.quota_refresh_button,self.quota_disconnect_button,self.quota_select_button):
            b.configure(state="normal" if connected and not self.busy and not self.closing else "disabled")
        for combo in self.quota_controls:combo.configure(state="disabled" if locked else "readonly")
        self.quota_controls[1].configure(values=q["buckets"])
        for w in (self.quota_threshold_entry,self.quota_long_threshold_entry,self.quota_auto_check,self.quota_save_button):w.configure(state="disabled" if locked else "normal")
        config=q["config"]
        dirty=(self.quota_mode.get()!=config["mode"] or self.quota_bucket.get()!=config["bucket"] or
               self.quota_auto.get()!=config["auto_resume"] or self.quota_threshold.get()!=str(config["thresholds"][self.quota_mode.get()]["primary"]) or self.quota_long_threshold.get()!=str(config["thresholds"][self.quota_mode.get()]["secondary"]))
        self.quota_arm_button.configure(text="关闭守护" if enabled else "先保存策略" if dirty else "开启守护",
                  state="normal" if connected and (enabled or not dirty) and not self.busy and not self.closing else "disabled")
        self.quota_start_button.configure(state="normal" if connected and enabled and self.connection_mode.get()=="AI Link 托管" and not self.busy and not self.closing else "disabled")
        statuses={"watching":"监测中","interrupt_pending":"等待中断确认","paused":"已暂停，等待充满","resume_pending":"续聊结果待核实"}
        records=q["config"]["threads"]
        rows=[("✓" if records.get(t["id"],{}).get("selected") else "—",t["name"][:90],statuses.get(records.get(t["id"],{}).get("state"),"未选择"),t["id"]) for t in q["choices"]]
        self.replace_rows(self.quota_tree,rows)
        self.quota_thread_detail()
