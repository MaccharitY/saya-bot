"""工具调用的执行机制 —— "MCP 的空间"在这里真正落地。

`services/llm.py` 铺的是**管道**：工具清单怎么递给模型、结果怎么回灌。
这里加的是**轮子**：拿到工具调用之后真去执行、把结果喂回去、再问一轮，
直到模型给出答复。

⚠️ 这里刻意不关心「工具从哪来」：本地函数、MCP server、将来的别的什么，
到这一步都只是一个 `Tool`。

**工具出错不许把主流程带崩**：异常一律转成一段错误文本喂回模型，
让它自己决定换个说法，或者干脆告诉用户查不到。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from saya.services.llm import (
    ChatResult,
    LLMClient,
    assistant_tool_message,
    tool_result_message,
)

JSON = dict[str, Any]
logger = logging.getLogger("saya.tools")

DEFAULT_MAX_ROUNDS = 3  # 最多让模型连着调几轮工具
MAX_TOOL_OUTPUT_CHARS = 2000  # 单个工具的返回给模型的上限


@dataclass(frozen=True, slots=True)
class Tool:
    """一个能给模型用的工具。`handler` 收解析好的参数字典，返回一段文本。"""

    name: str
    description: str
    parameters: JSON
    handler: Callable[[JSON], str]

    def spec(self) -> JSON:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def object_schema(properties: JSON, *, required: Sequence[str] = ()) -> JSON:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
    }


class ToolBox:
    def __init__(self, tools: Sequence[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    def __bool__(self) -> bool:
        return bool(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> list[JSON]:
        return [tool.spec() for tool in self._tools.values()]

    def call(self, name: str, arguments: str) -> str:
        """执行一次工具调用。**永不抛异常** —— 出错就把错误当结果喂回去。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"没有名为「{name}」的工具。可用的是：{', '.join(self._tools) or '（一个都没有）'}"

        try:
            parsed = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return f"参数不是合法 JSON：{arguments[:200]}"
        if not isinstance(parsed, dict):
            parsed = {}

        try:
            output = tool.handler(parsed)
        except Exception as exc:  # noqa: BLE001 - 工具坏了不该带崩整条对话
            logger.warning("工具 %s(%s) 执行失败：%s", name, _brief(arguments), exc)
            return f"工具「{name}」执行失败：{type(exc).__name__}: {exc}"

        text = str(output).strip() or "（这个工具没有返回内容）"
        if len(text) > MAX_TOOL_OUTPUT_CHARS:
            text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n…（结果太长，已截断）"
        # ⚠️ **参数一定要记。** 只记工具名和返回长度，出问题时根本不知道
        # 它查的是什么 —— 实测撞过：6 次查询全返回"没找到"，
        # 而日志里只看得到"返回 24 字"，完全无线索。
        logger.info("工具 %s(%s) → %d 字", name, _brief(arguments), len(text))
        return text


def _brief(arguments: str, limit: int = 60) -> str:
    """把参数压成一行，给日志用。"""
    flat = " ".join(str(arguments).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


@dataclass(frozen=True, slots=True)
class ToolRun:
    """跑完工具之后的最终答复，以及**几轮加起来**的账。

    把各轮的钱与时间加起来很重要：调了工具的那条回复，
    真实成本是"问一次 + 调一次 + 再问一次"的总和，只记最后一轮会低估。
    """

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost: float
    rounds: int
    calls: tuple[str, ...] = ()
    # 同理累加：每一轮的缓存命中都是单独的事实，只留总数就对不上账了
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0


def run_with_tools(
    client: LLMClient,
    messages: Sequence[JSON],
    toolbox: ToolBox,
    *,
    temperature: float,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> ToolRun:
    """把对话跑完，中间允许模型调工具。

    没有工具时就退化成一次普通调用（不会多花任何钱）。
    """
    history = list(messages)  # 复制一份，别把调用方的列表改了
    specs = toolbox.specs() or None

    tokens_in = tokens_out = latency_ms = 0
    cache_hit = cache_miss = reasoning = 0
    cost = 0.0
    calls: list[str] = []
    rounds = 0

    def account(result: ChatResult) -> None:
        nonlocal tokens_in, tokens_out, latency_ms, cost, cache_hit, cache_miss, reasoning
        tokens_in += result.tokens_in
        tokens_out += result.tokens_out
        latency_ms += result.latency_ms
        cost += result.cost
        cache_hit += result.cache_hit_tokens
        cache_miss += result.cache_miss_tokens
        reasoning += result.reasoning_tokens

    result = client.chat(history, tools=specs, temperature=temperature)

    while True:
        account(result)

        if not result.wants_tools:
            break

        if rounds >= max_rounds:
            # 轮次用尽却还在要工具：**把工具摘掉**再问一次，逼它用手上已有的信息作答。
            # 不做这一步的话这里会返回空文本，用户看到的就是"发了消息没反应"。
            logger.info("工具轮次用尽（%d 轮），摘掉工具再收一次尾", rounds)
            result = client.chat(history, temperature=temperature)
            account(result)
            break

        rounds += 1
        history.append(assistant_tool_message(result))
        for call in result.tool_calls:
            calls.append(call.name)
            output = toolbox.call(call.name, call.arguments)
            logger.info("工具 %s：返回 %d 字", call.name, len(output))
            history.append(tool_result_message(call.id, output))

        result = client.chat(history, tools=specs, temperature=temperature)

    return ToolRun(
        text=result.text,
        model=result.model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost=round(cost, 6),
        rounds=rounds,
        calls=tuple(calls),
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
        reasoning_tokens=reasoning,
    )
