# -*- coding: utf-8 -*-
"""消息库（wechatauto）访问：连接、跨分片读消息、会话解析、诊断相关。

以 mixin 类形式提供方法，挂载到主 App 类上（方法内直接使用 self 的状态与锁）。
"""

import threading
import traceback
from pathlib import Path
from tkinter import messagebox

from .config import DEFAULT_DB_DIR
from .utils import strip_member_count, normalize_text_content


class DatabaseMixin:
    """提供与微信消息数据库交互的所有方法。"""

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
        """返回 (username, 展示名)。"""
        from .utils import strip_member_count

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
        return db.get_nickname(username) or username

    def _load_session_rows(self):
        if self.db is None:
            self._open_db()
        with self._db_lock:
            db = self.db
            sessions = db.get_sessions(limit=80) or []
            usernames = [s.get("username") or "" for s in sessions]
            from .utils import strip_member_count
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
        """一次性查 contact.db 批量解析展示名。"""
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