"""记忆的测试 —— 全部离线（临时 SQLite 文件，不联网）。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.memory import Memory  # noqa: E402
from saya.domain import IncomingMessage  # noqa: E402

T0 = datetime(2026, 10, 16, 1, 0, 0)


class MemoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = Memory(
            Path(self._tmp.name) / "memory.db",
            messages_per_session=3,
            topics_per_session=2,
            members_kept=3,
            members_with_note=2,
            recent_inject=5,
            topic_gap_minutes=30,
        )
        self.addCleanup(self.memory.close)

    def say(self, *, qq: int = 111, name: str = "甲", text: str = "你好",
            at: datetime = T0, group_id: int = 1, offset_seconds: int = 0) -> None:
        self.memory.record(
            session=f"g:{group_id}", qq=qq, name=name, text=text,
            at=at + timedelta(seconds=offset_seconds),
        )


class RecordAndReadTest(MemoryTestCase):
    def test_recent_returns_in_chronological_order(self):
        self.say(text="第一句", offset_seconds=1)
        self.say(qq=222, name="乙", text="第二句", offset_seconds=2)

        self.assertEqual([m.text for m in self.memory.recent("g:1")], ["第一句", "第二句"])

    def test_bot_replies_are_recorded_but_do_not_count_as_members(self):
        self.say(text="在吗")
        self.memory.record(session="g:1", qq=999, name="莎夜", text="在的", at=T0 + timedelta(seconds=5), from_bot=True)

        recent = self.memory.recent("g:1")
        self.assertEqual(len(recent), 2)
        self.assertTrue(recent[-1].from_bot)
        self.assertIsNone(self.memory.member(999), "机器人自己不该进成员榜")

    def test_member_name_is_updated_but_never_blanked(self):
        self.say(name="旧名字")
        self.say(name="新名字", offset_seconds=1)
        self.assertEqual(self.memory.member(111).name, "新名字")

        self.say(name="", offset_seconds=2)  # 没带名字的一次
        self.assertEqual(self.memory.member(111).name, "新名字", "空名字不该把已有名字抹掉")

    def test_groups_do_not_leak_into_each_other(self):
        self.say(group_id=1, text="一群的话")
        self.say(group_id=2, text="二群的话")
        self.assertEqual([m.text for m in self.memory.recent("g:1")], ["一群的话"])
        self.assertEqual([m.text for m in self.memory.recent("g:2")], ["二群的话"])

    def test_private_messages_are_recorded_too(self):
        """⚠️ 回归用例：私聊曾经完全不记，导致它答"你上一条我没看着"。

        "跟某个人聊过之后记住他"恰恰是记忆最该管的事。
        """
        message = IncomingMessage(
            source="private", user_id=222, sender_name="甲", text="在吗"
        )
        self.memory.remember(message)

        self.assertEqual(self.memory.stats()["messages"], 1)
        self.assertEqual([m.text for m in self.memory.recent("p:222")], ["在吗"])

    def test_group_and_private_sessions_do_not_mix(self):
        """群里说的话不该出现在私聊的上下文里，反之亦然。"""
        self.say(group_id=1, text="群里的话")
        self.memory.remember(
            IncomingMessage(source="private", user_id=222, sender_name="甲", text="私聊的话")
        )

        self.assertEqual([m.text for m in self.memory.recent("g:1")], ["群里的话"])
        self.assertEqual([m.text for m in self.memory.recent("p:222")], ["私聊的话"])

    def test_two_people_dm_the_bot_separately(self):
        for user_id in (222, 333):
            self.memory.remember(
                IncomingMessage(
                    source="private", user_id=user_id, sender_name=f"u{user_id}", text=f"{user_id} 的话"
                )
            )
        self.assertEqual(len(self.memory.recent("p:222")), 1)
        self.assertEqual(self.memory.recent("p:222")[0].text, "222 的话")
        self.assertEqual(self.memory.recent("p:333")[0].text, "333 的话")


class ActivityTest(MemoryTestCase):
    def test_activity_counts_once_per_topic_not_per_message(self):
        """**这是活跃度的核心规则**：刷屏的人不该霸榜，参与话题多的人才该排前面。"""
        for index in range(5):
            self.say(text=f"刷屏第 {index} 句", offset_seconds=index)

        self.assertEqual(self.memory.member(111).activity, 1)

    def test_new_topic_after_the_gap_gives_another_point(self):
        self.say(text="第一轮", at=T0)
        self.say(text="两小时后", at=T0 + timedelta(hours=2))

        self.assertEqual(self.memory.member(111).activity, 2)
        self.assertEqual(self.memory.stats()["topics"], 2)

    def test_two_people_in_one_topic_each_get_one_point(self):
        self.say(qq=111, name="甲", offset_seconds=1)
        self.say(qq=222, name="乙", offset_seconds=2)

        self.assertEqual(self.memory.member(111).activity, 1)
        self.assertEqual(self.memory.member(222).activity, 1)


class TopicTest(MemoryTestCase):
    def test_current_topic_follows_the_latest(self):
        self.say(text="老话题", at=T0)
        self.say(text="新话题", at=T0 + timedelta(hours=2))
        self.assertEqual(self.memory.current_topic("g:1").started_at, T0 + timedelta(hours=2))

    def test_topic_summary_can_be_written(self):
        self.say()
        topic = self.memory.current_topic("g:1")
        self.memory.set_topic_summary(topic.id, "午饭吃什么", "大家在讨论去哪吃")

        topic = self.memory.current_topic("g:1")
        self.assertEqual(topic.title, "午饭吃什么")
        self.assertEqual(topic.summary, "大家在讨论去哪吃")

    def test_no_topic_for_an_unknown_group(self):
        self.assertIsNone(self.memory.current_topic("g:999"))


class PruneTest(MemoryTestCase):
    def test_messages_are_capped_per_group(self):
        for index in range(6):
            self.say(text=f"m{index}", offset_seconds=index)

        messages = self.memory.recent("g:1", limit=99)
        self.assertEqual(len(messages), 3, "每群只留最近 3 条（测试里设的上限）")
        self.assertEqual([m.text for m in messages], ["m3", "m4", "m5"])

    def test_cap_is_per_group(self):
        for index in range(6):
            self.say(group_id=1, text=f"a{index}", offset_seconds=index)
            self.say(group_id=2, text=f"b{index}", offset_seconds=index)
        self.assertEqual(len(self.memory.recent("g:1", limit=99)), 3)
        self.assertEqual(len(self.memory.recent("g:2", limit=99)), 3)

    def test_topics_are_capped_and_their_speakers_cleaned_up(self):
        for index in range(4):
            self.say(text=f"t{index}", at=T0 + timedelta(hours=3 * index))

        self.assertEqual(self.memory.stats()["topics"], 2)
        # 被删话题的参与者记录也该跟着走，否则会一直攒着
        orphans = self.memory._db.execute(
            "SELECT COUNT(*) AS n FROM topic_speakers WHERE topic_id NOT IN (SELECT id FROM topics)"
        ).fetchone()["n"]
        self.assertEqual(int(orphans), 0)

    def test_low_activity_members_are_dropped(self):
        """淘汰：活跃度低的先走（测试里只留 3 个）。"""
        for person in range(4):
            for topic in range(person + 1):
                self.memory.record(
                    session=f"g:{1000 + person}", qq=1000 + person, name=f"p{person}",
                    text="x", at=T0 + timedelta(hours=3 * topic),
                )

        remaining = {m.qq for m in self.memory.top_members(limit=9)}
        self.assertEqual(len(remaining), 3)
        self.assertNotIn(1000, remaining, "活跃度最低的 p0 该被淘汰")

    def test_notes_are_cleared_beyond_the_top_ranked(self):
        for person in range(4):
            for topic in range(person + 1):
                self.memory.record(
                    session=f"g:{1000 + person}", qq=1000 + person, name=f"p{person}",
                    text="x", at=T0 + timedelta(hours=3 * topic),
                )
                self.memory.set_note(1000 + person, f"{person} 的摘要")

        # 再写一条触发裁剪
        self.say(group_id=2000, qq=2000, name="trigger", text="x", at=T0)

        notes = {m.qq: m.note for m in self.memory.top_members(limit=9)}
        self.assertTrue(notes.get(1003), "活跃度最高的该留着摘要")
        self.assertEqual(notes.get(1001, ""), "", "排名靠后的摘要该被清掉")


class StatsTest(MemoryTestCase):
    def test_stats_reports_counts(self):
        self.say()
        stats = self.memory.stats()
        self.assertEqual(stats["messages"], 1)
        self.assertEqual(stats["members"], 1)
        self.assertEqual(stats["topics"], 1)


class LegacyMigrationTest(unittest.TestCase):
    """老库升级。

    ⚠️ 这条是**真·事故的回归用例**：老表里 `group_id` 是 `NOT NULL`，
    新代码不再写它。只"加一列 session"而不除老列的话，
    升级后的库**一条都插不进去**（`NOT NULL constraint failed`）——
    而全新建的库完全正常，所以普通测试全绿、真机上记忆却彻底停工。
    """

    OLD_SCHEMA = """
    CREATE TABLE members (
        qq INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
        activity INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL, last_at TEXT NOT NULL
    );
    CREATE INDEX idx_topics_group ON topics(group_id, last_at DESC);
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
        qq INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
        at TEXT NOT NULL, topic_id INTEGER, from_bot INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_messages_group ON messages(group_id, id DESC);
    CREATE TABLE topic_speakers (
        topic_id INTEGER NOT NULL, qq INTEGER NOT NULL, PRIMARY KEY (topic_id, qq)
    );
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "old.db"

        con = sqlite3.connect(self.path)
        con.executescript(self.OLD_SCHEMA)
        con.execute(
            "INSERT INTO messages (group_id, qq, name, text, at) VALUES (1, 111, '甲', '老消息', ?)",
            (T0.isoformat(timespec="seconds"),),
        )
        con.execute(
            "INSERT INTO topics (group_id, started_at, last_at) VALUES (1, ?, ?)",
            (T0.isoformat(timespec="seconds"), T0.isoformat(timespec="seconds")),
        )
        con.commit()
        con.close()

    def test_old_data_survives_with_group_session(self):
        memory = Memory(self.path)
        self.addCleanup(memory.close)
        recent = memory.recent("g:1")
        self.assertEqual([m.text for m in recent], ["老消息"], "老数据要保留，并归到 g:1")

    def test_can_still_write_after_migration(self):
        """**这条就是那个事故**：升级后必须还能写。"""
        memory = Memory(self.path)
        self.addCleanup(memory.close)
        memory.record(session="g:1", qq=222, name="乙", text="升级后写的新消息", at=T0)

        self.assertEqual(len(memory.recent("g:1", limit=99)), 2)

    def test_legacy_column_is_gone(self):
        memory = Memory(self.path)
        self.addCleanup(memory.close)
        columns = {
            row["name"] for row in memory._db.execute("PRAGMA table_info(messages)")
        }
        self.assertNotIn("group_id", columns)
        self.assertIn("session", columns)


if __name__ == "__main__":
    unittest.main()
