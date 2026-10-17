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
import hashlib
import json
import logging
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# 让脚本能直接跑，不用先 pip install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.config import Config  # noqa: E402
from saya.console import prepare_console  # noqa: E402
from saya.domain import ImageRef  # noqa: E402
from saya.services.cv_tools import (  # noqa: E402
    CvToolsError,
    cv_subprocess_env,
    start_cv_tools,
)
from saya.services.llm import LLMClient, LLMError  # noqa: E402
from saya.services.vision import ALL_ROUTES, Vision, VisionError  # noqa: E402

# __file__ = project_main/experiment/run_experiment.py → parents[1] = project_main
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

logger = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    """文件**内容**的哈希。

    ⚠️ 复现要记哈希不是记路径：同一个路径下的 manifest 换了内容，
    路径完全看不出来。pre/13 第 7 节。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    """当前提交。工作区不干净时追加 `-dirty` —— 那说明结果对不上任何一个提交。"""
    repo = Path(__file__).resolve().parents[1]
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not sha:
            return ""
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{sha}-dirty" if dirty else sha


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


def _spans(text: str, word: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = text.find(word)
    while start != -1:
        out.append((start, start + len(word)))
        start = text.find(word, start + 1)
    return out


def _keyword_hit(text: str, word: str, rivals: Iterable[str]) -> bool:
    """`word` 在这段回答里算不算命中。

    ⚠️ **不能只做 `in`。** 子串匹配在这份测试集里有**真实的跨题冲突**
    （全表扫过，不是假想）：

    - `'3' ⊂ '13'` —— 7 条题期望 `3`、4 条期望 `13`
    - `'马' ⊂ '斑马'` —— fine 层 1 条期望 `马`、2 条期望 `斑马`。
      期望「马」的题答「斑马」会被判对，而**那正是 fine 层要测的细粒度错误** ——
      于是那一层的准确率被系统性抬高，且偏向"话更多"的那条路线。

    两条规则，各治一种：

    1. **纯数字**：要求两侧不是数字。挡住 `3 ⊂ 13`。
    2. **其余（含中文）**：命中位置若**落在某个更长关键词的范围里**，不算命中。
       挡住 `马 ⊂ 斑马`。⚠️ 不能简单要求"两侧不是汉字" ——
       那会把「一**匹马**」这种正常回答也毙掉。
    """
    if word.isdigit():
        return re.search(rf"(?<!\d){re.escape(word)}(?!\d)", text) is not None

    spans = _spans(text, word)
    if not spans:
        return False

    blocked: list[tuple[int, int]] = []
    for rival in rivals:
        if len(rival) > len(word) and word in rival:
            blocked.extend(_spans(text, rival))
    if not blocked:
        return True
    return any(
        not any(low <= start and end <= high for low, high in blocked)
        for start, end in spans
    )


# 归一化时丢掉的量词。参考答案里常带（`3 pieces`），模型回答里常不带（`3`）。
_UNITS = {
    "pieces", "piece", "pcs", "pc", "items", "item", "个", "只", "张", "条", "块",
}


def _normalize_answer(text: str) -> str:
    """把短答案压成可比较的形式。

    PerceptionBench 的答案是「短且唯一」的，所以判 `exact` 是合适的；
    但标点、大小写、量词这些**不承载信息**的差异不该判错。
    """
    value = text.strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .。!！?？,，:：;；\"'“”‘’()（）")
    words = [w for w in value.split(" ") if w not in _UNITS]
    value = " ".join(words)
    try:
        return str(int(value))  # `015` → `15`
    except ValueError:
        return value


def grade(
    answer: str,
    keywords: list[str],
    *,
    rivals: Iterable[str] = (),
    mode: str = "keyword",
) -> bool | None:
    """判分。没有关键词就不判（返回 None）。

    - `mode="keyword"`：关键词**全部命中**才算对（coco128 那套用的）
    - `mode="exact"`：归一化之后**相等**才算对（PerceptionBench 那套用的 ——
      它的答案是短且唯一的，用包含判反而会放过「15400」里的「1540」）

    `rivals` 是**整份测试集里的所有关键词** —— 用来识别子串冲突
    （`马 ⊂ 斑马`）。数字那类（`3 ⊂ 13`）按边界判，不依赖 rivals。
    """
    if not keywords:
        return None
    if mode == "exact":
        target = _normalize_answer(keywords[0])
        return bool(target) and _normalize_answer(answer) == target
    text = answer.lower()
    pool = [r.lower() for r in rivals]
    return all(_keyword_hit(text, k.lower(), pool) for k in keywords)


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
        f"  [!] 此时 temperature 被服务端无视，配置里的 "
        f"{config.vision_temperature} 不起作用。\n"
        f"  [!] 思考 token 按输出计价，延迟也会明显变长。\n"
        f"  → 要做温度对照，请把 SAYA_REASONING_EFFORT 设成 none。"
    )


def run_one(
    vision: Vision, item: dict, *, images_dir: Path, rivals: Iterable[str] = ()
) -> dict:
    image_path = (images_dir / item["file"]).resolve()
    if not image_path.is_file():
        return {
            "id": item["id"],
            "tier": item.get("tier", ""),
            "route": vision.route,
            # ⚠️ `file` 是**聚类分析的重抽样单位**（pre/13 §3.3）：
            # 同一张图会被 2–3 道题复用，题目因此不独立。
            # 记录里不带它，事后只能从 id 里反推 —— 那是脆的。
            "file": item.get("file", ""),
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
            # ⚠️ 模型名换版本时**不变**（2026-10-17 升到 V4.1-Flash-1017，
            # `model` 仍是 deepseek-flash）。只有这个指纹能看出整轮跑的是不是一个后端。
            "fingerprint": result.fingerprint,
        }
    except (VisionError, LLMError, CvToolsError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    record = {
        "id": item["id"],
        "tier": item.get("tier", ""),
        "route": vision.route,
        # ⚠️ 聚类分析的重抽样单位（pre/13 §3.3），必须落盘
        "file": item.get("file", ""),
        "question": item["question"],
        "answer": answer,
        "correct": grade(
            answer,
            item.get("expect_keywords") or [],
            rivals=rivals,
            mode=item.get("match", "keyword"),
        ),
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
        # ⚠️ **准确率的分母是全部样本（ITT）**：失败 / 报错 / 没判分的一律算错。
        # 以前分母用的是"判过分的那些"（`graded`），于是**现场看到的数字比
        # 报告里的高**（92% 对 88% 这种），照现场数字引用就是错的。
        # 口径必须和 `analyze.py` 的 `_correct` 一致（pre/13 §5）。
        if rows:
            lines.append(
                f"  准确率 ITT {correct}/{len(rows)} = {correct / len(rows):.0%}"
                f"（失败/未判分计为错；其中判过分的 {len(graded)} 条）"
            )
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
            tools = start_cv_tools(
            config.cv_mcp_command, extra_env=cv_subprocess_env(
                model_dir=config.cv_model_dir,
                ocr_key=config.baidu_ocr_key,
                ocr_secret=config.baidu_ocr_secret,
                ocr_endpoint=config.baidu_ocr_endpoint,
            )
        )
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


def warm_up(
    visions: dict[str, Vision],
    tools,
    item: dict,
    *,
    images_dir: Path,
    cache_dir: Path,
) -> dict:
    """跑前的预热 —— **结果全部丢弃**，只为把冷启动代价挤出去。

    ⚠️ 不预热有多严重（**本项目实测，不是理论**）：本地 CV 首次 `detect`
    **17441 ms**（含权重下载 + CUDA 初始化），热态 **31 ms** —— **差 560 倍**。
    那一条会落在路线 B / C 的第一道题上，而**延迟正是本实验的主指标之一**。
    模型端点同理：第一次调用还要付 DNS 冷启动。

    ⚠️ 试跑时踩过：不预热，干跑横幅上的警告会变成 `??`；
    这里每一步都各自 try —— **预热失败不该把整个实验拦下来**，
    但要把失败**记进结果文件**，否则"预热过了"就是一句空话。

    返回的记录写进结果文件的开头，这样"到底预热没有"事后可查。
    """
    image = (images_dir / item["file"]).resolve()
    record: dict = {"kind": "warmup", "image": item["file"], "steps": {}}

    if tools is not None:
        annotated = cache_dir / "annotated" / "_warmup.png"
        annotated.parent.mkdir(parents=True, exist_ok=True)
        for name, call in (
            ("detect", lambda: tools.detect(image)),
            ("read_text", lambda: tools.ocr(image)),
            ("annotate", lambda: tools.annotate(image, annotated)),
        ):
            try:
                call()
                record["steps"][name] = "ok"
            except Exception as exc:  # 预热不该拦下实验，但要留下证据
                record["steps"][name] = f"{type(exc).__name__}: {exc}"
                logger.warning("预热步骤 %s 失败：%s", name, exc)

    # 模型端点：跑一条最短的路（不调工具的 direct），只为把连接与 DNS 烤热
    first_route = next(iter(visions))
    try:
        visions[first_route].describe_many(
            [ImageRef(url=str(image), local_path=str(image))], item["question"]
        )
        record["steps"]["model"] = "ok"
    except Exception as exc:
        record["steps"]["model"] = f"{type(exc).__name__}: {exc}"
        logger.warning("预热模型调用失败：%s", exc)

    return record


def _read_progress(path: Path) -> tuple[list[str], list[dict], set[tuple[str, str]], int]:
    """读回已经跑完的部分 → (完好的原始行, 数据记录, 已完成的(题号,路线), 丢了几行)。

    ⚠️ **只保留能解析成 JSON 的行。** 边跑边落盘意味着被强杀时会留下一条
    写了一半的记录；那半行不清掉，后面追加的内容会让它变成一个**跨两行的
    坏 JSON**，把整个文件毁掉（分析脚本读不了）。
    """
    kept: list[str] = []
    records: list[dict] = []
    dropped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            dropped += 1
            continue
        kept.append(line)
        if not parsed.get("kind"):
            records.append(parsed)
    done = {
        (record["id"], record["route"])
        for record in records
        if record.get("id") and record.get("route")
    }
    return kept, records, done, dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三条视觉路线的对照实验")
    parser.add_argument("--manifest", required=True, help="测试集清单")
    parser.add_argument("--out", required=True, help="结果写到哪（jsonl）")
    parser.add_argument("--routes", nargs="+", default=list(ALL_ROUTES), choices=list(ALL_ROUTES))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟用）")
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="跳过跑前预热。⚠️ 只在调试时用 —— 冷启动会让 B/C 首题延迟高两个数量级",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列一遍要跑什么、检查图片与配置，不调模型（不花钱）",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="忽略已有的结果文件，从头跑（默认是**续跑**：跳过已经有的题目×路线）",
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
        tools = start_cv_tools(
            config.cv_mcp_command, extra_env=cv_subprocess_env(
                model_dir=config.cv_model_dir,
                ocr_key=config.baidu_ocr_key,
                ocr_secret=config.baidu_ocr_secret,
                ocr_endpoint=config.baidu_ocr_endpoint,
            )
        )

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

    # ---- 断点续跑 ----
    # ⚠️ 以前这里是 `open("w")` —— **开跑即截断**。跑到一半断了（网络、Ctrl-C、
    # 关机）之后重跑同一命令，已完成的那部分会**全部重买一遍**（两百多次真实
    # 计费的调用，不是小钱）；而且进程恰好在 `write` 期间被杀的话会留下半行
    # JSON，让分析脚本整个打不开。
    # 现在**默认就是续跑**：跳过已经有的 (题号, 路线)。想让重跑覆盖，显式给 `--fresh`。
    records: list[dict] = []
    done: set[tuple[str, str]] = set()
    resuming = out_path.is_file() and not args.fresh
    if resuming:
        kept, records, done, dropped = _read_progress(out_path)
        print(
            f"续跑：已有 {len(records)} 条记录，跳过 {len(done)} 个（题号, 路线）组合。"
            f"要整个重跑请加 --fresh。"
        )
        if dropped:
            print(f"  [!] 丢掉 {dropped} 行只写了一半的记录（说明上次是中断的）")
        # 把半截行清掉 —— 之后的追加才仍然是合法的 jsonl
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            for line in kept:
                handle.write(line + "\n")

    try:
        with out_path.open("a" if resuming else "w", encoding="utf-8") as handle:
            if resuming:
                handle.write(
                    json.dumps(
                        {
                            "kind": "resume",
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "already_done": len(done),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.flush()
            else:
                handle.write(
                    json.dumps(
                        {"kind": "environment_baseline", **baseline}, ensure_ascii=False
                    )
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
                            # ⚠️ 复现三件套：**内容哈希**（路径相同内容可能不同）、
                            # 代码 SHA（工作区要干净）、跑的时间窗（关系计价时段，见 pre/13 3.6）
                            "manifest": str(manifest_path),
                            "manifest_sha256": _sha256(manifest_path),
                            "items": len(items),
                            "git_sha": _git_sha(),
                            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            # 计价三档：报告里要写明用的是哪一套（高峰/空闲）
                            "price_in": config.price_in,
                            "price_cache_hit": config.price_cache_hit,
                            "price_out": config.price_out,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.flush()

            cache_dir = Path(config.data_dir) / "images"
            visions = {
                route: Vision(
                    client,
                    cache_dir=cache_dir,
                    route=route,
                    temperature=config.vision_temperature,
                    tools=tools,
                )
                for route in args.routes
            }

            if not args.no_warmup:
                print("\n→ 预热（结果丢弃，不进统计）…")
                warm = warm_up(
                    visions, tools, items[0], images_dir=images_dir, cache_dir=cache_dir
                )
                handle.write(json.dumps(warm, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"  预热：{warm['steps']}")

            # ⚠️ **逐条轮转**，不是按路线分块（pre/13 §3.1）。
            # 理由**不是**缓存 —— 那条查证后不成立（实验不传 system_prompt，
            # 没有可复用的长前缀）。真正的理由是**时间上的均衡**：
            # 分块跑时三条路线落在不同的时间段，服务端负载 / 网络 / 模型热更新
            # 都会**整条路线**地摊到某一组头上。轮转让它们在时间上交织。
            # 附带好处：中途崩了不至于只有一条路线有完整数据、根本没法对比。
            # 整份测试集的关键词 —— 判分时用来识别**子串冲突**（`3 ⊂ 13`、`马 ⊂ 斑马`）
            rivals = {k for one in items for k in (one.get("expect_keywords") or [])}

            for index, item in enumerate(items, start=1):
                for route in args.routes:
                    if (item["id"], route) in done:
                        continue  # 上次已经跑过（续跑）
                    record = run_one(
                        visions[route], item, images_dir=images_dir, rivals=rivals
                    )
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

    # ⚠️ pre/13 §3.5 要求"跑完检查整轮是不是同一个 model 字符串，变了作废"。
    # `run_meta` 里记的是**配置里的名字**，而这里数的是**服务端实际返回的**——
    # 服务商中途换权重的话，只有后者看得出来，数据会静默地混成两个模型。
    # （协议条款写进注释就够了，屏幕上不打内部索引。）
    seen_models = sorted({r.get("model") for r in records if r.get("model")})
    if len(seen_models) > 1:
        print(f"\n[!] 整轮里出现了 {len(seen_models)} 个不同的 model 字符串：{seen_models}")
        print("    中途换过模型的数据不能直接用 —— 这批作废，要重跑。")
    elif seen_models:
        print(f"\n模型（服务端实际返回）：{seen_models[0]}（配置里写的是 {config.llm_model}）")

    # ⚠️ **模型名靠不住。** 服务端换版本时 `model` 字段照旧 ——
    # 2026-10-17 平台公告升到 V4.1-Flash-1017，我们这边一个字都看不出来。
    # 真正能分辨后端的是 `system_fingerprint`，所以两个都要查。
    seen_fp = sorted({r.get("fingerprint") for r in records if r.get("fingerprint")})
    if len(seen_fp) > 1:
        print(f"\n[!] 整轮里出现了 {len(seen_fp)} 个不同的 system_fingerprint：{seen_fp}")
        print("    服务端中途换过版本 —— 这批数据不能混着用，要重跑。")
    elif seen_fp:
        print(f"后端指纹（system_fingerprint）：{seen_fp[0]}")
    else:
        print("\n[!] 本次没有记录到 system_fingerprint —— 服务端是否换了版本无从判断。")

    print(summarize(records))
    print(f"\n结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    # ⚠️ 这个必须加：跑到一半因为一个字符崩掉 = **钱白花**（调用已经发出去了）
    prepare_console()
    raise SystemExit(main())
