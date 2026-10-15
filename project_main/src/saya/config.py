"""配置加载。

优先级：真实环境变量 > .env 文件 > 代码默认值。
密钥只从环境读取，**永不写进仓库**（见 CLAUDE.md 红线）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def parse_dotenv(text: str) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE。忽略空行与 # 注释。"""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def load_env(dotenv_path: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if dotenv_path is not None and dotenv_path.is_file():
        env.update(parse_dotenv(dotenv_path.read_text(encoding="utf-8")))
    env.update(os.environ)
    return env


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _split_csv_int(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _split_csv(raw))


@dataclass(frozen=True, slots=True)
class Config:
    adapter: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    vision_model: str
    vision_route: str
    trigger_mode: str
    trigger_keywords: tuple[str, ...]
    group_allowlist: tuple[int, ...]
    group_denylist: tuple[int, ...]
    reply_probability: float
    rate_limit_per_group_per_min: int
    trace_enabled: bool
    trace_path: str
    experiment_tag: str

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
            vision_model=get("VISION_MODEL", "deepseek-vl"),
            vision_route=get("VISION_ROUTE", "direct_mllm"),
            trigger_mode=get("TRIGGER_MODE", "mention"),
            trigger_keywords=_split_csv(get("TRIGGER_KEYWORDS")),
            group_allowlist=_split_csv_int(get("GROUP_ALLOWLIST")),
            group_denylist=_split_csv_int(get("GROUP_DENYLIST")),
            reply_probability=float(get("REPLY_PROBABILITY", "0.1")),
            rate_limit_per_group_per_min=int(get("RATE_LIMIT_PER_GROUP_PER_MIN", "5")),
            trace_enabled=get("TRACE_ENABLED", "true").lower() in _TRUE,
            trace_path=get("TRACE_PATH", "./logs/trace.jsonl"),
            experiment_tag=get("EXPERIMENT_TAG", "baseline"),
        )

    def require_api_key(self) -> str:
        if not self.llm_api_key:
            raise RuntimeError("未配置 SAYA_LLM_API_KEY（照 .env.example 建一个 .env）")
        return self.llm_api_key
