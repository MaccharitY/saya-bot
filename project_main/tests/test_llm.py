"""模型通道的单元测试 —— 全部用假 transport，**不发真请求、不花钱**。"""

from __future__ import annotations

import json
import logging
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.services.llm import (  # noqa: E402
    LLMClient,
    LLMError,
    ToolCall,
    _urllib_transport,
    estimate_cost,
    image_block,
    text_message,
    tool_result_message,
)


def fake_response(
    *,
    text: str = "你好",
    tool_calls: list[dict] | None = None,
    tokens_in: int = 100,
    tokens_out: int = 20,
) -> dict:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": message}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


class FakeTransport:
    """记录请求、返回预设响应。代替真实 HTTP。"""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> dict:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        return self.response


def make_client(transport, **overrides) -> LLMClient:
    options = {
        "base_url": "https://api.deepseek.com",
        "api_key": "test-key",
        "model": "deepseek-flash",
        "transport": transport,
    }
    options.update(overrides)
    return LLMClient(**options)


class ClientTest(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(LLMError):
            LLMClient(base_url="x", api_key="", model="m", transport=FakeTransport({}))

    def test_endpoint_and_headers(self):
        transport = FakeTransport(fake_response())
        make_client(transport).chat([text_message("user", "在吗")])

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")

    def test_base_url_trailing_slash_is_tolerated(self):
        transport = FakeTransport(fake_response())
        make_client(transport, base_url="https://api.deepseek.com/").chat([text_message("user", "hi")])
        self.assertEqual(transport.calls[0]["url"], "https://api.deepseek.com/chat/completions")

    def test_payload_carries_model_and_messages(self):
        transport = FakeTransport(fake_response())
        make_client(transport).chat([text_message("system", "你是助手"), text_message("user", "你好")])

        payload = transport.calls[0]["payload"]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertNotIn("tools", payload)

    def test_parses_text_and_usage(self):
        result = make_client(FakeTransport(fake_response(text=" 猫 ", tokens_in=10, tokens_out=5))).chat(
            [text_message("user", "hi")]
        )
        self.assertEqual(result.text, "猫")  # 前后空白被去掉
        self.assertEqual(result.tokens_in, 10)
        self.assertEqual(result.tokens_out, 5)
        self.assertFalse(result.wants_tools)


class ToolCallTest(unittest.TestCase):
    def _tool_call(self) -> dict:
        return {
            "id": "call_1",
            "type": "function",
            "function": {"name": "detect", "arguments": '{"类别": "猫"}'},
        }

    def test_tools_are_sent_when_provided(self):
        transport = FakeTransport(fake_response())
        tools = [{"type": "function", "function": {"name": "detect", "parameters": {}}}]
        make_client(transport).chat([text_message("user", "hi")], tools=tools)
        self.assertEqual(transport.calls[0]["payload"]["tools"], tools)

    def test_tool_choice_passed_through(self):
        transport = FakeTransport(fake_response())
        make_client(transport).chat([text_message("user", "hi")], tool_choice="auto")
        self.assertEqual(transport.calls[0]["payload"]["tool_choice"], "auto")

    def test_parses_tool_calls(self):
        transport = FakeTransport(fake_response(text="", tool_calls=[self._tool_call()]))
        result = make_client(transport).chat([text_message("user", "hi")])

        self.assertTrue(result.wants_tools)
        self.assertEqual(result.tool_calls[0].name, "detect")
        self.assertEqual(result.tool_calls[0].parsed_arguments(), {"类别": "猫"})

    def test_empty_arguments_become_empty_dict(self):
        call = ToolCall(id="1", name="ping", arguments="")
        self.assertEqual(call.parsed_arguments(), {})

    def test_invalid_arguments_raise_clearly(self):
        call = ToolCall(id="1", name="detect", arguments="{不是 JSON")
        with self.assertRaises(LLMError):
            call.parsed_arguments()

    def test_non_object_arguments_become_empty_dict(self):
        call = ToolCall(id="1", name="detect", arguments="[1,2,3]")
        self.assertEqual(call.parsed_arguments(), {})


class ErrorHandlingTest(unittest.TestCase):
    def test_missing_choices_raises(self):
        transport = FakeTransport({"model": "x", "usage": {}})
        with self.assertRaises(LLMError):
            make_client(transport).chat([text_message("user", "hi")])

    def test_tool_result_message_shape(self):
        message = tool_result_message("call_1", "检测到 3 只猫")
        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call_1")

    def test_image_block_shape(self):
        block = image_block("data:image/jpeg;base64,AAAA")
        self.assertEqual(block["type"], "image_url")
        self.assertEqual(block["image_url"]["url"], "data:image/jpeg;base64,AAAA")


class CostTest(unittest.TestCase):
    def test_zero_price_means_zero_cost(self):
        self.assertEqual(estimate_cost(1_000_000, 1_000_000, 0.0, 0.0), 0.0)

    def test_computes_per_million_tokens(self):
        # 1 元/百万输入、2 元/百万输出
        self.assertEqual(estimate_cost(500_000, 500_000, 1.0, 2.0), 1.5)

    def test_result_carries_cost(self):
        transport = FakeTransport(fake_response(tokens_in=1_000_000, tokens_out=0))
        result = make_client(transport, price_in=3.0, price_out=9.0).chat(
            [text_message("user", "hi")]
        )
        self.assertEqual(result.cost, 3.0)


class ReasoningEffortTest(unittest.TestCase):
    """思考模式强度（2026-10-16 加的）。

    ⚠️ 这里守的是一个**静默失效**：思考模式下 temperature 会被服务端无视。
    参数照样发出去、服务端照收，但根本不看 —— 如果不喊一声，
    "我们固定了温度"这句话就是假的，而实验数据不会自己告诉你。
    """

    def setUp(self) -> None:
        self.transport = FakeTransport(fake_response(text="在的"))
        self._logger = logging.getLogger("saya.llm")
        self._records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(inner, record: logging.LogRecord) -> None:
                self._records.append(record)

        self._handler = Capture(level=logging.WARNING)
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.WARNING)

    def tearDown(self) -> None:
        self._logger.removeHandler(self._handler)

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self._records if r.levelno >= logging.WARNING]

    def test_effort_is_sent_when_configured(self):
        client = make_client(self.transport, reasoning_effort="high")
        client.chat([{"role": "user", "content": "在吗"}])
        self.assertEqual(self.transport.calls[0]["payload"]["reasoning_effort"], "high")

    def test_effort_is_omitted_when_not_configured(self):
        """没配就**不传这个键** —— 别替服务端决定默认值。"""
        client = make_client(self.transport)
        client.chat([{"role": "user", "content": "在吗"}])
        self.assertNotIn("reasoning_effort", self.transport.calls[0]["payload"])

    def test_none_means_thinking_is_off(self):
        client = make_client(self.transport, reasoning_effort="none")
        self.assertFalse(client.thinking)

    def test_high_means_thinking_is_on(self):
        self.assertTrue(make_client(self.transport, reasoning_effort="high").thinking)

    def test_unset_means_thinking_is_off(self):
        self.assertFalse(make_client(self.transport).thinking)

    def test_temperature_no_op_is_warned_about(self):
        client = make_client(self.transport, reasoning_effort="high")
        client.chat([{"role": "user", "content": "在吗"}], temperature=0.8)

        self.assertTrue(
            any("temperature 不生效" in text for text in self.warnings()),
            "思考模式下温度失效这件事必须喊出来",
        )

    def test_the_warning_is_said_once_not_every_call(self):
        """每次都喊就成噪音了，喊一次够了。"""
        client = make_client(self.transport, reasoning_effort="high")
        for _ in range(5):
            client.chat([{"role": "user", "content": "在吗"}], temperature=0.8)

        hits = [t for t in self.warnings() if "temperature 不生效" in t]
        self.assertEqual(len(hits), 1)

    def test_no_warning_when_thinking_is_off(self):
        client = make_client(self.transport, reasoning_effort="none")
        client.chat([{"role": "user", "content": "在吗"}], temperature=0.8)
        self.assertEqual(self.warnings(), [])


class TransportErrorTest(unittest.TestCase):
    """⚠️ **卡住超时抛的是 `TimeoutError`** —— 它是 `OSError`，但**不是** `URLError`。

    只捕 `URLError` 的话它会裸穿出去，而调用方捕的是 `LLMError`：
    机器人埋点里只会写一句"未捕获的异常"（看不出是连不上模型），
    实验则直接中断、把**已经花掉钱**的结果全丢在内存里。
    """

    def call(self, error: Exception) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=error):
            _urllib_transport(
                "https://api.deepseek.com/chat/completions",
                {"Authorization": "Bearer x"},
                {"model": "m"},
                1.0,
            )

    def test_timeout_becomes_llm_error(self):
        with self.assertRaises(LLMError):
            self.call(TimeoutError("timed out"))

    def test_connection_reset_becomes_llm_error(self):
        with self.assertRaises(LLMError):
            self.call(ConnectionResetError("connection reset by peer"))

    def test_url_error_still_becomes_llm_error(self):
        with self.assertRaises(LLMError):
            self.call(urllib.error.URLError("名字解析失败"))


class RealRequestShapeTest(unittest.TestCase):
    """payload 必须能被 json 序列化 —— 免得踩到不可序列化对象的坑。"""

    def test_payload_is_json_serializable(self):
        transport = FakeTransport(fake_response())
        make_client(transport).chat(
            [
                text_message("system", "人格"),
                {"role": "user", "content": [{"type": "text", "text": "看图"}, image_block("data:x")]},
            ],
            tools=[{"type": "function", "function": {"name": "detect", "parameters": {}}}],
        )
        json.dumps(transport.calls[0]["payload"], ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
