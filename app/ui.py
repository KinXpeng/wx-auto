# -*- coding: utf-8 -*-
"""界面层：主窗口构建、状态徽标、日志输出、选择会话弹窗、诊断。

以 mixin 类形式提供方法，挂载到主 App 类上；这些方法大多在主线程运行，
后台线程只会通过 self.root.after(0, ...) 或 self.log / self.set_status 触达。
"""

import threading
import traceback
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from .theme import (
    C_BG, C_CARD, C_SUBTEXT, C_TEXT, C_GREEN, C_GRAY, C_RED,
    FONT, FONT_BOLD, FONT_TITLE, FONT_TAG, FONT_MONO, configure_style,
)
from .utils import strip_member_count, now_str


class UiMixin:
    """提供界面构建、状态与日志、会话选择、诊断。"""

    # ---- 基础窗口/样式 ----
    def _configure_style(self):
        configure_style(self.root)

    def _build_ui(self):
        root_bg = C_BG
        self.root.configure(bg=root_bg)

        outer = tk.Frame(self.root, bg=root_bg)
        outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        def _row_label(parent, text):
            tk.Label(parent, text=text, bg=root_bg, fg=C_SUBTEXT, font=FONT_TAG).pack(
                anchor="w", pady=(0, 3))
            return parent

        # 第一行：左侧"微信数据目录"标签 + 右侧运行状态徽标（去掉重复的顶部标题）
        db_header = tk.Frame(outer, bg=root_bg)
        db_header.pack(fill=tk.X)
        tk.Label(db_header, text="微信数据目录", bg=root_bg, fg=C_SUBTEXT, font=FONT_TAG).pack(
            side=tk.LEFT)
        status_box = tk.Frame(db_header, bg=root_bg)
        status_box.pack(side=tk.RIGHT)
        self._status_box = status_box
        self._dot_win = tk.Canvas(status_box, width=16, height=16, bg=root_bg, highlightthickness=0)
        self._dot = self._dot_win.create_oval(3, 3, 13, 13, fill=C_GRAY, outline="")
        self._dot_win.pack(side=tk.LEFT, padx=(0, 5))
        self.status_label = tk.Label(
            status_box, textvariable=self.status_var, bg=root_bg, fg=C_GRAY, font=FONT_BOLD
        )
        self.status_label.pack(side=tk.LEFT)

        # 数据目录输入框
        db_row = tk.Frame(outer, bg=root_bg)
        db_row.pack(fill=tk.X, pady=(4, 8))
        ttk.Entry(db_row, textvariable=self.db_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(db_row, text="检测", width=7, command=self.detect_db).pack(
            side=tk.LEFT, padx=(8, 0))

        # 目标会话
        _row_label(outer, "目标会话（群名/好友备注/昵称）")
        tgt_row = tk.Frame(outer, bg=root_bg)
        tgt_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Entry(tgt_row, textvariable=self.target_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(tgt_row, text="选择会话", width=9, command=self.pick_session).pack(
            side=tk.LEFT, padx=(8, 0))

        # 定时自动发送
        _row_label(outer, "定时自动发送")
        row1 = tk.Frame(outer, bg=root_bg)
        row1.pack(fill=tk.X)
        ttk.Checkbutton(row1, text="启用：每隔", variable=self.schedule_enable_var).pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.schedule_minutes_var, width=5).pack(side=tk.LEFT, padx=4)
        tk.Label(row1, text="分钟发一条", bg=root_bg).pack(side=tk.LEFT)
        self.schedule_text = scrolledtext.ScrolledText(
            outer, height=2, wrap=tk.WORD, font=FONT_MONO,
            bg=C_CARD, fg=C_TEXT, insertbackground=C_TEXT, relief="solid", bd=1,
        )
        self.schedule_text.pack(fill=tk.X, pady=(4, 8))
        self.schedule_text.insert(tk.END, "大家好，我还在线。")

        # 关键词自动回复
        _row_label(outer, "关键词自动回复")
        ttk.Checkbutton(
            outer, text="启用：收到包含关键词的消息后自动回复", variable=self.keyword_enable_var
        ).pack(anchor="w")
        kw = tk.Frame(outer, bg=root_bg)
        kw.pack(fill=tk.X, pady=(6, 0))
        tk.Label(kw, text="触发关键词", bg=root_bg, fg=C_SUBTEXT, font=FONT_TAG).pack(
            anchor="w", pady=(0, 2))
        self.keyword_entry = ttk.Entry(kw, textvariable=self.keyword_var)
        self.keyword_entry.pack(fill="x")
        tk.Label(kw, text="自动回复内容", bg=root_bg, fg=C_SUBTEXT, font=FONT_TAG).pack(
            anchor="w", pady=(8, 2))
        self.reply_text = scrolledtext.ScrolledText(
            kw, height=2, wrap=tk.WORD, font=FONT_MONO, bg=C_CARD, fg=C_TEXT,
            bd=1, relief="solid",
        )
        self.reply_text.pack(fill="x")
        self.reply_text.insert(tk.END, "该用户正忙，请稍后再试")
        ttk.Checkbutton(
            kw, text="响应自己发的消息（自测用；回复内容勿包含关键词）",
            variable=self.reply_self_var,
        ).pack(anchor="w", pady=(6, 0))

        # 操作区
        action_box = tk.Frame(outer, bg=root_bg)
        action_box.pack(fill=tk.X, pady=(12, 8))
        ttk.Button(action_box, text="启动", style="Accent.TButton", command=self.start).pack(side=tk.LEFT)
        ttk.Button(action_box, text="停止", command=self.stop).pack(side=tk.LEFT, padx=8)
        ttk.Button(action_box, text="诊断", command=self.diagnose).pack(side=tk.LEFT)

        # 日志
        _row_label(outer, "运行日志")
        self.log_box = scrolledtext.ScrolledText(
            outer, height=7, wrap=tk.WORD, state=tk.DISABLED, font=FONT_MONO,
            bg=C_CARD, fg=C_TEXT, bd=1, relief="solid",
        )
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        tip = ("适配微信 4.x，请保持微信已登录，发送时不要锁屏。"
               "群名请填实际的群名/昵称，不要带末尾的人数括号。")
        tk.Label(outer, text=tip, bg=root_bg, fg=C_SUBTEXT, font=FONT_TAG, justify="left").pack(anchor="w")

    def _apply_status_visual(self, text: str | None = None):
        text = text or self.status_var.get()
        color = C_GRAY
        if "运行中" in text:
            color = C_GREEN
        elif "失败" in text or "错误" in text:
            color = C_RED
        try:
            self._dot_win.itemconfig(self._dot, fill=color)
            self.status_label.configure(fg=color)
        except Exception:
            pass

    # ---- 日志 / 状态（支持后台线程调用，统一回主线程） ----
    def log(self, text: str):
        if self._closing:
            return
        line = f"[{now_str()}] {text}\n"

        def _append():
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.insert(tk.END, line)
            self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)

        self.root.after(0, _append)

    def set_status(self, text: str):
        if self._closing:
            return

        def _set(self=self, text=text):
            self.status_var.set(text)
            self._apply_status_visual(text)

        self.root.after(0, _set)

    # ---- 选择会话 ----
    def pick_session(self):
        if self._picking:
            return
        self._picking = True
        self.set_status("正在读取会话…")
        self._show_loading("正在加载会话列表…\n首次或数据库有更新时需要解密，请稍候")
        threading.Thread(target=self._pick_worker, daemon=True).start()

    def _pick_worker(self):
        try:
            rows = self._load_session_rows()
            self.root.after(0, lambda rows=rows: self._pick_finish(rows, None))
        except Exception as e:
            self.log(f"选择会话失败：{e}\n{traceback.format_exc()}")
            self.root.after(0, lambda e=e: self._pick_finish(None, e))

    def _pick_finish(self, rows, err):
        self._close_loading()
        self._picking = False
        self.set_status("运行中" if self.running else "未运行")
        if err is not None:
            messagebox.showerror("错误", str(err))
            return
        if not rows:
            messagebox.showinfo("提示", "会话列表为空")
            return
        self._show_pick_dialog(rows)

    def _show_loading(self, text: str):
        self._loading_win = None
        try:
            win = tk.Toplevel(self.root)
            win.title("加载中")
            win.transient(self.root)
            win.attributes("-topmost", True)
            win.resizable(False, False)
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 320) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - 100) // 2)
            win.geometry(f"+{x}+{y}")
            ttk.Label(win, text=text, justify="center", padding=(24, 16)).pack()
            win.grab_set()
            self._loading_win = win
        except Exception:
            self._loading_win = None

    def _close_loading(self):
        win = getattr(self, "_loading_win", None)
        if win is not None:
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            self._loading_win = None

    def _show_pick_dialog(self, rows: list):
        win = tk.Toplevel(self.root)
        win.title("选择会话")
        win.geometry("520x480")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="双击选择，或选中后点「确定」").pack(anchor=tk.W, padx=10, pady=8)
        lb = tk.Listbox(win, font=FONT)
        lb.pack(fill=tk.BOTH, expand=True, padx=10)
        for label, _, _ in rows:
            lb.insert(tk.END, label)

        def confirm(_event=None):
            sel = lb.curselection()
            if not sel:
                return
            _, title, username = rows[sel[0]]
            self.target_var.set(title)
            self._resolved_user = username
            self.log(f"已选择会话：{title} -> {username}")
            win.destroy()

        lb.bind("<Double-Button-1>", confirm)
        lb.bind("<Return>", confirm)
        ttk.Button(win, text="确定", command=confirm).pack(pady=8)

    # ---- 诊断 ----
    def diagnose(self):
        try:
            db = self._open_db()
            info = db.get_self_info() or {}
            sessions = db.get_sessions(limit=5) or []
            target = strip_member_count(self.target_var.get())
            from .utils import normalize_text_content
            lines = [
                f"数据目录：{self.db_dir_var.get().strip()}",
                f"当前账号：{info.get('nick_name')} / {info.get('username')}",
                f"最近会话数抽样：{len(sessions)}",
            ]
            if target:
                user, show = self.resolve_target(target)
                latest = self._latest_sort_seq(user)
                recent = self._collect_new_messages(user, since_seq=max(0, latest - 1))
                sample = ""
                if recent:
                    sample = normalize_text_content(str(recent[-1].get("content") or ""))[:40]
                lines.append(f"目标解析：{show} -> {user}")
                lines.append(f"跨分片最新 sort_seq：{latest}")
                lines.append(f"最新消息抽样：{sample or '无'}")
            text = "\n".join(lines)
            self.log("诊断完成：\n  " + text)
            messagebox.showinfo("诊断", text)
        except Exception as e:
            self.log(f"诊断失败：{e}\n{traceback.format_exc()}")
            messagebox.showerror("诊断失败", str(e))