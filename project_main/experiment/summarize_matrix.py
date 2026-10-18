"""把四组判分结果压成一张单变量对照矩阵。

行：D/B/C（全部四层）与 D/B/C（去掉 OCR 层，三层等权）。
列：基准（第二轮）→ 换工具（第三轮）→ 换思考（第四轮）→ 换词表（开放轮）。

每一列只改一个变量：
- 工具：第三轮 - 第二轮
- 思考：第四轮 - 第三轮
- 词表：开放轮 - 第三轮（同强工具、同 none，只换检测器词表）

用法：
    python project_main/experiment/summarize_matrix.py \
        --baseline pre/experiments/results-pb-judged.jsonl \
        --tools pre/experiments/results-pb2-judged.jsonl \
        --reasoning pre/experiments/results-pb-max-judged.jsonl \
        --vocab pre/experiments/results-pb-open-judged.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

TIERS = ("counting", "localization", "fine", "ocr")
NO_OCR = ("counting", "localization", "fine")
ROUTE_MAP = {"direct": "D", "delegated": "B", "delegated-open": "B", "hybrid": "C", "hybrid-open": "C"}


def load(path: str) -> dict[str, dict[str, bool]]:
    """{(题号, D/B/C): 判官对错}"""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") or not row.get("route"):
            continue
        out[(row["id"], ROUTE_MAP[row["route"]])] = bool(row.get("judge"))
    return out


def accuracy(data: dict[tuple[str, str], bool], route: str, tiers: tuple[str, ...]) -> float:
    values = [v for (item_id, r), v in data.items() if r == route and _tier(item_id) in tiers]
    return sum(values) / len(values) if values else float("nan")


def _tier(item_id: str) -> str:
    for tier in TIERS:
        if item_id.startswith(tier):
            return tier
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--reasoning", required=True)
    parser.add_argument("--vocab", required=True)
    args = parser.parse_args()

    sets = {
        "基准（第二轮）": load(args.baseline),
        "换强工具（第三轮）": load(args.tools),
        "换思考（第四轮）": load(args.reasoning),
        "换词表（开放轮）": load(args.vocab),
    }

    print("## 全部四层（120 题）\n")
    print("| 路线 | " + " | ".join(sets) + " |")
    print("|---|" + "---:|" * len(sets))
    for route in ("D", "B", "C"):
        cells = " | ".join(f"{accuracy(data, route, TIERS)*100:.1f}%" for data in sets.values())
        print(f"| {route} | {cells} |")

    print("\n## 去掉 OCR 层（90 题，三层等权）\n")
    print("| 路线 | " + " | ".join(sets) + " |")
    print("|---|" + "---:|" * len(sets))
    for route in ("D", "B", "C"):
        cells = " | ".join(f"{accuracy(data, route, NO_OCR)*100:.1f}%" for data in sets.values())
        print(f"| {route} | {cells} |")

    print("\n## 单变量对照（百分点）\n")
    comparisons = (
        ("工具：第三轮 - 第二轮", "基准（第二轮）", "换强工具（第三轮）"),
        ("思考：第四轮 - 第三轮", "换强工具（第三轮）", "换思考（第四轮）"),
        ("词表：开放轮 - 第三轮", "换强工具（第三轮）", "换词表（开放轮）"),
    )
    print("| 对照 | 层 | D | B | C |")
    print("|---|---|---:|---:|---:|")
    for label, before, after in comparisons:
        for name, tiers in (("全层", TIERS), ("去 OCR", NO_OCR)):
            deltas = []
            for route in ("D", "B", "C"):
                delta = accuracy(sets[after], route, tiers) - accuracy(sets[before], route, tiers)
                deltas.append(f"{delta*100:+.1f}pp")
            print(f"| {label} | {name} | {' | '.join(deltas)} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
