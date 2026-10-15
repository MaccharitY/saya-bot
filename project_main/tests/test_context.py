"""上下文拼接的测试 —— 记忆再多也不能全进提示词。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.context import MAX_CHARS, build_history  # noqa: E402
from saya.core.memory import Memory  # noqa: E402
from saya.domain import IncomingMessage  # noqa: E402

T0 = datetime(2026, 10, 16, 1, 0, 0)


def group_message(*, qq: int = 111, name: str = "甲", text: str = "在吗",
                  group_id: int = 1) -> IncomingMessage:
    return IncomingMessage(
        source="group", group_id=group_id, user_id=qq, sender_name=name, text=text
    )


class ContextTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = Memory(Path(self._tmp.name) / "m.db")
        self.addCleanup(self.memory.close)

    def say(self, *, qq: int = 111, name: str = "甲", text: str = "你好",
            at: datetime = T0, group_id: int = 1, from_bot: bool = False) -> None:
        self.memory.record(
            session=f"g:{group_id}", qq=qq, name=name, text=text, at=at, from_bot=from_bot
        )

    def build(self, message: IncomingMessage, *, now: datetime | None = None) -> str:
        return build_history(self.memory, message, bot_qq=999, bot_name="莎夜", now=now)


class BuildHistoryTest(ContextTestCase):
    def test_empty_memory_produces_nothing(self):
        self.assertEqual(self.build(group_message()), "")

    def test_recent_messages_are_included(self):
        self.say(qq=222, name="乙", text="今天天气不错")
        text = self.build(group_message())
        self.assertIn("群里最近说过的话", text)
        self.assertIn("乙：今天天气不错", text)

    def test_bot_replies_show_under_its_own_name(self):
        """它自己说过的话要用它的名字出现 —— 否则下次会重复自己。"""
        self.say(qq=999, name="", text="在的", from_bot=True)
        text = self.build(group_message())
        self.assertIn("莎夜：在的", text)

    def test_speaker_section_is_about_the_current_speaker_only(self):
        """**这是省 token 的关键**：只查正在说话的人，不是把成员名单倒进去。"""
        self.say(qq=111, name="甲", text="a")
        self.say(qq=222, name="乙", text="b")
        self.say(qq=333, name="丙", text="c")

        text = self.build(group_message(qq=222, name="乙"))

        self.assertIn("正在说话的人", text)
        self.assertIn("乙", text.split("正在说话的人")[1])
        self.assertNotIn("丙", text.split("正在说话的人")[1], "别人的资料不该出现在这里")

    def test_unknown_speaker_gets_no_section(self):
        text = self.build(group_message(qq=8888, name="生面孔"))
        self.assertNotIn("正在说话的人", text)

    def test_member_note_is_included_when_present(self):
        self.say(qq=111, name="甲")
        self.memory.set_note(111, "喜欢音游，自称手残")
        text = self.build(group_message(qq=111, name="甲"))
        self.assertIn("喜欢音游", text)

    def test_current_topic_is_included(self):
        self.say()
        topic = self.memory.current_topic("g:1")
        self.memory.set_topic_summary(topic.id, "午饭", "大家在挑店")

        text = self.build(group_message())
        self.assertIn("当前话题", text)
        self.assertIn("午饭", text)
        self.assertIn("大家在挑店", text)

    def test_newlines_in_messages_are_collapsed(self):
        """多行消息会把上下文撑得很长，压成一行。"""
        self.say(text="第一行\n第二行\n第三行")
        text = self.build(group_message())
        self.assertNotIn("第一行\n第二行", text)

    def test_private_message_gets_no_group_sections(self):
        self.say(qq=111, name="甲", text="群里说的话")
        private = IncomingMessage(source="private", user_id=111, sender_name="甲", text="私聊")

        text = self.build(private)
        self.assertNotIn("群里最近说过的话", text, "私聊时不该把群聊记录倒进来")

    def test_private_history_is_included(self):
        """⚠️ 回归用例：私聊曾经完全没有上下文，它只能答"你上一条我没看着"。"""
        self.memory.remember(
            IncomingMessage(source="private", user_id=222, sender_name="甲", text="昨天说的那件事")
        )
        private = IncomingMessage(source="private", user_id=222, sender_name="甲", text="那个怎么样了")

        text = self.build(private)
        self.assertIn("你们之前聊过", text)
        self.assertIn("昨天说的那件事", text)

    def test_one_persons_dm_is_not_visible_in_anothers(self):
        self.memory.remember(
            IncomingMessage(source="private", user_id=222, sender_name="甲", text="甲的悄悄话")
        )
        other = IncomingMessage(source="private", user_id=333, sender_name="乙", text="在吗")

        self.assertNotIn("甲的悄悄话", self.build(other), "别人的私聊不该被倒进来")

    def test_block_is_capped(self):
        for index in range(40):
            self.say(text="很长的内容" * 20, at=T0 + timedelta(seconds=index))

        self.assertLessEqual(len(self.build(group_message())), MAX_CHARS + 40)

    def test_relative_time_is_used(self):
        self.say(qq=111, name="甲")
        text = self.build(group_message(qq=111), now=T0 + timedelta(minutes=5))
        self.assertIn("5 分钟前", text)


if __name__ == "__main__":
    unittest.main()
