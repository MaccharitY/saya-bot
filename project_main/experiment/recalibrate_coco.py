"""给 coco128 那一轮**重新判分**：计数按接近程度，其余交给判官。

为什么重判：那一轮用的是关键词包含。翻样本时发现两类系统性错判 ——
计数题答「6」而真值「7」被判全错；非计数题答「棒球运动员」而参考「人」也被判全错。
两种都不是能力差异，是尺子的问题。

- **计数**：`closeness = max(0, 1 - |答 - 真| / 真)`，非数字记 0。
  它不替代准确率，是**另一列**；准确率（完全相等）仍然保留。
- **非计数**：用一个判官判「意思是否一致」。提示词是**我们自己的**（简短版），
  不是 PerceptionBench 那份 —— 那份是为它的题型写的。这一点要写进报告。

⚠️ 判官与作答模型同源（都是 deepseek-flash），绝对数字不可与论文比。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_MAIN = Path(__file__).resolve().parents[1]
DEFAULT_ENV = PROJECT_MAIN / ".env"

_VERDICT = re.compile(r"\[judge\]\s*(True|False)", re.IGNORECASE)

JUDGE_PROMPT = """你是判分员。下面是一道看图问答题、参考答案、以及学生的回答。
参考答案视为标准答案。判断学生的回答**在意思上**是否与参考答案一致：
同义词、上位词、以及更具体但同指的说法都算一致（例如参考「人」，学生答「棒球运动员」算一致；
参考「大象」，学生答「小象」算一致；参考「泰迪熊」，学生答「毛绒玩具」算一致）。
但如果学生答的是整个场景而不是被问的东西（例如问「汽车」答「红绿灯」），算不一致。

只输出一行，不要解释：
[judge] True 或 [judge] False

问题：{question}
参考答案：{reference}
学生回答：{answer}
"""


def closeness(answer: str, expected: str) -> float | None:
    """计数题的接近程度，0～1。答非数字记 0；真值不是数字则不判。"""
    try:
        target = int(str(expected).strip())
    except (TypeError, ValueError):
        return None
    match = re.search(r"\d+", str(answer or ""))
    if not match:
        return 0.0
    if target <= 0:
        return 1.0 if int(match.group()) == target else 0.0
    return max(0.0, 1.0 - abs(int(match.group()) - target) / target)


def parse_verdict(text: str) -> bool | None:
    found = _VERDICT.findall(text or "")
    return None if not found else found[-1].lower() == "true"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给 coco128 那轮重新判分")
    parser.add_argument("--results", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(PROJECT_MAIN / "src"))
    from saya.config import Config
    from saya.services.llm import LLMClient, text_message

    config = Config.from_env(dotenv_path=Path(args.env_file))
    client = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
    )

    items = {i["id"]: i for i in json.loads(Path(args.manifest).read_text(encoding="utf-8"))}
    records = [
        json.loads(line)
        for line in Path(args.results).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    meta = [r for r in records if r.get("kind")]
    rows = [r for r in records if r.get("route")]
    if args.limit:
        rows = rows[: args.limit]

    out_rows = []
    judged = 0
    for index, row in enumerate(rows, 1):
        item = items.get(row["id"], {})
        reference = " / ".join(item.get("expect_keywords") or [])
        record = dict(row)
        if row.get("tier") == "counting":
            value = closeness(row.get("answer", ""), reference)
            record["closeness"] = value
            record["judge"] = None
        else:
            prompt = JUDGE_PROMPT.format(
                question=item.get("question", ""),
                reference=reference,
                answer=row.get("answer") or "",
            )
            verdict, raw = None, ""
            for attempt in range(3):
                try:
                    result = client.chat([text_message("user", prompt)], temperature=0.0)
                    raw = result.text
                    verdict = parse_verdict(raw)
                    break
                except Exception as exc:  # noqa: BLE001
                    raw = f"{type(exc).__name__}: {exc}"
                    time.sleep(1 + attempt)
            record["judge"] = verdict
            record["judge_raw"] = raw
            judged += verdict is not None
        out_rows.append(record)
        if index % 50 == 0:
            print(f"  已处理 {index}/{len(rows)}", flush=True)

    with Path(args.out).open("w", encoding="utf-8", newline="") as handle:
        for record in meta + out_rows:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---- 汇总 ----
    print(f"\n写出 {args.out}（判官成功 {judged} 条）")
    by_count = {}
    by_judge = {}
    for record in out_rows:
        route = record["route"]
        if record.get("closeness") is not None:
            acc = by_count.setdefault(route, [0.0, 0])
            acc[0] += record["closeness"]
            acc[1] += 1
        elif record.get("judge") is not None:
            acc = by_judge.setdefault(route, [0, 0])
            acc[0] += int(record["judge"])
            acc[1] += 1
    print("  计数层（接近度均值）：")
    for route, (total, count) in by_count.items():
        print(f"    {route:<10} {total / count:.2f}  (n={count})")
    print("  非计数层（判官准确率）：")
    for route, (good, count) in by_judge.items():
        print(f"    {route:<10} {good}/{count} = {good / count:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
