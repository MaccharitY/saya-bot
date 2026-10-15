"""Mock adapter 与 OneBot v11 解析的单元测试。

零依赖（标准库 unittest）。运行：
    cd project_main && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.adapter.mock import MockAdapter  # noqa: E402
from saya.adapter.onebot11 import parse_event  # noqa: E402
from saya.domain import IncomingMessage, OutgoingMessage  # noqa: E402


def _collect(adapter: MockAdapter) -> list[IncomingMessage]:
    return list(adapter.events())


class ParseEventTest(unittest.TestCase):
    def test_meta_event_is_dropped(self):
        raw = {"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 99999}
        self.assertIsNone(parse_event(raw))

    def test_unknown_message_type_is_dropped(self):
        self.assertIsNone(parse_event({"post_type": "message", "message_type": "guild"}))

    def test_group_message_without_group_id_is_rejected(self):
        raw = {"post_type": "message", "message_type": "group", "user_id": 1, "message": []}
        with self.assertRaises(ValueError):
            parse_event(raw)

    def test_plain_string_message_is_tolerated(self):
        raw = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 42,
            "message": "纯字符串形式",
        }
        message = parse_event(raw)
        assert message is not None
        self.assertEqual(message.text, "纯字符串形式")

    def test_sender_name_is_sanitised(self):
        """昵称是用户可控的：带换行会破坏提示词排版，超长会白烧 token。"""
        raw = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1,
            "user_id": 2,
            "message": [],
            "sender": {"nickname": "坏\n名\n字" + "长" * 100},
        }
        message = parse_event(raw)
        assert message is not None
        self.assertNotIn("\n", message.sender_name)
        self.assertLessEqual(len(message.sender_name), 32)

    def test_missing_sender_is_tolerated(self):
        raw = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1,
            "user_id": 2,
            "message": [],
        }
        message = parse_event(raw)
        assert message is not None
        self.assertEqual(message.sender_name, "")


class MockAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MockAdapter()
        self.messages = _collect(self.adapter)

    def test_only_message_events_survive(self):
        self.assertEqual(len(self.adapter.load_raw_events()), 5)
        self.assertEqual(len(self.messages), 4)

    def test_plain_group_text(self):
        first = self.messages[0]
        self.assertEqual(first.source, "group")
        self.assertEqual(first.group_id, 123456789)
        self.assertEqual(first.text, "今天天气不错")
        self.assertFalse(first.mentioned_self)
        self.assertEqual(first.images, ())
        self.assertIsNotNone(first.received_at)

    def test_at_me_with_image(self):
        second = self.messages[1]
        self.assertTrue(second.mentioned_self)
        self.assertEqual(len(second.images), 1)
        self.assertTrue(second.images[0].url.startswith("https://"))
        self.assertIn("[@我]", second.text)
        self.assertIn("[图片]", second.text)

    def test_private_message(self):
        third = self.messages[2]
        self.assertEqual(third.source, "private")
        self.assertIsNone(third.group_id)
        self.assertEqual(third.text, "在吗")

    def test_at_all_is_not_mentioning_me(self):
        fourth = self.messages[3]
        self.assertFalse(fourth.mentioned_self)
        self.assertIn("[@all]", fourth.text)
        self.assertIn("[表情]", fourth.text)

    def test_sender_name_prefers_group_card(self):
        """群里大家认的是名片，不是昵称。"""
        second = self.messages[1]
        self.assertEqual(second.sender_name, "乙")

    def test_sender_name_falls_back_to_nickname(self):
        first = self.messages[0]
        self.assertEqual(first.sender_name, "群友甲")


class EchoSmokeTest(unittest.TestCase):
    """M0 的端到端最小闭环：收消息 → 回一条 → send 被记录。"""

    def test_echo_round_trip(self):
        adapter = MockAdapter()

        for message in adapter.events():
            adapter.send(
                OutgoingMessage(
                    source=message.source,
                    target_id=message.group_id or message.user_id,
                    text=f"echo: {message.text}",
                )
            )
        self.assertEqual(len(adapter.sent), 4)
        self.assertEqual(adapter.sent[0].target_id, 123456789)
        self.assertEqual(adapter.sent[0].text, "echo: 今天天气不错")


if __name__ == "__main__":
    unittest.main()
