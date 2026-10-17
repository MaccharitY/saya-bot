"""从 PerceptionBench 造测试集（2026，CC BY-NC 4.0）。

为什么换掉 coco128：缩略图（640px 封顶）让「小目标」那一层**人眼也判不了**，
而且 COCO 的类目是给检测器定的，不是给"人怎么描述这张图"定的
（实测出现过标注写「橙子」、图里是菠萝）。

PerceptionBench 每条题**只考一种原子感知能力**，答案短且唯一，
而且自带能力标签 —— 正好接上我们要的四层。

⚠️ 数据本体（1.6 GB 的 JSONL）**不进仓库**，见 `.gitignore`。
本脚本只抽出选中的那些图，几十 MB。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
from collections import defaultdict
from pathlib import Path

# PerceptionBench 的能力标签 → 我们的层名
TIER_OF = {
    "visual_counting_error": "counting",
    "visual_localization_error": "localization",
    "fine_grained_recognition_error": "fine",
    "ocr_error": "ocr",
}

# 只取单图题：多图题要按 <|image_N|> 顺序送图，那套还没接
SINGLE_IMAGE = 1
# 太小的图**人眼也判不了** —— 换掉 coco128 就是为了这条，别再放进来
MIN_SIDE = 400
# 参考答案太长就不是"唯一确定"了，判分也会变脆
MAX_ANSWER_CHARS = 24


def image_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return width, height
            i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    return None


def decode_image(ref: str) -> tuple[bytes, str]:
    header, _, payload = ref.partition(",")
    ext = "png" if "png" in header else "jpg"
    return base64.b64decode(payload), ext


def clean_question(text: str) -> str:
    """去掉 `<|image_N|>` 占位符 —— 我们一次只送一张图，位置由调用方决定。"""
    out = []
    i = 0
    while i < len(text):
        if text.startswith("<|image_", i):
            end = text.find("|>", i)
            if end != -1:
                i = end + 2
                continue
        out.append(text[i])
        i += 1
    return "".join(out).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 PerceptionBench 造测试集")
    parser.add_argument("--source", required=True, help="PerceptionBench.jsonl 路径")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--per-tier", type=int, default=30)
    args = parser.parse_args(argv)

    src = Path(args.source)
    out = Path(args.out)
    images_dir = out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    picked: dict[str, list[dict]] = defaultdict(list)
    skipped = defaultdict(int)

    with src.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            tier = TIER_OF.get(record.get("error_category", ""))
            if tier is None:
                continue
            if len(picked[tier]) >= args.per_tier:
                continue
            if len(record.get("image") or []) != SINGLE_IMAGE:
                skipped["多图"] += 1
                continue
            answer = str(record.get("answer", "")).strip()
            if not answer or len(answer) > MAX_ANSWER_CHARS:
                skipped["答案太长/为空"] += 1
                continue
            data, ext = decode_image(record["image"][0])
            size = image_size(data)
            if size is None or max(size) < MIN_SIDE:
                skipped["分辨率不足"] += 1
                continue

            digest = hashlib.sha256(data).hexdigest()[:12]
            name = f"{tier}_{digest}.{ext}"
            (images_dir / name).write_bytes(data)
            picked[tier].append(
                {
                    "id": f"{tier}_{record['index']:04d}",
                    "tier": tier,
                    "file": f"images/{name}",
                    "question": clean_question(record["problem"]),
                    "expect_keywords": [answer],
                    # ⚠️ 这是**短答案唯一定**的题，不是关键词包含 —— 判分方式要跟着变
                    "match": "exact",
                    "source": record.get("source_bmk", ""),
                    "pb_index": record["index"],
                }
            )

    items = [item for tier in TIER_OF.values() for item in picked.get(tier, [])]
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"写出 {manifest}")
    for tier in TIER_OF.values():
        print(f"  {tier:<14} {len(picked.get(tier, []))}")
    print(f"  合计           {len(items)}")
    if skipped:
        print("跳过：", dict(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
