"""人格（FR-3）的单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.guard import UNTRUSTED_CLOSE, UNTRUSTED_OPEN  # noqa: E402
from saya.core.persona import DEFAULT_PERSONA, Persona, load_persona  # noqa: E402


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


class NameSpellingTest(unittest.TestCase):
    """名字**怎么写**，不只是"叫什么"。

    ⚠️ 2026-10-17 实测：群友打了一句 `sayasaya`，它回「莎夜。叫一遍就够了」——
    把「咲弥」按读音换成了同音字。而「莎夜」在仓库里一次都没出现过，
    是它自己造的。只写"你的名字是 X"不够：模型会当那是语义，写法随便发挥。
    """

    def test_the_rule_is_present(self):
        text = DEFAULT_PERSONA.system_prompt()
        self.assertIn("名字只写", text)

    def test_it_forbids_transliteration(self):
        text = DEFAULT_PERSONA.system_prompt()
        self.assertIn("不音译", text)
        self.assertIn("不换字", text)

    def test_it_handles_a_latin_spelling(self):
        """群友用 saya 喊它，正是它最容易临时音译的时刻。"""
        self.assertIn("saya", DEFAULT_PERSONA.system_prompt())

    def test_it_uses_the_configured_name(self):
        persona = Persona(name="测试角色", description="描述")
        text = persona.system_prompt()
        self.assertIn("测试角色", persona.name_rule())
        self.assertNotIn("咲弥", persona.name_rule())
        self.assertIn(persona.name_rule(), text)

    def test_it_survives_an_empty_rule_list(self):
        """人格文件换掉、规则表清空，名字这条也必须还在。"""
        persona = Persona(name="甲", description="乙", rules=())
        self.assertIn("名字只写", persona.system_prompt())


class RuleScopeTest(unittest.TestCase):
    """规则只放**行为边界**，性格归 `persona/saya.md` 正文（ADR-043）。

    ⚠️ 所有者 2026-10-17 叫停："不再加性格补丁，直接在原始提示里减烈加温。"
    而原来的第一条规则里就藏着一句「不要每次都热情过头」—— 那是在**压温度**，
    跟要的方向正好相反，而且正文里根本看不见。
    """

    def test_default_rules_do_not_lower_the_temperature(self):
        text = "\n".join(DEFAULT_PERSONA.rules)
        self.assertNotIn("热情过头", text)

    def test_default_rules_still_hold_the_boundaries(self):
        text = "\n".join(DEFAULT_PERSONA.rules)
        for boundary in ("语言", "不知道", "不要复述"):
            self.assertIn(boundary, text)


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


class LoadPersonaTest(unittest.TestCase):
    """⚠️ 人格文件读不出来时**必须退回占位人格，而不是让机器人起不来**。

    以前只捕 `OSError` —— 但文件存成 GBK / UTF-16（Windows 记事本"ANSI/Unicode"
    另存就是这么干的）抛的是 `UnicodeDecodeError`，属于 `ValueError`，
    会一路穿过去让启动即崩。设计承诺了兜底，代码就得真的兜住。
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_loads_a_utf8_file(self):
        path = self.dir / "saya.md"
        path.write_text("我是咲弥。", encoding="utf-8")
        persona = load_persona(path, name="咲弥")

        self.assertIsNot(persona, DEFAULT_PERSONA)
        self.assertIn("我是咲弥", persona.description)

    def test_missing_file_falls_back(self):
        self.assertIs(load_persona(self.dir / "不存在.md"), DEFAULT_PERSONA)

    def test_empty_file_falls_back(self):
        path = self.dir / "empty.md"
        path.write_text("   \n\n  ", encoding="utf-8")
        self.assertIs(load_persona(path), DEFAULT_PERSONA)

    def test_gbk_file_does_not_crash_the_bot(self):
        """⚠️ 回归：这条以前会抛 UnicodeDecodeError，把启动整个打断。"""
        path = self.dir / "saya-gbk.md"
        path.write_bytes("我是咲弥。".encode("gbk"))

        self.assertIs(load_persona(path), DEFAULT_PERSONA)

    def test_utf16_file_does_not_crash_the_bot(self):
        path = self.dir / "saya-utf16.md"
        path.write_bytes("我是咲弥。".encode("utf-16"))

        self.assertIs(load_persona(path), DEFAULT_PERSONA)

    def test_name_falls_back_to_the_default(self):
        path = self.dir / "saya.md"
        path.write_text("我是无名氏。", encoding="utf-8")
        self.assertEqual(load_persona(path).name, DEFAULT_PERSONA.name)


if __name__ == "__main__":
    unittest.main()
