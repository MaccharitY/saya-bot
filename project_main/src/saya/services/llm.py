"""模型通道（FR-3，同时是 FR-9 / 路线 B 的地基）。

用**标准库**直连 OpenAI 兼容端点，刻意不引入 `openai` 包：本机没装任何第三方包，
零依赖意味着 clone 下来就能跑、单测不用摆环境。哪天需要流式或自动重试，
换成 openai SDK 只动这一个文件。

**工具调用（function calling）的管道在这里铺好** —— 这就是"MCP 的空间"：
"工具清单怎么递给模型、模型怎么回工具调用、结果怎么回灌"这套管子，
与"工具从哪来"无关。将来把工具换成 MCP server 提供的，这里一行都不用改。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

JSON = dict[str, Any]
Transport = Callable[[str, dict[str, str], JSON, float], JSON]


class LLMError(RuntimeError):
    """调用模型失败。信息要够，方便在群里/日志里排查。"""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # 原样保留模型给的 JSON 字符串，不替它猜

    def parsed_arguments(self) -> JSON:
        if not self.arguments.strip():
            return {}
        try:
            value = json.loads(self.arguments)
        except json.JSONDecodeError:
            raise LLMError(
                f"工具 {self.name} 的参数不是合法 JSON：{self.arguments[:200]}"
            ) from None
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    tool_calls: tuple[ToolCall, ...]
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost: float
    raw: JSON

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        price_in: float = 0.0,
        price_out: float = 0.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("未配置 API key（照 .env.example 建一个 .env）")
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._price_in = price_in
        self._price_out = price_out
        self._transport = transport or _urllib_transport

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: Sequence[JSON],
        *,
        tools: Iterable[JSON] | None = None,
        tool_choice: str | JSON | None = None,
        temperature: float = 0.7,
    ) -> ChatResult:
        payload: JSON = {
            "model": self._model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = list(tools)
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        data = self._transport(self._endpoint, headers, payload, self._timeout)
        latency_ms = int((time.monotonic() - started) * 1000)
        return _parse_result(data, latency_ms, self._price_in, self._price_out)


def _urllib_transport(url: str, headers: dict[str, str], payload: JSON, timeout: float) -> JSON:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise LLMError(f"模型返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"连不上模型端点（{url}）：{exc.reason}") from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        raise LLMError(f"模型返回的不是 JSON：{body[:200]}") from None
    if not isinstance(result, dict):
        raise LLMError(f"模型返回的 JSON 顶层不是对象：{body[:200]}")
    return result


def _parse_result(data: JSON, latency_ms: int, price_in: float, price_out: float) -> ChatResult:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"响应里没有 choices：{str(data)[:300]}")
    message = choices[0].get("message") or {}

    calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        calls.append(
            ToolCall(
                id=str(call.get("id", "")),
                name=str(function.get("name", "")),
                arguments=str(function.get("arguments", "")),
            )
        )

    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)

    return ChatResult(
        text=(message.get("content") or "").strip(),
        tool_calls=tuple(calls),
        model=str(data.get("model") or ""),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost=estimate_cost(tokens_in, tokens_out, price_in, price_out),
        raw=data,
    )


def estimate_cost(tokens_in: int, tokens_out: int, price_in: float, price_out: float) -> float:
    """费用（元）。price_* 的单位是「元 / 百万 token」。

    ⚠️ 价格表要你自己从官网抄进 `.env` —— 代码里**不写死任何价格数字**，
    写死了会过期，而且会让报告里的成本核算失去依据。
    """
    return round(tokens_in / 1_000_000 * price_in + tokens_out / 1_000_000 * price_out, 6)


# ---- 消息成形的工具函数（对 OpenAI 兼容格式的封装，别处不要手写这些结构）----


def text_message(role: str, text: str) -> JSON:
    return {"role": role, "content": text}


def text_block(text: str) -> JSON:
    return {"type": "text", "text": text}


def image_block(data_url: str) -> JSON:
    """图片内容块。传 `data:image/jpeg;base64,...` 形式（官方支持内联 base64）。"""
    return {"type": "image_url", "image_url": {"url": data_url}}


def tool_result_message(tool_call_id: str, content: str) -> JSON:
    """把工具的执行结果回灌给模型。

    **MCP 工具将来也走这一条路** —— 无论工具是本地函数还是 MCP server 提供的，
    回灌给模型的格式是一样的。
    """
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
