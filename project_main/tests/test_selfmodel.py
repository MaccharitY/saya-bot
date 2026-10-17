"""`core/selfmodel.py` —— 她对自己怎么跑的认识。

⚠️ 这一节的重点不是文案，是**不能漂移**（ADR-037）：
每个数字和开关都由能力清单带进来，改了配置这段跟着变。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core import selfmodel  # noqa: E402
from saya.core.selfmodel import Capabilities  # noqa: E402

FULL = Capabilities(
    memory_enabled=True,
    messages_per_side=50,
    topics_per_session=20,
    notebook_enabled=True,
    vision_enabled=True,
    vision_route="direct",
    wiki_enabled=True,
    corpus_available=True,
    tool_names=("arcaea_lookup", "moegirl_lookup"),
)


class MemoryTest(unittest.TestCase):
    def test_reports_the_real_limits(self):
        text = selfmodel.build(FULL)
        self.assertIn("50 条消息", text)
        self.assertIn("20 条话题摘要", text)

    def test_says_older_content_cannot_be_recovered(self):
        """⚠️ 她曾经觉得清掉的东西还能找回来 —— 这一句就是冲那个来的。"""
        text = selfmodel.build(FULL)
        self.assertIn("没法恢复", text)
        self.assertIn("删除后无法恢复", text)

    def test_missing_memory_is_stated_plainly(self):
        text = selfmodel.build(Capabilities())
        self.assertIn("会话记忆未启用", text)
        self.assertNotIn("50 条", text)

    def test_missing_notebook_is_stated_plainly(self):
        text = selfmodel.build(
            Capabilities(memory_enabled=True, messages_per_side=50, notebook_enabled=False)
        )
        self.assertIn("长期记忆：未启用", text)


class AbilityTest(unittest.TestCase):
    def test_lists_vision_and_its_route(self):
        self.assertIn("视图模式 direct", selfmodel.build(FULL))

    def test_disabled_vision_is_said(self):
        text = selfmodel.build(Capabilities(vision_enabled=False))
        self.assertIn("看图：未启用", text)

    def test_lists_knowledge_sources(self):
        text = selfmodel.build(FULL)
        self.assertIn("萌娘百科", text)
        self.assertIn("本地剧情语料", text)

    def test_no_corpus_is_not_claimed(self):
        text = selfmodel.build(
            Capabilities(wiki_enabled=True, corpus_available=False)
        )
        self.assertIn("萌娘百科", text)
        self.assertNotIn("本地剧情语料", text)

    def test_lists_tool_names(self):
        text = selfmodel.build(FULL)
        self.assertIn("arcaea_lookup", text)
        self.assertIn("moegirl_lookup", text)

    def test_no_tools_is_said(self):
        self.assertIn("可调用的工具：没有", selfmodel.build(Capabilities()))

    def test_states_what_is_missing(self):
        text = selfmodel.build(FULL)
        for missing in ("语音", "视频", "文件", "主动发言"):
            self.assertIn(missing, text)

    def test_knows_she_sends_one_reply_per_message(self):
        """⚠️ 实测：让她"连发 30 条"，她答「那得靠**程序那边**多发几次」——
        把自己和程序说成了两回事。发消息也是她自己发的。"""
        text = selfmodel.build(FULL)
        self.assertIn("每收到一条消息，最多回一条", text)
        self.assertIn("不是「别处有个程序」在代劳", text)

    def test_tells_her_not_to_guess(self):
        text = selfmodel.build(FULL)
        self.assertIn("直接说办不到", text)
        self.assertIn("也许可以", text)


class PromptWiringTest(unittest.TestCase):
    def test_persona_includes_the_block_when_given(self):
        from saya.core.persona import DEFAULT_PERSONA

        block = selfmodel.build(FULL)
        prompt = DEFAULT_PERSONA.system_prompt(self_model=block)
        self.assertIn("你的记忆与能力", prompt)

    def test_persona_works_without_it(self):
        from saya.core.persona import DEFAULT_PERSONA

        self.assertNotIn("你的记忆与能力", DEFAULT_PERSONA.system_prompt())


if __name__ == "__main__":
    unittest.main()
