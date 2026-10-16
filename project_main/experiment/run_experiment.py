"""对照实验执行器：同一批图片，三条路线各跑一遍，记录结果。

设计要点（对应 `pre/12-experiment-design.md`）：
- **配对设计**：同一张图、同一个问题，三条路线各问一次，结果一一对应
- **大脑相同**：三条路线共用同一个模型（`Config.llm_model`），只变架构
- **环境基线**：跑之前先记一次 DNS / 往返耗时，写进结果文件 ——
  否则日后看到离谱的延迟，无法判断是架构差异还是环境噪声

用法（在 WSL 里）：

    python3 project_main/experiment/run_experiment.py \
        --manifest pre/experiments/testset/manifest.json \
        --out pre/experiments/results-baseline.jsonl \
        --routes direct delegated hybrid

manifest 格式（一行一条，或一个 JSON 数组）：

    {"id": "count_dogs_03", "file": "images/count_dogs_03.jpg",
     "tier": "counting", "question": "图里有几只狗？", "expect_keywords": ["3"]}

`expect_keywords` 全部出现在回答里才算"答对"。**这是个简化判据**：
它偏爱包含关键词的回答，不判语义。局限要写进报告的 methodology。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

# 让脚本能直接跑，不用先 pip install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.config import Config  # noqa: E402
from saya.console import keep_console_from_crashing  # noqa: E402
from saya.domain import ImageRef  # noqa: E402
from saya.services.cv_tools import CvToolsError, start_cv_tools  # noqa: E402
from saya.services.llm import LLMClient, LLMError  # noqa: E402
from saya.services.vision import ALL_ROUTES, Vision, VisionError  # noqa: E402

# __file__ = project_main/experiment/run_experiment.py → parents[1] = project_main
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_manifest(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"manifest 是空的：{path}")

    if text.lstrip().startswith("["):
        items = json.loads(text)
    else:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]

    for item in items:
        for field in ("id", "file", "question"):
            if field not in item:
                raise SystemExit(f"manifest 里的条目缺字段 {field}：{item}")
    return items


def environment_baseline(base_url: str) -> dict:
    """记下跑实验时的网络基线 —— 没有它，日后的延迟数字无法解释。"""
    import socket
    import time as _time
    from urllib.parse import urlsplit

    host = urlsplit(base_url).hostname or ""
    dns_ms = None
    try:
        started = _time.monotonic()
        socket.getaddrinfo(host, 443)
        dns_ms = round((_time.monotonic() - started) * 1000, 1)
    except Exception:  # noqa: BLE001 - 基线记不到就不记，不该挡住实验
        pass

    return {"dns_ms": dns_ms, "host": host}


def grade(answer: str, keywords: list[str]) -> bool | None:
    """关键词判据：全部出现才算对。没有关键词就不判（返回 None）。"""
    if not keywords:
        return None
    lowered = answer.lower()
    return all(keyword.lower() in lowered for keyword in keywords)


def thinking_banner(config: Config) -> str:
    """把思考模式明明白白打在启动横幅上。

    ⚠️ **思考模式下 temperature 不生效**（DeepSeek 官方 API 行为）。而这个实验的
    全部对照都建立在"固定温度"上 —— 开着思考模式还宣称控制了温度，等于骗自己。
    所以这里不能只在日志里提一句，得摆在最显眼的地方。
    """
    effort = (config.reasoning_effort or "").strip().lower()
    if effort in ("", "none", "off", "disabled"):
        return f"思考模式：关（reasoning_effort={effort or '未设'}）—— temperature 生效"
    return (
        f"思考模式：开（reasoning_effort={effort}）\n"
        f"  [!] 此时 **temperature 被服务端无视**，配置里的 "
        f"{config.vision_temperature} 不起作用。\n"
        f"  [!] 思考 token 按**输出**计价，延迟也会明显变长。\n"
        f"  → 要做温度对照，请把 SAYA_REASONING_EFFORT 设成 none。"
    )


def run_one(vision: Vision, item: dict, *, images_dir: Path) -> dict:
    image_path = (images_dir / item["file"]).resolve()
    if not image_path.is_file():
        return {
            "id": item["id"],
            "tier": item.get("tier", ""),
            "route": vision.route,
            "error": f"图片不存在：{image_path}",
            "answer": "",
            "correct": None,
        }

    image = ImageRef(url=str(image_path), local_path=str(image_path))
    started = time.monotonic()
    answer = ""
    meta: dict = {}
    error = ""

    try:
        result = vision.describe_many([image], item["question"])
        answer = result.answer
        meta = {
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "latency_ms": result.latency_ms,
            "cost": result.cost,
            # ⚠️ **必须当场记**：这几个数只在响应里有，跑完就再也买不回来了。
            # 报告里"成本"那一栏的正确性全靠它（命中价与未命中价差一两个数量级）。
            "cache_hit_tokens": result.cache_hit_tokens,
            "cache_miss_tokens": result.cache_miss_tokens,
            "reasoning_tokens": result.reasoning_tokens,
        }
    except (VisionError, LLMError, CvToolsError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    record = {
        "id": item["id"],
        "tier": item.get("tier", ""),
        "route": vision.route,
        "question": item["question"],
        "answer": answer,
        "correct": grade(answer, item.get("expect_keywords") or []),
        "error": error,
        "wall_ms": int((time.monotonic() - started) * 1000),
    }
    record.update(meta)
    return record


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarize(records: list[dict]) -> str:
    """按路线（以及路线 × 难度层）汇总。延迟一律给 P50 与 P95，不给平均值。"""
    lines: list[str] = []
    by_route: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_route[record["route"]].append(record)

    for route, rows in sorted(by_route.items()):
        ok = [r for r in rows if not r.get("error")]
        graded = [r for r in ok if r.get("correct") is not None]
        correct = sum(1 for r in graded if r["correct"])
        latencies = [r["latency_ms"] for r in ok if r.get("latency_ms")]
        tokens = [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in ok]
        cost = sum(r.get("cost", 0.0) for r in ok)

        lines.append(f"\n■ 路线 {route}")
        lines.append(f"  样本 {len(rows)}  失败 {len(rows) - len(ok)}")
        if graded:
            lines.append(f"  准确率 {correct}/{len(graded)} = {correct / len(graded):.0%}")
        if latencies:
            lines.append(
                f"  延迟 P50 {percentile(latencies, 0.5):.0f} ms"
                f"  P95 {percentile(latencies, 0.95):.0f} ms"
                f"  （中位 {statistics.median(latencies):.0f} ms）"
            )
        if tokens:
            lines.append(f"  token 平均 {statistics.mean(tokens):.0f}（in+out）")
        hits = [r.get("cache_miss_tokens", 0) for r in ok if r.get("cache_miss_tokens")]
        if hits:
            total_hit = sum(r.get("cache_hit_tokens", 0) for r in ok)
            total_miss = sum(hits)
            lines.append(
                f"  输入 cache 命中 {total_hit}/{total_hit + total_miss}"
                f" = {total_hit / (total_hit + total_miss):.0%}（未命中按 PRICE_IN 计价）"
            )
        if cost:
            lines.append(f"  累计成本 {cost:.4f} 元")

        tiers = sorted({r.get("tier", "") for r in rows if r.get("tier")})
        for tier in tiers:
            tier_rows = [r for r in rows if r.get("tier") == tier and not r.get("error")]
            tier_graded = [r for r in tier_rows if r.get("correct") is not None]
            if not tier_graded:
                continue
            tier_correct = sum(1 for r in tier_graded if r["correct"])
            lines.append(f"    · {tier:<12} {tier_correct}/{len(tier_graded)}")

    return "\n".join(lines)


def dry_run(items: list[dict], images_dir: Path, routes: list[str], config: Config) -> int:
    """只检查装配，不调模型。

    **存在的意义**：正式跑要花钱，而"图片路径不对 / 附属起不来 / 配置写错"
    这些错误完全可以在不花钱的情况下先发现。实测就是靠它先抓到了问题。
    """
    print("== 干跑：只检查，不调模型 ==")
    print(f"  测试集 {len(items)} 条")
    print(f"  会跑的路线 {routes}（每条都会把所有图过一遍）")
    print(f"  模型 {config.llm_model} @ {config.llm_base_url}")
    print("  " + thinking_banner(config).replace("\n", "\n  "))

    missing = [item for item in items if not (images_dir / item["file"]).is_file()]
    print(f"  图片：{len(items) - len(missing)} 张找得到，{len(missing)} 张找不到")
    for item in missing[:5]:
        print(f"    × {item['file']}")

    by_tier: dict[str, int] = {}
    for item in items:
        by_tier[item.get("tier", "?")] = by_tier.get(item.get("tier", "?"), 0) + 1
    print(f"  分层：{by_tier}")

    if any(route in ("delegated", "hybrid") for route in routes):
        print("\n  → 试着启动附属（专用 CV 模型）…")
        if not config.cv_mcp_command.strip():
            print("    × 没配 SAYA_CV_MCP_COMMAND，delegated / hybrid 跑不了")
            return 1
        try:
            tools = start_cv_tools(config.cv_mcp_command)
        except CvToolsError as exc:
            print(f"    × 附属起不来：{exc}")
            return 1
        try:
            print(f"    √ 附属就绪：{tools.server_info.get('name', '?')}")
            print(f"      提供工具：{[t.name for t in tools.list_tools()]}")
        finally:
            tools.close()

    total = len(items) * len(routes)
    print(f"\n正式跑将调用模型 {total} 次。确认没问题就去掉 --dry-run。")
    print("（注：路线 delegated / hybrid 每次调用还会额外走一遍本地 CV 工具）")
    return 0 if not missing else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三条视觉路线的对照实验")
    parser.add_argument("--manifest", required=True, help="测试集清单")
    parser.add_argument("--out", required=True, help="结果写到哪（jsonl）")
    parser.add_argument("--routes", nargs="+", default=list(ALL_ROUTES), choices=list(ALL_ROUTES))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列一遍要跑什么、检查图片与配置，**不调模型**（不花钱）",
    )
    parser.add_argument("--env-file", help=".env 路径")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    items = load_manifest(manifest_path)
    if args.limit:
        items = items[: args.limit]
    images_dir = manifest_path.parent

    # ⚠️ 必须默认读 project_main/.env —— 传 None 的话 Config 只读环境变量、
    # 完全不看 .env 文件，于是模型名/API key/附属命令全是默认值：
    # 表现成"未配置 API key"，但其实配置就摆在文件里。干跑抓到的。
    env_file = Path(args.env_file).resolve() if args.env_file else DEFAULT_ENV_FILE
    config = Config.from_env(dotenv_path=env_file)

    if args.dry_run:
        return dry_run(items, images_dir, args.routes, config)

    client = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.require_api_key(),
        model=config.llm_model,
        price_in=config.price_in,
        price_out=config.price_out,
        price_cache_hit=config.price_cache_hit,
        reasoning_effort=config.reasoning_effort,
    )

    tools = None
    if any(route in ("delegated", "hybrid") for route in args.routes):
        tools = start_cv_tools(config.cv_mcp_command)

    baseline = environment_baseline(config.llm_base_url)
    print(f"环境基线：DNS 解析 {baseline['dns_ms']} ms（{baseline['host']}）")
    print(f"测试集 {len(items)} 条，路线 {args.routes}，模型 {config.llm_model}")
    print(thinking_banner(config))

    # ⚠️ **边跑边落盘**，不要攒到最后统一写。
    # 一次正式实验是两百多次真实计费的调用，跑完要好几分钟到几十分钟 ——
    # 攒在内存里的话，中途任何一次网络抖动、Ctrl-C、或没预料到的异常，
    # 都会让**已经花掉的钱全部作废**。每条 flush 一次，代价可以忽略。
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    try:
        with out_path.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"kind": "environment_baseline", **baseline}, ensure_ascii=False)
                + "\n"
            )
            handle.write(
                json.dumps(
                    {
                        "kind": "run_meta",
                        "model": config.llm_model,
                        "routes": args.routes,
                        "temperature": config.vision_temperature,
                        "reasoning_effort": config.reasoning_effort,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.flush()

            for route in args.routes:
                vision = Vision(
                    client,
                    cache_dir=Path(config.data_dir) / "images",
                    route=route,
                    temperature=config.vision_temperature,
                    tools=tools,
                )
                for index, item in enumerate(items, start=1):
                    record = run_one(vision, item, images_dir=images_dir)
                    record["experiment_tag"] = config.experiment_tag
                    records.append(record)

                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()

                    mark = (
                        "√" if record.get("correct")
                        else ("×" if record.get("correct") is False else "·")
                    )
                    note = record.get("error", "")[:40]
                    print(
                        f"  [{route:<9}] {index:>3}/{len(items)} {item['id']:<24} {mark}"
                        f"  {record.get('latency_ms', 0):>6} ms  {note}"
                    )
    finally:
        if tools is not None:
            tools.close()

    print(summarize(records))
    print(f"\n结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    # ⚠️ 这个必须加：跑到一半因为一个字符崩掉 = **钱白花**（调用已经发出去了）
    keep_console_from_crashing()
    raise SystemExit(main())
