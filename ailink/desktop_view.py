"""Native responsive widgets; all dimensions below are logical 96-DPI pixels."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .layout import layout_for
from .quota_view import QuotaView, PURPLE

BG, INK, MUTED, GREEN, WHITE, BORDER = "#f4f7f5", "#18392c", "#526f60", "#1d7059", "#ffffff", "#dbe6df"
PAGES = {"overview": "连接概览", "quota": "额度守护", "strategy": "传输策略", "diagnostics": "连接诊断", "events": "运行记录", "kernel": "代理内核"}


class ScrollPage(tk.Frame):
    def __init__(self, parent, scale):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set, yscrollincrement=max(12, round(24 * scale)))
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.item = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", self.resize)
        self.content.bind("<Configure>", self.sync)

    def resize(self, event):
        self.canvas.itemconfigure(self.item, width=max(1, event.width))
        self.sync()

    def sync(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def reveal(self, widget):
        try:
            top = widget.winfo_rooty() - self.content.winfo_rooty()
            bottom = top + widget.winfo_height()
            visible_top = self.canvas.canvasy(0)
            visible_bottom = visible_top + self.canvas.winfo_height()
            total = max(1, self.content.winfo_height())
            if top < visible_top:
                self.canvas.yview_moveto(max(0, top - 12) / total)
            elif bottom > visible_bottom:
                self.canvas.yview_moveto(max(0, bottom - self.canvas.winfo_height() + 20) / total)
        except tk.TclError:
            pass


class DesktopView(QuotaView):
    def setup_view(self, root):
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.pages, self.scroll_pages, self.navigation = {}, {}, {}
        self.active_page = "overview"
        self.layout_signature = None
        self.resize_timer = None
        self.theme_timer = None
        self.theme_color = GREEN
        self.reduce_motion = tk.BooleanVar(value=False)
        root.title("AI Link · AI 用户体验优化")
        root.configure(bg=BG)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.callback_error
        root.minsize(self.s(560), self.s(440))
        self.window_size(1120, 780)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TEntry", fieldbackground=WHITE, padding=self.s(7), bordercolor=BORDER)
        style.configure("TCombobox", fieldbackground=WHITE, padding=self.s(6))
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK,
                        rowheight=self.s(34), borderwidth=0, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", background="#ecf3ee", foreground=MUTED,
                        font=("Microsoft YaHei UI", 9), padding=self.s(7))
        style.map("Treeview", background=[("selected", "#d7eadf")], foreground=[("selected", INK)])
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(root, bg=WHITE, width=self.s(190), highlightbackground=BORDER, highlightthickness=1)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(8, weight=1)
        self.brand = self.label(self.sidebar, "↗  AI Link", 23, GREEN, True)
        self.brand.grid(row=0, column=0, sticky="w", padx=self.s(20), pady=(self.s(25), self.s(6)))
        self.tagline = self.label(self.sidebar, "AI 用户体验优化", 10, MUTED)
        self.tagline.grid(row=1, column=0, sticky="w", padx=self.s(22), pady=(0, self.s(30)))
        for index, (key, name) in enumerate(PAGES.items()):
            button = self.button(self.sidebar, name, lambda k=key: self.show(k))
            button.configure(anchor="w")
            button.grid(row=index+2, column=0, sticky="ew", padx=self.s(12), pady=self.s(3))
            self.navigation[key] = button
        self.sidebar_footer = self.label(self.sidebar, "WINDOWS DESKTOP  ·  v0.4", 8, MUTED)
        self.sidebar_footer.grid(row=9, column=0, pady=self.s(22))
        self.main = tk.Frame(root, bg=BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(2, weight=1)
        self.top = tk.Frame(self.main, bg=BG)
        self.top.grid(row=0, column=0, sticky="ew", padx=self.s(24), pady=(self.s(22), self.s(12)))
        self.top.columnconfigure(0, weight=1)
        self.page_title = self.label(self.top, "连接概览", 22, INK, True)
        self.page_title.grid(row=0, column=0, sticky="w")
        self.view_menu = tk.Menubutton(self.top, text="视图 ▾", bg=WHITE, fg=GREEN, relief="flat", padx=self.s(12), pady=self.s(9))
        menu = tk.Menu(self.view_menu, tearoff=False)
        for name, width, height in [("最小窗口 560 × 440", 560, 440), ("紧凑窗口 640 × 520", 640, 520),
                                    ("标准窗口 1120 × 780", 1120, 780), ("宽屏窗口 1440 × 900", 1440, 900)]:
            menu.add_command(label=name, command=lambda w=width, h=height: self.window_size(w, h))
        menu.add_separator()
        menu.add_command(label="最大化", command=lambda: self.root.state("zoomed"))
        menu.add_checkbutton(label="减少动画", variable=self.reduce_motion)
        self.view_menu.configure(menu=menu)
        self.view_menu.grid(row=0, column=1, padx=self.s(8))
        self.export_button = self.button(self.top, "导出报告", self.export)
        self.export_button.grid(row=0, column=2)
        self.page_choice = tk.StringVar(value=PAGES["overview"])
        self.page_picker = ttk.Combobox(self.top, textvariable=self.page_choice, values=list(PAGES.values()), state="readonly", width=18)
        self.page_picker.bind("<<ComboboxSelected>>", lambda _: self.show(next(k for k,v in PAGES.items() if v == self.page_choice.get())))
        self.message = self.label(self.main, "本机就绪。先检查上游代理，再开启优化。", 10, MUTED, anchor="w", justify="left", wraplength=self.s(700))
        self.message.grid(row=1, column=0, sticky="ew", padx=self.s(24), pady=(0, self.s(12)))
        self.container = tk.Frame(self.main, bg=BG)
        self.container.grid(row=2, column=0, sticky="nsew", padx=self.s(24))
        self.container.rowconfigure(0, weight=1)
        self.container.columnconfigure(0, weight=1)
        for key in PAGES:
            scroll = ScrollPage(self.container, self.scale)
            self.scroll_pages[key] = scroll
            self.pages[key] = scroll.content
        self.build_overview()
        self.build_quota()
        self.build_strategy()
        self.build_diagnostics()
        self.build_events()
        self.build_kernel()
        self.statusbar = self.label(self.main, "关闭窗口恢复原代理；最小化继续运行。", 9, MUTED, anchor="w", justify="left", wraplength=self.s(700))
        self.statusbar.grid(row=3, column=0, sticky="ew", padx=self.s(24), pady=self.s(12))
        root.bind("<Configure>", self.schedule_layout, add="+")
        root.bind("<MouseWheel>", self.wheel, add="+")
        root.bind("<FocusIn>", self.focus_visible, add="+")
        root.after_idle(self.reflow)

    def s(self, value):
        return max(0, round(value * self.scale))

    def window_size(self, width, height):
        if self.root.state() == "zoomed":
            self.root.state("normal")
        width = min(self.s(width), self.root.winfo_screenwidth() - self.s(30))
        height = min(self.s(height), self.root.winfo_screenheight() - self.s(65))
        self.root.geometry(f"{width}x{height}")

    def label(self, parent, text="", size=10, color=INK, bold=False, **kwargs):
        label = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                         font=("Microsoft YaHei UI", size, "bold" if bold else "normal"), **kwargs)
        if "wraplength" in kwargs:
            label.bind("<Configure>", lambda e: label.configure(wraplength=max(self.s(40), e.width-self.s(4)))
                       if abs(int(label.cget("wraplength")) - max(self.s(40), e.width-self.s(4))) > self.s(3) else None)
        return label

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=GREEN if primary else WHITE,
                         fg=WHITE if primary else GREEN, activebackground="#185c49" if primary else "#edf5ef",
                         activeforeground=WHITE if primary else GREEN, relief="flat", borderwidth=0,
                         padx=self.s(15), pady=self.s(10), cursor="hand2", disabledforeground="#8ba093", takefocus=True)

    def card(self, parent, padx=18, pady=16):
        return tk.Frame(parent, bg=WHITE, highlightbackground=BORDER, highlightthickness=1, padx=self.s(padx), pady=self.s(pady))

    def note(self, parent, text):
        return self.label(parent, text, 10, MUTED, justify="left", anchor="w", wraplength=self.s(700))

    def show(self, key):
        if key not in self.pages:
            return
        self.active_page = key
        for page in self.scroll_pages.values():
            page.grid_remove()
        self.scroll_pages[key].grid(row=0, column=0, sticky="nsew")
        self.page_title.configure(text=PAGES[key])
        self.page_choice.set(PAGES[key])
        for name, button in self.navigation.items():
            button.configure(bg=("#eee9fc" if key=="quota" else "#e3f0e8") if name == key else WHITE,
                             fg=PURPLE if name=="quota" else GREEN)
        self.animate_theme(PURPLE if key=="quota" else GREEN)
        self.root.after_idle(self.reflow)

    def animate_theme(self, target):
        if self.theme_timer:
            self.root.after_cancel(self.theme_timer)
        start=self.theme_color
        def frame(step=0):
            t=1 if self.reduce_motion.get() else min(1,step/16)
            ease=1-(1-t)**3
            color="#"+"".join(f"{round(int(start[i:i+2],16)*(1-ease)+int(target[i:i+2],16)*ease):02x}" for i in (1,3,5))
            self.theme_color=color
            self.page_title.configure(fg=color)
            self.view_menu.configure(fg=color)
            self.brand.configure(fg=color)
            self.theme_timer=self.root.after(16,lambda:frame(step+1)) if t<1 else None
        frame()

    def schedule_layout(self, event):
        if event.widget is self.root:
            if self.resize_timer:
                self.root.after_cancel(self.resize_timer)
            self.resize_timer = self.root.after(60, self.reflow)

    def reflow(self):
        self.resize_timer = None
        policy = layout_for(self.root.winfo_width() / self.scale)
        viewport_height = self.container.winfo_height()
        if self.active_page == "events":
            self.events.configure(height=max(8, min(30, int((viewport_height-self.s(65))/max(1,self.s(34))))))
        elif self.active_page == "diagnostics":
            self.results.configure(height=max(6, min(20, int((viewport_height-self.diagnostic_card.winfo_reqheight()-self.s(100))/max(1,self.s(34))))))
        signature = (policy.navigation, policy.metric_columns, policy.form_columns, policy.provider_columns)
        if signature == self.layout_signature:
            return
        self.layout_signature = signature
        self.layout_policy = policy
        if policy.navigation == "top":
            self.sidebar.grid_remove()
            self.page_picker.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(self.s(10),0))
        else:
            self.sidebar.grid()
            self.sidebar.configure(width=self.s(policy.sidebar))
            self.page_picker.grid_remove()
        compact = policy.navigation == "compact"
        self.brand.configure(text="↗" if compact else "↗  AI Link")
        self.brand.grid_configure(padx=self.s(20 if not compact else 25))
        self.tagline.configure(text="AI 优化" if compact else "AI 用户体验优化")
        self.tagline.grid_configure(padx=self.s(12 if compact else 22))
        self.sidebar_footer.configure(text="v0.4" if compact else "WINDOWS DESKTOP  ·  v0.4")
        for key, button in self.navigation.items():
            button.configure(text=PAGES[key][:2] if compact else PAGES[key], padx=self.s(10 if compact else 15))
        for widget in (self.top, self.message, self.container, self.statusbar):
            widget.grid_configure(padx=self.s(policy.padding))
        self.export_button.configure(text="导出" if policy.navigation == "top" else "导出报告")
        for index, card in enumerate(self.metric_cards):
            card.grid(row=index//policy.metric_columns, column=index%policy.metric_columns, sticky="nsew",
                      padx=(0,self.s(8)), pady=(0,self.s(8)))
        for column in range(4):
            self.metrics.columnconfigure(column, weight=1 if column<policy.metric_columns else 0, uniform="metrics" if column<policy.metric_columns else "")
        for index, field in enumerate(self.field_frames):
            field.grid(row=index//policy.form_columns, column=index%policy.form_columns, sticky="ew", padx=(0,self.s(14)), pady=(0,self.s(10)))
        for column in range(2):
            self.form_grid.columnconfigure(column, weight=1 if column<policy.form_columns else 0, uniform="form" if column<policy.form_columns else "")
        for index, checkbox in enumerate(self.provider_checks):
            checkbox.grid(row=index//policy.provider_columns, column=index%policy.provider_columns, sticky="w", padx=(0,self.s(20)), pady=self.s(3))
        for index,gauge in enumerate(self.quota_gauges):
            gauge["card"].grid(row=index//policy.provider_columns,column=index%policy.provider_columns,sticky="nsew",padx=(0,self.s(8)),pady=(0,self.s(8)))
        for col in range(2):
            self.quota_metrics.columnconfigure(col,weight=1 if col<policy.provider_columns else 0,uniform="quota" if col<policy.provider_columns else "")
        self.root.after_idle(lambda: self.scroll_pages[self.active_page].sync())

    def wheel(self, event):
        if isinstance(event.widget, (ttk.Treeview, ttk.Combobox)):
            return
        page = self.scroll_pages[self.active_page]
        page.canvas.yview_scroll(-int(event.delta/120) or (-1 if event.delta>0 else 1), "units")
        return "break"

    def focus_visible(self, event):
        if isinstance(event.widget, (ttk.Entry, ttk.Combobox, tk.Button, tk.Checkbutton)):
            page = self.scroll_pages[self.active_page]
            if str(event.widget).startswith(str(page.content)):
                self.root.after_idle(lambda: page.reveal(event.widget))

    def build_overview(self):
        page = self.pages["overview"]
        hero = tk.Frame(page, bg="#e6f2e9", padx=self.s(22), pady=self.s(20))
        hero.pack(fill="x")
        self.optimization_state = self.label(hero, "优化待开启", 11, GREEN, True)
        self.optimization_state.pack(anchor="w")
        self.label(hero, "为你的 AI 应用，统一连接入口。", 23, INK, True, anchor="w", justify="left", wraplength=self.s(700)).pack(fill="x", pady=(self.s(13),self.s(8)))
        self.note(hero, "安全重试建连 · 可选备用代理 · 保留原始 TLS 加密").pack(fill="x")
        actions = tk.Frame(hero, bg=hero.cget("bg"))
        actions.pack(fill="x", pady=(self.s(20),0))
        self.toggle_button = self.button(actions, "开启网络优化", self.toggle, True)
        self.toggle_button.pack(side="left")
        self.quick_button = self.button(actions, "先检测连接", self.quick)
        self.quick_button.pack(side="left", padx=self.s(10))
        self.path_label = self.note(hero, "正在读取连接路径…")
        self.path_label.pack(fill="x", pady=(self.s(15),0))
        self.metrics = tk.Frame(page, bg=BG)
        self.metrics.pack(fill="x", pady=(self.s(16),self.s(6)))
        self.metric_values, self.metric_cards = {}, []
        for key, name in [("active","活动隧道"),("connected","成功建连"),("retries","建连重试"),("bytes","累计传输")]:
            card = self.card(self.metrics, 14, 12)
            self.metric_cards.append(card)
            self.label(card,name,10,MUTED).pack(anchor="w")
            self.metric_values[key] = self.label(card,"0",22,INK,True)
            self.metric_values[key].pack(anchor="w",pady=(self.s(6),0))
        self.label(page,"AI 服务连接",14,INK,True).pack(anchor="w",pady=(0,self.s(10)))
        self.overview_tree=self.tree(page,["service","outcome","latency","route"],["服务","最近结果","首次耗时","检测路径"],[180,150,100,140],4)
        self.note(page,"重试仅发生在隧道建立前。已经断开的加密会话需要 AI 应用自行重连；HTTP 可达不等于模型功能可用。").pack(fill="x",pady=self.s(14))

    def build_strategy(self):
        card=self.card(self.pages["strategy"],20,18)
        card.pack(fill="x")
        self.label(card,"本地上游与建连恢复",15,INK,True).pack(anchor="w",pady=(0,self.s(18)))
        self.form_grid=tk.Frame(card,bg=WHITE)
        self.form_grid.pack(fill="x")
        self.fields,self.field_frames,self.field_entries={},[],[]
        names=[("primary","主代理端口 · 127.0.0.1"),("backup","备用端口（可选）"),("attempts","最多尝试次数 · 1–8"),
               ("connect_timeout","单次超时 / 秒 · 2–15"),("retry_budget","重试预算 / 秒 · 5–90"),
               ("keepalive_idle","空闲保活 / 秒 · 30–300"),("keepalive_interval","保活间隔 / 秒 · 10–60")]
        for key,name in names:
            frame=tk.Frame(self.form_grid,bg=WHITE)
            self.field_frames.append(frame)
            self.label(frame,name,10,MUTED).pack(anchor="w",pady=(0,self.s(5)))
            var=tk.StringVar()
            entry=ttk.Entry(frame,textvariable=var,width=12)
            entry.pack(fill="x")
            self.fields[key]=var
            self.field_entries.append(entry)
        self.terminal=tk.BooleanVar()
        self.terminal_check=tk.Checkbutton(card,text="同时配置新终端的用户代理环境变量",variable=self.terminal,bg=WHITE,fg=INK,activebackground=WHITE,selectcolor=WHITE,anchor="w",justify="left",wraplength=self.s(350))
        self.terminal_check.pack(fill="x",pady=(self.s(12),self.s(5)))
        self.note(card,"关闭优化后恢复环境变量。备用端口需对应真实的 HTTP / mixed 代理。保活仅控制本中继套接字，远端节点连接仍由 VPN 内核负责。").pack(fill="x",pady=self.s(9))
        self.save_button=self.button(card,"保存传输策略",self.save_config,True)
        self.save_button.pack(anchor="w",pady=(self.s(12),0))

    def tree(self,parent,columns,headings,widths,height=6):
        frame=tk.Frame(parent,bg=WHITE)
        frame.pack(fill="x")
        frame.rowconfigure(0,weight=1)
        frame.columnconfigure(0,weight=1)
        tree=ttk.Treeview(frame,columns=columns,show="headings",height=height,selectmode="browse")
        for column,heading,width in zip(columns,headings,widths):
            tree.heading(column,text=heading)
            tree.column(column,width=self.s(width),minwidth=self.s(width),stretch=True)
        vertical=ttk.Scrollbar(frame,orient="vertical",command=tree.yview)
        horizontal=ttk.Scrollbar(frame,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        tree.grid(row=0,column=0,sticky="nsew")
        vertical.grid(row=0,column=1,sticky="ns")
        horizontal.grid(row=1,column=0,sticky="ew")
        tree.bind("<Shift-MouseWheel>",lambda e:(tree.xview_scroll(-int(e.delta/120),"units"),"break")[-1])
        return tree

    def build_diagnostics(self):
        page=self.pages["diagnostics"]
        card=self.card(page,18,16)
        self.diagnostic_card=card
        card.pack(fill="x",pady=(0,self.s(14)))
        self.label(card,"真实连接诊断",15,INK,True).pack(anchor="w")
        services=tk.Frame(card,bg=WHITE)
        services.pack(fill="x",pady=self.s(12))
        self.provider_vars,self.provider_checks={},[]
        from .diagnostics import PROVIDERS
        for key,provider in PROVIDERS.items():
            var=tk.BooleanVar(value=key in ("codex","gemini"))
            checkbox=tk.Checkbutton(services,text=provider["name"],variable=var,bg=WHITE,activebackground=WHITE,fg=INK)
            self.provider_checks.append(checkbox)
            self.provider_vars[key]=var
        self.duration=tk.StringVar(value="快速检测")
        self.duration_picker=ttk.Combobox(card,textvariable=self.duration,state="readonly",width=18,values=["快速检测","30 秒持续检测","60 秒持续检测"])
        self.duration_picker.pack(anchor="w",pady=(0,self.s(10)))
        actions=tk.Frame(card,bg=WHITE)
        actions.pack(fill="x")
        self.run_button=self.button(actions,"运行路径对比",self.run_diagnostics,True)
        self.run_button.pack(side="left")
        self.cancel_button=self.button(actions,"取消检测",lambda:self.service.diagnostics.stop.set())
        self.cancel_button.pack(side="left",padx=self.s(10))
        self.note(card,"对比原上游与中继，不使用账号、Cookie 或 API Key。持续检测重复发送 HEAD，不是模型流式验收。").pack(fill="x",pady=(self.s(12),0))
        self.progress_label=self.label(page,"尚未检测",10,MUTED,anchor="w")
        self.progress_label.pack(fill="x",pady=(0,self.s(8)))
        self.results=self.tree(page,["service","route","outcome","status","latency","held"],["服务","路径","结果","HTTP","首次耗时","保持时长"],[160,100,120,65,100,90],6)
        self.results.bind("<<TreeviewSelect>>",self.result_detail)
        self.result_note=self.note(page,"选择一项结果可查看检测说明；窄窗口可横向滚动表格。")
        self.result_note.pack(fill="x",pady=self.s(12))

    def build_events(self):
        page=self.pages["events"]
        self.note(page,"本次运行最多保留 150 条事件，不记录对话内容。长记录可横向滚动查看。").pack(fill="x",pady=(0,self.s(14)))
        self.events=self.tree(page,["time","level","message"],["时间","级别","事件"],[100,85,650],12)

    def build_kernel(self):
        page=self.pages["kernel"]
        card=self.card(page,20,20)
        card.pack(fill="x")
        self.label(card,"FlClash 内核适配",16,INK,True).pack(anchor="w")
        self.kernel_status=self.note(card,"正在读取…")
        self.kernel_status.pack(fill="x",pady=self.s(18))
        self.note(card,"将 TCP 保活间隔设为 15 秒，并在切换策略时保留已有连接。仅备份这两个字段，不修改节点、订阅、路由或证书校验。\n\n请从托盘彻底退出 FlClash 后操作，保存后重新打开生效。运行时不会改写配置。").pack(fill="x",pady=self.s(6))
        actions=tk.Frame(card,bg=WHITE)
        actions.pack(fill="x",pady=(self.s(18),0))
        self.kernel_apply=self.button(actions,"保存保活优化",lambda:self.work(self.service.kernel.apply,"内核设置已保存，重启 FlClash 后生效。"),True)
        self.kernel_apply.pack(side="left")
        self.kernel_restore=self.button(actions,"恢复内核设置",lambda:self.work(self.service.kernel.restore,"原内核设置已恢复。"))
        self.kernel_restore.pack(side="left",padx=self.s(10))
        self.note(page,"关闭应用会恢复原代理。若进程意外结束，独立守护会尝试恢复；也可运行随附的紧急恢复程序。").pack(fill="x",pady=self.s(20))
