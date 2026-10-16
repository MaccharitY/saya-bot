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
    """Holm 校正（**逐步下降**）。返回 {键: (该步的阈值, 是否显著)}。

    ⚠️⚠️ **必须是逐步的，不能每个 p 各自比自己的阈值。** 原来的实现就是后者，
    于是能判出「更大的 p 显著、更小的 p 不显著」—— 同一份证据两个相反判定。
    实测：`{a: 0.02, b: 0.02, c: 0.9}` 里 a 不显著（阈值 0.05/3）
    而 b **显著**（阈值 0.05/2）。正确的 Holm 应当**全部不显著**。

    Holm 的程序是：按 p 升序，从最小的开始比，**只要有一个不显著就停下**，
    后面的全部判不显著。（阈值照旧按 α/(m−i) 报出来，方便看"差多少"。）
    """
    ordered = sorted(pvalues.items(), key=lambda pair: pair[1])
    m = len(ordered)
    out: dict[str, tuple[float, bool]] = {}
    stopped = False
    for index, (key, p) in enumerate(ordered):
        threshold = alpha / (m - index)
        significant = (not stopped) and p <= threshold
        if not significant:
            stopped = True
        out[key] = (threshold, significant)
    return out


def cluster_sign_flip_p(
    contributions: list[float], *, rounds: int = ROUNDS, seed: int = SEED
) -> float:
    """**按图翻转符号**的置换检验 p 值（双侧）。确证族的 p 就该用这个。

    ⚠️⚠️ 这是个真问题，不是措辞问题。原来的 `★显著` 由**题目级**的
    McNemar 精确检验决定，而区间是**按图聚类**算的 —— 两个口径不一致，
    于是能同时打出"95%CI 含 0"和"★显著"，自相矛盾。而同一张图上的题
    共享视觉内容、回答相关，题目级的检验**低估方差、让 p 偏小**，
    等于偷偷放宽了门槛 —— 这正是本文件开头那段话警告的事，也是
    pre/13 §3.3 承诺过要处理的。

    做法：先算每张图对统计量的**贡献**（该图名下"a 对 b 错"减"b 对 a 错"），
    再随机给每张图一个 ±1 的符号求和 —— 观测值落在极端尾部的比例就是 p。
    重抽样的单位是**图**，同一张图的题不会被拆开。

    假设：H0 下各图的贡献关于 0 对称（聚类符号翻转的标准假设）。
    ⚠️ 它**不假设**题目之间独立 —— 图内部随便怎么相关都行。
    """
    observed = abs(sum(contributions))
    if not contributions or observed == 0:
        return 1.0
    rng = random.Random(seed)
    hits = 0
    for _ in range(rounds):
        total = 0.0
        for value in contributions:
            total += -value if rng.random() < 0.5 else value
        if abs(total) >= observed:
            hits += 1
    # +1 修正：蒙特卡洛估计不该报出 0 —— 那会被读成"不可能更极端"
    return (hits + 1) / (rounds + 1)


def wilcoxon_signed_rank_p(diffs: list[float]) -> float:
    """配对 Wilcoxon 符号秩检验（双侧，正态近似 + 并列校正）。

    ⚠️ **题目级**，没有处理聚类 —— 和确证族那边同一个道理，它偏乐观。
    所以只用在**次要指标（延迟）**上，且报告里要标明口径。
    （没有 scipy，所以手写；n 大于 ~20 时正态近似已经够用。）
    """
    usable = [d for d in diffs if d != 0]
    n = len(usable)
    if n == 0:
        return 1.0
    ranked = sorted(range(n), key=lambda index: abs(usable[index]))
    ranks = [0.0] * n
    index = 0
    while index < n:
        stop = index
        while stop + 1 < n and abs(usable[ranked[stop + 1]]) == abs(usable[ranked[index]]):
            stop += 1
        average = (index + stop) / 2 + 1  # 秩从 1 开始
        for position in range(index, stop + 1):
            ranks[ranked[position]] = average
        index = stop + 1

    w_plus = sum(r for r, d in zip(ranks, usable) if d > 0)
    mean = n * (n + 1) / 4
    # 并列校正：Σ(t³−t) 用同一批秩算
    tie_sum = 0.0
    for value in set(ranks):
        count = ranks.count(value)
        tie_sum += count ** 3 - count
    variance = n * (n + 1) * (2 * n + 1) / 24 - tie_sum / 48
    if variance <= 0:
        return 1.0
    z = (w_plus - mean) / math.sqrt(variance)
    return min(1.0, 2 * (1 - _normal_cdf(abs(z))))


def hodges_lehmann(diffs: list[float]) -> float:
    """配对差的中位数无关估计（Walsh 平均的中位数）。

    比"差的中位数"稳 —— 它把两两平均也算进去，不受个别极值摆布。
    """
    if not diffs:
        return float("nan")
    walsh = sorted(
        (diffs[i] + diffs[j]) / 2 for i in range(len(diffs)) for j in range(i, len(diffs))
    )
    return percentile(walsh, 0.5)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


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
        """(只有 a 对, 只有 b 对)。**题目级** —— 只作描述，不用来判显著。"""
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

    def cluster_contributions(self, a: str, b: str, tier: str | None = None) -> list[float]:
        """**每张图**对"a 比 b 好多少"的贡献。`cluster_sign_flip_p` 的输入。

        一张图的贡献 = 该图名下所有题的 `(a 对? 1 : 0) − (b 对? 1 : 0)` 之和。
        只有两条路线都有记录的题才算 —— 配对比较不能拿单边凑数。

        ⚠️ 贡献为 0 的图**也要留在表里**（翻转 0 等于没翻，但它们本来就该
        参与这次"重新分配"）。丢掉的后果是置换分布少了一批观测，p 会偏小。
        """
        out: list[float] = []
        for items in self.clusters.values():
            total = 0.0
            for item in items:
                if a not in self.by_item[item] or b not in self.by_item[item]:
                    continue
                if tier is not None and self.tier_of[item] != tier:
                    continue
                total += self._correct(item, a) - self._correct(item, b)
            out.append(total)
        return out

    def paired(self, a: str, b: str, key: str) -> list[tuple[float, float]]:
        """两条路线都有记录、且这个字段都有的题 → [(a 的值, b 的值)]。"""
        out: list[tuple[float, float]] = []
        for item, per_route in self.by_item.items():
            if a not in per_route or b not in per_route:
                continue
            left, right = per_route[a].get(key), per_route[b].get(key)
            if left is None or right is None:
                continue
            out.append((float(left), float(right)))
        return out

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


def _comparison_line(data: Data, a: str, b: str, tier: str) -> str:
    """一条配对比较的完整结论：风险差 + 聚类区间 + 聚类置换 p。"""
    ids = list(data.by_item)
    point = data.risk_diff(ids, a, b, tier)
    boot = cluster_bootstrap(data.clusters, lambda i: data.risk_diff(i, a, b, tier))
    lo, hi = ci(boot)
    p_value = cluster_sign_flip_p(data.cluster_contributions(a, b, tier))
    mark = "显著" if p_value <= ALPHA else "不显著"
    return (
        f"风险差 {point:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]  "
        f"置换 p={p_value:.4f}（按图）  α={ALPHA} 不校正  → {mark}"
    )


def report(data: Data, routes: list[str], *, confirm_tier: str, primary: tuple[str, str]) -> str:
    lines: list[str] = []
    add = lines.append

    if data.missing_file:
        add("⚠️ 结果文件里缺 file 字段 —— 聚类分析退化成了题目级。")
        add("   重跑一次执行器（它现在会写 file），否则下面的区间是偏窄的。")
        add("")

    add(f"题目 {len(data.by_item)} 条，图片 {len(data.clusters)} 张，"
        f"其中被复用的 {data.reused} 张")
    add(f"路线 {routes}，自助重抽样 {ROUNDS} 次（单位=图片，种子={SEED}）")

    # ---- 数据完整性 ----
    # ⚠️ 配对比较**只算两条路线都有记录**的题。跑到一半的结果文件会让某些题
    # 少一条路线 —— 那时候"完整"的报告其实建立在残缺数据上，必须说出来。
    counts = Counter(len(per_route) for per_route in data.by_item.values())
    full = counts.get(len(routes), 0)
    add(f"数据完整性：{full}/{len(data.by_item)} 题三条路线齐全"
        + (f"；[!] 缺路线的 {len(data.by_item) - full} 题在配对表里被略过"
           if full < len(data.by_item) else ""))
    add("")

    # ---- 准确率：按路线 × 层 ----
    add("■ 准确率（95% 区间用聚类自助法；失败按不正确计入）")
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
    add("  ⚠️ '全部'只作描述，不是主指标 —— 各层预期方向相反，合并会互相抵消")
    add("")

    # ---- 主分析 ----
    # ⚠️ 协议 pre/13 §6 写的是"B vs A，单一比较、不校正"。以前 `--primary`
    # 收下了却从没用过 —— 参数是死的，唯一的判定只有下面那个 Holm 族
    # （最严阈值 0.0167）。于是"按协议写、按工具出数字"两边打架。
    left, right = primary
    add(f"■ 主分析（事先定死：{left} vs {right}；单一比较，按协议不校正）")
    if left not in routes or right not in routes:
        add(f"  [!] 结果里没有 {left} 或 {right} 这两条路线 —— 主分析做不了。")
    else:
        add("  " + _comparison_line(data, left, right, confirm_tier))
    add("")

    # ---- 确证族 ----
    add(f"■ 确证族：{confirm_tier} 层的两两比较（Holm 校正；比哪几条是事先定好的）")
    pvalues: dict[str, float] = {}
    rows: list[tuple[str, float, tuple[float, float], tuple[int, int], float]] = []
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
            # ⚠️ **p 值也必须按图算**，和区间同一个口径。
            # 用题目级的 McNemar 会低估方差、让 p 偏小（同一张图的题不独立），
            # 于是能同时打出"95%CI 含 0"和"★显著" —— 自相矛盾，而且方向是
            # **偷偷放宽**门槛。这正是文件开头那段警告的事。
            pvalues[key] = cluster_sign_flip_p(data.cluster_contributions(a, b, confirm_tier))
            rows.append((key, point, (lo, hi), (only_a, only_b), mcnemar_exact_p(only_a, only_b)))

    corrected = holm(pvalues)
    for key, point, (lo, hi), (only_a, only_b), mcnemar in rows:
        mark = "★显著" if corrected[key][1] else "不显著"
        add(f"  {key:<22} 风险差 {point:+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]")
        add(f"  {'':<22} 置换 p={pvalues[key]:.4f}（按图翻转符号，判显著用这个）"
            f"  阈值 {corrected[key][0]:.4f}  → {mark}")
        add(f"  {'':<22} 参考 McNemar p={mcnemar:.4f}（题目级、偏乐观，仅作对照）"
            f"  不一致对 {only_a}:{only_b}")
    add("")

    # ---- 探索层 ----
    add("■ 其余各层（探索性，生成假设用 —— 只报效应量与区间，不报 p）")
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
    # ⚠️ 只统计**跑成功**的那些，和 `run_experiment.summarize` 一个口径。
    # 不筛的话一次 60 秒超时会把端到端 P50 整个带偏。
    add("■ 延迟（单位 ms；只统计跑成功的，失败/超时不计 —— 与执行器口径一致）")
    for route in routes:
        ok = [r for r in data.rows if r.get("route") == route and not r.get("error")]
        vals = [r["latency_ms"] for r in ok if r.get("latency_ms")]
        walls = [r["wall_ms"] for r in ok if r.get("wall_ms")]
        outs = [r.get("tokens_out", 0) for r in ok if r.get("tokens_out")]
        add(f"  {route:<10} 模型 P50 {percentile(vals, 0.5):>7.0f}  "
            f"P95 {percentile(vals, 0.95):>7.0f}   "
            f"端到端 P50 {percentile(walls, 0.5):>7.0f}  "
            f"P95 {percentile(walls, 0.95):>7.0f}   "
            f"输出 token 中位 {percentile(outs, 0.5):>6.0f}")
    add("  ⚠️ 报延迟必须同时报输出 token：长回答自然慢，不报就没法比")

    # 配对差异 —— 协议 §6 的次要分析（Wilcoxon 符号秩 + Hodges-Lehmann）
    add("")
    add(f"  {primary[0]} − {primary[1]} 的配对差：")
    for label, field in (("模型耗时", "latency_ms"), ("端到端", "wall_ms")):
        pairs = data.paired(primary[0], primary[1], field)
        if len(pairs) < 8:
            add(f"    {label:<6} n={len(pairs)} 太少，不做检验")
            continue
        diffs = [left - right for left, right in pairs]
        add(f"    {label:<6} n={len(diffs)}  "
            f"Hodges-Lehmann 中位差 {hodges_lehmann(diffs):+.0f} ms  "
            f"Wilcoxon 符号秩 p={wilcoxon_signed_rank_p(diffs):.4f}")
    add("    ⚠️ 这两项是**题目级**的，没处理聚类（同一张图的题不独立）→ p 偏乐观。")
    add("      确证族那边已经改成按图算，延迟这边没有对应的成熟做法，所以只当次要参考。")
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
    add("  ⚠️ API 边际成本与本地算力摊销分列不合并 —— 两者不是一回事，不能相加")
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
        add("■ 预热：没有记录 —— 冷启动可能污染了 B/C 的首题延迟")
    for meta in data.meta:
        if meta.get("kind") == "run_meta":
            add(f"■ 运行：模型 {meta.get('model')}  提交 {meta.get('git_sha')}  "
                f"manifest {str(meta.get('manifest_sha256'))[:12]}  "
                f"温度 {meta.get('temperature')}  effort {meta.get('reasoning_effort')}")
    return "\n".join(lines)


def load(path: Path) -> list[dict]:
    """读 jsonl。**容错到"最后一行只写了一半"**。

    ⚠️ 执行器是边跑边写的，被强杀（Ctrl-C、断网、关机）时正好会留下一条
    半截的 JSON 行。以前这里是无保护 `json.loads` —— 一个字符的残缺就让
    **整份数据打不开**，而它是跑了好几百次调用、花了钱才换来的。

    ⚠️ 但也不许**悄悄**跳过：少读了数据会让结论失真，所以残行要数出来、
    报到 stderr，并由 `main` 再说一遍。
    """
    records: list[dict] = []
    broken = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            broken += 1
            print(
                f"[!] 第 {number} 行不是完整的 JSON，已跳过：{line[:60]}",
                file=sys.stderr,
            )
    if broken:
        print(
            f"[!] 共跳过 {broken} 行 —— 上次运行是中途中断的，"
            f"下面的结论建立在残缺数据上。",
            file=sys.stderr,
        )
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
    from saya.console import prepare_console  # noqa: E402

    prepare_console()
    raise SystemExit(main())
