# -*- coding: utf-8 -*-
"""配置：路径常量、从 UI 读取/写入 config.json，以及运行时配置快照的同步。

以 mixin 类形式提供方法，挂载到主 App 类上使用（方法内直接使用 self 的
Tk 变量与锁），与拆分前行为完全一致。
"""

from pathlib import Path
import json
import sys
import tkinter as tk

DEFAULT_DB_DIR = r"D:\xwechat_files"

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
if getattr(sys, "frozen", False):
    CONFIG_PATH = Path(sys.executable).with_name("config.json")


class ConfigMixin:
    """提供：读取 UI 控件配置 / 持久化 / 运行时快照同步。"""

    # ---- UI 控件 → 配置字典 ----
    def _read_ui_config(self) -> dict:
        """只在 Tk 主线程读取控件；后台线程使用配置快照，不要调用本方法。"""
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

    # ---- 后台线程要用的最新配置：由主线程定时刷新 ----
    def _watch_runtime_config(self):
        """事件驱动更新配置快照：Variable 用 trace，多行文本用 <KeyRelease>，
        外加低频兜底轮询，避免空转。"""
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
        if not self._runtime_sync_pending:
            try:
                self._runtime_sync_id = self.root.after(750, self._sync_runtime_config)
            except tk.TclError:
                self._runtime_sync_id = None

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

    # ---- 磁盘读写 ----
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