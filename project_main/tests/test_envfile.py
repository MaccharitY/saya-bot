"""`tools/config.py` 的单元测试 —— 全部用**合成内容**，不读也不写真 `.env`。

⚠️ 这个工具会改写一个**装着 API key** 的文件，所以测试的重点不是"功能对不对"，
而是**"会不会把文件改坏"**：

- 只改该改的那一行，其余行、注释、顺序、**行尾符**都不能动
- 值写进去要能**原样读回来**（解析规则必须和 `saya.config` 一致）
- 写坏了要有备份

放在主测试套件里（而不是 `tools/test_*.py`）是有意的：
`tools/` 下的测试要手动跑，**不跑就会烂**，而这个是改配置的入口，不能烂。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_MAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_MAIN / "src"))

from saya.config import parse_dotenv  # noqa: E402
from saya.envfile import EnvFile, inline_comment, replace_key  # noqa: E402

# 合成内容：**没有任何真 key**，但形状和真 .env 一样（含行尾注释）
SAMPLE_LF = (
    "# ---- 模型接入 ----\n"
    "SAYA_LLM_BASE_URL=https://api.deepseek.com\n"
    "SAYA_LLM_API_KEY=sk-not-a-real-key\n"
    "SAYA_OWNER_ID=1001\n"
    "SAYA_GROUP_POLICIES=111:mention\n"
    "SAYA_TRIGGER_KEYWORDS=           # 逗号分隔，仅 keyword 模式用\n"
    "SAYA_ADMIN_IDS=                  # 管理，最多 3 个\n"
    "# SAYA_OWNER_ID=9999   ← 被注释掉的那行不该被改\n"
    "SAYA_OWNER_ID_SUFFIX=keep\n"
)


class ReplaceKeyTest(unittest.TestCase):
    """行编辑是这工具的核心 —— 它决定"会不会改坏文件"。"""

    def test_replaces_only_the_target_line(self):
        lines = SAMPLE_LF.splitlines(keepends=True)
        out, found = replace_key(lines, "SAYA_OWNER_ID", "2002")
        self.assertTrue(found)
        self.assertEqual(
            "".join(out),
            SAMPLE_LF.replace("SAYA_OWNER_ID=1001", "SAYA_OWNER_ID=2002"),
        )

    def test_keeps_trailing_comment(self):
        lines = ["SAYA_ADMIN_IDS=   # 管理，最多 3 个\n"]
        out, _ = replace_key(lines, "SAYA_ADMIN_IDS", "20001")
        self.assertEqual(out[0], "SAYA_ADMIN_IDS=20001   # 管理，最多 3 个\n")

    def test_does_not_touch_a_commented_out_line(self):
        """注释掉的 `# SAYA_OWNER_ID=...` 是给人看的记录，不是配置。"""
        lines = ["# SAYA_OWNER_ID=9999\n", "SAYA_OWNER_ID=1001\n"]
        out, _ = replace_key(lines, "SAYA_OWNER_ID", "2002")
        self.assertEqual(out[0], "# SAYA_OWNER_ID=9999\n")
        self.assertEqual(out[1], "SAYA_OWNER_ID=2002\n")

    def test_does_not_match_a_longer_key_with_the_same_prefix(self):
        """⚠️ 前缀相同但更长的键**绝不能**被误伤 —— 这是最常见的写坏方式。"""
        lines = ["SAYA_OWNER_ID_SUFFIX=keep\n", "SAYA_OWNER_ID=1001\n"]
        out, _ = replace_key(lines, "SAYA_OWNER_ID", "2002")
        self.assertEqual(out[0], "SAYA_OWNER_ID_SUFFIX=keep\n")
        self.assertEqual(out[1], "SAYA_OWNER_ID=2002\n")

    def test_appends_when_key_is_absent(self):
        out, found = replace_key(["A=1\n"], "SAYA_NEW", "x")
        self.assertFalse(found)
        self.assertEqual(out, ["A=1\n", "SAYA_NEW=x\n"])

    def test_append_respects_crlf(self):
        """行尾风格要跟着文件走，别在 CRLF 文件里插一行 LF。"""
        out, _ = replace_key(["A=1\r\n"], "SAYA_NEW", "x")
        self.assertEqual(out[-1], "SAYA_NEW=x\r\n")

    def test_append_adds_missing_final_newline_first(self):
        out, _ = replace_key(["A=1"], "SAYA_NEW", "x")
        self.assertEqual(out, ["A=1\n", "SAYA_NEW=x\n"])


class InlineCommentTest(unittest.TestCase):
    """规则必须和 `saya.config._strip_inline_comment` 一致，否则写进去读出来会变。"""

    def test_space_hash_is_a_comment(self):
        self.assertEqual(inline_comment("old   # 说明"), "   # 说明")

    def test_hash_without_leading_space_is_not_a_comment(self):
        self.assertEqual(inline_comment("abc#def"), "")

    def test_quoted_value_is_not_treated_as_comment(self):
        self.assertEqual(inline_comment('"a # b"'), "")


class RoundTripTest(unittest.TestCase):
    """⚠️ 写进去的值得能**原样读回来** —— 这条断了，机器人就会静默用错配置。"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / ".env"
        # ⚠️ **必须 newline=""**：`write_text` 默认会把 `\n` 翻成 `\r\n`，
        # 于是"LF 文件"这个前提根本不成立，两个行尾测试全都会假失败
        # （写测试的当天就这么栽过一次）。
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(SAMPLE_LF)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_value_survives_the_round_trip(self):
        for key, value in (
            ("SAYA_OWNER_ID", "10000888"),
            ("SAYA_GROUP_POLICIES", "111:mention,222:silent"),
            ("SAYA_TRIGGER_KEYWORDS", "咲弥,在吗"),
            ("SAYA_ADMIN_IDS", "20001,20002,20003"),
            ("SAYA_REPLY_PROBABILITY", "0.25"),
        ):
            env = EnvFile(self.path)
            env.set(key, value)
            env.save()
            self.assertEqual(
                parse_dotenv(self.path.read_text(encoding="utf-8")).get(key), value, key
            )

    def test_save_preserves_lf_line_endings(self):
        """⚠️ 用 read_text/write_text 会把 LF 变 CRLF，整个文件每一行都变 ——
        只改一个值却让 diff 一片红。实测撞过，所以钉住。"""
        EnvFile(self.path).save()
        raw = self.path.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0)

    def test_save_preserves_crlf_line_endings(self):
        crlf = Path(self.dir.name) / "crlf.env"
        crlf.write_bytes(SAMPLE_LF.replace("\n", "\r\n").encode("utf-8"))
        env = EnvFile(crlf)
        env.set("SAYA_OWNER_ID", "2002")
        env.save()
        raw = crlf.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), SAMPLE_LF.count("\n"))
        self.assertEqual(raw.count(b"\n") - raw.count(b"\r\n"), 0)

    def test_save_makes_a_backup(self):
        env = EnvFile(self.path)
        env.set("SAYA_OWNER_ID", "2002")
        env.save()
        self.assertEqual(
            self.path.with_name(".env.bak").read_text(encoding="utf-8"), SAMPLE_LF
        )

    def test_other_lines_are_untouched(self):
        env = EnvFile(self.path)
        env.set("SAYA_OWNER_ID", "2002")
        env.save()
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("SAYA_LLM_API_KEY=sk-not-a-real-key", text)
        self.assertIn("SAYA_OWNER_ID_SUFFIX=keep", text)
        self.assertIn("# SAYA_OWNER_ID=9999", text)

    def test_temp_dir_is_not_reported_as_risky(self):
        """⚠️ 测试跑在系统临时目录，那里不是 git 仓库。

        `git check-ignore` 会返回 128。判不了就**当作安全放过** ——
        否则每个临时文件都被报成"有泄露风险"，实测把测试结果淹了一屏，
        真正的告警反而看不见了。
        """
        self.assertEqual(EnvFile(self.path).save(), ())




