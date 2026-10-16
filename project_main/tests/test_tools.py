"""工具调用机制的单元测试。

全程离线：脚本化的假 transport，按顺序吐出预设响应。
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.core.tools import Tool, ToolBox, object_schema, run_with_tools  # noqa: E402
from saya.services.llm import LLMClient  # noqa: E402

# 故意让工具失败的那几个用例会往日志里写东西，看着像报错，这里静音。
_saya_logger = logging.getLogger("saya")
_saya_logger.addHandler(logging.NullHandler())
_saya_logger.propagate = False


def text_response(text: str = "在的", *, tokens_in: int = 100, tokens_out: int = 10) -> dict:
    return {
        "model": "deepseek-flash",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


def tool_response(
    name: str = "echo",
    arguments: str = "{}",
    *,
    call_id: str = "call_1",
    tokens_in: int = 100,
    tokens_out: int = 20,
) -> dict:
    return {
        "model": "deepseek-flash",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


class ScriptedTransport:
    """按脚本依次返回响应，并记下每次请求。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout) -> dict:
        self.calls.append(payload)
        if not self.responses:
            raise AssertionError("模型被调用的次数比脚本预设的多")
        return self.responses.pop(0)


def make_client(transport: ScriptedTransport) -> LLMClient:
    return LLMClient(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-flash",
        transport=transport,
    )


def echo_tool(handler=None) -> Tool:
    def default(args: dict) -> str:
        return f"收到 {args.get('x')}"

    return Tool(
        name="echo",
        description="回声",
        parameters=object_schema({"x": {"type": "string"}}),
        handler=handler or default,
    )


class ToolBoxTest(unittest.TestCase):
    def test_unknown_tool_is_reported_not_raised(self):
        box = ToolBox([echo_tool()])
        self.assertIn("没有名为", box.call("nope", "{}"))

    def test_bad_json_is_reported_not_raised(self):
        box = ToolBox([echo_tool()])
        self.assertIn("不是合法 JSON", box.call("echo", "{坏掉的"))

    def test_handler_exception_becomes_text(self):
        def boom(args: dict) -> str:
            raise RuntimeError("炸了")

        box = ToolBox([echo_tool(boom)])
        result = box.call("echo", "{}")
        self.assertIn("执行失败", result)
        self.assertIn("炸了", result)

    def test_empty_arguments_are_treated_as_no_arguments(self):
        box = ToolBox([echo_tool(lambda args: f"参数 {len(args)} 个")])
        self.assertIn("参数 0 个", box.call("echo", ""))

    def test_oversized_output_is_truncated(self):
        box = ToolBox([echo_tool(lambda args: "啊" * 5000)])
        self.assertLessEqual(len(box.call("echo", "{}")), 2100)

    def test_specs_shape(self):
        spec = ToolBox([echo_tool()]).specs()[0]
        self.assertEqual(spec["type"], "function")
        self.assertEqual(spec["function"]["name"], "echo")
        self.assertEqual(spec["function"]["parameters"]["required"], [])


class RunWithToolsTest(unittest.TestCase):
    def run_it(self, responses, toolbox=None, *, max_rounds=3, messages=None):
        transport = ScriptedTransport(responses)
        client = make_client(transport)
        messages = messages or [{"role": "user", "content": "你好"}]
        run = run_with_tools(
            client,
            messages,
            toolbox if toolbox is not None else ToolBox(),
            temperature=0.8,
            max_rounds=max_rounds,
        )
        return run, transport, messages

    def test_no_tools_is_a_single_call(self):
        run, transport, _ = self.run_it([text_response("在的")])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(run.text, "在的")
        self.assertEqual(run.rounds, 0)
        self.assertNotIn("tools", transport.calls[0])

    def test_tool_call_is_executed_and_fed_back(self):
        run, transport, _ = self.run_it(
            [tool_response("echo", '{"x": "喵"}'), text_response("查到了")],
            ToolBox([echo_tool()]),
        )

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(run.text, "查到了")
        self.assertEqual(run.rounds, 1)
        self.assertEqual(run.calls, ("echo",))

        follow_up = transport.calls[1]["messages"]
        self.assertEqual([m["role"] for m in follow_up], ["user", "assistant", "tool"])
        self.assertIn("收到 喵", follow_up[-1]["content"])
        self.assertEqual(follow_up[-1]["tool_call_id"], "call_1")

    def test_assistant_tool_message_replays_the_original_arguments(self):
        """参数要按模型给的**原始字符串**回填，不能重新序列化。"""
        _, transport, _ = self.run_it(
            [tool_response("echo", '{"x":   "空格要保持"}'), text_response("好")],
            ToolBox([echo_tool()]),
        )
        replayed = transport.calls[1]["messages"][1]["tool_calls"][0]
        self.assertEqual(replayed["function"]["arguments"], '{"x":   "空格要保持"}')

    def test_a_failing_tool_does_not_break_the_reply(self):
        def boom(args: dict) -> str:
            raise RuntimeError("网络断了")

        run, transport, _ = self.run_it(
            [tool_response("echo", "{}"), text_response("查不到，先不聊这个")],
            ToolBox([echo_tool(boom)]),
        )
        self.assertEqual(run.text, "查不到，先不聊这个")
        self.assertIn("执行失败", transport.calls[1]["messages"][-1]["content"])

    def test_usage_is_summed_across_rounds(self):
        """⚠️ 调了工具的那条回复，真实成本是"问一次 + 再问一次"的总和。

        只记最后一轮会低估 —— 而成本对比正是要拿这些数字做的。
        """
        run, _, _ = self.run_it(
            [
                tool_response("echo", "{}", tokens_in=100, tokens_out=20),
                text_response("好", tokens_in=200, tokens_out=30),
            ],
            ToolBox([echo_tool()]),
        )
        self.assertEqual(run.tokens_in, 300)
        self.assertEqual(run.tokens_out, 50)
        self.assertAlmostEqual(run.cost, 0.0)

    def test_running_out_of_rounds_still_produces_an_answer(self):
        """轮次用尽却还在要工具时，必须**摘掉工具再问一次**。

        不做这一步就会返回空文本，用户看到的是"发了消息没反应"。
        """
        max_rounds = 2
        script = [tool_response("echo", "{}") for _ in range(max_rounds + 1)]
        script.append(text_response("硬着头皮答"))

        run, transport, _ = self.run_it(script, ToolBox([echo_tool()]), max_rounds=max_rounds)

        self.assertEqual(run.text, "硬着头皮答")
        self.assertEqual(run.rounds, max_rounds)
        self.assertNotIn("tools", transport.calls[-1], "收尾那一轮不该再带工具")

    def test_stripping_tools_also_tells_the_model_they_are_gone(self):
        """⚠️ **光摘工具不够，还得说一声。**

        不说的话模型不知道自己没工具了，就把**工具调用标记当普通文本吐出来** ——
        2026-10-16 实测，整段 DSML 被发进了群里。
        """
        max_rounds = 2
        script = [tool_response("echo", "{}") for _ in range(max_rounds + 1)]
        script.append(text_response("硬着头皮答"))

        _, transport, _ = self.run_it(script, ToolBox([echo_tool()]), max_rounds=max_rounds)

        last_messages = transport.calls[-1]["messages"]
        joined = " ".join(str(m.get("content", "")) for m in last_messages)
        self.assertIn("工具接口现在不可用", joined)

    def test_callers_messages_are_not_mutated(self):
        original = [{"role": "user", "content": "你好"}]
        _, _, messages = self.run_it(
            [tool_response("echo", "{}"), text_response("好")],
            ToolBox([echo_tool()]),
            messages=original,
        )
        self.assertEqual(len(messages), 1)

    def test_multiple_tool_calls_in_one_round_are_all_run(self):
        two = tool_response("echo", '{"x":"一"}', call_id="a")
        two["choices"][0]["message"]["tool_calls"].append(
            {
                "id": "b",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"x":"二"}'},
            }
        )
        run, transport, _ = self.run_it([two, text_response("好")], ToolBox([echo_tool()]))

        self.assertEqual(run.calls, ("echo", "echo"))
        roles = [m["role"] for m in transport.calls[1]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "tool"])


if __name__ == "__main__":
    unittest.main()
