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


if __name__ == "__main__":
    unittest.main()
