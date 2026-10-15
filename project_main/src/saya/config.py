"""配置加载。

优先级：真实环境变量 > .env 文件 > 代码默认值。
密钥只从环境读取，**永不写进仓库**（见 CLAUDE.md 红线）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """配置有问题。信息必须指出**是哪一项**、错在哪。"""


def parse_dotenv(text: str) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE。忽略空行、整行注释，**以及行尾注释**。

    行尾注释的规则与常见 dotenv 实现一致：值没被引号包住时，只有 `#` 前面是空白
    才算注释起点 —— 这样值里带 `#` 的密钥不会被截断。

    ⚠️ 行尾注释这条不是可选项：`.env.example` 里就大量使用它，
    不处理的话用户照抄一份就会崩（曾经真的崩过）。
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = _strip_inline_comment(value.strip())
    return result


def _strip_inline_comment(value: str) -> str:
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]

    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            return value[:index].strip()
    return value


def load_env(dotenv_path: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if dotenv_path is not None and dotenv_path.is_file():
        env.update(parse_dotenv(dotenv_path.read_text(encoding="utf-8")))
    env.update(os.environ)
    return env


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _split_csv_int(raw: str, *, key: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in _split_csv(raw):
        try:
            values.append(int(part))
        except ValueError:
            raise ConfigError(
                f"配置项 {key} 只能是逗号分隔的数字，但读到了 {part!r}"
            ) from None
    return tuple(values)


def _optional_int(raw: str) -> int | None:
    return int(raw) if raw.strip() else None


@dataclass(frozen=True, slots=True)
class Config:
    adapter: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    vision_model: str
    vision_route: str
    vision_enable: bool
    temperature: float
    vision_temperature: float
    trigger_mode: str
    trigger_keywords: tuple[str, ...]
    group_allowlist: tuple[int, ...]
    group_denylist: tuple[int, ...]
    reply_probability: float
    rate_limit_per_group_per_min: int
    trace_enabled: bool
    trace_path: str
    experiment_tag: str
    data_dir: str
    onebot_api_base: str
    onebot_listen_host: str
    onebot_listen_port: int
    onebot_event_path: str
    onebot_access_token: str
    self_id: int | None
    price_in: float
    price_out: float
    dns_cache_ttl: float

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> Config:
        env = load_env(dotenv_path)

        def get(key: str, default: str = "") -> str:
            return env.get(f"SAYA_{key}", default)

        return cls(
            adapter=get("ADAPTER", "mock"),
            llm_base_url=get("LLM_BASE_URL", "https://api.deepseek.com"),
            llm_model=get("LLM_MODEL", "deepseek-flash"),
            llm_api_key=get("LLM_API_KEY"),
            vision_model=get("VISION_MODEL", "deepseek-flash"),
            vision_route=get("VISION_ROUTE", "direct"),
            vision_enable=get("VISION_ENABLE", "true").lower() in _TRUE,
            temperature=float(get("TEMPERATURE", "0.8")),
            # 看图任务要的是「事实描述」，温度压低以减少编造
            vision_temperature=float(get("VISION_TEMPERATURE", "0.2")),
            trigger_mode=get("TRIGGER_MODE", "mention"),
            trigger_keywords=_split_csv(get("TRIGGER_KEYWORDS")),
            group_allowlist=_split_csv_int(get("GROUP_ALLOWLIST"), key="SAYA_GROUP_ALLOWLIST"),
            group_denylist=_split_csv_int(get("GROUP_DENYLIST"), key="SAYA_GROUP_DENYLIST"),
            reply_probability=float(get("REPLY_PROBABILITY", "0.1")),
            rate_limit_per_group_per_min=int(get("RATE_LIMIT_PER_GROUP_PER_MIN", "5")),
            trace_enabled=get("TRACE_ENABLED", "true").lower() in _TRUE,
            trace_path=get("TRACE_PATH", "./logs/trace.jsonl"),
            experiment_tag=get("EXPERIMENT_TAG", "baseline"),
            data_dir=get("DATA_DIR", "./data"),
            onebot_api_base=get("ONEBOT_API_BASE", "http://127.0.0.1:3000"),
            onebot_listen_host=get("ONEBOT_LISTEN_HOST", "127.0.0.1"),
            onebot_listen_port=int(get("ONEBOT_LISTEN_PORT", "8080")),
            onebot_event_path=get("ONEBOT_EVENT_PATH", "/onebot"),
            onebot_access_token=get("ONEBOT_ACCESS_TOKEN"),
            self_id=_optional_int(get("SELF_ID")),
            price_in=float(get("PRICE_IN", "0")),
            price_out=float(get("PRICE_OUT", "0")),
            dns_cache_ttl=float(get("DNS_CACHE_TTL", "300")),
        )

    def require_api_key(self) -> str:
        if not self.llm_api_key:
            raise RuntimeError("未配置 SAYA_LLM_API_KEY（照 .env.example 建一个 .env）")
        return self.llm_api_key
