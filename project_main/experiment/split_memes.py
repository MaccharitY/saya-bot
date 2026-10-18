"""把 QQ 表情面板的长截图切成单张表情。

截图是规整网格：格子之间是约 6 px 的纯色缝，横竖间距约 300 px。
不硬编码行列数 —— 先找"整行/整列几乎没有变化"的缝隙，再按缝隙切。
顶部的状态栏/黑边会被当成一个过矮的格子自动跳过。

用法：
    python project_main/experiment/split_memes.py \
        --input pre/experiments/testset/qqtestimg/QQimgmeme/<截图>.jpg \
        --out pre/experiments/chat-real/images/meme
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

MAX_GUTTER = 12  # 缝隙最大宽度（px）；比这宽的多半是界面留白，不是格子缝
MIN_CELL = 60  # 小于这个边长的"格子"是状态栏/碎片，跳过


def _runs(profile: np.ndarray, *, threshold: float = 1.0, max_width: int = MAX_GUTTER):
    """返回满足 `profile < threshold` 的连续区间，且宽度不超过 max_width。"""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(profile):
        if value < threshold:
            if start is None:
                start = index
        elif start is not None:
            if index - start <= max_width:
                runs.append((start, index - 1))
            start = None
    if start is not None and len(profile) - start <= max_width:
        runs.append((start, len(profile) - 1))
    return runs


def _segments(length: int, gutters: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """缝隙之间的内容区间（含首尾）。"""
    cells: list[tuple[int, int]] = []
    cursor = 0
    for start, end in gutters:
        if start > cursor:
            cells.append((cursor, start - 1))
        cursor = end + 1
    if cursor < length:
        cells.append((cursor, length - 1))
    return cells


def split(input_path: Path, out_dir: Path) -> int:
    image = Image.open(input_path).convert("RGB")
    gray = np.asarray(image.convert("L")).astype(np.float32)

    cols = _segments(gray.shape[1], _runs(gray.std(axis=0)))
    rows = _segments(gray.shape[0], _runs(gray.std(axis=1)))

    out_dir.mkdir(parents=True, exist_ok=True)
    saved = skipped = 0
    for row_index, (y0, y1) in enumerate(rows):
        if y1 - y0 + 1 < MIN_CELL:
            skipped += 1
            continue
        for col_index, (x0, x1) in enumerate(cols):
            if x1 - x0 + 1 < MIN_CELL:
                skipped += 1
                continue
            cell = gray[y0 : y1 + 1, x0 : x1 + 1]
            if float(cell.std()) < 1.0:  # 纯色格：空白占位
                skipped += 1
                continue
            name = f"meme_{row_index:02d}_{col_index}.jpg"
            image.crop((x0, y0, x1 + 1, y1 + 1)).save(out_dir / name, quality=95)
            saved += 1

    print(f"网格：{len(cols)} 列 × {len(rows)} 行；保存 {saved} 张，跳过 {skipped} 格")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="切分 QQ 表情面板长截图")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    return split(Path(args.input), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
