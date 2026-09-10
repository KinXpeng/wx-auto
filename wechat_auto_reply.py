# -*- coding: utf-8 -*-
"""
微信自动回复（适配微信 4.x / Weixin.exe）

监听：解密读取本机消息库（wechatauto-replica）
发送：驱动 PC 微信界面发送（quick_send）
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

CONFIG_PATH = Path(__file__).with_name("config.json")
if getattr(sys, "frozen", False):
    CONFIG_PATH = Path(sys.executable).with_name("config.json")
DEFAULT_DB_DIR = r"D:\xwechat_files"

# ===== UI 主题令牌 =====
C_BG = "#F5F6F7"          # 界面主背景
C_CARD = "#FFFFFF"        # 卡片面板底色
C_BORDER = "#E3E6EA"     # 卡片/分隔线
C_TEXT = "#1F2329"        # 主文字
C_SUBTEXT = "#8A919F"     # 次级文字
C_GREEN = "#07C160"       # 微信绿：启动/运行中
C_GREEN_DK = "#059B4C"    # 启动按钮 hover/按下
C_GRAY = "#B7BDC7"       # 停止/未运行
C_RED = "#FA5151"         # 错误/失败
C_ACCENT_BG = "#E8F7EF"   # 绿色卡片底色（运行状态）
C_ACCENT_BG_RED = "#FDECEC"  # 红色卡片底色（错误）
FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_TAG = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 10)


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def strip_member_count(name: str) -> str:
    """MayDearBeeSure (27) -> MayDearBeeSure"""
    return re.sub(r"\s*\(\d+\)\s*$", "", (name or "").strip())


def normalize_text_content(content: str) -> str:
    """群消息常见格式：wxid_xxx:\\n正文"""
    text = (content or "").strip()
    if "\n" in text and re.match(r"^[\w\-@.]+:\s*$", text.split("\n", 1)[0]):
        text = text.split("\n", 1)[1].strip()
    return text


class WeChatAutoReplyApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("微信自动回复 · 微信 4.x")
        self.root.geometry("600x720")
        self.root.minsize(560, 660)

        self.running = False
        self.db = None
        self.worker_thread = None
        self._stop_event = threading.Event()
        self._closing = False
        self._last_scheduled_at = 0.0
        self._send_lock = threading.Lock()
        self._resolved_user = ""  # 监听用 username / chatroom id
        self._self_wxid = ""
        self._msg_watermark = 0
        self._last_auto_reply_at = 0.0
        self._recent_reply_texts = set()  # 刚发出的自动回复，避免自激循环
        self._recent_sent_texts = {}  # 本程序实际发过的文本 -> 发送时间，用于识别真正的自己消息
        self._handled_keys = set()
        self._db_lock = threading.Lock()  # 数据库连接可能被多线程复用，串行化读写
        self._picking = False  # 会话加载中，避免重复点击
        self._runtime_lock = threading.Lock()
        self._runtime_config = {}
        self._runtime_sync_id = None
        self._patch_msg_table_lookup()

        self.db_dir_var = tk.StringVar(value=DEFAULT_DB_DIR)
        self.target_var = tk.StringVar(value="")
        self.schedule_enable_var = tk.BooleanVar(value=False)
        self.schedule_minutes_var = tk.StringVar(value="5")
        self.keyword_enable_var = tk.BooleanVar(value=True)
        self.keyword_var = tk.StringVar(value="在吗")
        self.reply_self_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="未运行")

        self._configure_style()
        self._dot = None
        self._dot_win = None
        self._build_ui()
        self._load_config()
        self._capture_runtime_config()
        self._runtime_sync_id = None
        self._runtime_sync_pending = False
        self._watch_runtime_config()
        self._apply_status_visual("未运行")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_style(self):
        """应用自定义 ttk 主题/样式，让界面在新旧 Windows 上保持一致观感。"""
        try:
            import tkinter.font as tkfont

            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
                try:
                    tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            style = ttk.Style(self.root)
            # 兼容系统里可用的圆角现代主题；失败则退回默认
            for theme in ("vista", "xpnative"):
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
            style.configure("TFrame", background=C_BG)
            style.configure("TLabel", background=C_BG, foreground=C_TEXT, font=FONT)
            style.configure("TEntry", padding=5, fieldbackground=C_CARD, borderwidth=1)
            style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT, font=FONT)
            style.configure(
                "TLabelframe",
                background=C_BG,
                bordercolor=C_BORDER,
                lightcolor=C_BORDER,
                darkcolor=C_BORDER,
            )
            style.configure("TLabelframe.Label", background=C_BG, foreground=C_SUBTEXT, font=FONT_BOLD)
            # 主按钮：用绿色粗体文字 + 适度内边距，任何主题下都清晰可读
            style.configure("Accent.TButton", font=FONT_BOLD, foreground=C_GREEN, padding=(10, 4))
            style.configure("TButton", font=FONT, padding=(8, 4))
        except Exception:
            pass

    def _build_ui(self):
        root_bg = C_BG
        self.root.configure(bg=root_bg)

        outer = tk.Frame(self.root, bg=root_bg)
        outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # ==== 顶部：标题 + 运行状态徽标 ====
        header = tk.Frame(outer, bg=root_bg)
        header.pack(fill=tk.X, pady=(0, 10))
        tk.Label(header, text="微信自动回复", font=FONT_TITLE, bg=root_bg, fg=C_TEXT).pack(side=tk.LEFT)
        status_box = tk.Frame(header, bg=root_bg)
        status_box.pack(side=tk.RIGHT)
        self._status_box = status_box
        self._dot_win = tk.Canvas(status_box, width=16, height=16, bg=root_bg, highlightthickness=0)
        self._dot = self._dot_win.create_oval(3, 3, 13, 13, fill=C_GRAY, outline="")
        self._dot_win.pack(side=tk.LEFT, padx=(0, 5))
        self.status_label = tk.Label(
            status_box, textvariable=self.status_var, bg=root_bg, fg=C_SUBTEXT, font=FONT
        )
        self.status_label.pack(side=tk.LEFT)

        def _row_label(parent, text):
            tk.Label(parent, text=text, bg=root_bg, fg=C_SUBTEXT, font=FONT_TAG).pack(
                anchor="w", pady=(0, 3))
            return parent

        # ==== 数据目录 ====
        _row_label(outer, "微信数据目录")
        db_row = tk.Frame(outer, bg=root_bg)
        db_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Entry(db_row, textvariable=self.db_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(db_row, text="检测", width=7, command=self.detect_db).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # ==== 目标会话 ====
        _row_label(outer, "目标会话（群名/好友备注/昵称）")
        tgt_row = tk.Frame(outer, bg=root_bg)
        tgt_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Entry(tgt_row, textvariable=self.target_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(tgt_row, text="选择会话", width=9, command=self.pick_session).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # ==== 定时自动发送 ====
        _row_label(outer, "定时自动发送")
        row1 = tk.Frame(outer, bg=root_bg)
        row1.pack(fill=tk.X)
        ttk.Checkbutton(row1, text="启用：每隔", variable=self.schedule_enable_var).pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.schedule_minutes_var, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="分钟发一条").pack(side=tk.LEFT)
        self.schedule_text = scrolledtext.ScrolledText(
            outer, height=2, wrap=tk.WORD, font=FONT_MONO,
            bg=C_CARD, fg=C_TEXT, insertbackground=C_TEXT, relief="solid", bd=1
        )
        self.schedule_text.pack(fill=tk.X, pady=(4, 8))
        self.schedule_text.insert(tk.END, "大家好，我还在线。")

        # ==== 关键词自动回复 ====
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
            kw, height=2, wrap=tk.WORD, font=FONT_MONO, bg=C_CARD, fg=C_TEXT, bd=1, relief="solid"
        )
        self.reply_text.pack(fill="x")
        self.reply_text.insert(tk.END, "该用户正忙，请稍后再试")
        ttk.Checkbutton(
            kw, text="响应自己发的消息（自测用；回复内容勿包含关键词）", variable=self.reply_self_var
        ).pack(anchor="w", pady=(6, 0))

        # ==== 操作区 ====
        action_box = tk.Frame(outer, bg=root_bg)
        action_box.pack(fill=tk.X, pady=(12, 8))
        ttk.Button(action_box, text="启动", style="Accent.TButton", command=self.start).pack(side=tk.LEFT)
        ttk.Button(action_box, text="停止", command=self.stop).pack(side=tk.LEFT, padx=8)
        ttk.Button(action_box, text="诊断", command=self.diagnose).pack(side=tk.LEFT)

        # ==== 日志 ====
        _row_label(outer, "运行日志")
        self.log_box = scrolledtext.ScrolledText(
            outer, height=7, wrap=tk.WORD, state=tk.DISABLED, font=FONT_MONO,
            bg=C_CARD, fg=C_TEXT, bd=1, relief="solid"
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
        except Exception:
            pass

    def _read_ui_config(self) -> dict:
        """只在 Tk 主线程读取控件，后台线程使用配置快照。"""
        return {
            "db_dir": self.db_dir_var.get().strip(),
            "target": self.target_var.get().strip(),
            "schedule_enable": self.schedule_enable_var.get(),
            "schedule_minutes": self.schedule_minutes_var.get().strip(),
            "schedule_content": self.schedule_text.get("1.0", tk.END).strip(),
            "keyword_enable": self.keyword_enable_var.get(),
            "keyword": self.keyword_var.get().strip(),
            "reply_content": self.reply_text.get("1.0", tk.END).strip(),
            "reply_self": self.reply_self_var.get(),
        }

    def _capture_runtime_config(self) -> dict:
        data = self._read_ui_config()
        with self._runtime_lock:
            self._runtime_config = data
        return data

    def _runtime_snapshot(self) -> dict:
        with self._runtime_lock:
            return dict(self._runtime_config)

    def _watch_runtime_config(self):
        """用事件驱动更新配置快照：Variable 用 trace，多行文本用 <KeyRelease>，
        外加低频兜底轮询（只在本机未改动时为空操作），避免 250ms 空转。"""
        traceable = (
            self.db_dir_var,
            self.target_var,
            self.schedule_enable_var,
            self.schedule_minutes_var,
            self.keyword_enable_var,
            self.keyword_var,
            self.reply_self_var,
        )
        for v in traceable:
            v.trace_add("write", self._queue_runtime_sync)
        for w in (self.schedule_text, self.reply_text):
            w.bind("<KeyRelease>", self._queue_runtime_sync)
        self._runtime_sync_id = self.root.after(1000, self._sync_runtime_config)

    def _queue_runtime_sync(self, *_args):
        if self._closing or self._runtime_sync_pending:
            return
        self._runtime_sync_pending = True
        if self._runtime_sync_id is not None:
            try:
                self.root.after_cancel(self._runtime_sync_id)
            except tk.TclError:
                pass
        self._runtime_sync_id = self.root.after(60, self._sync_runtime_config)

    def _sync_runtime_config(self):
        self._runtime_sync_id = None
        self._runtime_sync_pending = False
        if self._closing:
            return
        try:
            self._capture_runtime_config()
        except tk.TclError:
            self._runtime_sync_id = None
            return
        # 兜底：仅当没有任何用户改动事件时保持低频扫描，成本极低
        if not self._runtime_sync_pending:
            try:
                self._runtime_sync_id = self.root.after(750, self._sync_runtime_config)
            except tk.TclError:
                self._runtime_sync_id = None

    def log(self, msg: str):
        if self._closing:
            return
        line = f"[{now_str()}] {msg}\n"

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

    def _load_config(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self.db_dir_var.set(data.get("db_dir", DEFAULT_DB_DIR))
            self.target_var.set(data.get("target", ""))
            self.schedule_enable_var.set(bool(data.get("schedule_enable", False)))
            self.schedule_minutes_var.set(str(data.get("schedule_minutes", "5")))
            self.keyword_enable_var.set(bool(data.get("keyword_enable", True)))
            self.keyword_var.set(data.get("keyword", "在吗"))
            self.reply_self_var.set(bool(data.get("reply_self", False)))
            self.schedule_text.delete("1.0", tk.END)
            self.schedule_text.insert(tk.END, data.get("schedule_content", "大家好，我还在线。"))
            self.reply_text.delete("1.0", tk.END)
            self.reply_text.insert(tk.END, data.get("reply_content", "该用户正忙，请稍后再试"))
        except Exception as e:
            self.log(f"读取配置失败：{e}")

    def _save_config(self):
        data = self._read_ui_config()
        # 先写临时文件再原子替换，避免写入中断时 config.json 被截断/损坏
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(CONFIG_PATH)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass

    @staticmethod
    def _patch_msg_table_lookup():
        """修复 wechatauto：同一会话可能存在于多个 message_*.db，原逻辑取第一个旧分片。"""
        try:
            from wechatauto.db import WeChatDB, _md5_hex
        except Exception:
            return
        if getattr(WeChatDB, "_multi_shard_patched", False):
            return

        def _find_msg_table(self, user, conns):
            target = "Msg_" + _md5_hex(user.encode())
            best = None
            best_seq = -1
            for conn in conns:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (target,),
                ).fetchone()
                if not row:
                    continue
                try:
                    mx = conn.execute(f"SELECT MAX(sort_seq) FROM {target}").fetchone()[0] or 0
                except Exception:
                    mx = 0
                if mx >= best_seq:
                    best_seq = mx
                    best = (conn, target)
            return best

        WeChatDB._find_msg_table = _find_msg_table
        WeChatDB._multi_shard_patched = True

    def _open_db(self, db_dir: str | None = None):
        from wechatauto import WeChatDB, list_accounts
        from wechatauto.db import auto_detect_db_dir

        self._patch_msg_table_lookup()
        if db_dir is None:
            if threading.current_thread() is threading.main_thread():
                db_dir = self._read_ui_config()["db_dir"]
            else:
                db_dir = self._runtime_snapshot()["db_dir"]
        db_dir = db_dir.strip() or auto_detect_db_dir() or DEFAULT_DB_DIR
        accounts = list_accounts(db_dir)
        if not accounts:
            raise RuntimeError(f"在目录中未找到微信账号数据：{db_dir}")
        self.db = WeChatDB(db_dir=db_dir)
        info = self.db.get_self_info() or {}
        self._self_wxid = (
            info.get("username")
            or info.get("userName")
            or info.get("wxid")
            or info.get("alias")
            or ""
        )
        nick = info.get("nick_name") or info.get("nickName") or ""
        self.log(f"已连接消息库：{db_dir}；当前账号：{nick or self._self_wxid}")
        self.log(f"当前账号 wxid：{self._self_wxid or '未获取到（自己消息过滤会失效）'}")
        return self.db

    def _collect_new_messages(self, user: str, since_seq: int):
        """跨所有 message 分片拉取 sort_seq > since_seq 的新消息。"""
        from wechatauto.db import _md5_hex

        with self._db_lock:
            db = self.db
            target = "Msg_" + _md5_hex(user.encode())
            rows = []
            for rel in db._message_dbs():
                conn = db._open(rel)
                try:
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (target,),
                    ).fetchone()
                    if not exists:
                        continue
                    raw = conn.execute(
                        f"SELECT local_id, local_type, real_sender_id, create_time, "
                        f"message_content, source, packed_info_data, compress_content, sort_seq "
                        f"FROM {target} WHERE sort_seq > ? ORDER BY sort_seq ASC LIMIT 200",
                        (since_seq,),
                    ).fetchall()
                    for r in raw:
                        rows.append(db._msg_row_to_dict(r))
                except Exception as e:
                    self.log(f"读取分片失败 {rel}: {e}")
                finally:
                    conn.close()
            rows.sort(key=lambda m: m.get("sort_seq") or 0)
            # 去重：同一 sort_seq + local_id
            uniq = []
            seen = set()
            for m in rows:
                key = (m.get("sort_seq"), m.get("local_id"), m.get("content"))
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(m)
            return uniq

    def _latest_sort_seq(self, user: str) -> int:
        from wechatauto.db import _md5_hex

        with self._db_lock:
            db = self.db
            target = "Msg_" + _md5_hex(user.encode())
            best = 0
            for rel in db._message_dbs():
                conn = db._open(rel)
                try:
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (target,),
                    ).fetchone()
                    if not exists:
                        continue
                    mx = conn.execute(f"SELECT MAX(sort_seq) FROM {target}").fetchone()[0] or 0
                    if mx > best:
                        best = mx
                finally:
                    conn.close()
            return int(best or 0)

    def detect_db(self):
        try:
            from wechatauto.db import auto_detect_db_dir
            from wechatauto import list_accounts

            found = auto_detect_db_dir()
            candidates = []
            if found:
                candidates.append(found)
            for p in (DEFAULT_DB_DIR, str(Path.home() / "Documents" / "xwechat_files")):
                if p not in candidates and Path(p).exists():
                    candidates.append(p)
            for p in candidates:
                accounts = list_accounts(p)
                if accounts:
                    self.db_dir_var.set(p)
                    self.log(f"检测到数据目录：{p}（账号 {len(accounts)} 个）")
                    messagebox.showinfo("检测成功", f"数据目录：\n{p}\n账号数：{len(accounts)}")
                    return
            messagebox.showwarning("未检测到", "未找到 xwechat_files，请手动填写数据目录")
        except Exception as e:
            self.log(f"检测失败：{e}")
            messagebox.showerror("检测失败", str(e))

    def resolve_target(self, display_name: str) -> tuple[str, str]:
        """返回 (username, 展示名)"""
        name = strip_member_count(display_name)
        if not name:
            raise RuntimeError("目标会话为空")
        db = self.db or self._open_db()

        if name.endswith("@chatroom") or name.startswith("wxid_") or name == "filehelper":
            show = db.get_nickname(name) or db.group_id_to_name(name) or name
            return name, show

        gid = db.group_name_to_id(name)
        if gid:
            return gid, name

        uid = db.username_by_nickname(name)
        if uid:
            return uid, name

        hits = db.search_contact(name) or []
        if hits:
            item = hits[0]
            return item.get("username"), item.get("remark") or item.get("nick_name") or name

        raise RuntimeError(f"找不到会话：{name}（请确认群名/备注完全一致）")

    def display_name_of(self, username: str) -> str:
        db = self.db
        if not db:
            return username
        if username.endswith("@chatroom"):
            return db.group_id_to_name(username) or username
        remark_or_nick = db.get_nickname(username)
        return remark_or_nick or username

    def pick_session(self):
        if self._picking:
            return
        self._picking = True
        self.set_status("正在读取会话…")
        self._show_loading("正在加载会话列表…\n首次或数据库有更新时需要解密，请稍候")
        threading.Thread(target=self._pick_worker, daemon=True).start()

    def _pick_worker(self):
        """后台加载会话列表，避免界面卡顿。"""
        try:
            rows = self._load_session_rows()
            self.root.after(0, lambda rows=rows: self._pick_finish(rows, None))
        except Exception as e:
            self.log(f"选择会话失败：{e}\n{traceback.format_exc()}")
            self.root.after(0, lambda e=e: self._pick_finish(None, e))

    def _pick_finish(self, rows, err):
        """回到主线程：关闭加载窗并展示结果。"""
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
        """弹一个明显的加载提示窗（居中于主窗口）。"""
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

    def _load_session_rows(self):
        if self.db is None:
            self._open_db()  # 建立连接本身独立，不加锁
        with self._db_lock:
            db = self.db
            sessions = db.get_sessions(limit=80) or []
            usernames = [s.get("username") or "" for s in sessions]
            name_map = self._resolve_display_names(db, usernames)
            rows = []
            for s in sessions:
                username = s.get("username") or ""
                if not username or username in ("brandsessionholder", "officialaccounts"):
                    continue
                title = name_map.get(username) or username
                unread = s.get("unread") or 0
                label = f"{title}    [{username}]"
                if unread:
                    label = f"{title}（未读{unread}）    [{username}]"
                rows.append((label, title, username))
        return rows

    def _resolve_display_names(self, db, usernames: list) -> dict:
        """一次性查 contact.db 批量解析展示名（比逐个 get_nickname 快很多）。"""
        names: dict = {}
        unames = list(dict.fromkeys(u for u in usernames if u))
        if not unames:
            return names
        conn = db._contact_conn()
        if not conn:
            return names
        try:
            ph = ",".join("?" * len(unames))
            rows = conn.execute(
                "SELECT username, nick_name, remark FROM contact "
                f"WHERE username IN ({ph})",
                unames,
            ).fetchall()
            for u, n, r in rows:
                names[u] = r or n or u
        finally:
            conn.close()
        return names

    def _show_pick_dialog(self, rows: list):
        win = tk.Toplevel(self.root)
        win.title("选择会话")
        win.geometry("520x460")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="双击选择，或选中后点「确定」").pack(anchor=tk.W, padx=10, pady=8)
        lb = tk.Listbox(win)
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
        ttk.Button(win, text="确定", command=confirm).pack(pady=8)

    def diagnose(self):
        try:
            db = self._open_db()
            info = db.get_self_info() or {}
            sessions = db.get_sessions(limit=5) or []
            target = strip_member_count(self.target_var.get())
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
            self.log("诊断完成：\n  " + "\n  ".join(lines))
            messagebox.showinfo("诊断", "\n".join(lines))
        except Exception as e:
            self.log(f"诊断失败：{e}\n{traceback.format_exc()}")
            messagebox.showerror("诊断失败", str(e))

    def start(self):
        if self.running:
            messagebox.showinfo("提示", "已经在运行中")
            return

        config = self._capture_runtime_config()
        target = strip_member_count(config["target"])
        if not target:
            messagebox.showwarning("提示", "请先填写或选择目标会话")
            return
        if config["keyword_enable"] and not config["keyword"]:
            messagebox.showwarning("提示", "已启用关键词回复，请填写触发关键词")
            return
        if config["schedule_enable"]:
            try:
                minutes = float(config["schedule_minutes"])
                if minutes <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("提示", "定时分钟数请填写大于 0 的数字")
                return

        self._save_config()
        self._stop_event.clear()
        self.running = True
        self._last_scheduled_at = time.time()
        self.set_status("运行中")
        self.log(f"启动中，目标会话：{target}")
        self.worker_thread = threading.Thread(
            target=self._worker_loop, args=(config,), daemon=True
        )
        self.worker_thread.start()

    def _request_stop(self):
        self.running = False
        self._stop_event.set()

    def stop(self):
        self._request_stop()
        thread = self.worker_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self.set_status("已停止")
        self.log("已停止")

    def on_close(self):
        self._closing = True
        self._request_stop()
        if self._runtime_sync_id is not None:
            try:
                self.root.after_cancel(self._runtime_sync_id)
            except tk.TclError:
                pass
            self._runtime_sync_id = None
        try:
            self._save_config()
        except Exception:
            pass
        # 等后台线程退出后再销毁主界面，避免 Tk 对象被后台线程访问
        thread = self.worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self.root.destroy()

    def _send_text(self, who_display: str, content: str):
        from wechatauto.guia import quick_send

        with self._send_lock:
            resp = quick_send(content, who_display)
            ok = True
            if resp is not None:
                if hasattr(resp, "is_success"):
                    ok = bool(resp.is_success)
                elif isinstance(resp, dict):
                    ok = bool(resp.get("success", True))
            if not ok:
                raise RuntimeError(f"发送失败：{resp}")
            # 把自己刚发的内容加入忽略集，防止「回复里也带关键词」时连环触发
            text = normalize_text_content(content)
            if text:
                self._recent_reply_texts.add(text)
                self._recent_sent_texts[text] = time.time()
                # 清理 5 分钟前发过的记录
                cutoff = time.time() - 300
                self._recent_sent_texts = {k: v for k, v in self._recent_sent_texts.items() if v > cutoff}
                if len(self._recent_reply_texts) > 20:
                    self._recent_reply_texts = set(list(self._recent_reply_texts)[-10:])
            self._last_auto_reply_at = time.time()

    def _handle_message(self, msg: dict, who_display: str):
        config = self._runtime_snapshot()
        if not self.running or not config["keyword_enable"]:
            return
        try:
            mtype = str(msg.get("type") or "")
            sender = msg.get("sender_username") or ""

            # 多媒体消息 content 不可靠，直接跳过
            if mtype in ("image", "Image", "图片", "voice", "Voice", "语音", "video", "Video", "视频", "emoji", "Emoji", "表情"):
                return

            content = normalize_text_content(str(msg.get("content") or ""))
            if not content:
                return

            # 判断是否为本人发送
            # 注意：wechatauto 读到的某些 @ 消息会把 sender 错标成自己，所以同时参考：
            # 1) 程序真实发过的内容；2) 消息自带的 is_sender 标记；3) sender == self_wxid 且不是 @ 开头
            is_self_by_sender = bool(self._self_wxid and sender and sender == self._self_wxid)
            is_self_by_flag = any(msg.get(k) for k in ("is_sender", "isSelf", "isself", "IsSender") if k in msg)
            recent_sent_at = self._recent_sent_texts.get(content)
            is_self_by_content = bool(recent_sent_at and time.time() - recent_sent_at < 300)

            if is_self_by_content or is_self_by_flag:
                is_self = True
            elif is_self_by_sender and content.startswith("@"):
                # 兼容：@ 开头的消息被错标成自己时，不当作自己消息
                is_self = False
            else:
                is_self = is_self_by_sender

            if is_self and not config["reply_self"]:
                self.log(f"忽略自己消息：{content[:40]}（sender={sender}, self={self._self_wxid}）")
                return

            # 非文本/应用消息但带文本内容时，记录一下以便排查
            if mtype and mtype not in ("文本", "text", "Text", "1", "appmsg", "App", "49"):
                self.log(f"收到非文本消息 type={mtype} sender={sender} content={content[:60]!r}")

            reply = config["reply_content"]
            reply_norm = normalize_text_content(reply)

            # 1) 忽略自动回复正文本身（即使勾了响应自己）
            if reply_norm and content == reply_norm:
                return
            if content in self._recent_reply_texts:
                return

            keyword = config["keyword"]
            if not keyword or keyword not in content:
                return

            self.log(f"关键词匹配 type={mtype} sender={sender} content={content[:60]!r}")

            # 2) 冷却：一次命中后短时间内不再回，避免连发/重复分片
            if time.time() - self._last_auto_reply_at < 3.0:
                self.log(f"冷却中，忽略：{content[:40]}")
                return

            # 3) 去重：同一条逻辑消息只处理一次
            key = f"{msg.get('sort_seq')}|{sender}|{content}"
            if key in self._handled_keys:
                return
            self._handled_keys.add(key)
            if len(self._handled_keys) > 300:
                self._handled_keys = set(list(self._handled_keys)[-120:])

            if not reply:
                return

            self.log(f"命中关键词「{keyword}」← {content[:80]}")
            self._send_text(who_display, reply)
            self.log(f"已自动回复 → {reply[:80]}")
        except Exception as e:
            self.log(f"处理消息失败：{e}\n{traceback.format_exc()}")

    def _maybe_schedule_send(self, who_display: str):
        config = self._runtime_snapshot()
        if not config["schedule_enable"]:
            return
        try:
            minutes = float(config["schedule_minutes"] or "0")
        except ValueError:
            return
        if minutes <= 0:
            return
        now = time.time()
        if now - self._last_scheduled_at < minutes * 60:
            return
        content = config["schedule_content"]
        if not content:
            return
        self._send_text(who_display, content)
        self._last_scheduled_at = now
        self.log(f"定时发送 → {content[:80]}")

    def _worker_loop(self, config: dict):
        who_display = strip_member_count(config["target"])
        try:
            self._open_db(config["db_dir"])
            user, show = self.resolve_target(who_display)
            self._resolved_user = user
            if not self._closing:
                self.root.after(0, lambda show=show: self.target_var.set(show))
            self.log(f"监听对象已解析：{show} -> {user}")

            # 从跨分片最新位置开始，避免读到旧分片导致永远收不到新消息
            self._msg_watermark = self._latest_sort_seq(user)
            self._last_auto_reply_at = 0.0
            self._recent_reply_texts.clear()
            self._recent_sent_texts.clear()
            self._handled_keys.clear()
            self.log(f"跨分片监听已启动，水位 sort_seq={self._msg_watermark}")
        except Exception as e:
            self.log(f"启动失败：{e}\n{traceback.format_exc()}")
            self.running = False
            self.set_status("启动失败")
            return

        try:
            while self.running and not self._stop_event.is_set():
                try:
                    new_msgs = self._collect_new_messages(user, self._msg_watermark)
                    if new_msgs:
                        self._msg_watermark = max(
                            self._msg_watermark,
                            max(int(m.get("sort_seq") or 0) for m in new_msgs),
                        )
                        for msg in new_msgs:
                            self._handle_message(msg, show)
                    self._maybe_schedule_send(show)
                except Exception as e:
                    self.log(f"轮询异常：{e}")
                self._stop_event.wait(1.0)
        finally:
            self.set_status("已停止")
            self.log("工作线程结束")


def main():
    root = tk.Tk()
    WeChatAutoReplyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
