"""长期记忆（关系记忆）的单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.notebook import (  # noqa: E402
    FORGET,
    REMEMBER,
    Notebook,
    read_intent,
)

OWNER = 10001


class IntentTest(unittest.TestCase):
    def test_plain_remember(self):
        intent = read_intent("记住：我不吃辣")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, REMEMBER)

    def test_remember_keeps_the_whole_sentence(self):
        """存整条原话，不存提示词后面那截 —— 上下文才是以后用得上的部分。"""
        intent = read_intent("帮我记一下，我下周三要考试")
        self.assertEqual(intent.payload, "帮我记一下，我下周三要考试")

    def test_standing_change_is_remembered(self):
        intent = read_intent("以后叫我老板")
        self.assertEqual(intent.kind, REMEMBER)

    def test_dont_forget_is_not_a_forget_request(self):
        """「别忘了带伞」是**让记住**，不是"忘记"。一个字之差，方向相反。"""
        intent = read_intent("别忘了带伞")
        self.assertEqual(intent.kind, REMEMBER)

    def test_bare_acknowledgement_is_ignored(self):
        """「记住了」「我记住了」是在回话，不是在交代事情。"""
        self.assertIsNone(read_intent("记住了"))
        self.assertIsNone(read_intent("我记住了"))
        self.assertIsNone(read_intent("咱记住就行"))

    def test_polite_request_is_not_mistaken_for_an_acknowledgement(self):
        """⚠️ 回归测试：「帮我记住…」里也有个"我"字。

        第一版把「我 + 记住」一律当成应答，结果**把最常见的请求句式一起挡掉了**。
        差别只在前面那两个字：我记住（应答）/ 帮我记住（交代）。
        """
        for text in ("帮我记住，钥匙在抽屉里", "给我记住：明天带伞", "替我记住这个日子"):
            intent = read_intent(text)
            self.assertIsNotNone(intent, msg=f"漏判：{text!r}")
            self.assertEqual(intent.kind, REMEMBER)

    def test_everyday_talk_is_ignored(self):
        for text in ("今天天气不错", "这游戏挺好玩的", "在吗", ""):
            self.assertIsNone(read_intent(text), msg=f"误判：{text!r}")

    def test_forget_request_carries_a_keyword(self):
        intent = read_intent("忘掉猫的事")
        self.assertEqual(intent.kind, FORGET)
        self.assertIn("猫", intent.payload)

    def test_forget_without_a_target_does_nothing(self):
        """没说清忘什么就什么都不删。"""
        self.assertIsNone(read_intent("忘记"))

    def test_japanese_cue(self):
        intent = read_intent("覚えて、私は猫が好き")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, REMEMBER)


class NotebookTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.book = Notebook(Path(tmp.name) / "notebook.db")
        self.addCleanup(self.book.close)

    def test_add_and_read_back(self):
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我不吃辣")
        notes = self.book.all_active()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].content, "我不吃辣")
        self.assertEqual(notes[0].subject_qq, OWNER)

    def test_identical_note_is_not_stored_twice(self):
        """同一人反复说同一句话，只留一条、把时间往前挪。"""
        first = self.book.add(subject_qq=OWNER, subject_role="owner", content="我不吃辣")
        second = self.book.add(subject_qq=OWNER, subject_role="owner", content="我不吃辣")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(self.book.count(), 1)

    def test_same_words_from_two_people_are_two_notes(self):
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我不吃辣")
        self.book.add(subject_qq=999, subject_role="admin", content="我不吃辣")
        self.assertEqual(self.book.count(), 2)

    def test_empty_content_is_rejected(self):
        self.assertIsNone(self.book.add(subject_qq=OWNER, subject_role="owner", content="   "))
        self.assertEqual(self.book.count(), 0)

    def test_long_content_is_truncated(self):
        self.book.add(subject_qq=OWNER, subject_role="owner", content="啊" * 1000)
        self.assertLessEqual(len(self.book.all_active()[0].content), 300)

    def test_speaker_notes_come_first(self):
        """正在说话的人的记忆要排前面 —— 这里不是检索，是排序。"""
        self.book.add(subject_qq=999, subject_role="admin", content="别人的事")
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我的事")

        self.assertEqual(self.book.for_prompt(OWNER)[0].content, "我的事")
        self.assertEqual(self.book.for_prompt(999)[0].content, "别人的事")

    def test_forget_containing_deactivates_matching_notes(self):
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我养了一只猫")
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我不吃辣")

        removed = self.book.forget_containing(OWNER, "猫")

        self.assertEqual(removed, 1)
        self.assertEqual(self.book.count(), 1)
        self.assertEqual(self.book.all_active()[0].content, "我不吃辣")

    def test_forget_containing_only_touches_that_person(self):
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我养了一只猫")
        self.book.add(subject_qq=999, subject_role="admin", content="我养了一只猫")

        self.book.forget_containing(OWNER, "猫")

        remaining = self.book.all_active()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].subject_qq, 999)

    def test_forget_containing_escapes_like_wildcards(self):
        """关键词里的 % 和 _ 是 SQL 通配符，不转义就会误伤。"""
        self.book.add(subject_qq=OWNER, subject_role="owner", content="进度是 100%")
        self.book.add(subject_qq=OWNER, subject_role="owner", content="完全无关的一条")

        removed = self.book.forget_containing(OWNER, "%")

        self.assertEqual(removed, 1)
        self.assertEqual(self.book.all_active()[0].content, "完全无关的一条")

    def test_forget_steps_back_when_the_wording_does_not_match(self):
        """⚠️ 真实说法的落差：人说的是「忘掉猫的事」，记下来的原话是「我养了一只猫」。

        整段关键词对不上，所以精确匹配落空时要削掉尾巴虚词再试一次。
        """
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我养了一只猫")
        self.assertEqual(self.book.forget_containing(OWNER, "猫的事"), 1)
        self.assertEqual(self.book.count(), 0)

    def test_forget_by_id(self):
        note_id = self.book.add(subject_qq=OWNER, subject_role="owner", content="随便")
        self.assertTrue(self.book.forget(note_id))
        self.assertEqual(self.book.count(), 0)
        self.assertFalse(self.book.forget(note_id))

    def test_notes_survive_reopening(self):
        """它是**独立落盘**的一本，不是内存里的临时账。"""
        path = self.book._path
        self.book.add(subject_qq=OWNER, subject_role="owner", content="我不吃辣")
        self.book.close()

        again = Notebook(path)
        self.addCleanup(again.close)
        self.assertEqual(again.all_active()[0].content, "我不吃辣")

    def test_notes_are_never_pruned(self):
        """跟会话记忆最大的区别：**写得再多也不裁**。

        会话记忆满了会删旧的（memory.py），这本不会 ——
        主人交代过的事被自动删掉，比记不住更糟。
        """
        for index in range(300):
            self.book.add(
                subject_qq=OWNER,
                subject_role="owner",
                content=f"第 {index} 件事",
                at=datetime(2026, 1, 1),
            )
        self.assertEqual(self.book.count(), 300)


if __name__ == "__main__":
    unittest.main()
