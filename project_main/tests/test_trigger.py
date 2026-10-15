"""触发判定（FR-2）的单元测试。零依赖，标准库 unittest。

    cd project_main && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.config import Config  # noqa: E402
from saya.core.trigger import DEDUP_MEMORY, Trigger  # noqa: E402
from saya.domain import IncomingMessage  # noqa: E402

NO_DOTENV = Path("不存在的文件.env")


def base_config(**overrides):
    config = Config.from_env(dotenv_path=NO_DOTENV)
    return replace(config, **overrides) if overrides else config


def group_message(
    text: str = "你好",
    *,
    mentioned: bool = False,
    message_id: str | None = "1",
    group_id: int = 123456789,
) -> IncomingMessage:
    return IncomingMessage(
        source="group",
        group_id=group_id,
        user_id=111,
        text=text,
        mentioned_self=mentioned,
        message_id=message_id,
    )


def private_message(text: str = "在吗", *, message_id: str = "p1") -> IncomingMessage:
    return IncomingMessage(
        source="private", user_id=222, text=text, message_id=message_id
    )


class FixedRandom:
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ModeTest(unittest.TestCase):
    def test_mention_mode_requires_being_mentioned(self):
        trigger = Trigger(base_config(trigger_mode="mention"))
        self.assertFalse(trigger.should_respond(group_message()).respond)
        self.assertTrue(trigger.should_respond(group_message(mentioned=True)).respond)

    def test_keyword_mode_matches_any_keyword(self):
        trigger = Trigger(base_config(trigger_mode="keyword", trigger_keywords=("莎夜", "在吗")))
        self.assertTrue(trigger.should_respond(group_message("莎夜今天怎么样")).respond)
        self.assertFalse(trigger.should_respond(group_message("今天天气不错")).respond)

    def test_random_mode_uses_probability(self):
        config = base_config(trigger_mode="random", reply_probability=0.5)
        self.assertTrue(Trigger(config, rng=FixedRandom(0.4)).should_respond(group_message()).respond)
        self.assertFalse(Trigger(config, rng=FixedRandom(0.6)).should_respond(group_message()).respond)

    def test_always_mode(self):
        trigger = Trigger(base_config(trigger_mode="always"))
        self.assertTrue(trigger.should_respond(group_message()).respond)

    def test_unknown_mode_is_a_loud_failure(self):
        trigger = Trigger(base_config(trigger_mode="乱写的"))
        with self.assertRaises(ValueError):
            trigger.should_respond(group_message())

    def test_reason_is_recorded_for_observability(self):
        trigger = Trigger(base_config(trigger_mode="mention"))
        self.assertEqual(trigger.should_respond(group_message()).reason, "skip:mention")
        self.assertEqual(
            trigger.should_respond(group_message(mentioned=True)).reason, "hit:mention"
        )


class PrivateMessageTest(unittest.TestCase):
    def test_private_always_responds_even_in_mention_mode(self):
        trigger = Trigger(base_config(trigger_mode="mention"))
        decision = trigger.should_respond(private_message())
        self.assertTrue(decision.respond)
        self.assertEqual(decision.reason, "private")


class GroupFilterTest(unittest.TestCase):
    def test_denylist_wins(self):
        trigger = Trigger(
            base_config(trigger_mode="always", group_denylist=(123456789,)),
        )
        decision = trigger.should_respond(group_message())
        self.assertFalse(decision.respond)
        self.assertEqual(decision.reason, "group_denied")

    def test_allowlist_restricts_to_listed_groups(self):
        trigger = Trigger(
            base_config(trigger_mode="always", group_allowlist=(999,)),
        )
        self.assertEqual(trigger.should_respond(group_message()).reason, "group_not_allowed")

    def test_empty_allowlist_allows_everyone(self):
        trigger = Trigger(base_config(trigger_mode="always", group_allowlist=()))
        self.assertTrue(trigger.should_respond(group_message()).respond)


class RateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.trigger = Trigger(
            base_config(trigger_mode="always", rate_limit_per_group_per_min=2),
            clock=self.clock,
        )

    def test_blocks_after_limit_reached(self):
        self.trigger.note_answered(group_message(message_id="1"))
        self.trigger.note_answered(group_message(message_id="2"))
        decision = self.trigger.should_respond(group_message(message_id="3"))
        self.assertFalse(decision.respond)
        self.assertEqual(decision.reason, "rate_limited")

    def test_window_slides(self):
        self.trigger.note_answered(group_message(message_id="1"))
        self.trigger.note_answered(group_message(message_id="2"))
        self.clock.now = 61.0
        self.assertTrue(self.trigger.should_respond(group_message(message_id="3")).respond)

    def test_zero_limit_blocks_everything(self):
        trigger = Trigger(base_config(trigger_mode="always", rate_limit_per_group_per_min=0))
        self.assertEqual(trigger.should_respond(group_message()).reason, "rate_limited")

    def test_limit_is_per_group(self):
        self.trigger.note_answered(group_message(message_id="1", group_id=1))
        self.trigger.note_answered(group_message(message_id="2", group_id=1))
        decision = self.trigger.should_respond(group_message(message_id="3", group_id=2))
        self.assertTrue(decision.respond)


class DedupTest(unittest.TestCase):
    def test_same_message_is_not_answered_twice(self):
        trigger = Trigger(base_config(trigger_mode="always", rate_limit_per_group_per_min=99))
        message = group_message(message_id="abc")
        self.assertTrue(trigger.should_respond(message).respond)
        trigger.note_answered(message)
        self.assertEqual(trigger.should_respond(message).reason, "duplicate")

    def test_private_messages_are_also_deduped(self):
        trigger = Trigger(base_config(trigger_mode="always"))
        message = private_message(message_id="p9")
        trigger.note_answered(message)
        self.assertEqual(trigger.should_respond(message).reason, "duplicate")

    def test_messages_without_id_are_not_deduped(self):
        trigger = Trigger(base_config(trigger_mode="always", rate_limit_per_group_per_min=99))
        trigger.note_answered(group_message(message_id=None))
        self.assertTrue(trigger.should_respond(group_message(message_id=None)).respond)

    def test_dedup_memory_is_bounded(self):
        # 限流要放得足够高，否则这里测的是限流而不是去重
        trigger = Trigger(
            base_config(trigger_mode="always", rate_limit_per_group_per_min=100_000)
        )
        trigger.note_answered(group_message(message_id="old"))
        for index in range(DEDUP_MEMORY):
            trigger.note_answered(group_message(message_id=f"m{index}"))
        # 最早的那条已被挤出记忆，因此不再算重复
        self.assertTrue(trigger.should_respond(group_message(message_id="old")).respond)


if __name__ == "__main__":
    unittest.main()
