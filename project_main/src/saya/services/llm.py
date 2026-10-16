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
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from saya.services import net

JSON = dict[str, Any]
Transport = Callable[[str, dict[str, str], JSON, float], JSON]

logger = logging.getLogger("saya.llm")

DEFAULT_RETRIES = 2  # 网络层失败时最多再试几次（**不含 4xx** —— 那是明确答复，重试无意义）
RETRY_DELAY = 0.8  # 首次重试前等多久，之后翻倍


class LLMError(RuntimeError):
    """调用模型失败。信息要够，方便在群里/日志里排查。

    `retryable` 说明**再试一次有没有意义**：连不上、超时、5xx 是瞬时的，
    重试常常就好了；4xx 和"返回的不是 JSON"是明确答复，重试只是浪费时间。
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


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
    # ⚠️ 这三个是**这次调用当时才有的事实**，事后再也拿不回来。
    # 价格可以重抄、成本可以重算，但"这一问命中了多少缓存"只存在于**响应**里 ——
    # 不记，跑完那次实验就永久丢了，要补只能重跑（重花钱）。
    # 缓存命中价与未命中价差一两个数量级，报告的"成本"一栏全靠它。
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # ⚠️ `reasoning_tokens` 是 `tokens_out` 的**子集**，不是额外的一笔
    # （2026-10-16 实测：completion_tokens=56，其中 reasoning_tokens=54）。
    # 它只用来回答"这次输出里有多少是思考"，**绝不能再加进成本** —— 加一遍就是双倍计。
    reasoning_tokens: int = 0

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
        price_cache_hit: float = 0.0,
        reasoning_effort: str | None = None,
        retries: int = DEFAULT_RETRIES,
        retry_delay: float = RETRY_DELAY,
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
        self._price_cache_hit = price_cache_hit
        self._reasoning_effort = (reasoning_effort or "").strip().lower()
        self._retries = max(0, retries)
        self._retry_delay = max(0.0, retry_delay)
        self._temperature_ignored_warned = False
        self._transport = transport or _urllib_transport

    @property
    def model(self) -> str:
        return self._model

    @property
    def thinking(self) -> bool:
        """开着思考模式吗。⚠️ 开着的时候 temperature 会被服务端**无视**。"""
        return self._reasoning_effort not in ("", "none", "off", "disabled")

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
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
            self._warn_temperature_once(temperature)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # ⚠️ **一次失败就放弃，在群里表现就是"机器人没反应"。**
        # 这台机器的网络本来就时不时抽一下（见 services/net.py 记的那些 DNS 问题），
        # 实测撞过：萌娘百科查成功了，紧接着再调模型生成回复时来一发
        # `SSL: UNEXPECTED_EOF_WHILE_READING` —— 用户看到的就是它突然不理人。
        # 所以对**瞬时**失败重试；明确答复（4xx、返回的不是 JSON）不重试，那是浪费时间。
        delay = self._retry_delay
        for attempt in range(self._retries + 1):
            started = time.monotonic()
            try:
                data = self._transport(self._endpoint, headers, payload, self._timeout)
            except LLMError as exc:
                if attempt >= self._retries or not getattr(exc, "retryable", False):
                    raise
                logger.warning(
                    "模型调用失败（第 %d 次），%.1fs 后重试：%s", attempt + 1, delay, exc
                )
                time.sleep(delay)
                delay *= 2
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            if attempt:
                logger.info("重试第 %d 次成功", attempt)
            return _parse_result(
                data, latency_ms, self._price_in, self._price_out, self._price_cache_hit
            )

        raise LLMError("重试完了还是失败")  # 到不了这儿，循环里要么 return 要么 raise

    def _warn_temperature_once(self, temperature: float) -> None:
        """⚠️ **思考模式下 temperature 不生效**（见 DeepSeek 官方 API 文档）。

        这是个**静默失效**：参数照样发出去，服务端照收，但根本不看。
        所以配置里写着"固定温度 0.8"的实验，在思考模式下其实没有任何温度控制 ——
        这种坑必须在第一次就喊出来，不能等对数据的时候才发现。
        """
        if self._temperature_ignored_warned or not self.thinking:
            return
        self._temperature_ignored_warned = True
        logger.warning(
            "思考模式（reasoning_effort=%s）下 temperature 不生效 —— "
            "发出去的 %.2f 会被服务端无视。温度对照实验请先设为 none。",
            self._reasoning_effort,
            temperature,
        )


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
        # 5xx 是**服务端自己出问题**，多半是一时的；4xx 是我们发错了，重试没意义
        raise LLMError(
            f"模型返回 HTTP {exc.code}：{detail}", retryable=exc.code >= 500
        ) from exc
    except urllib.error.URLError as exc:
        # 连不上时清一次 DNS 缓存：万一缓存里的 IP 已失效（CDN 轮换），
        # 清掉让下次重新解析，比一直撞同一个死 IP 好
        net.clear()
        raise LLMError(f"连不上模型端点（{url}）：{exc.reason}", retryable=True) from exc
    except OSError as exc:
        # ⚠️ 这条不能少：**卡住不动而超时**抛的是 `TimeoutError`，
        # 它是 `OSError` 但**不是** `URLError` —— 只捕 URLError 的话它会裸穿出去。
        # 而调用方（pipeline / 实验执行器）捕的是 `LLMError`，于是：
        # 机器人埋点里写一句"未捕获的异常"（看不出是连不上模型），
        # 实验则直接中断、把已经花掉钱的结果全丢在内存里。
        net.clear()
        raise LLMError(
            f"与模型端点通信失败（{url}）：{type(exc).__name__}: {exc}", retryable=True
        ) from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        raise LLMError(f"模型返回的不是 JSON：{body[:200]}") from None
    if not isinstance(result, dict):
        raise LLMError(f"模型返回的 JSON 顶层不是对象：{body[:200]}")
    return result


def _parse_result(
    data: JSON,
    latency_ms: int,
    price_in: float,
    price_out: float,
    price_cache_hit: float = 0.0,
) -> ChatResult:
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
    cache_hit, cache_miss = _cache_split(usage, tokens_in)
    reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)

    return ChatResult(
        text=(message.get("content") or "").strip(),
        tool_calls=tuple(calls),
        model=str(data.get("model") or ""),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost=estimate_cost(
            tokens_in,
            tokens_out,
            price_in,
            price_out,
            cache_hit_tokens=cache_hit,
            price_cache_hit=price_cache_hit,
        ),
        raw=data,
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
        reasoning_tokens=reasoning,
    )


def _cache_split(usage: JSON, tokens_in: int) -> tuple[int, int]:
    """从 `usage` 里取出「输入中命中缓存 / 未命中」的 token 数。

    DeepSeek 原生字段是 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`；
    OpenAI 兼容层走 `prompt_tokens_details.cached_tokens`。两个都读。

    **读不到就返回 `(0, tokens_in)`** —— 即整段输入按未命中算。方向是保守的：
    未命中比命中贵得多，宁可把成本算高，也不要凭空报一个低数字。
    """
    details = usage.get("prompt_tokens_details") or {}
    raw_hit = usage.get("prompt_cache_hit_tokens")
    if raw_hit is None:
        raw_hit = details.get("cached_tokens")
    if raw_hit is None:
        return 0, tokens_in

    hit = max(0, min(int(raw_hit), tokens_in))
    raw_miss = usage.get("prompt_cache_miss_tokens")
    miss = tokens_in - hit if raw_miss is None else max(0, int(raw_miss))
    return hit, miss


def estimate_cost(
    tokens_in: int,
    tokens_out: int,
    price_in: float,
    price_out: float,
    *,
    cache_hit_tokens: int = 0,
    price_cache_hit: float = 0.0,
) -> float:
    """费用（元）。price_* 的单位是「元 / 百万 token」。

    ⚠️ 价格表要你自己从官网抄进 `.env` —— 代码里**不写死任何价格数字**，
    写死了会过期，而且会让报告里的成本核算失去依据。

    ⚠️ **输入是两档价**：命中缓存比未命中便宜一两个数量级（实测 0.02 vs 1 元/M）。
    `tokens_in` 是**总输入**，`cache_hit_tokens` 是其中命中的那部分。
    不传 `cache_hit_tokens` 就是全按未命中算 —— 那是**上界**，不会低估。
    同理 `price_cache_hit` 不填（0）会把命中部分算成免费，也是往低算的方向，
    所以两个都要填：**只填 `SAYA_PRICE_IN` 会高估，只填命中价会低估。**

    ⚠️ `tokens_out` 要传 `completion_tokens` 的**原值**：思考 token 已经含在里面了
    （实测 56 里含 54），**不要把 `reasoning_tokens` 再加一遍**。
    """
    hit = max(0, min(cache_hit_tokens, tokens_in))
    miss = tokens_in - hit
    return round(
        (miss * price_in + hit * price_cache_hit + tokens_out * price_out) / 1_000_000,
        6,
    )


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


def assistant_tool_message(result: ChatResult) -> JSON:
    """把"模型要求调用工具"这一轮原样回填进对话。

    ⚠️ 这条**必须**回填：协议要求每个 tool 结果前面都得有一条提出该调用的 assistant 消息，
    少了它下一轮会被服务端直接拒掉。参数按模型给的**原始字符串**回填，不要重新序列化 ——
    一转手就可能把它的格式改掉，而服务端只认它自己发出来的那份。
    """
    return {
        "role": "assistant",
        "content": result.text or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in result.tool_calls
        ],
    }
