"""盘点多模态真实工况的图集：尺寸、格式、动图、文字密度。

输入是所有者提供的两个目录：
    QQimgcommon/   普通图（截图、照片、图表等）
    QQimgmeme/     表情包长截图（需先用 split_memes.py 切开）

输出一行一张图的 CSV；`ocr_chars` 需要 `--ocr`（用附属那套 RapidOCR，本地跑）。

用法：
    python project_main/experiment/chat_workload_inventory.py \
        --common pre/experiments/testset/qqtestimg/QQimgcommon \
        --meme pre/experiments/chat-real/images/meme \
        --out pre/experiments/chat-real/inventory.csv --ocr
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from PIL import Image

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _ocr_chars(path: Path, engine) -> int:
    result, _ = engine(str(path))
    if not result:
        return 0
    return sum(len(str(item[1])) for item in result if item and len(item) > 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="盘点真实工况图集")
    parser.add_argument("--common", required=True)
    parser.add_argument("--meme", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ocr", action="store_true", help="跑本地 OCR 统计文字量")
    args = parser.parse_args()

    engine = None
    if args.ocr:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415 - 只在需要时加载

        engine = RapidOCR()

    sources = [("common", Path(args.common)), ("meme", Path(args.meme))]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for group, root in sources:
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                continue
            try:
                with Image.open(path) as image:
                    width, height = image.size
                    fmt = image.format or path.suffix.lstrip(".").upper()
                    animated = int(bool(getattr(image, "is_animated", False)))
            except Exception as exc:  # noqa: BLE001 - 坏图要计数，不该中断盘点
                rows.append(
                    {"group": group, "file": str(path), "error": type(exc).__name__}
                )
                continue
            rows.append(
                {
                    "group": group,
                    "file": str(path),
                    "width": width,
                    "height": height,
                    "format": fmt,
                    "animated": animated,
                    "bytes": path.stat().st_size,
                    "ocr_chars": _ocr_chars(path, engine) if engine else "",
                    "error": "",
                }
            )

    fieldnames = [
        "group", "file", "width", "height", "format", "animated",
        "bytes", "ocr_chars", "error",
    ]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"盘点 {len(rows)} 张 -> {out}")
    for group in ("common", "meme"):
        subset = [r for r in rows if r["group"] == group and not r["error"]]
        chars = [int(r["ocr_chars"]) for r in subset if r["ocr_chars"] != ""]
        print(
            f"  {group}: {len(subset)} 张"
            + (f"，文字中位 {sorted(chars)[len(chars) // 2]} 字符" if chars else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
