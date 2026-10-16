"""对照实验的分析 —— **读冻结的 jsonl，不联网、不调模型**。

用法：
    python3 experiment/analyze.py --results ../pre/experiments/results.jsonl

⚠️ **为什么和执行器分开**：执行器一旦跑完就是**证据**，不该再改；
分析可以反复迭代，但它读的是冻结的结果文件。
（pre/13-experimental-protocol.md 第 7 节）

⚠️ **为什么不是简单的百分比统计**：测试集里 **73 张图出了 105 道题，
27 张图被跨层复用**。同一张图的两道题共享视觉内容、回答相关 ——
题目**不独立**。用普通的比例检验（或 McNemar）会**低估方差、让 p 偏小**，
等于偷偷放宽了显著性门槛。

所以本脚本的主力是**聚类自助法（cluster bootstrap）**：
重抽样的单位是**图片**，抽到一张图就把它名下的所有题目一起带进来。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 让脚本能直接跑（和控制台兜底共用一处），不用先 pip install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ⚠️ 只依赖标准库：本项目本体零第三方依赖，分析脚本也照办（不需要 scipy）
ROUNDS = 5000
SEED = 20261016  # 固定种子 —— 同样的数据每次跑出同样的区间

ALPHA = 0.05


# ---------------------------------------------------------------- 精确检验

def mcnemar_exact_p(b: int, c: int) -> float:
    """McNemar 精确检验（双侧）。b、c 是**不一致对**的两个方向。

    配对二分类的标准检验。功效只取决于不一致对的数量，与题目总数无关。
    """
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(pvalues: dict[str, float], alpha: float = ALPHA) -> dict[str, tuple[float, bool]]:
    """Holm 校正。返回 {键: (校正后阈值, 是否显著)}。

    多重比较不校正 = 比得越多越容易"发现"差异。这一族是事先定死的
    （pre/13 第 6 节：counting 层的三条路线两两比较）。
    """
    ordered = sorted(pvalues.items(), key=lambda pair: pair[1])
    m = len(ordered)
    out: dict[str, tuple[float, bool]] = {}
    for index, (key, p) in enumerate(ordered):
        threshold = alpha / (m - index)
        out[key] = (threshold, p <= threshold)
    return out


# ---------------------------------------------------------------- 自助法

def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def cluster_bootstrap(
    clusters: dict[str, list[str]],
    statistic,
    *,
    rounds: int = ROUNDS,
    seed: int = SEED,
) -> list[float]:
    """以**图片**为重抽样单位。

    clusters: {图片: [该图名下的题目 id]}
    抽一张图 = 把它名下**所有题目一起**带进来，配对关系因此不会被打散。
    """
    keys = sorted(clusters)
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(rounds):
        picked: list[str] = []
        for _ in range(len(keys)):
            picked.extend(clusters[keys[rng.randrange(len(keys))]])
        value = statistic(picked)
        if value is not None and not math.isnan(value):
            out.append(value)
    return out


def ci(values: list[float], alpha: float = ALPHA) -> tuple[float, float]:
    return percentile(values, alpha / 2), percentile(values, 1 - alpha / 2)


# ---------------------------------------------------------------- 主流程

class Data:
    """把 jsonl 整理成"每个题号 → 每条路线各一条记录"。"""

    def __init__(self, records: list[dict]) -> None:
        self.meta = [r for r in records if r.get("kind")]
        self.rows = [r for r in records if not r.get("kind")]

        self.by_item: dict[str, dict[str, dict]] = defaultdict(dict)
        self.image_of: dict[str, str] = {}
        self.tier_of: dict[str, str] = {}
        self.routes: list[str] = []
        for row in self.rows:
            item, route = row.get("id"), row.get("route")
            if not item or not route:
                continue
            self.by_item[item][route] = row
            # ⚠️ 老结果文件里可能没有 file 字段，退回用 id 当聚类键 —— 那会
            # **把聚类分析退化成题目级**，所以要在报告里喊出来
            self.image_of[item] = row.get("file") or f"__no_file__{item}"
            self.tier_of[item] = row.get("tier", "")
            if route not in self.routes:
                self.routes.append(route)

        self.clusters: dict[str, list[str]] = defaultdict(list)
        for item, image in self.image_of.items():
            self.clusters[image].append(item)

        self.reused = sum(1 for ids in self.clusters.values() if len(ids) > 1)
        self.missing_file = any(k.startswith("__no_file__") for k in self.clusters)

    # ---- 统计量（都作用在"题目 id 列表"上）----

    def _items(self, ids: list[str], route: str, tier: str | None = None) -> list[str]:
        return [
            i for i in ids
            if route in self.by_item[i] and (tier is None or self.tier_of[i] == tier)
        ]

    def accuracy(self, ids: list[str], route: str, tier: str | None = None) -> float:
        sel = self._items(ids, route, tier)
        if not sel:
            return float("nan")
        return sum(self._correct(i, route) for i in sel) / len(sel)

    def _correct(self, item: str, route: str) -> int:
        """ITT：**失败 / 报错 / 未判分 一律算不正确**（pre/13 第 5 节）。"""
        return 1 if self.by_item[item][route].get("correct") else 0

    def risk_diff(self, ids: list[str], a: str, b: str, tier: str | None = None) -> float:
        """配对风险差：只保留**两条路线都有记录**的题目。"""
        both = [i for i in ids if a in self.by_item[i] and b in self.by_item[i]]
        if tier is not None:
            both = [i for i in both if self.tier_of[i] == tier]
        if not both:
            return float("nan")
        return (sum(self._correct(i, a) for i in both)
                - sum(self._correct(i, b) for i in both)) / len(both)

    def discordant(self, a: str, b: str, tier: str | None = None) -> tuple[int, int]:
        """(只有 a 对, 只有 b 对)。"""
        only_a = only_b = 0
        for item, per_route in self.by_item.items():
            if a not in per_route or b not in per_route:
                continue
            if tier is not None and self.tier_of[item] != tier:
                continue
            ca, cb = self._correct(item, a), self._correct(item, b)
            only_a += ca and not cb
            only_b += cb and not ca
        return only_a, only_b

    def design_effect(self, route: str) -> float:
        """聚类方差 / 朴素二项方差。>1 说明独立性假设被偏离了多少。

        这是**透明性指标**：告诉读者"如果按题目算，方差被低估了几倍"。
        """
        all_ids = list(self.by_item)
        sel = self._items(all_ids, route)
        if len(sel) < 2:
            return float("nan")
        p = self.accuracy(all_ids, route)
        naive = p * (1 - p) / len(sel)
        if naive <= 0:
            return float("nan")
        boot = cluster_bootstrap(self.clusters, lambda ids: self.accuracy(ids, route))
        if len(boot) < 2:
            return float("nan")
        mean = sum(boot) / len(boot)
        var = sum((x - mean) ** 2 for x in boot) / (len(boot) - 1)
        return var / naive


# ---------------------------------------------------------------- 输出

def fmt(value: float, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "  n/a"
    return f"{value:.{digits}f}"


def report(data: Data, routes: list[str], *, confirm_tier: str, primary: tuple[str, str]) -> str:
    lines: list[str] = []
    add = lines.append

    if data.missing_file:
        add("⚠️ 结果文件里缺 `file` 字段 —— 聚类分析**退化成了题目级**。")
        add("   重跑一次执行器（它现在会写 file），否则下面的区间是偏窄的。")
        add("")

    add(f"题目 {len(data.by_item)} 条，图片 {len(data.clusters)} 张，"
        f"其中被复用的 {data.reused} 张")
    add(f"路线 {routes}，自助重抽样 {ROUNDS} 次（单位=图片，种子={SEED}）")
    add("")

    # ---- 准确率：按路线 × 层 ----
    add("■ 准确率（95% 区间用**聚类**自助法；失败按不正确计入）")
    tiers = ["counting", "small", "fine", "common"]
    header = f"  {'层':<10}" + "".join(f"{r:>18}" for r in routes)
    add(header)
    for tier in tiers:
        cells = []
        for route in routes:
            ids = list(data.by_item)
            point = data.accuracy(ids, route, tier)
            boot = cluster_bootstrap(
                data.clusters, lambda i, r=route, t=tier: data.accuracy(i, r, t)
            )
            lo, hi = ci(boot)
            cells.append(f"{point:.2f} [{lo:.2f},{hi:.2f}]".rjust(18))
        add(f"  {tier:<10}" + "".join(cells))
    add("  " + "-" * (10 + 18 * len(routes)))
    cells = []
    for route in routes:
        ids = list(data.by_item)
        point = data.accuracy(ids, route)
        boot = cluster_bootstrap(data.clusters, lambda i, r=route: data.accuracy(i, r))
        lo, hi = ci(boot)
        cells.append(f"{point:.2f} [{lo:.2f},{hi:.2f}]".rjust(18))
    add(f"  {'全部':<10}" + "".join(cells))
    add("  ⚠️ '全部'只作描述，**不是主指标** —— 各层预期方向相反，合并会互相抵消")
    add("")

    # ---- 确证族 ----
    add(f"■ 确证族：{confirm_tier} 层的两两比较（Holm 校正；预注册见 pre/13 第 6 节）")
    pvalues: dict[str, float] = {}
    cells: list[tuple[str, float, tuple[float, float], tuple[int, int]]] = []
    for index, a in enumerate(routes):
        for b in routes[index + 1:]:
            ids = list(data.by_item)
            point = data.risk_diff(ids, a, b, confirm_tier)
            boot = cluster_bootstrap(
                data.clusters, lambda i, x=a, y=b: data.risk_diff(i, x, y, confirm_tier)
            )
            lo, hi = ci(boot)
            only_a, only_b = data.discordant(a, b, confirm_tier)
            key = f"{a} - {b}"
            pvalues[key] = mcnemar_exact_p(only_a, only_b)
            cells.append((key, point, (lo, hi), (only_a, only_b)))

    corrected = holm(pvalues)
    for key, point, (lo, hi), (only_a, only_b) in cells:
        mark = "★显著" if corrected[key][1] else "不显著"
        add(f"  {key:<22} 风险差 {point:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]  "
            f"不一致对 {only_a}:{only_b}  McNemar p={pvalues[key]:.4f}  → {mark}")
    add("")

    # ---- 探索层 ----
    add("■ 其余各层（**探索性，生成假设用** —— 只报效应量与区间，不报 p）")
    for tier in tiers:
        if tier == confirm_tier:
            continue
        for index, a in enumerate(routes):
            for b in routes[index + 1:]:
                ids = list(data.by_item)
                point = data.risk_diff(ids, a, b, tier)
                boot = cluster_bootstrap(
                    data.clusters, lambda i, x=a, y=b, t=tier: data.risk_diff(i, x, y, t)
                )
                lo, hi = ci(boot)
                add(f"  {tier:<10} {a} - {b:<10} 风险差 {point:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]")
    add("")

    # ---- 延迟 ----
    add("■ 延迟（P50 / P95，单位 ms；区间同样是聚类自助）")
    for route in routes:
        vals = [
            r["latency_ms"] for r in data.rows
            if r.get("route") == route and r.get("latency_ms")
        ]
        walls = [
            r["wall_ms"] for r in data.rows
            if r.get("route") == route and r.get("wall_ms")
        ]
        outs = [
            r.get("tokens_out", 0) for r in data.rows
            if r.get("route") == route and r.get("tokens_out")
        ]
        add(f"  {route:<10} 模型 P50 {percentile(vals, 0.5):>7.0f}  "
            f"P95 {percentile(vals, 0.95):>7.0f}   "
            f"端到端 P50 {percentile(walls, 0.5):>7.0f}  "
            f"输出 token 中位 {percentile(outs, 0.5):>6.0f}")
    add("  ⚠️ 报延迟必须同时报输出 token：长回答自然慢，不报就没法比（pre/12 第 6 节）")
    add("")

    # ---- 成本与失败 ----
    add("■ 成本与失败")
    for route in routes:
        rows = [r for r in data.rows if r.get("route") == route]
        failed = sum(1 for r in rows if r.get("error"))
        cost = sum(r.get("cost", 0.0) for r in rows)
        tin = sum(r.get("tokens_in", 0) for r in rows)
        hit = sum(r.get("cache_hit_tokens", 0) for r in rows)
        add(f"  {route:<10} 失败 {failed}/{len(rows)} = {failed/max(1,len(rows)):.0%}   "
            f"累计 {cost:.4f} 元   输入 {tin} token（命中缓存 {hit}）")
    add("  ⚠️ API 边际成本与本地算力摊销**分列不合并**（pre/12 第 7 节）")
    add("")

    # ---- 设计效应 ----
    add("■ 设计效应（聚类方差 ÷ 独立假设下的方差）")
    for route in routes:
        add(f"  {route:<10} {fmt(data.design_effect(route), 2)}")
    add("  >1 说明题目独立性假设被偏离；也是'为什么不能用普通比例检验'的证据")
    add("")

    # ---- 预热与环境 ----
    warm = [m for m in data.meta if m.get("kind") == "warmup"]
    if warm:
        add(f"■ 预热：{warm[0].get('steps')}")
    else:
        add("■ 预热：**没有记录** —— 冷启动可能污染了 B/C 的首题延迟（pre/13 §3.2）")
    for meta in data.meta:
        if meta.get("kind") == "run_meta":
            add(f"■ 运行：模型 {meta.get('model')}  提交 {meta.get('git_sha')}  "
                f"manifest {str(meta.get('manifest_sha256'))[:12]}  "
                f"温度 {meta.get('temperature')}  effort {meta.get('reasoning_effort')}")
    return "\n".join(lines)


def load(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分析对照实验结果")
    parser.add_argument("--results", required=True, help="执行器输出的 jsonl")
    parser.add_argument("--confirm-tier", default="counting", help="确证族所在的层")
    parser.add_argument("--primary", nargs=2, default=["delegated", "direct"],
                        help="主分析的两条路线")
    parser.add_argument("--out", help="把报告也写到这个文件")
    args = parser.parse_args(argv)

    data = Data(load(Path(args.results)))
    if not data.rows:
        print("结果文件里没有数据记录", file=sys.stderr)
        return 1

    text = report(
        data, data.routes,
        confirm_tier=args.confirm_tier,
        primary=(args.primary[0], args.primary[1]),
    )
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\n报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    from saya.console import keep_console_from_crashing  # noqa: E402

    keep_console_from_crashing()
    raise SystemExit(main())
