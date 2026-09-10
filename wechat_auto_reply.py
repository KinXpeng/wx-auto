# -*- coding: utf-8 -*-
"""
微信自动回复（适配微信 4.x / Weixin.exe）— 入口与组合根。

监听：解密读取本机消息库（wechatauto-replica）
发送：驱动 PC 微信界面发送（quick_send）

本文件只负责：组装各功能模块、初始化应用状态、启动主循环。
具体的实现被拆到 app/ 包中：
  - app/theme.py    UI 主题令牌与样式
  - app/utils.py    通用小工具函数
  - app/config.py   配置路径 / 读取 / 写入 / 运行时快照
  - app/db.py       微信消息库访问
  - app/ui.py       界面构建 / 状态 / 日志 / 会话选择 / 诊断
  - app/run.py      运行控制 / 收发逻辑 / 后台轮询线程
"""

import sys
import threading
import tkinter as tk

from app.config import ConfigMixin, DEFAULT_DB_DIR
from app.theme import configure_style
from app.utils import now_str, strip_member_count, normalize_text_content
from app.db import DatabaseMixin
from app.ui import UiMixin
from app.run import RunMixin


class WeChatAutoReplyApp(ConfigMixin, UiMixin, DatabaseMixin, RunMixin):
    """组合各功能模块的 GUI 主体类。

    各 mixin 的方法通过 MRO 解析，全部沿用原有方法名与 self 状态，
    因此对外行为与拆分前完全一致。
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("微信自动回复 · 微信 4.x")
        self.root.geometry("600x720")
        self.root.minsize(560, 660)

        # ---- 运行状态（线程安全） ----
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
        self._recent_sent_texts = {}  # 程序实际发过的文本 -> 时间，识别真正的自己消息
        self._handled_keys = set()
        self._db_lock = threading.Lock()  # 数据库连接跨线程复用，串行化读写
        self._picking = False  # 会话加载中，避免重复点击
        self._runtime_lock = threading.Lock()
        self._runtime_config = {}
        self._runtime_sync_id = None
        self._runtime_sync_pending = False
        self._patch_msg_table_lookup()

        # ---- UI 变量 ----
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


def main():
    root = tk.Tk()
    WeChatAutoReplyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()