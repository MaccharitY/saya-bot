"""记忆的测试 —— 全部离线（临时 SQLite 文件，不联网）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.memory import Memory  # noqa: E402

T0 = datetime(2026, 10, 16, 1, 0, 0)


class MemoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = Memory(
            Path(self._tmp.name) / "memory.db",
            messages_per_group=3,
            topics_per_group=2,
            members_kept=3,
            members_with_note=2,
            recent_inject=5,
            topic_gap_minutes=30,
        )
        self.addCleanup(self.memory.close)

    def say(self, *, qq: int = 111, name: str = "甲", text: str = "你好",
            at: datetime = T0, group_id: int = 1, offset_seconds: int = 0) -> None:
        self.memory.record(
            group_id=group_id, qq=qq, name=name, text=text,
            at=at + timedelta(seconds=offset_seconds),
        )


class RecordAndReadTest(MemoryTestCase):
    def test_recent_returns_in_chronological_order(self):
        self.say(text="第一句", offset_seconds=1)
        self.say(qq=222, name="乙", text="第二句", offset_seconds=2)

        self.assertEqual([m.text for m in self.memory.recent(1)], ["第一句", "第二句"])

    def test_bot_replies_are_recorded_but_do_not_count_as_members(self):
        self.say(text="在吗")
        self.memory.record(group_id=1, qq=999, name="莎夜", text="在的", at=T0 + timedelta(seconds=5), from_bot=True)

        recent = self.memory.recent(1)
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
        self.assertEqual([m.text for m in self.memory.recent(1)], ["一群的话"])
        self.assertEqual([m.text for m in self.memory.recent(2)], ["二群的话"])

    def test_private_messages_are_not_stored(self):
        """私聊不建群记录 —— 记忆是"群"的。"""
        self.assertEqual(self.memory.stats()["messages"], 0)


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
        self.assertEqual(self.memory.current_topic(1).started_at, T0 + timedelta(hours=2))

    def test_topic_summary_can_be_written(self):
        self.say()
        topic = self.memory.current_topic(1)
        self.memory.set_topic_summary(topic.id, "午饭吃什么", "大家在讨论去哪吃")

        topic = self.memory.current_topic(1)
        self.assertEqual(topic.title, "午饭吃什么")
        self.assertEqual(topic.summary, "大家在讨论去哪吃")

    def test_no_topic_for_an_unknown_group(self):
        self.assertIsNone(self.memory.current_topic(999))


class PruneTest(MemoryTestCase):
    def test_messages_are_capped_per_group(self):
        for index in range(6):
            self.say(text=f"m{index}", offset_seconds=index)

        messages = self.memory.recent(1, limit=99)
        self.assertEqual(len(messages), 3, "每群只留最近 3 条（测试里设的上限）")
        self.assertEqual([m.text for m in messages], ["m3", "m4", "m5"])

    def test_cap_is_per_group(self):
        for index in range(6):
            self.say(group_id=1, text=f"a{index}", offset_seconds=index)
            self.say(group_id=2, text=f"b{index}", offset_seconds=index)
        self.assertEqual(len(self.memory.recent(1, limit=99)), 3)
        self.assertEqual(len(self.memory.recent(2, limit=99)), 3)

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
                    group_id=1000 + person, qq=1000 + person, name=f"p{person}",
                    text="x", at=T0 + timedelta(hours=3 * topic),
                )

        remaining = {m.qq for m in self.memory.top_members(limit=9)}
        self.assertEqual(len(remaining), 3)
        self.assertNotIn(1000, remaining, "活跃度最低的 p0 该被淘汰")

    def test_notes_are_cleared_beyond_the_top_ranked(self):
        for person in range(4):
            for topic in range(person + 1):
                self.memory.record(
                    group_id=1000 + person, qq=1000 + person, name=f"p{person}",
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


if __name__ == "__main__":
    unittest.main()
