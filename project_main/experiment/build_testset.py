"""从 coco128 造对照实验的测试集。

**为什么用带标注的数据集**：报告里的"准确率"必须有标准答案。
YOLO 标签给了每张图里每个框的类别与归一化坐标，所以
「图里有几只狗」的答案是**从标注算出来的**，不是我猜的、也不是人标的。

难度分层（对应文献里 MLLM 的已知短板，见 pre/11）：

    counting  一张图里同一类有 ≥3 个        —— 文献反复证伪 MLLM 的计数能力
    small     目标占图面积很小              —— 小目标
    fine      含 coco128 里的**稀有类**      —— 长尾/细粒度
    common    只含常见类                    —— 预期三路线接近，作为对照

⚠️ **文字识别（OCR）层没有做**：COCO 不含文字标注，得另找来源。
   这一层单列在 manifest 之外，别假装覆盖了。

用法：
    python3 project_main/experiment/build_testset.py \
        --coco /tmp/coco128/coco128 \
        --out pre/experiments/testset \
        --per-tier 20
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

# COCO 80 类，**按标准的 id 顺序**（0 起）。coco128 的标签用的就是这个顺序，
# 但包里没有 data.yaml，所以这里直接写死 —— 它是稳定的公开约定，不会漂。
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# 这些类的中文名（用于出题与判分）。**没收录的类就让那张图跳过** ——
# 不硬编，也不假装覆盖。
CN_NAMES = {
    "person": "人", "bicycle": "自行车", "car": "汽车", "motorcycle": "摩托车",
    "airplane": "飞机", "bus": "公交车", "train": "火车", "truck": "卡车",
    "boat": "船", "traffic light": "红绿灯", "fire hydrant": "消防栓",
    "stop sign": "停车标志", "parking meter": "停车计时器", "bench": "长椅",
    "bird": "鸟", "cat": "猫", "dog": "狗", "horse": "马", "sheep": "羊",
    "cow": "牛", "elephant": "大象", "bear": "熊", "zebra": "斑马",
    "giraffe": "长颈鹿", "backpack": "背包", "umbrella": "雨伞",
    "handbag": "手提包", "tie": "领带", "suitcase": "行李箱", "frisbee": "飞盘",
    "skis": "滑雪板", "snowboard": "单板滑雪板", "sports ball": "球",
    "kite": "风筝", "baseball bat": "棒球棒", "baseball glove": "棒球手套",
    "skateboard": "滑板", "surfboard": "冲浪板", "tennis racket": "网球拍",
    "bottle": "瓶子", "wine glass": "酒杯", "cup": "杯子", "fork": "叉子",
    "knife": "刀", "spoon": "勺子", "bowl": "碗", "banana": "香蕉",
    "apple": "苹果", "sandwich": "三明治", "orange": "橙子", "broccoli": "西兰花",
    "carrot": "胡萝卜", "hot dog": "热狗", "pizza": "披萨", "donut": "甜甜圈",
    "cake": "蛋糕", "chair": "椅子", "couch": "沙发", "potted plant": "盆栽",
    "bed": "床", "dining table": "餐桌", "toilet": "马桶", "tv": "电视",
    "laptop": "笔记本电脑", "mouse": "鼠标", "remote": "遥控器",
    "keyboard": "键盘", "cell phone": "手机", "microwave": "微波炉",
    "oven": "烤箱", "toaster": "烤面包机", "sink": "水槽", "refrigerator": "冰箱",
    "book": "书", "clock": "时钟", "vase": "花瓶", "scissors": "剪刀",
    "teddy bear": "泰迪熊", "hair drier": "吹风机", "toothbrush": "牙刷",
}


def parse_labels(path: Path) -> list[tuple[int, float]]:
    """返回 [(类别 id, 框面积占整图的比例), ...]。YOLO 格式是归一化坐标。"""
    boxes: list[tuple[int, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        class_id = int(parts[0])
        width, height = float(parts[3]), float(parts[4])
        boxes.append((class_id, width * height))
    return boxes


def load_names() -> dict[int, str]:
    """id → 类名。直接用内置的 COCO 类名表，不去读数据集里的 yaml。"""
    return {index: name for index, name in enumerate(COCO_CLASSES)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 coco128 造测试集")
    parser.add_argument("--coco", required=True, help="coco128 目录（含 images/ labels/ data.yaml）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--per-tier", type=int, default=20, help="每层挑几张")
    args = parser.parse_args(argv)

    coco = Path(args.coco)
    out = Path(args.out)
    images_dir = out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    names = load_names()
    image_files = sorted((coco / "images" / "train2017").glob("*.jpg"))

    # 先统计每个类在整个子集里的出现次数 —— 用来分"常见/稀有"
    frequency: Counter[int] = Counter()
    parsed: dict[Path, list[tuple[int, float]]] = {}
    for image in image_files:
        label = coco / "labels" / "train2017" / f"{image.stem}.txt"
        if not label.is_file():
            continue
        boxes = parse_labels(label)
        if not boxes:
            continue
        parsed[image] = boxes
        for class_id, _ in boxes:
            frequency[class_id] += 1

    common_ids = {cid for cid, _ in frequency.most_common(20)}

    tiers: dict[str, list[tuple[Path, list[tuple[int, float]]]]] = {
        "counting": [], "small": [], "fine": [], "common": [],
    }

    for image, boxes in parsed.items():
        class_ids = {cid for cid, _ in boxes}
        if any(names.get(cid) not in CN_NAMES for cid in class_ids):
            continue  # 有没收录中文名的类，跳过，别硬编

        counts = Counter(cid for cid, _ in boxes)
        biggest = max(area for _, area in boxes)

        if max(counts.values()) >= 3:
            tiers["counting"].append((image, boxes))
        if biggest < 0.10 and len(boxes) >= 1:
            tiers["small"].append((image, boxes))
        if class_ids - common_ids:
            tiers["fine"].append((image, boxes))
        if class_ids <= common_ids:
            tiers["common"].append((image, boxes))

    items: list[dict] = []
    for tier, entries in tiers.items():
        # 用文件名排序保证**可复现**：同样的数据永远挑出同样的图
        for image, boxes in sorted(entries, key=lambda pair: pair[0].name)[: args.per_tier]:
            items.append(_make_item(tier, image, boxes, names, images_dir))

    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    by_tier = Counter(item["tier"] for item in items)
    print(f"共 {len(items)} 条：")
    for tier, count in sorted(by_tier.items()):
        print(f"  {tier:<10} {count}")
    print(f"\n清单：{manifest}")
    print(f"图片：{images_dir}（{len(list(images_dir.iterdir()))} 个文件）")
    print("\n[!] 没有 OCR（文字识别）层 —— COCO 不含文字标注，这一层得另找来源。")
    return 0


def _make_item(
    tier: str, image: Path, boxes: list[tuple[int, float]], names: dict[int, str], images_dir: Path
) -> dict:
    shutil.copy(image, images_dir / image.name)

    counts = Counter(cid for cid, _ in boxes)
    target_id, target_count = counts.most_common(1)[0]
    target_cn = CN_NAMES[names.get(target_id, "")]

    if tier == "counting":
        question = f"图里有多少个{target_cn}？只回答一个阿拉伯数字。"
        keywords = [str(target_count)]
    else:
        # 识别类问题：问最大的那个东西，答案就是它的类别名
        biggest_id = max(boxes, key=lambda pair: pair[1])[0]
        target_cn = CN_NAMES[names.get(biggest_id, "")]
        question = "图里最主要的东西是什么？用一两个词回答。"
        keywords = [target_cn]

    return {
        "id": f"{tier}_{image.stem}",
        "file": f"images/{image.name}",
        "tier": tier,
        "question": question,
        "expect_keywords": keywords,
        "note": f"标注：{dict((CN_NAMES[names[c]], n) for c, n in counts.items())}",
    }


if __name__ == "__main__":
    raise SystemExit(main())
