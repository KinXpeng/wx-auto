# -*- coding: utf-8 -*-
"""通用小工具函数：纯函数，无外部依赖（不依赖 tkinter）。"""

import re
from datetime import datetime


def now_str() -> str:
    """当前时间 HH:MM:SS。"""
    return datetime.now().strftime("%H:%M:%S")


def strip_member_count(name: str) -> str:
    """去掉群名末尾的人数括号：`MayDearBeeSure (27)` -> `MayDearBeeSure`。"""
    return re.sub(r"\s*\(\d+\)\s*$", "", (name or "").strip())


def normalize_text_content(content: str) -> str:
    """去除群消息常见的前缀 `wxid_xxx:`，只保留正文。"""
    text = (content or "").strip()
    if "\n" in text and re.match(r"^[\w\-@.]+:\s*$", text.split("\n", 1)[0]):
        text = text.split("\n", 1)[1].strip()
    return text