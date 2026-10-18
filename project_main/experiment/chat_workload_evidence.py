"""真实工况的"空证据率"：工具在这些图上到底产出了什么。

对每张真实图调用一次检测与 OCR，记录：
- 检测是否为空、检出多少个框（解析结果头）
- OCR 是否为空、读出多少字符

空证据率是群聊工况的关键指标 —— 工具"什么都没说出来"时，
委派/混合路线就只剩模型在猜。

用法：
    python project_main/experiment/chat_workload_evidence.py \
        --manifest pre/experiments/chat-real/manifest.json \
        --out pre/experiments/chat-real/evidence.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from saya.config import Config  # noqa: E402
from saya.services.cv_tools import (  # noqa: E402
    CvToolsError,
    cv_subprocess_env,
    start_cv_tools,
)

_BOXES = re.compile(r"检测到 (\d+) 个目标")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args()

    import json

    items = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    images_dir = Path(args.manifest).parent
    config = Config.from_env(dotenv_path=Path(args.env_file))
    tools = start_cv_tools(
        config.cv_mcp_command,
        extra_env=cv_subprocess_env(
            model_dir=config.cv_model_dir,
            ocr_key=config.baidu_ocr_key,
            ocr_secret=config.baidu_ocr_secret,
            ocr_endpoint=config.baidu_ocr_endpoint,
        ),
    )

    rows = []
    try:
        for item in items:
            image = images_dir / item["file"]
            detect = ocr = ""
            error = ""
            try:
                detect = tools.detect(image)
                ocr = tools.ocr(image)
            except CvToolsError as exc:
                error = str(exc)
            match = _BOXES.search(detect)
            ocr_lines = [line for line in ocr.splitlines() if line and not line.startswith("识别到")]
            rows.append(
                {
                    "id": item["id"],
                    "type": item.get("tier", ""),
                    "detect_empty": int("没有检测到" in detect),
                    "detect_count": int(match.group(1)) if match else 0,
                    "ocr_empty": int("没有识别到" in ocr),
                    "ocr_chars": sum(len(line) for line in ocr_lines),
                    "error": error,
                }
            )
    finally:
        tools.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    n = len(rows)
    print(f"{n} 张 -> {out}")
    print(f"检测为空 {sum(r['detect_empty'] for r in rows)}/{n}")
    print(f"OCR 为空  {sum(r['ocr_empty'] for r in rows)}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
