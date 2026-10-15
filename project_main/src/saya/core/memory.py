"""记忆（FR-7）。

分三层，**存储与注入严格分开** —— 这是 token 账的关键：

    存储（便宜，SQLite）          注入（贵，进提示词）
    ─────────────────────        ──────────────────────────
    每群 50 条原始消息      →     最近 10 条
    每群 20 个话题概述      →     当前话题那一条
    成员清单（QQ 号当索引） →     只查**正在说话的那个人**的详情

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
DEFAULT_MESSAGES_PER_GROUP = 50  # 每群精确保留多少条
DEFAULT_TOPICS_PER_GROUP = 20  # 每群保留多少个话题概述
DEFAULT_MEMBERS_KEPT = 300  # 成员榜保留多少个
DEFAULT_MEMBERS_WITH_NOTE = 100  # 前多少名保留摘要
DEFAULT_RECENT_INJECT = 10  # 注入提示词时带最近几条
DEFAULT_TOPIC_GAP_MINUTES = 30  # 隔多久算新话题


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


_SCHEMA = """
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
    group_id   INTEGER NOT NULL,
    title      TEXT    NOT NULL DEFAULT '',
    summary    TEXT    NOT NULL DEFAULT '',
    started_at TEXT    NOT NULL,
    last_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_group ON topics(group_id, last_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    qq       INTEGER NOT NULL,
    name     TEXT    NOT NULL DEFAULT '',
    text     TEXT    NOT NULL,
    at       TEXT    NOT NULL,
    topic_id INTEGER,
    from_bot INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, id DESC);

CREATE TABLE IF NOT EXISTS topic_speakers (
    topic_id INTEGER NOT NULL,
    qq       INTEGER NOT NULL,
    PRIMARY KEY (topic_id, qq)
);
CREATE INDEX IF NOT EXISTS idx_speakers_qq ON topic_speakers(qq);
"""


class Memory:
    def __init__(
        self,
        path: Path | str,
        *,
        messages_per_group: int = DEFAULT_MESSAGES_PER_GROUP,
        topics_per_group: int = DEFAULT_TOPICS_PER_GROUP,
        members_kept: int = DEFAULT_MEMBERS_KEPT,
        members_with_note: int = DEFAULT_MEMBERS_WITH_NOTE,
        recent_inject: int = DEFAULT_RECENT_INJECT,
        topic_gap_minutes: int = DEFAULT_TOPIC_GAP_MINUTES,
    ) -> None:
        self._path = Path(path)
        self._messages_per_group = messages_per_group
        self._topics_per_group = topics_per_group
        self._members_kept = members_kept
        self._members_with_note = members_with_note
        self._recent_inject = recent_inject
        self._topic_gap = timedelta(minutes=topic_gap_minutes)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self._path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- 写入 ----

    def remember(self, message: IncomingMessage) -> None:
        """记录一条群消息：归入话题、更新成员、算活跃度、按上限裁剪。"""
        if message.source != "group" or message.group_id is None:
            return
        self.record(
            group_id=message.group_id,
            qq=message.user_id,
            name=message.sender_name,
            text=message.text,
            at=message.received_at or datetime.now(),
        )

    def record(
        self,
        *,
        group_id: int,
        qq: int,
        name: str,
        text: str,
        at: datetime,
        from_bot: bool = False,
    ) -> None:
        stamp = at.isoformat(timespec="seconds")
        with self._lock:
            topic_id = self._current_topic_id(group_id, at)
            self._db.execute(
                "INSERT INTO messages (group_id, qq, name, text, at, topic_id, from_bot)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (group_id, qq, name, text, stamp, topic_id, int(from_bot)),
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

            self._prune(group_id)
            self._db.commit()

    def _current_topic_id(self, group_id: int, at: datetime) -> int:
        """没有开着的（或离上一条太久的）话题就新开一个。"""
        row = self._db.execute(
            "SELECT id, last_at FROM topics WHERE group_id = ? ORDER BY last_at DESC LIMIT 1",
            (group_id,),
        ).fetchone()

        if row is not None:
            last_at = datetime.fromisoformat(row["last_at"])
            if at - last_at <= self._topic_gap:
                self._db.execute(
                    "UPDATE topics SET last_at = ? WHERE id = ?", (at.isoformat(timespec="seconds"), row["id"])
                )
                return int(row["id"])

        cursor = self._db.execute(
            "INSERT INTO topics (group_id, started_at, last_at) VALUES (?, ?, ?)",
            (group_id, at.isoformat(timespec="seconds"), at.isoformat(timespec="seconds")),
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

    def _prune(self, group_id: int) -> None:
        """存新删旧。上限都在上面几个 DEFAULT_* 里。"""
        self._db.execute(
            """
            DELETE FROM messages WHERE group_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE group_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (group_id, group_id, self._messages_per_group),
        )

        stale = self._db.execute(
            """
            SELECT id FROM topics WHERE group_id = ? AND id NOT IN (
                SELECT id FROM topics WHERE group_id = ? ORDER BY last_at DESC LIMIT ?
            )
            """,
            (group_id, group_id, self._topics_per_group),
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

    def set_topic_summary(self, topic_id: int, title: str, summary: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE topics SET title = ?, summary = ? WHERE id = ?",
                (title[:60], summary[:500], topic_id),
            )
            self._db.commit()

    # ---- 读取（注入用）----

    def recent(self, group_id: int, limit: int | None = None) -> list[RecentMessage]:
        limit = limit or self._recent_inject
        rows = self._db.execute(
            "SELECT qq, name, text, at, from_bot FROM messages"
            " WHERE group_id = ? ORDER BY id DESC LIMIT ?",
            (group_id, limit),
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

    def current_topic(self, group_id: int) -> TopicInfo | None:
        row = self._db.execute(
            "SELECT * FROM topics WHERE group_id = ? ORDER BY last_at DESC LIMIT 1",
            (group_id,),
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
        }
