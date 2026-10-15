"""入口：按配置把各部件装起来并启动。

    python run.py --check     # 先自检：配置对不对、模型通不通、端口占没占
    python run.py             # 正式启动

**先跑 --check 再启动** —— 省得开了半天才发现 key 写错或端口被占。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from saya.adapter.base import Adapter, AdapterError
from saya.adapter.mock import MockAdapter
from saya.adapter.onebot11_http import OneBot11HttpAdapter
from saya.config import Config
from saya.core.persona import DEFAULT_PERSONA
from saya.core.pipeline import Pipeline
from saya.core.trigger import Trigger
from saya.obs.trace import TraceWriter
from saya.services.llm import LLMClient, LLMError, text_message
from saya.services.vision import (
    IMPLEMENTED_ROUTES,
    ROUTE_DELEGATED,
    ROUTE_HYBRID,
    Vision,
)

# __file__ = project_main/src/saya/__main__.py → parents[2] = project_main
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

ADAPTER_CHOICES = ("mock", "onebot11_http")


def build_adapter(config: Config) -> Adapter:
    if config.adapter == "mock":
        return MockAdapter()

    if config.adapter in ("onebot11_http", "onebot11"):
        return OneBot11HttpAdapter(
            api_base=config.onebot_api_base,
            listen_host=config.onebot_listen_host,
            listen_port=config.onebot_listen_port,
            event_path=config.onebot_event_path,
            access_token=config.onebot_access_token,
            self_id=config.self_id,
        )

    raise AdapterError(
        f"未知的接入后端：{config.adapter}（可选：{' / '.join(ADAPTER_CHOICES)}）"
    )


def build_llm(config: Config) -> LLMClient:
    return LLMClient(
        base_url=config.llm_base_url,
        api_key=config.require_api_key(),
        model=config.llm_model,
        price_in=config.price_in,
        price_out=config.price_out,
    )


def build_pipeline(config: Config) -> Pipeline:
    client = build_llm(config)
    vision = Vision(
        client,
        cache_dir=Path(config.data_dir) / "images",
        route=config.vision_route,
        temperature=config.vision_temperature,
    )
    return Pipeline(
        config=config,
        adapter=build_adapter(config),
        llm=client,
        vision=vision,
        persona=DEFAULT_PERSONA,
        trigger=Trigger(config),
        trace=TraceWriter(config.trace_path, enabled=config.trace_enabled),
    )


def run_check(config: Config) -> int:
    """自检：不连 QQ、不发消息。返回 0 表示可以启动。

    **一次把所有问题都报出来**，而不是碰到第一个就退出 ——
    否则要修一个、跑一次，来回好几趟。
    """
    print("== saya_bot 自检 ==")
    print(f"  接入后端 : {config.adapter}")
    print(f"  模型     : {config.llm_model} @ {config.llm_base_url}")
    print(f"  看图路线 : {config.vision_route}（已实现的只有 direct）")
    print(f"  触发模式 : {config.trigger_mode}")
    print(f"  温度     : 对话 {config.temperature} / 看图 {config.vision_temperature}")
    print(f"  埋点     : {config.trace_path}（启用={config.trace_enabled}）")
    print(f"  数据目录 : {config.data_dir}")

    problems: list[str] = []

    known_routes = IMPLEMENTED_ROUTES + (ROUTE_DELEGATED, ROUTE_HYBRID)
    if config.vision_route not in known_routes:
        problems.append(
            f"看图路线 {config.vision_route!r} 不认识 —— 合法的值是 "
            f"{' / '.join(known_routes)}。"
            f"（旧版配置里的 direct_mllm 已改名，请改成 direct）"
        )
    elif config.vision_route not in IMPLEMENTED_ROUTES:
        problems.append(
            f"看图路线 {config.vision_route!r} 尚未实现（它需要 MCP / 专用 CV 模型）。"
            f"现在请先用 {' 或 '.join(IMPLEMENTED_ROUTES)}。"
        )

    if config.adapter.startswith("onebot11"):
        print("\n→ 试着占用事件端口…")
        probe = build_adapter(config)
        try:
            host, port = probe.start()
            print(f"✓ 端口可用：http://{host}:{port}{config.onebot_event_path}")
            print("  （把上面这个地址填进 NapCat 的「HTTP 上报」）")
        except AdapterError as exc:
            problems.append(f"事件端口不可用：{exc}")
        finally:
            probe.close()

    if not config.llm_api_key:
        problems.append("未配置 SAYA_LLM_API_KEY —— 照 .env.example 复制一份 .env 并填入")
    else:
        # 只报长度、不回显内容 —— 免得密钥被贴进聊天记录或截图
        print(f"\n  API key  : 已配置（长度 {len(config.llm_api_key)}）")
        print("→ 试着跟模型说一句话…")
        try:
            client = LLMClient(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
                model=config.llm_model,
            )
            result = client.chat([text_message("user", "只回复两个字：在的")])
            print(
                f"✓ 模型回了：{result.text!r}"
                f"（{result.tokens_in}+{result.tokens_out} tokens，{result.latency_ms} ms）"
            )
        except LLMError as exc:
            problems.append(f"模型调用失败：{exc}")

    if problems:
        print("\n== 发现的问题 ==")
        for index, problem in enumerate(problems, 1):
            print(f"  {index}. {problem}")
        return 1

    print("\n一切正常，可以启动：python run.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saya", description="saya_bot —— QQ 群聊机器人")
    parser.add_argument("--check", action="store_true", help="只自检，不启动、不发消息")
    parser.add_argument("--env-file", help="指定 .env 路径（默认 project_main/.env）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    env_file = Path(args.env_file) if args.env_file else DEFAULT_ENV_FILE
    if not env_file.is_file():
        hint = "" if args.env_file else "（照 .env.example 复制一份）"
        print(f"提示：没找到 {env_file}，将只用环境变量与默认值。{hint}")

    config = Config.from_env(dotenv_path=env_file)

    if args.check:
        return run_check(config)

    try:
        pipeline = build_pipeline(config)
    except (RuntimeError, AdapterError) as exc:
        print(f"启动失败：{exc}")
        return 1

    try:
        pipeline.run()
    except KeyboardInterrupt:
        print("\n收到中断，退出。")
    finally:
        pipeline.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
