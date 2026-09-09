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
        self.root.title("微信自动回复（微信4.x）")
        self.root.geometry("580x680")
        self.root.minsize(540, 600)

        self.running = False
        self.db = None
        self.worker_thread = None
        self._last_scheduled_at = 0.0
        self._send_lock = threading.Lock()
        self._resolved_user = ""  # 监听用 username / chatroom id
        self._self_wxid = ""
        self._msg_watermark = 0
        self._last_auto_reply_at = 0.0
        self._recent_reply_texts = set()  # 刚发出的自动回复，避免自激循环
        self._handled_keys = set()
        self._patch_msg_table_lookup()

        self.db_dir_var = tk.StringVar(value=DEFAULT_DB_DIR)
        self.target_var = tk.StringVar(value="")
        self.schedule_enable_var = tk.BooleanVar(value=False)
        self.schedule_minutes_var = tk.StringVar(value="5")
        self.keyword_enable_var = tk.BooleanVar(value=True)
        self.keyword_var = tk.StringVar(value="在吗")
        self.reply_self_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="未运行")

        self._build_ui()
        self._load_config()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="微信数据目录（xwechat_files）").pack(anchor=tk.W)
        db_row = ttk.Frame(frm)
        db_row.pack(fill=tk.X, **pad)
        ttk.Entry(db_row, textvariable=self.db_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(db_row, text="检测", width=8, command=self.detect_db).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frm, text="目标会话（群名 / 备注 / 昵称，不要带人数）").pack(anchor=tk.W)
        target_row = ttk.Frame(frm)
        target_row.pack(fill=tk.X, **pad)
        ttk.Entry(target_row, textvariable=self.target_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(target_row, text="选择会话", width=10, command=self.pick_session).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        schedule_box = ttk.LabelFrame(frm, text="定时自动发送", padding=10)
        schedule_box.pack(fill=tk.X, **pad)
        row1 = ttk.Frame(schedule_box)
        row1.pack(fill=tk.X)
        ttk.Checkbutton(row1, text="启用：每隔", variable=self.schedule_enable_var).pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.schedule_minutes_var, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="分钟，自动发一条消息").pack(side=tk.LEFT)
        ttk.Label(schedule_box, text="定时发送内容").pack(anchor=tk.W, pady=(8, 2))
        self.schedule_text = scrolledtext.ScrolledText(schedule_box, height=3, wrap=tk.WORD)
        self.schedule_text.pack(fill=tk.X)
        self.schedule_text.insert(tk.END, "大家好，我还在线。")

        keyword_box = ttk.LabelFrame(frm, text="关键词自动回复", padding=10)
        keyword_box.pack(fill=tk.X, **pad)
        ttk.Checkbutton(
            keyword_box,
            text="启用：收到包含关键词的消息后自动回复",
            variable=self.keyword_enable_var,
        ).pack(anchor=tk.W)
        ttk.Label(keyword_box, text="触发关键词").pack(anchor=tk.W, pady=(8, 2))
        ttk.Entry(keyword_box, textvariable=self.keyword_var).pack(fill=tk.X)
        ttk.Label(keyword_box, text="自动回复内容").pack(anchor=tk.W, pady=(8, 2))
        self.reply_text = scrolledtext.ScrolledText(keyword_box, height=3, wrap=tk.WORD)
        self.reply_text.pack(fill=tk.X)
        self.reply_text.insert(tk.END, "该用户正忙，请稍后再试")
        ttk.Checkbutton(
            keyword_box,
            text="响应自己发的消息（自测用；正常请关闭。回复内容勿包含关键词）",
            variable=self.reply_self_var,
        ).pack(anchor=tk.W, pady=(8, 0))

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill=tk.X, pady=8)
        ttk.Button(btn_row, text="启动", command=self.start).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="停止", command=self.stop).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_row, text="诊断", command=self.diagnose).pack(side=tk.LEFT)
        ttk.Label(btn_row, textvariable=self.status_var).pack(side=tk.RIGHT)

        ttk.Label(frm, text="运行日志").pack(anchor=tk.W)
        self.log_box = scrolledtext.ScrolledText(frm, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        tip = (
            "适配微信 4.x（Weixin）。请保持微信已登录；发送时不要锁屏。"
            "群名请填 MayDearBeeSure，不要填 MayDearBeeSure (27)。"
        )
        ttk.Label(frm, text=tip, foreground="#666666", wraplength=540).pack(anchor=tk.W, pady=(8, 0))

    def log(self, msg: str):
        line = f"[{now_str()}] {msg}\n"

        def _append():
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.insert(tk.END, line)
            self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)

        self.root.after(0, _append)

    def set_status(self, text: str):
        self.root.after(0, lambda: self.status_var.set(text))

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
        data = {
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
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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

    def _open_db(self):
        from wechatauto import WeChatDB, list_accounts
        from wechatauto.db import auto_detect_db_dir

        self._patch_msg_table_lookup()
        db_dir = self.db_dir_var.get().strip() or auto_detect_db_dir() or DEFAULT_DB_DIR
        self.db_dir_var.set(db_dir)
        accounts = list_accounts(db_dir)
        if not accounts:
            raise RuntimeError(f"在目录中未找到微信账号数据：{db_dir}")
        self.db = WeChatDB(db_dir=db_dir)
        info = self.db.get_self_info() or {}
        self._self_wxid = info.get("username") or ""
        nick = info.get("nick_name") or ""
        self.log(f"已连接消息库：{db_dir}；当前账号：{nick or self._self_wxid}")
        return self.db

    def _collect_new_messages(self, user: str, since_seq: int):
        """跨所有 message 分片拉取 sort_seq > since_seq 的新消息。"""
        from wechatauto.db import _md5_hex

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
        try:
            db = self._open_db()
            sessions = db.get_sessions(limit=80) or []
            rows = []
            for s in sessions:
                username = s.get("username") or ""
                if not username or username in ("brandsessionholder", "officialaccounts"):
                    continue
                title = self.display_name_of(username)
                unread = s.get("unread") or 0
                label = f"{title}    [{username}]"
                if unread:
                    label = f"{title}（未读{unread}）    [{username}]"
                rows.append((label, title, username))

            if not rows:
                messagebox.showinfo("提示", "会话列表为空")
                return

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
        except Exception as e:
            self.log(f"选择会话失败：{e}\n{traceback.format_exc()}")
            messagebox.showerror("错误", str(e))

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

        target = strip_member_count(self.target_var.get())
        if not target:
            messagebox.showwarning("提示", "请先填写或选择目标会话")
            return
        if self.keyword_enable_var.get() and not self.keyword_var.get().strip():
            messagebox.showwarning("提示", "已启用关键词回复，请填写触发关键词")
            return
        if self.schedule_enable_var.get():
            try:
                minutes = float(self.schedule_minutes_var.get().strip())
                if minutes <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("提示", "定时分钟数请填写大于 0 的数字")
                return

        self._save_config()
        self.running = True
        self._last_scheduled_at = time.time()
        self.set_status("运行中")
        self.log(f"启动中，目标会话：{target}")
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.running = False
        self.set_status("已停止")
        self.log("已停止")

    def on_close(self):
        self.running = False
        try:
            self._save_config()
        except Exception:
            pass
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
                if len(self._recent_reply_texts) > 20:
                    self._recent_reply_texts = set(list(self._recent_reply_texts)[-10:])
            self._last_auto_reply_at = time.time()

    def _handle_message(self, msg: dict):
        if not self.running or not self.keyword_enable_var.get():
            return
        try:
            mtype = str(msg.get("type") or "")
            if mtype and mtype not in ("文本", "text", "Text", "1"):
                return

            sender = msg.get("sender_username") or ""
            is_self = bool(self._self_wxid and sender == self._self_wxid)
            if is_self and not self.reply_self_var.get():
                return

            content = normalize_text_content(str(msg.get("content") or ""))
            if not content:
                return

            reply = self.reply_text.get("1.0", tk.END).strip()
            reply_norm = normalize_text_content(reply)

            # 1) 忽略自动回复正文本身（即使勾了响应自己）
            if reply_norm and content == reply_norm:
                return
            if content in self._recent_reply_texts:
                return

            keyword = self.keyword_var.get().strip()
            if not keyword or keyword not in content:
                return

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

            who_display = strip_member_count(self.target_var.get())
            self.log(f"命中关键词「{keyword}」← {content[:80]}")
            self._send_text(who_display, reply)
            self.log(f"已自动回复 → {reply[:80]}")

            # 发出后短暂抬高水位，减少立刻又读到自己刚发的消息
            try:
                seq = int(msg.get("sort_seq") or 0)
                if seq > self._msg_watermark:
                    self._msg_watermark = seq
            except Exception:
                pass
        except Exception as e:
            self.log(f"处理消息失败：{e}\n{traceback.format_exc()}")

    def _maybe_schedule_send(self, who_display: str):
        if not self.schedule_enable_var.get():
            return
        try:
            minutes = float(self.schedule_minutes_var.get().strip() or "0")
        except ValueError:
            return
        if minutes <= 0:
            return
        now = time.time()
        if now - self._last_scheduled_at < minutes * 60:
            return
        content = self.schedule_text.get("1.0", tk.END).strip()
        if not content:
            return
        self._send_text(who_display, content)
        self._last_scheduled_at = now
        self.log(f"定时发送 → {content[:80]}")

    def _worker_loop(self):
        who_display = strip_member_count(self.target_var.get())
        try:
            self._open_db()
            user, show = self.resolve_target(who_display)
            self._resolved_user = user
            self.target_var.set(show)
            self.log(f"监听对象已解析：{show} -> {user}")

            # 从跨分片最新位置开始，避免读到旧分片导致永远收不到新消息
            self._msg_watermark = self._latest_sort_seq(user)
            self._last_auto_reply_at = 0.0
            self._recent_reply_texts.clear()
            self._handled_keys.clear()
            self.log(f"跨分片监听已启动，水位 sort_seq={self._msg_watermark}")
        except Exception as e:
            self.log(f"启动失败：{e}\n{traceback.format_exc()}")
            self.running = False
            self.set_status("启动失败")
            return

        try:
            while self.running:
                try:
                    new_msgs = self._collect_new_messages(user, self._msg_watermark)
                    if new_msgs:
                        self._msg_watermark = max(
                            self._msg_watermark,
                            max(int(m.get("sort_seq") or 0) for m in new_msgs),
                        )
                        for msg in new_msgs:
                            self._handle_message(msg)
                    self._maybe_schedule_send(show)
                except Exception as e:
                    self.log(f"轮询异常：{e}")
                time.sleep(1.0)
        finally:
            self.set_status("已停止")
            self.log("工作线程结束")


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    WeChatAutoReplyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
