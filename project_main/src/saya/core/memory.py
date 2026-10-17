"""记忆（FR-7）。

分三层，**存储与注入严格分开** —— 这是 token 账的关键：

    存储（便宜，SQLite）          注入（贵，进提示词）
    ─────────────────────        ──────────────────────────
    每个会话 50 条原始消息  →     最近 10 条
    每个会话 20 个话题概述  →     当前话题那一条
    成员清单（QQ 号当索引） →     只查**正在说话的那个人**的详情

**"会话"既包括群，也包括私聊**：

    g:<群号>      群聊
    p:<对方QQ>    私聊

⚠️ 之前只记群消息，结果私聊里它**记不住上一条**（实测："你上一条我没看着"）——
而"跟某个人聊过之后记住他"恰恰是记忆最该管的事。现已按会话统一。

**为什么用 QQ 号当索引**：清单里只放 QQ 号与显示名（很便宜），
摘要、活跃度这些贵的字段**按需查**。不认识的成员一个 token 都不占。
这是"记忆 300 人但不炸上下文"的关键。

**活跃度按"每话题一次"计**：同一个人在一个话题里说 100 句也只 +1。
这样"话痨"不会因为刷屏霸榜，真正**参与了很多话题**的人才会排前面。

⚠️ 记忆内容全是群友说过的话，**注入提示词时必须过 `guard.wrap_history`** ——
否则就是一条存储型注入通道（昨天留一句话，今天变成指令）。见 guard.py。
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from saya.domain import IncomingMessage

# 默认上限。**都可配置** —— 这些数字是拍的起点，真实使用后再调。
DEFAULT_MESSAGES_PER_SIDE = 50  # 每个会话里，**她 / 其他人各留**这么多条
DEFAULT_TOPICS_PER_SESSION = 20  # 每个会话保留多少个话题概述
DEFAULT_MEMBERS_KEPT = 300  # 成员榜保留多少个
DEFAULT_MEMBERS_WITH_NOTE = 100  # 前多少名保留摘要
DEFAULT_TOPIC_GAP_MINUTES = 30  # 隔多久算新话题
DEFAULT_RECENT = 20  # `recent()` 不给 limit 时的条数
DEFAULT_IMAGES_PER_SESSION = 15  # 每个会话记着多少张最近发过的图

# ⚠️ **"注入时带几条"不归这里管** —— 那是 `core/context.py` 按字数预算定的
# （`RECENT_FETCH` 取候选，再按预算裁）。这里只管**存储**侧的裁剪。
# 以前这里有个 `recent_inject` 参数，现在注入早就不看它了，名字骗人，已删。


def session_key(message: IncomingMessage) -> str | None:
    """会话标识。群与私聊各成一路，互不串。"""
    if message.source == "group" and message.group_id is not None:
        return f"g:{message.group_id}"
    if message.source == "private":
        return f"p:{message.user_id}"
    return None


@dataclass(frozen=True, slots=True)
class RecentMessage:
    qq: int
    name: str
    text: str
    at: datetime
    from_bot: bool = False


@dataclass(frozen=True, slots=True)
class MemberInfo:
    qq: int
    name: str
    activity: int
    note: str
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class TopicInfo:
    id: int
    title: str
    summary: str
    started_at: datetime
    last_at: datetime


@dataclass(frozen=True, slots=True)
class RememberedImage:
    """群里最近发过的一张图。`path` 是本地缓存里的位置。"""

    qq: int
    name: str
    path: str
    at: datetime


_SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS members (
    qq         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL DEFAULT '',
    activity   INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT    NOT NULL,
    last_seen  TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS topics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session    TEXT    NOT NULL,
    title      TEXT    NOT NULL DEFAULT '',
    summary    TEXT    NOT NULL DEFAULT '',
    started_at TEXT    NOT NULL,
    last_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session  TEXT    NOT NULL,
    qq       INTEGER NOT NULL,
    name     TEXT    NOT NULL DEFAULT '',
    text     TEXT    NOT NULL,
    at       TEXT    NOT NULL,
    topic_id INTEGER,
    from_bot INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topic_speakers (
    topic_id INTEGER NOT NULL,
    qq       INTEGER NOT NULL,
    PRIMARY KEY (topic_id, qq)
);

-- 群里最近发过的图。**只是记着，不主动喂给模型** ——
-- 用途是"刚才那张图是什么"：有人先发了图（机器人没被 @ 所以没看），
-- 过一会儿才 @ 它问。没有这张表，它只能答"图没到我这儿"。
CREATE TABLE IF NOT EXISTS images (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT    NOT NULL,
    qq      INTEGER NOT NULL,
    name    TEXT    NOT NULL DEFAULT '',
    path    TEXT    NOT NULL,
    at      TEXT    NOT NULL
);
"""

# ⚠️ 索引**必须等迁移跑完再建** —— 老库的 messages 表还没有 session 列，
# 先建索引会直接报 "no such column: session"。实测撞过。
_SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_topics_session ON topics(session, last_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session, id DESC);
CREATE INDEX IF NOT EXISTS idx_speakers_qq ON topic_speakers(qq);
CREATE INDEX IF NOT EXISTS idx_images_session ON images(session, id DESC);
"""


class Memory:
    def __init__(
        self,
        path: Path | str,
        *,
        messages_per_side: int = DEFAULT_MESSAGES_PER_SIDE,
        topics_per_session: int = DEFAULT_TOPICS_PER_SESSION,
        members_kept: int = DEFAULT_MEMBERS_KEPT,
        members_with_note: int = DEFAULT_MEMBERS_WITH_NOTE,
        topic_gap_minutes: int = DEFAULT_TOPIC_GAP_MINUTES,
        images_per_session: int = DEFAULT_IMAGES_PER_SESSION,
    ) -> None:
        self._path = Path(path)
        self._messages_per_side = messages_per_side
        self._topics_per_session = topics_per_session
        self._members_kept = members_kept
        self._members_with_note = members_with_note
        self._topic_gap = timedelta(minutes=topic_gap_minutes)
        self._images_per_session = images_per_session

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self._path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA_TABLES)
        self._migrate()
        self._db.executescript(_SCHEMA_INDEXES)
        self._db.commit()

    def _migrate(self) -> None:
        """把老库（只有 group_id）升到新结构（session）。

        老库里的行全是群消息，所以 `g:<group_id>` 就是它们的 session。
        **数据不丢** —— 迁移比"删库重来"诚实。
        """
        for table in ("messages", "topics"):
            columns = {
                row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")
            }
            if "session" in columns or "group_id" not in columns:
                continue
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN session TEXT")
            self._db.execute(
                f"UPDATE {table} SET session = 'g:' || group_id WHERE session IS NULL"
            )

        self._drop_legacy_group_id()

    def _drop_legacy_group_id(self) -> None:
        """把老列 group_id 彻底除掉。

        ⚠️ **必须除，不能留着**：它带 `NOT NULL`，而新代码不再写它 ——
        留着的话**每次插入都会违反非空约束，整个记忆库从此写不进任何东西**。

        这个坑只有拿**真·老库**升级才会撞到（全新建的库完全正常），
        所以配了专门的回归测试 `test_old_database_migrates_and_keeps_writing`。
        """
        for table in ("messages", "topics"):
            columns = {
                row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")
            }
            if "session" not in columns or "group_id" not in columns:
                continue

            # 先删掉建在它上面的索引 —— 有索引时 DROP COLUMN 会失败。
            # PRAGMA 里 origin='c' 才是我们自己 CREATE INDEX 建的，
            # 'pk'/'u' 是主键/唯一约束自动生成的，不能动。
            for index in self._db.execute(f"PRAGMA index_list({table})").fetchall():
                if index["origin"] == "c":
                    self._db.execute(f"DROP INDEX IF EXISTS {index['name']}")

            self._db.execute(f"ALTER TABLE {table} DROP COLUMN group_id")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- 写入 ----

    def remember(self, message: IncomingMessage) -> None:
        """记录一条消息：**群与私聊都记**。

        私聊里记不住上一条，正是之前那个"你上一条我没看着"的成因 ——
        而"跟某个人聊过之后记住他"恰恰是记忆最该管的事。
        """
        session = session_key(message)
        if session is None:
            return
        self.record(
            session=session,
            qq=message.user_id,
            name=message.sender_name,
            text=message.text,
            at=message.received_at or datetime.now(),
        )

    def record(
        self,
        *,
        session: str,
        qq: int,
        name: str,
        text: str,
        at: datetime,
        from_bot: bool = False,
    ) -> None:
        stamp = at.isoformat(timespec="seconds")
        with self._lock:
            topic_id = self._current_topic_id(session, at)
            self._db.execute(
                "INSERT INTO messages (session, qq, name, text, at, topic_id, from_bot)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session, qq, name, text, stamp, topic_id, int(from_bot)),
            )

            if not from_bot:
                self._touch_member(qq, name, stamp)
                # 活跃度：**同一个话题里第一次发言才 +1**
                first_time = self._db.execute(
                    "INSERT OR IGNORE INTO topic_speakers (topic_id, qq) VALUES (?, ?)",
                    (topic_id, qq),
                ).rowcount
                if first_time:
                    self._db.execute(
                        "UPDATE members SET activity = activity + 1 WHERE qq = ?", (qq,)
                    )

            self._prune(session)
            self._db.commit()

    def _current_topic_id(self, session: str, at: datetime) -> int:
        """没有开着的（或离上一条太久的）话题就新开一个。"""
        row = self._db.execute(
            "SELECT id, last_at FROM topics WHERE session = ? ORDER BY last_at DESC LIMIT 1",
            (session,),
        ).fetchone()

        if row is not None:
            last_at = datetime.fromisoformat(row["last_at"])
            if at - last_at <= self._topic_gap:
                self._db.execute(
                    "UPDATE topics SET last_at = ? WHERE id = ?",
                    (at.isoformat(timespec="seconds"), row["id"]),
                )
                return int(row["id"])

        cursor = self._db.execute(
            "INSERT INTO topics (session, started_at, last_at) VALUES (?, ?, ?)",
            (session, at.isoformat(timespec="seconds"), at.isoformat(timespec="seconds")),
        )
        return int(cursor.lastrowid or 0)

    def _touch_member(self, qq: int, name: str, stamp: str) -> None:
        self._db.execute(
            """
            INSERT INTO members (qq, name, first_seen, last_seen) VALUES (?, ?, ?, ?)
            ON CONFLICT(qq) DO UPDATE SET
                name      = CASE WHEN excluded.name <> '' THEN excluded.name ELSE members.name END,
                last_seen = excluded.last_seen
            """,
            (qq, name, stamp, stamp),
        )

    def _prune(self, session: str) -> None:
        """存新删旧。上限都在上面几个 DEFAULT_* 里。"""
        # ⚠️ **按说话人分额，不是总条数封顶**（2026-10-17 改）。
        #
        # 原来是"每个会话留最新的 N 条"，**不分谁说的**。后果很具体：
        # **话多的一方把格子占满，话少的一方整块消失** ——
        # 实测 `g:1031477964` 里 50 条**全是别人的**，她一条都没有。
        # 于是她完全想不起自己说过什么（所有者原话："只记得别人的话"）。
        #
        # 现在她与别人**各留 N 条**，谁也别想挤掉谁。
        self._db.execute(
            """
            DELETE FROM messages
            WHERE session = ?
              AND id NOT IN (
                  SELECT id FROM (
                      SELECT id FROM messages
                      WHERE session = ? AND from_bot = 1
                      ORDER BY id DESC LIMIT ?
                  )
                  UNION
                  SELECT id FROM (
                      SELECT id FROM messages
                      WHERE session = ? AND from_bot = 0
                      ORDER BY id DESC LIMIT ?
                  )
              )
            """,
            (session, session, self._messages_per_side, session, self._messages_per_side),
        )

        stale = self._db.execute(
            """
            SELECT id FROM topics WHERE session = ? AND id NOT IN (
                SELECT id FROM topics WHERE session = ? ORDER BY last_at DESC LIMIT ?
            )
            """,
            (session, session, self._topics_per_session),
        ).fetchall()
        for row in stale:
            self._db.execute("DELETE FROM topic_speakers WHERE topic_id = ?", (row["id"],))
            self._db.execute("DELETE FROM topics WHERE id = ?", (row["id"],))

        # 成员榜：只留活跃的前 N 个；摘要只留给前 M 个
        self._db.execute(
            """
            DELETE FROM members WHERE qq NOT IN (
                SELECT qq FROM members ORDER BY activity DESC, last_seen DESC LIMIT ?
            )
            """,
            (self._members_kept,),
        )
        self._db.execute(
            """
            UPDATE members SET note = '' WHERE qq NOT IN (
                SELECT qq FROM members ORDER BY activity DESC, last_seen DESC LIMIT ?
            )
            """,
            (self._members_with_note,),
        )

    def set_note(self, qq: int, note: str) -> None:
        """写/改某个人的摘要。**摘要由模型生成，属于贵操作，按需调用。**"""
        with self._lock:
            self._db.execute("UPDATE members SET note = ? WHERE qq = ?", (note[:500], qq))
            self._db.commit()

    # ---- 图片 ----

    def remember_image(
        self, *, session: str, qq: int, name: str, path: Path | str, at: datetime
    ) -> None:
        """记下"谁在什么时候发了哪张图"。

        ⚠️ **图片本身不喂给模型**，这里只建索引。真要看的时候由 pipeline 决定
        （见 `recent_images` 与 `pipeline._recalled_image`）。
        """
        with self._lock:
            self._db.execute(
                "INSERT INTO images (session, qq, name, path, at) VALUES (?, ?, ?, ?, ?)",
                (session, qq, name, str(path), at.isoformat(timespec="seconds")),
            )
            self._prune_images(session)
            self._db.commit()

    def recent_images(self, session: str, limit: int = DEFAULT_IMAGES_PER_SESSION):
        """这个会话里最近发过的图，**从新到旧**。"""
        rows = self._db.execute(
            "SELECT qq, name, path, at FROM images WHERE session = ?"
            " ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
        return [
            RememberedImage(
                qq=int(row["qq"]),
                name=str(row["name"]),
                path=str(row["path"]),
                at=datetime.fromisoformat(row["at"]),
            )
            for row in rows
        ]

    def _prune_images(self, session: str) -> None:
        self._db.execute(
            """
            DELETE FROM images WHERE session = ? AND id NOT IN (
                SELECT id FROM images WHERE session = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (session, session, self._images_per_session),
        )

    def set_topic_summary(self, topic_id: int, title: str, summary: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE topics SET title = ?, summary = ? WHERE id = ?",
                (title[:60], summary[:500], topic_id),
            )
            self._db.commit()

    # ---- 读取（注入用）----

    def recent(self, session: str, limit: int = DEFAULT_RECENT) -> list[RecentMessage]:
        rows = self._db.execute(
            "SELECT qq, name, text, at, from_bot FROM messages"
            " WHERE session = ? ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
        return [
            RecentMessage(
                qq=int(row["qq"]),
                name=str(row["name"]),
                text=str(row["text"]),
                at=datetime.fromisoformat(row["at"]),
                from_bot=bool(row["from_bot"]),
            )
            for row in reversed(rows)  # 倒序取，正序给模型
        ]

    def member(self, qq: int) -> MemberInfo | None:
        row = self._db.execute("SELECT * FROM members WHERE qq = ?", (qq,)).fetchone()
        if row is None:
            return None
        return MemberInfo(
            qq=int(row["qq"]),
            name=str(row["name"]),
            activity=int(row["activity"]),
            note=str(row["note"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
        )

    def current_topic(self, session: str) -> TopicInfo | None:
        row = self._db.execute(
            "SELECT * FROM topics WHERE session = ? ORDER BY last_at DESC LIMIT 1",
            (session,),
        ).fetchone()
        if row is None:
            return None
        return TopicInfo(
            id=int(row["id"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            last_at=datetime.fromisoformat(row["last_at"]),
        )

    def top_members(self, limit: int = 10) -> list[MemberInfo]:
        rows = self._db.execute(
            "SELECT * FROM members ORDER BY activity DESC, last_seen DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            MemberInfo(
                qq=int(row["qq"]),
                name=str(row["name"]),
                activity=int(row["activity"]),
                note=str(row["note"]),
                last_seen=datetime.fromisoformat(row["last_seen"]),
            )
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self._db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "members": count("members"),
            "messages": count("messages"),
            "topics": count("topics"),
            "images": count("images"),
        }
