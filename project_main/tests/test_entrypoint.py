"""入口装配的测试 —— 不启动、不联网。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.__main__ import (  # noqa: E402
    ADAPTER_CHOICES,
    build_adapter,
    build_llm,
    is_our_own_bot,
    missing_env_keys,
    run_check,
)
from saya.adapter.base import AdapterError  # noqa: E402
from saya.adapter.mock import MockAdapter  # noqa: E402
from saya.adapter.onebot11_http import OneBot11HttpAdapter  # noqa: E402
from saya.config import Config  # noqa: E402

NO_DOTENV = Path("不存在的文件.env")


def make_config(**overrides) -> Config:
    # 端口固定为 0（系统分配）—— 测试绝不该去抢 8080 这种固定端口，
    # 否则你本机真跑着一个机器人时，测试就会莫名其妙地失败。
    options = {"onebot_listen_port": 0}
    options.update(overrides)
    return replace(Config.from_env(dotenv_path=NO_DOTENV), **options)


class BuildAdapterTest(unittest.TestCase):
    def test_mock_backend(self):
        self.assertIsInstance(build_adapter(make_config(adapter="mock")), MockAdapter)

    def test_onebot_http_backend(self):
        adapter = build_adapter(make_config(adapter="onebot11_http"))
        self.assertIsInstance(adapter, OneBot11HttpAdapter)

    def test_onebot_alias_is_accepted(self):
        self.assertIsInstance(build_adapter(make_config(adapter="onebot11")), OneBot11HttpAdapter)

    def test_unknown_backend_says_what_is_available(self):
        with self.assertRaises(AdapterError) as ctx:
            build_adapter(make_config(adapter="乱写的"))
        message = str(ctx.exception)
        for choice in ADAPTER_CHOICES:
            self.assertIn(choice, message)


class BuildLlmTest(unittest.TestCase):
    def test_missing_key_gives_actionable_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            build_llm(make_config(llm_api_key=""))
        self.assertIn(".env", str(ctx.exception))

    def test_builds_with_key(self):
        client = build_llm(make_config(llm_api_key="test-key", llm_model="deepseek-flash"))
        self.assertEqual(client.model, "deepseek-flash")


class RunCheckTest(unittest.TestCase):
    def test_stops_before_calling_model_when_no_key(self):
        """没 key 时应该**当场报错**，而不是连一通再失败。"""
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_check(make_config(llm_api_key=""))

        self.assertEqual(code, 1)
        self.assertIn("SAYA_LLM_API_KEY", buffer.getvalue())

    def test_key_is_not_echoed(self):
        """自检输出不许回显密钥 —— 输出常被截图或贴进聊天。"""
        import io
        from contextlib import redirect_stdout

        secret = "sk-thisShouldNeverAppearInOutput123456"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            # onebot 后端会在占用端口后停下，不会真去调模型
            run_check(make_config(llm_api_key=secret, adapter="mock"))

        self.assertNotIn(secret, buffer.getvalue())

    def test_prices_are_shown(self):
        """⚠️ 三档价必须显出来。

        少填一档会**静默**把成本算错（ADR-020），而 `0` 在配置文件里
        和"这个键压根没填"长得一模一样 —— 不显示就没人会发现。
        """
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run_check(
                make_config(
                    llm_api_key="", price_in=2.0, price_cache_hit=0.04, price_out=8.0
                )
            )

        output = buffer.getvalue()
        for price in ("2.0", "0.04", "8.0"):
            self.assertIn(price, output)

    def test_all_zero_prices_are_flagged(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run_check(
                make_config(
                    llm_api_key="", price_in=0.0, price_cache_hit=0.0, price_out=0.0
                )
            )

        self.assertIn("三档价都是 0", buffer.getvalue())

    def test_onebot_port_probe_reports_address(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_check(make_config(adapter="onebot11_http", llm_api_key=""))

        output = buffer.getvalue()
        self.assertEqual(code, 1)  # 因为没有 key
        self.assertIn("端口可用", output)

    def test_port_conflict_is_reported_not_crashed(self):
        import io
        from contextlib import redirect_stdout

        first = build_adapter(make_config(adapter="onebot11_http", onebot_listen_port=0))
        host, port = first.start()
        self.addCleanup(first.close)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_check(
                make_config(
                    adapter="onebot11_http", llm_api_key="k", onebot_listen_port=port
                )
            )
        self.assertEqual(code, 1)
        self.assertIn("端口", buffer.getvalue())


class IsOurOwnBotTest(unittest.TestCase):
    """端口被占时，得能认出**占着它的是不是我们自己的另一个实例**。

    ⚠️ 这个区分不是锦上添花：看不出来的时候，自检只能一律判失败，
    于是"一键启动"按钮**没法重启一个正在跑的机器人**，还甩给用户
    一句"自检没过，先把它修好"。实测就是这么卡住的。
    """

    def config(self, **overrides) -> Config:
        base = replace(
            Config.from_env(dotenv_path=NO_DOTENV),
            onebot_listen_host="127.0.0.1",
            onebot_listen_port=8080,
        )
        return replace(base, **overrides) if overrides else base

    def fake_response(self, body: bytes):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, size: int = -1) -> bytes:
                return body

        return Response()

    def test_recognises_our_own_adapter(self):
        with mock.patch(
            "urllib.request.urlopen",
            return_value=self.fake_response(b"saya_bot onebot adapter ok"),
        ):
            self.assertTrue(is_our_own_bot(self.config()))

    def test_some_other_service_is_not_ours(self):
        with mock.patch(
            "urllib.request.urlopen",
            return_value=self.fake_response(b"<html>some other thing</html>"),
        ):
            self.assertFalse(is_our_own_bot(self.config()))

    def test_unreachable_counts_as_not_ours(self):
        """探不通就当作"不是我们" —— 宁可当成真问题报出来，也别放过。"""
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            self.assertFalse(is_our_own_bot(self.config()))

    def test_wildcard_listen_address_is_probed_on_loopback(self):
        """`0.0.0.0` 是监听地址、不是可连的地址，探测时得换成回环。"""
        seen: list[str] = []

        def spy(url, timeout):
            seen.append(url)
            return self.fake_response(b"saya_bot onebot adapter ok")

        with mock.patch("urllib.request.urlopen", side_effect=spy):
            is_our_own_bot(self.config(onebot_listen_host="0.0.0.0"))

        self.assertIn("127.0.0.1", seen[0])


class RouteValidationTest(unittest.TestCase):
    """看图路线的配置必须**在自检时**就报错，而不是等真收到图片才炸。"""

    def _check(self, **overrides) -> str:
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_check(make_config(llm_api_key="", **overrides))
        self.assertEqual(code, 1)
        return buffer.getvalue()

    def test_legacy_route_name_is_caught_with_hint(self):
        """旧 .env 里的 direct_mllm 已改名 —— 必须给出明确提示。"""
        output = self._check(vision_route="direct_mllm")
        self.assertIn("direct", output)
        self.assertIn("已改名", output)

    def test_route_needing_tools_reports_missing_command(self):
        """delegated / hybrid 已实现，但需要配套的附属 —— 没配要明确说缺哪个配置项。"""
        output = self._check(vision_route="delegated")
        self.assertIn("SAYA_CV_MCP_COMMAND", output)
        self.assertIn("附属", output)

    def test_unknown_route_lists_valid_values(self):
        output = self._check(vision_route="乱写")
        self.assertIn("不认识", output)
        self.assertIn("hybrid", output)

    def test_valid_route_passes_this_gate(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_check(make_config(llm_api_key=""))
        # 仍然因为没 key 而失败，但**不该**报路线问题
        self.assertEqual(code, 1)
        self.assertNotIn("不认识", buffer.getvalue())


class MissingEnvKeysTest(unittest.TestCase):
    """示例文件加了新键，早先复制出去的 .env 不会跟着长 —— 得能提醒。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _pair(self, env_text: str, example_text: str) -> tuple[Path, Path]:
        env = self.dir / ".env"
        example = self.dir / ".env.example"
        env.write_text(env_text, encoding="utf-8")
        example.write_text(example_text, encoding="utf-8")
        return env, example

    def test_reports_keys_only_in_example(self):
        env, example = self._pair("A=1\n", "A=1\nB=2\nC=3\n")
        self.assertEqual(missing_env_keys(env, example), ["B", "C"])

    def test_nothing_missing(self):
        env, example = self._pair("A=1\nB=2\n", "A=1\nB=2\n")
        self.assertEqual(missing_env_keys(env, example), [])

    def test_missing_files_are_tolerated(self):
        self.assertEqual(missing_env_keys(Path("nope.env"), Path("nope.example")), [])


class TracePathTest(unittest.TestCase):
    def test_data_dir_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(data_dir=tmp)
            self.assertEqual(config.data_dir, tmp)


if __name__ == "__main__":
    unittest.main()
