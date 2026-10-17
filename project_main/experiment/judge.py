"""用 PerceptionBench 自带的判官提示词，给自由作答判分。

为什么必须用判官：那套题的答案是**短且唯一**的，但模型常常整句作答
（「图里一共有 10 只兔子」）。实测精确匹配会把话说清楚但答对的判错 ——
换宽松匹配之后**排序直接翻转**（hybrid 从 13% 跳到 38%），
说明判据本身就是结论的一部分，不能随便挑一个。

基准官方用 `gpt-oss-120b` 当判官（与人工一致率 99.7%）。
我们只有 `deepseek-flash`，**用它自评**，这层偏差要写进报告。

⚠️ 判官提示词是基准仓库里的原文，放在 `judge_prompt.txt`（CC BY-NC 4.0）。
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
DEFAULT_PROMPT = Path(__file__).resolve().parent / "judge_prompt.txt"

_VERDICT = re.compile(r"\[judge\]\s*(True|False)", re.IGNORECASE)


def parse_verdict(text: str) -> bool | None:
    """取**最后一个** `[judge] True/False` —— 模型有时会在推理里先写一个。"""
    found = _VERDICT.findall(text or "")
    if not found:
        return None
    return found[-1].lower() == "true"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用 LLM 判官给自由作答判分")
    parser.add_argument("--results", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV))
    parser.add_argument("--limit", type=int, default=0, help="只判前 N 条（冒烟用）")
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
    template = Path(args.prompt).read_text(encoding="utf-8")

    items = {i["id"]: i for i in json.loads(Path(args.manifest).read_text(encoding="utf-8"))}
    rows = [
        json.loads(line)
        for line in Path(args.results).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # ⚠️ **meta 记录要原样带过去**（run_meta / warmup / environment_baseline）。
    # 只保留带 route 的行会把后端指纹、预热、git sha 全丢掉 ——
    # 分析脚本读的就是这些，丢了它会报"预热没有记录"，报告也因此失去溯源性。
    meta_rows = [r for r in rows if r.get("kind")]
    rows = [r for r in rows if r.get("route")]
    if args.limit:
        rows = rows[: args.limit]

    counts = {}
    unknown = 0
    out_rows = []
    for index, row in enumerate(rows, 1):
        item = items.get(row["id"], {})
        reference = " / ".join(item.get("expect_keywords") or [])
        filled = (
            template.replace("{problem}", item.get("question", ""))
            .replace("{assistant_answer}", row.get("answer") or "")
            .replace("{reference_answer}", reference)
        )
        verdict = None
        raw = ""
        for attempt in range(3):
            try:
                result = client.chat([text_message("user", filled)], temperature=0.3)
                raw = result.text
                verdict = parse_verdict(raw)
                break
            except Exception as exc:  # noqa: BLE001 - 判分失败不该中断整轮
                raw = f"{type(exc).__name__}: {exc}"
                time.sleep(1 + attempt)
        if verdict is None:
            unknown += 1

        record = dict(row)
        record["judge"] = verdict
        record["judge_raw"] = raw
        out_rows.append(record)

        if verdict is not None:
            counts.setdefault(row["route"], [0, 0])
            counts[row["route"]][0] += 1
            counts[row["route"]][1] += int(verdict)
        if index % 40 == 0:
            print(f"  已判 {index}/{len(rows)}", flush=True)

    out = Path(args.out)
    with out.open("w", encoding="utf-8", newline="") as handle:
        for record in meta_rows + out_rows:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\n写出 {out}（判不了 {unknown} 条）")
    for route, (total, good) in counts.items():
        print(f"  {route:<10} {good}/{total} = {good / total:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
