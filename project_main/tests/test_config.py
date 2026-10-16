"""配置加载的测试。

`.env` 解析的边界情况在这里集中测 —— 它出过一次真事故：
`.env.example` 里用了**行尾注释**，而解析器只认整行注释，于是注释文字被当成值，
用户照抄一份 `.env` 就直接崩。所以下面有专门的回归用例。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.config import Config, ConfigError, load_env, parse_dotenv  # noqa: E402

NO_DOTENV = Path("不存在的文件.env")


class EnvEncodingTest(unittest.TestCase):
    """⚠️ `.env` 存错编码时要说清楚，而不是抛一个看不懂的栈。

    `.env` 是人在 Windows 侧用记事本编辑的，而"另存为 ANSI"是那个编辑器的默认选项之一。
    真撞上时应该看到"你这份文件不是 UTF-8"，而不是
    `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 ...`。

    ⚠️ 而且**不能静默用 `errors="replace"` 兜过去**：那会悄悄改掉密钥里的字节，
    换来的可能是一个"看起来读到了、但就是连不上"的 key —— 排查能耗掉一晚上。
    宁可起不来，但要说清楚为什么。
    """

    def test_gbk_env_raises_a_readable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_bytes("SAYA_LLM_MODEL=咲弥\n".encode("gbk"))

            with self.assertRaises(ConfigError) as caught:
                load_env(path)

            message = str(caught.exception)
            self.assertIn("UTF-8", message)
            self.assertIn(str(path), message)

    def test_utf8_env_loads_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("SAYA_LLM_MODEL=咲弥\n", encoding="utf-8")

            self.assertEqual(load_env(path)["SAYA_LLM_MODEL"], "咲弥")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(load_env(Path("不存在.env")).get("PATH"), __import__("os").environ.get("PATH"))


def write_env(text: str, directory: Path) -> Path:
    path = directory / ".env"
    path.write_text(text, encoding="utf-8")
    return path


class ParseDotenvTest(unittest.TestCase):
    def test_basic_pairs(self):
        self.assertEqual(
            parse_dotenv("A=1\nB=two\n"), {"A": "1", "B": "two"}
        )

    def test_blank_lines_and_whole_line_comments(self):
        self.assertEqual(parse_dotenv("\n# 注释\nA=1\n"), {"A": "1"})

    def test_inline_comment_is_stripped(self):
        """回归用例：这一条曾经让 .env 加载直接崩掉。"""
        self.assertEqual(
            parse_dotenv("SAYA_TRIGGER_MODE=mention        # mention | keyword\n"),
            {"SAYA_TRIGGER_MODE": "mention"},
        )

    def test_empty_value_followed_by_inline_comment(self):
        """`.env.example` 里大量使用这种写法：空值 + 行尾注释。"""
        self.assertEqual(
            parse_dotenv("SAYA_GROUP_ALLOWLIST=            # 逗号分隔群号\n"),
            {"SAYA_GROUP_ALLOWLIST": ""},
        )

    def test_hash_inside_value_is_preserved(self):
        """密码里带 # 时不能被截断 —— 只有 # 前面是空白才算注释。"""
        self.assertEqual(parse_dotenv("KEY=abc#def\n"), {"KEY": "abc#def"})

    def test_quoted_value_keeps_inner_hash(self):
        self.assertEqual(parse_dotenv('KEY="a # b"\n'), {"KEY": "a # b"})

    def test_quoted_value_without_closing_quote_does_not_crash(self):
        self.assertEqual(parse_dotenv('KEY="unterminated\n'), {"KEY": "unterminated"})

    def test_value_is_trimmed(self):
        self.assertEqual(parse_dotenv("A=  spaced  \n"), {"A": "spaced"})

    def test_line_without_equals_is_skipped(self):
        self.assertEqual(parse_dotenv("这行没有等号\nA=1\n"), {"A": "1"})


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_defaults_without_env_file(self):
        config = Config.from_env(dotenv_path=NO_DOTENV)
        self.assertEqual(config.adapter, "mock")
        self.assertEqual(config.vision_route, "direct")
        self.assertEqual(config.group_default_policy, "mention")
        self.assertEqual(config.group_policies, {})
        self.assertEqual(config.llm_model, "deepseek-flash")
        self.assertTrue(config.vision_enable)
        self.assertIsNone(config.self_id)
        self.assertIsNone(config.owner_id, "没配主人 = 谁都不能私聊它")

    def test_values_from_file(self):
        path = write_env(
            "SAYA_ADAPTER=onebot11_http\n"
            "SAYA_GROUP_DEFAULT_POLICY=mention\n"
            "SAYA_GROUP_POLICIES=123456789:mention,999:silent\n"
            "SAYA_TRIGGER_KEYWORDS=莎夜,在吗\n"
            "SAYA_OWNER_ID=1001\nSAYA_ADMIN_IDS=2001,2002\nSAYA_FRIEND_ALLOWLIST=3001\n"
            "SAYA_SELF_ID=99999\nSAYA_PRICE_IN=1.5\n",
            self.dir,
        )
        config = Config.from_env(dotenv_path=path)

        self.assertEqual(config.adapter, "onebot11_http")
        self.assertEqual(config.group_policies, {123456789: "mention", 999: "silent"})
        self.assertEqual(config.trigger_keywords, ("莎夜", "在吗"))
        self.assertEqual(config.owner_id, 1001)
        self.assertEqual(config.admin_ids, (2001, 2002))
        self.assertEqual(config.friend_allowlist, (3001,))
        self.assertEqual(config.self_id, 99999)
        self.assertEqual(config.price_in, 1.5)

    def test_bad_group_policy_gives_clear_error(self):
        path = write_env("SAYA_GROUP_POLICIES=123456789:乱写的\n", self.dir)
        with self.assertRaises(ConfigError) as ctx:
            Config.from_env(dotenv_path=path)
        self.assertIn("SAYA_GROUP_POLICIES", str(ctx.exception))

    def test_group_policy_without_colon_gives_clear_error(self):
        path = write_env("SAYA_GROUP_POLICIES=123456789\n", self.dir)
        with self.assertRaises(ConfigError) as ctx:
            Config.from_env(dotenv_path=path)
        self.assertIn("群号:策略", str(ctx.exception))

    def test_non_numeric_group_id_gives_clear_error(self):
        path = write_env("SAYA_GROUP_POLICIES=abc:mention\n", self.dir)
        with self.assertRaises(ConfigError):
            Config.from_env(dotenv_path=path)

    def test_real_environment_wins_over_file(self):
        import os

        path = write_env("SAYA_LLM_MODEL=from-file\n", self.dir)
        os.environ["SAYA_LLM_MODEL"] = "from-env"
        self.addCleanup(os.environ.pop, "SAYA_LLM_MODEL", None)

        self.assertEqual(Config.from_env(dotenv_path=path).llm_model, "from-env")

    def test_example_file_parses_cleanly(self):
        """守住这个：仓库里的 .env.example 必须能被自己的解析器读通。
        用户就是照它抄的，抄了跑不起来最气人。"""
        example = Path(__file__).resolve().parents[1].parent / ".env.example"
        self.assertTrue(example.is_file(), f"找不到 {example}")

        parsed = parse_dotenv(example.read_text(encoding="utf-8"))
        self.assertIn("SAYA_ADAPTER", parsed)
        self.assertEqual(parsed["SAYA_ADAPTER"], "mock")
        # 行尾注释必须已经被剥掉，不能混进值里
        for key, value in parsed.items():
            self.assertNotIn("#", value, f"{key} 的值里混进了注释：{value!r}")

    def test_bad_int_in_id_list_gives_clear_error(self):
        path = write_env("SAYA_ADMIN_IDS=111,abc\n", self.dir)
        with self.assertRaises(ConfigError) as ctx:
            Config.from_env(dotenv_path=path)
        self.assertIn("SAYA_ADMIN_IDS", str(ctx.exception))
        self.assertIn("abc", str(ctx.exception))

    def test_missing_api_key_raises_actionable_error(self):
        config = Config.from_env(dotenv_path=NO_DOTENV)
        with self.assertRaises(RuntimeError) as ctx:
            config.require_api_key()
        self.assertIn(".env", str(ctx.exception))

    def test_bad_port_does_not_silently_default(self):
        path = write_env("SAYA_ONEBOT_LISTEN_PORT=not-a-number\n", self.dir)
        with self.assertRaises(ValueError):
            Config.from_env(dotenv_path=path)


if __name__ == "__main__":
    unittest.main()
