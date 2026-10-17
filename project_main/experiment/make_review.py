"""把实验结果摊成**人能一条条看**的形式（协议要求的人工抽检）。

两份产物：
- `*.html`：按**图片**分组，图 + 题目 + 三条路线的回答 + 自动判分，一屏一组
- `*.csv` ：一行一条（题 × 路线），最后一列留空给人填

支持三种判分来源，按记录里有的字段自动选：
- `correct`：关键词 / 精确匹配（原本就有）
- `judge`：LLM 判官
- `closeness`：计数题的接近程度（0～1，不是对错）

⚠️ **只读视图**，不回写结果文件。
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
from collections import defaultdict
from pathlib import Path

DEFAULT_ROUTES = ("direct", "delegated", "hybrid")


def load(manifest_path: Path, results_path: Path):
    items = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    answers = {}
    for row in records:
        if row.get("kind") or not row.get("route"):
            continue
        answers[(row["id"], row["route"])] = row
    return items, answers


def auto_label(row: dict) -> str:
    """这一条自动判成什么。三种来源的表示方式不同，别混着看。"""
    if row.get("closeness") is not None:
        return f"接近度 {row['closeness']:.2f}"
    if row.get("judge") is not None:
        return "对" if row["judge"] else "错"
    if row.get("correct") is not None:
        return "对" if row["correct"] else "错"
    return "未判"


def sample_rows(items: list[dict], routes, *, count: int) -> list[tuple[dict, str]]:
    """按**固定步长**抽，同样输入永远抽出同一批 —— 抽检要可复现。"""
    pairs = [(item, route) for item in items for route in routes]
    if count <= 0 or count >= len(pairs):
        return pairs
    step = len(pairs) / count
    return [pairs[int(i * step)] for i in range(count)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成人工抽检表")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--out-dir", required=True, help="输出目录")
    parser.add_argument("--name", default="review", help="输出文件名前缀")
    parser.add_argument("--sample", type=int, default=0, help="抽多少条（0=全部）")
    parser.add_argument("--routes", nargs="+", default=list(DEFAULT_ROUTES))
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    items, answers = load(manifest_path, Path(args.results))
    picked = sample_rows(items, args.routes, count=args.sample)
    manifest_dir = manifest_path.parent
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(out_dir / f"{args.name}.csv", picked, answers)
    _write_html(out_dir / f"{args.name}.html", picked, answers, manifest_dir)
    print(f"已写出 {out_dir / (args.name + '.html')} 与 {out_dir / (args.name + '.csv')}")
    print(f"抽检 {len(picked)} 条 / 共 {len(items) * len(args.routes)} 条")
    return 0


def _write_csv(path: Path, picked, answers) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["图片", "题目id", "层", "问题", "期望", "路线", "回答", "自动判分", "人工判分（请填 对/错）"]
        )
        for item, route in picked:
            row = answers.get((item["id"], route), {})
            writer.writerow(
                [
                    item.get("file", ""),
                    item["id"],
                    item.get("tier", ""),
                    item["question"],
                    " / ".join(item.get("expect_keywords") or []),
                    route,
                    (row.get("answer") or "").replace("\n", " "),
                    auto_label(row),
                    "",
                ]
            )


def _write_html(path: Path, picked, answers, manifest_dir: Path) -> None:
    by_image: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for item, route in picked:
        by_image[item.get("file", "")].append((item, route))

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>人工抽检</title>",
        "<style>",
        "body{font:14px/1.6 system-ui,sans-serif;margin:24px;max-width:1100px}",
        "h1{font-size:20px} h2{font-size:14px;margin:26px 0 6px;color:#333}",
        ".img{max-width:420px;border:1px solid #ddd;border-radius:4px}",
        "table{border-collapse:collapse;width:100%;margin:6px 0 2px}",
        "td,th{border:1px solid #e3e3e3;padding:6px 8px;vertical-align:top;text-align:left}",
        "th{background:#f6f6f6;font-weight:600} .exp{color:#666}",
        ".ok{color:#0a7a2f} .bad{color:#b3261e} .mid{color:#8a6d00}",
        "</style>",
        "<h1>人工抽检</h1>",
        "<p class='exp'>按图片分组。重点看那些判成<span class='bad'>错</span>的"
        "是不是其实答对了，以及判成<span class='ok'>对</span>的是不是蒙对的。</p>",
    ]
    for image, pairs in by_image.items():
        parts.append(f"<h2>{html.escape(image)}</h2>")
        # 用相对路径算，输出目录放在哪都不会指错
        src = os.path.relpath(manifest_dir / image, start=path.parent).replace(os.sep, "/")
        parts.append(f"<img class='img' src='{html.escape(src)}'>")
        for item, route in pairs:
            row = answers.get((item["id"], route), {})
            label = auto_label(row)
            css = "bad" if label == "错" else ("mid" if label.startswith("接近度") else "ok")
            text = html.escape((row.get("answer") or "").replace("\n", " "))
            parts.append(
                "<table><tr><th colspan='3'>"
                f"[{html.escape(item.get('tier', ''))}] {html.escape(item['question'])} "
                f"<span class='exp'>（期望：{html.escape(' / '.join(item.get('expect_keywords') or []))}）"
                "</span></th></tr>"
                f"<tr><td style='width:90px'>{route}</td><td>{text}</td>"
                f"<td style='width:110px' class='{css}'>{html.escape(label)}</td></tr></table>"
            )
    path.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
