# -*- coding: utf-8 -*-
"""运行态：启动/停止/后台轮询线程、发送消息、关键词命中处理、定时发送。

以 mixin 类形式提供方法，挂载到主 App 类上。
"""

import threading
import time
import traceback
from tkinter import messagebox

from .utils import strip_member_count, normalize_text_content


class RunMixin:
    """提供应用运行控制与收发逻辑。"""

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
            except Exception:
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
                cutoff = time.time() - 300
                self._recent_sent_texts = {
                    k: v for k, v in self._recent_sent_texts.items() if v > cutoff
                }
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
            if mtype in (
                "image", "Image", "图片", "voice", "Voice", "语音",
                "video", "Video", "视频", "emoji", "Emoji", "表情",
            ):
                return

            content = normalize_text_content(str(msg.get("content") or ""))
            if not content:
                return

            # 判断是否本人发送（兼容 wechatauto 对 @ 消息的错误标记）
            is_self_by_sender = bool(self._self_wxid and sender and sender == self._self_wxid)
            is_self_by_flag = any(
                msg.get(k) for k in ("is_sender", "isSelf", "isself", "IsSender") if k in msg
            )
            recent_sent_at = self._recent_sent_texts.get(content)
            is_self_by_content = bool(recent_sent_at and time.time() - recent_sent_at < 300)

            if is_self_by_content or is_self_by_flag:
                is_self = True
            elif is_self_by_sender and content.startswith("@"):
                is_self = False
            else:
                is_self = is_self_by_sender

            if is_self and not config["reply_self"]:
                self.log(f"忽略自己消息：{content[:40]}（sender={sender}, self={self._self_wxid}）")
                return

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

            # 2) 冷却：一次命中后短时间内不再回
            if time.time() - self._last_auto_reply_at < 3.0:
                self.log(f"冷却中，忽略：{content[:40]}")
                return

            # 3) 去重：同一条消息只处理一次
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