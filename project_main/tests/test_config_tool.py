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
REPO_ROOT = PROJECT_MAIN.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(PROJECT_MAIN / "src"))

from config import (  # noqa: E402
    MAX_ADMINS,
    EnvFile,
    cmd_list,
    inline_comment,
    parse_policy,
    parse_policies,
    parse_qq,
    replace_key,
    split_csv,
)
from saya.config import parse_dotenv  # noqa: E402

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
            env.save(quiet=True)
            self.assertEqual(
                parse_dotenv(self.path.read_text(encoding="utf-8")).get(key), value, key
            )

    def test_save_preserves_lf_line_endings(self):
        """⚠️ 用 read_text/write_text 会把 LF 变 CRLF，整个文件每一行都变 ——
        只改一个值却让 diff 一片红。实测撞过，所以钉住。"""
        EnvFile(self.path).save(quiet=True)
        raw = self.path.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0)

    def test_save_preserves_crlf_line_endings(self):
        crlf = Path(self.dir.name) / "crlf.env"
        crlf.write_bytes(SAMPLE_LF.replace("\n", "\r\n").encode("utf-8"))
        env = EnvFile(crlf)
        env.set("SAYA_OWNER_ID", "2002")
        env.save(quiet=True)
        raw = crlf.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), SAMPLE_LF.count("\n"))
        self.assertEqual(raw.count(b"\n") - raw.count(b"\r\n"), 0)

    def test_save_makes_a_backup(self):
        env = EnvFile(self.path)
        env.set("SAYA_OWNER_ID", "2002")
        env.save(quiet=True)
        self.assertEqual(
            self.path.with_name(".env.bak").read_text(encoding="utf-8"), SAMPLE_LF
        )

    def test_other_lines_are_untouched(self):
        env = EnvFile(self.path)
        env.set("SAYA_OWNER_ID", "2002")
        env.save(quiet=True)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("SAYA_LLM_API_KEY=sk-not-a-real-key", text)
        self.assertIn("SAYA_OWNER_ID_SUFFIX=keep", text)
        self.assertIn("# SAYA_OWNER_ID=9999", text)

    def test_no_false_alarm_when_outside_a_repo(self):
        """⚠️ 测试跑在系统临时目录，那里不是 git 仓库。

        不加这一条，`git check-ignore` 会返回 128、stdout 为空，
        于是**每个临时文件都被当成"有泄露风险"** —— 实测把测试结果淹了一屏，
        真正的告警反而看不见了。退出码不是 0/1 时应当直接跳过。
        """
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        env = EnvFile(self.path)
        env.set("SAYA_OWNER_ID", "2002")
        with redirect_stdout(buffer):
            env.save()          # 故意不加 quiet，要看到它到底打了什么
        self.assertNotIn("API key", buffer.getvalue())


class ValidationTest(unittest.TestCase):
    def fake_env(self, content: str) -> EnvFile:
        """造一个**不落盘到项目里**的 EnvFile，只用来测纯逻辑。

        用 `addCleanup` 注册回收，别把 TemporaryDirectory 挂在对象上续命 ——
        那样解释器收尾时会报 ResourceWarning。
        """
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / ".env"
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content + "\n")
        return EnvFile(path)

    def test_qq_rejects_non_numeric(self):
        with self.assertRaises(SystemExit):
            parse_qq("abc")

    def test_qq_rejects_zero_and_negative(self):
        for bad in ("0", "-5"):
            with self.assertRaises(SystemExit):
                parse_qq(bad)

    def test_unknown_policy_lists_the_valid_ones(self):
        with self.assertRaises(SystemExit) as caught:
            parse_policy("乱写的")
        self.assertIn("mention", str(caught.exception))

    def test_admin_limit_is_enforced(self):
        env = self.fake_env(f"SAYA_ADMIN_IDS={','.join(str(i) for i in range(MAX_ADMINS))}")
        with self.assertRaises(SystemExit) as caught:
            cmd_list(env, "SAYA_ADMIN_IDS", "管理员", "add", 99999, limit=MAX_ADMINS)
        self.assertIn("满", str(caught.exception))

    def test_adding_a_duplicate_is_a_no_op(self):
        env = self.fake_env("SAYA_ADMIN_IDS=20001")
        self.assertEqual(cmd_list(env, "SAYA_ADMIN_IDS", "管理员", "add", 20001, limit=MAX_ADMINS), 0)
        self.assertEqual(env.get("SAYA_ADMIN_IDS"), "20001")


class ParsingHelpersTest(unittest.TestCase):
    def test_split_csv_drops_blanks(self):
        self.assertEqual(split_csv("a, b ,,c"), ["a", "b", "c"])
        self.assertEqual(split_csv(""), [])
        self.assertEqual(split_csv(None), [])

    def test_parse_policies_skips_malformed_entries(self):
        self.assertEqual(
            parse_policies("111:mention, 222:silent ,乱写,:空"),
            {"111": "mention", "222": "silent"},
        )


if __name__ == "__main__":
    unittest.main()
