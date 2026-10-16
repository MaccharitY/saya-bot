"""触发判定的测试。

现在判定返回**两个开关**（`observe` / `respond`）：记不记、回不回。
分组测：私聊白名单、按群策略、限流与去重。
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
GROUP = 123456789


def base_config(**overrides):
    config = Config.from_env(dotenv_path=NO_DOTENV)
    return replace(config, **overrides) if overrides else config


def group_message(
    text: str = "你好",
    *,
    mentioned: bool = False,
    message_id: str | None = "1",
    group_id: int = GROUP,
    user_id: int = 111,
) -> IncomingMessage:
    return IncomingMessage(
        source="group",
        group_id=group_id,
        user_id=user_id,
        text=text,
        mentioned_self=mentioned,
        message_id=message_id,
    )


def private_message(
    text: str = "在吗", *, user_id: int = 222, message_id: str = "p1"
) -> IncomingMessage:
    return IncomingMessage(source="private", user_id=user_id, text=text, message_id=message_id)


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


class PrivateWhitelistTest(unittest.TestCase):
    """私聊白名单是**结构性**防注入：把陌生人直接对机器人下指令的路关掉。"""

    def _trigger(self, **overrides) -> Trigger:
        return Trigger(base_config(group_default_policy="silent", **overrides))

    def test_owner_is_allowed(self):
        trigger = self._trigger(owner_id=1001)
        decision = trigger.evaluate(private_message(user_id=1001))
        self.assertTrue(decision.observe)
        self.assertTrue(decision.respond)
        self.assertEqual(decision.reason, "private_allowed")

    def test_admins_and_friends_are_allowed(self):
        trigger = self._trigger(owner_id=1001, admin_ids=(2001, 2002), friend_allowlist=(3001,))
        for user_id in (2001, 2002, 3001):
            with self.subTest(user_id=user_id):
                self.assertTrue(trigger.evaluate(private_message(user_id=user_id)).respond)

    def test_stranger_is_ignored_and_not_even_recorded(self):
        trigger = self._trigger(owner_id=1001)
        decision = trigger.evaluate(private_message(user_id=6666))
        self.assertFalse(decision.respond)
        self.assertFalse(decision.observe, "陌生人的私聊内容没必要留")
        self.assertEqual(decision.reason, "private_not_allowed")

    def test_nobody_configured_means_nobody_can_dm(self):
        """没配白名单时**谁都不能私聊** —— 安全默认，而不是谁都能。"""
        trigger = self._trigger()
        self.assertFalse(trigger.evaluate(private_message(user_id=1)).respond)


class GroupPolicyTest(unittest.TestCase):
    def test_silent_group_is_not_even_recorded(self):
        trigger = Trigger(base_config(group_policies={GROUP: "silent"}))
        decision = trigger.evaluate(group_message(mentioned=True))
        self.assertFalse(decision.observe)
        self.assertFalse(decision.respond)
        self.assertEqual(decision.reason, "group_silent")

    def test_mention_policy_records_everything_but_only_replies_when_mentioned(self):
        """**感知与回复分离** —— 否则它只在被叫到时才"看见世界"，记忆无从统计。"""
        trigger = Trigger(base_config(group_policies={GROUP: "mention"}))

        quiet = trigger.evaluate(group_message("大家早上好", mentioned=False))
        self.assertTrue(quiet.observe, "没被 @ 也要记录")
        self.assertFalse(quiet.respond)
        self.assertEqual(quiet.reason, "skip:mention")

        loud = trigger.evaluate(group_message("咲弥在吗", mentioned=True, message_id="2"))
        self.assertTrue(loud.observe)
        self.assertTrue(loud.respond)
        self.assertEqual(loud.reason, "hit:mention")

    def test_keyword_policy(self):
        # 关掉跟随 —— 这条只测"关键词本身"，跟随见 KeywordFollowTest
        trigger = Trigger(
            base_config(
                group_policies={GROUP: "keyword"},
                trigger_keywords=("咲弥", "在吗"),
                keyword_followup=0,
            )
        )
        self.assertTrue(trigger.evaluate(group_message("咲弥今天怎么样")).respond)
        self.assertFalse(trigger.evaluate(group_message("今天天气不错", message_id="2")).respond)

    def test_random_policy(self):
        config = base_config(group_policies={GROUP: "random"}, reply_probability=0.5)
        self.assertTrue(
            Trigger(config, rng=FixedRandom(0.4)).evaluate(group_message()).respond
        )
        self.assertFalse(
            Trigger(config, rng=FixedRandom(0.6)).evaluate(group_message()).respond
        )

    def test_always_policy(self):
        trigger = Trigger(base_config(group_policies={GROUP: "always"}))
        self.assertTrue(trigger.evaluate(group_message(mentioned=False)).respond)

    def test_selective_degrades_to_mention_and_says_so(self):
        """未实现的策略要**降级并写明**，不能静默 —— 静默会让人以为它坏了。"""
        trigger = Trigger(base_config(group_policies={GROUP: "selective"}))
        decision = trigger.evaluate(group_message(mentioned=True))
        self.assertTrue(decision.respond)
        self.assertIn("selective", decision.reason)
        self.assertIn("降级", decision.reason)

    def test_unknown_policy_is_a_loud_failure(self):
        trigger = Trigger(base_config(group_policies={GROUP: "乱写的"}))
        with self.assertRaises(ValueError):
            trigger.evaluate(group_message())

    def test_unlisted_group_uses_default_policy(self):
        trigger = Trigger(
            base_config(group_policies={999: "silent"}, group_default_policy="mention")
        )
        self.assertTrue(trigger.evaluate(group_message(mentioned=True)).respond)


class KeywordFollowTest(unittest.TestCase):
    """关键词命中后**跟随这个人接下来的几条**（2026-10-16 所有者提的）。

    为什么：叫人只用叫一次。说了「咲弥 这个怎么弄」之后，紧接着的
    「那这个呢」「还是不行」不该再要求他每条都带上名字。
    """

    def make(self, clock=None, **overrides):
        options = {
            "group_policies": {GROUP: "keyword"},
            "trigger_keywords": ("咲弥",),
            "keyword_followup": 5,
            "keyword_followup_seconds": 600.0,
        }
        options.update(overrides)
        return Trigger(base_config(**options), clock=clock or FakeClock())

    def test_follows_the_next_messages_without_the_keyword(self):
        trigger = self.make()
        self.assertTrue(trigger.evaluate(group_message("咲弥 这个怎么弄")).respond)
        for index in range(5):
            decision = trigger.evaluate(
                group_message("那这个呢", message_id=f"f{index}")
            )
            self.assertTrue(decision.respond, f"第 {index + 1} 条应该跟随")
            self.assertEqual(decision.reason, "hit:keyword-follow")

    def test_window_runs_out(self):
        trigger = self.make()
        trigger.evaluate(group_message("咲弥 这个怎么弄"))
        for index in range(5):
            trigger.evaluate(group_message("继续", message_id=f"f{index}"))
        after = trigger.evaluate(group_message("还在吗", message_id="after"))
        self.assertFalse(after.respond, "五条之后就该重新叫它了")

    def test_only_the_person_who_called_it(self):
        """⚠️ 窗口挂在 (群号, 用户) 上 —— 别人说话既不消耗它，也蹭不到。"""
        trigger = self.make()
        trigger.evaluate(group_message("咲弥 在吗"))

        other = trigger.evaluate(group_message("我也说一句", message_id="o1", user_id=999))
        self.assertFalse(other.respond, "别人不该被跟随")
        self.assertEqual(other.reason, "skip:keyword")

        still = trigger.evaluate(group_message("那这个呢", message_id="f1", user_id=111))
        self.assertTrue(still.respond, "别人说话不该消耗他的窗口")

    def test_expires_after_the_time_window(self):
        """⚠️ 光计数不够：早上叫过它，晚上回来说五句话不该被它接话。"""
        clock = FakeClock()
        trigger = self.make(clock=clock)
        trigger.evaluate(group_message("咲弥 在吗"))

        clock.now += 601
        late = trigger.evaluate(group_message("今天天气不错", message_id="late"))
        self.assertFalse(late.respond, "超时了就不该再跟随")

    def test_hitting_the_keyword_again_resets_the_window(self):
        clock = FakeClock()
        trigger = self.make(clock=clock)
        trigger.evaluate(group_message("咲弥 在吗"))
        clock.now += 300
        trigger.evaluate(group_message("继续", message_id="f1"))
        trigger.evaluate(group_message("咲弥 还有", message_id="f2"))  # 又叫了一次
        clock.now += 500

        again = trigger.evaluate(group_message("接着问", message_id="f3"))
        self.assertTrue(again.respond, "重新叫过之后窗口应该从头算")

    def test_zero_turns_it_off(self):
        trigger = self.make(keyword_followup=0)
        trigger.evaluate(group_message("咲弥 在吗"))
        self.assertFalse(trigger.evaluate(group_message("那这个呢", message_id="f1")).respond)

    def test_mention_policy_is_unaffected(self):
        """mention 群里每次 @ 都会回，不需要跟随 —— 别把窗口开到那边去。"""
        trigger = self.make(group_policies={GROUP: "mention"})
        trigger.evaluate(group_message("咲弥 在吗", mentioned=True))
        self.assertFalse(
            trigger.evaluate(group_message("那这个呢", message_id="f1")).respond
        )


class RateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.trigger = Trigger(
            base_config(
                group_policies={GROUP: "always"}, rate_limit_per_group_per_min=2
            ),
            clock=self.clock,
        )

    def test_blocks_after_limit_but_still_records(self):
        self.trigger.note_answered(group_message(message_id="1"))
        self.trigger.note_answered(group_message(message_id="2"))
        decision = self.trigger.evaluate(group_message(message_id="3"))

        self.assertFalse(decision.respond)
        self.assertTrue(decision.observe, "限流只挡回复，不挡记录")
        self.assertEqual(decision.reason, "rate_limited")

    def test_window_slides(self):
        self.trigger.note_answered(group_message(message_id="1"))
        self.trigger.note_answered(group_message(message_id="2"))
        self.clock.now = 61.0
        self.assertTrue(self.trigger.evaluate(group_message(message_id="3")).respond)

    def test_zero_limit_blocks_replies(self):
        trigger = Trigger(
            base_config(group_policies={GROUP: "always"}, rate_limit_per_group_per_min=0)
        )
        self.assertEqual(trigger.evaluate(group_message()).reason, "rate_limited")

    def test_limit_is_per_group(self):
        # 两个群都要用 always —— 否则默认策略 mention 下没被 @ 本来就不回，
        # 测出来的是策略而不是限流
        self.trigger = Trigger(
            base_config(
                group_default_policy="always", rate_limit_per_group_per_min=2
            ),
            clock=self.clock,
        )
        self.trigger.note_answered(group_message(message_id="1", group_id=1))
        self.trigger.note_answered(group_message(message_id="2", group_id=1))
        decision = self.trigger.evaluate(group_message(message_id="3", group_id=2))
        self.assertTrue(decision.respond)


class DedupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = base_config(
            group_policies={GROUP: "always"}, rate_limit_per_group_per_min=100_000
        )

    def test_same_message_is_never_processed_twice(self):
        trigger = Trigger(self.config)
        message = group_message(message_id="abc")
        trigger.note_answered(message)
        decision = trigger.evaluate(message)
        self.assertEqual(decision.reason, "duplicate")
        self.assertFalse(decision.observe)

    def test_private_messages_are_also_deduped(self):
        trigger = Trigger(base_config(owner_id=1, group_default_policy="silent"))
        message = private_message(message_id="p9", user_id=1)
        trigger.note_answered(message)
        self.assertEqual(trigger.evaluate(message).reason, "duplicate")

    def test_dedup_memory_is_bounded(self):
        trigger = Trigger(self.config)
        trigger.note_answered(group_message(message_id="old"))
        for index in range(DEDUP_MEMORY):
            trigger.note_answered(group_message(message_id=f"m{index}"))
        # 最早的那条已被挤出记忆，因此不再算重复
        self.assertTrue(trigger.evaluate(group_message(message_id="old")).respond)


if __name__ == "__main__":
    unittest.main()
