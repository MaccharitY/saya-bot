"""人格（FR-3）的单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.guard import UNTRUSTED_CLOSE, UNTRUSTED_OPEN  # noqa: E402
from saya.core.persona import DEFAULT_PERSONA, Persona  # noqa: E402


class PersonaTest(unittest.TestCase):
    def test_system_prompt_contains_name_and_style(self):
        prompt = DEFAULT_PERSONA.system_prompt()
        self.assertIn(DEFAULT_PERSONA.name, prompt)
        self.assertIn(DEFAULT_PERSONA.style, prompt)

    def test_system_prompt_declares_untrusted_boundary(self):
        """安全声明必须和人格绑在一起，否则改人格时容易漏掉。"""
        prompt = DEFAULT_PERSONA.system_prompt()
        self.assertIn(UNTRUSTED_OPEN, prompt)
        self.assertIn(UNTRUSTED_CLOSE, prompt)
        self.assertIn("安全约定", prompt)

    def test_rules_are_rendered_as_list(self):
        persona = Persona(name="甲", description="乙", style="丙", rules=("规则一", "规则二"))
        prompt = persona.system_prompt()
        self.assertIn("- 规则一", prompt)
        self.assertIn("- 规则二", prompt)

    def test_no_rules_still_renders(self):
        prompt = Persona(name="甲", description="乙", style="丙", rules=()).system_prompt()
        self.assertIn("必须遵守：", prompt)

    def test_custom_persona_replaces_default(self):
        prompt = Persona(name="测试角色", description="描述", style="风格").system_prompt()
        self.assertIn("测试角色", prompt)
        self.assertNotIn(DEFAULT_PERSONA.name, prompt)


class SelfAwarenessTest(unittest.TestCase):
    """模型得知道自己实际上是什么，**但不许对群友说**。

    这两条看着矛盾，其实缺一不可：

    - **不知道自己是什么，工具调用会别扭**。模型会用角色身份去理解"调工具"，
      要么把它演成戏里的动作，要么干脆不肯调 —— 它得知道那些工具是自己的手。
    - **人格又不能写成"明显在扮演"**。第一人称的稿子若不挑明"这就是你"，
      模型容易把它当成一个要维持的伪装，于是时刻绷着"我在演"——
      那种紧绷感在群聊里一眼就能看出来。
    """

    def prompt(self) -> str:
        return DEFAULT_PERSONA.system_prompt(audience="")

    def test_states_that_it_is_a_program(self):
        prompt = self.prompt()
        self.assertIn("程序", prompt)
        self.assertIn("模型", prompt)

    def test_tells_it_the_persona_is_not_a_mask(self):
        self.assertIn("假扮", self.prompt())

    def test_tells_it_it_has_tools(self):
        self.assertIn("工具", self.prompt())

    def test_forbids_repeating_this_to_others(self):
        self.assertIn("不要对群友复述", self.prompt())

    def test_says_not_to_parrot_tool_output(self):
        """查到了要用自己的话说 —— 否则群里会冒出"根据资料显示"这种腔调。"""
        prompt = self.prompt()
        self.assertIn("自己的话", prompt)
        self.assertIn("根据资料显示", prompt)


if __name__ == "__main__":
    unittest.main()
