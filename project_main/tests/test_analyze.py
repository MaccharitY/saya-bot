"""实验分析的单元测试 —— 全部用合成数据，**不联网、不花钱**。

⚠️ 这一组守的是**报告结论的计算底座**。这些函数算错了不会报错，
只会**安静地给出一个好看的数字** —— 那正是最坏的情况。
所以每个统计量都拿手算得出的已知答案钉住。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_MAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_MAIN / "experiment"))

from analyze import (  # noqa: E402
    Data,
    cluster_bootstrap,
    cluster_sign_flip_p,
    hodges_lehmann,
    holm,
    mcnemar_exact_p,
    wilcoxon_signed_rank_p,
)


def record(item, route, *, file, tier="counting", correct=True, **extra):
    row = {
        "id": item, "route": route, "file": file, "tier": tier,
        "correct": correct, "error": "", "latency_ms": 100, "wall_ms": 120,
        "cost": 0.001, "tokens_in": 10, "tokens_out": 5,
    }
    row.update(extra)
    return row


class McNemarTest(unittest.TestCase):
    def test_no_discordant_pairs_is_p_one(self):
        self.assertEqual(mcnemar_exact_p(0, 0), 1.0)

    def test_symmetric_in_the_two_directions(self):
        self.assertEqual(mcnemar_exact_p(3, 12), mcnemar_exact_p(12, 3))

    def test_known_value(self):
        # n=14，min=1：2×(C(14,0)+C(14,1))/2^14 = 2×15/16384
        self.assertAlmostEqual(mcnemar_exact_p(1, 13), 2 * 15 / 16384, places=10)

    def test_balanced_split_is_not_significant(self):
        self.assertGreater(mcnemar_exact_p(5, 5), 0.9)

    def test_lopsided_split_is_significant(self):
        self.assertLess(mcnemar_exact_p(2, 18), 0.001)


class HolmTest(unittest.TestCase):
    def test_smallest_p_gets_the_strictest_threshold(self):
        result = holm({"a": 0.001, "b": 0.02, "c": 0.5})
        self.assertEqual(result["a"], (0.05 / 3, True))
        self.assertEqual(result["b"], (0.05 / 2, True))
        self.assertEqual(result["c"], (0.05, False))

    def test_just_above_threshold_is_not_significant(self):
        result = holm({"a": 0.02, "b": 0.03, "c": 0.9})
        # 0.02 > 0.05/3，所以第一个不显著
        self.assertFalse(result["a"][1])

    def test_stopping_at_the_first_failure(self):
        """⚠️⚠️ 这条钉的是**原来的实现根本不叫 Holm**。

        老实现是"每个 p 各自比自己的阈值"，没有逐步下降。于是
        `{a: 0.02, b: 0.02, c: 0.9}` 判出 a 不显著（阈值 0.0167）而 b **显著**
        （阈值 0.025）—— **同一个 p 值、相反的两个判定**。
        真正的 Holm 在 a 处就停了，后面一律不显著。
        """
        result = holm({"a": 0.02, "b": 0.02, "c": 0.9})
        self.assertFalse(result["a"][1])
        self.assertFalse(result["b"][1], "前一步没过，后面的一律不显著")
        self.assertFalse(result["c"][1])

    def test_oriented_so_a_bigger_p_is_never_significant_alone(self):
        """把一个显著排在一个不显著之后，它也不许显著。"""
        result = holm({"big": 0.2, "small": 0.009, "middle": 0.04})
        self.assertTrue(result["small"][1])
        self.assertFalse(result["middle"][1])
        self.assertFalse(result["big"][1])

    def test_single_comparison_is_just_alpha(self):
        result = holm({"only": 0.049})
        self.assertEqual(result["only"][0], 0.05)
        self.assertTrue(result["only"][1])


class ClusterSignFlipTest(unittest.TestCase):
    """⚠️ 确证族的 p 值必须**按图**算，和区间同一个口径。

    老实现用的是题目级 McNemar，于是能同时打出"95%CI 含 0"和"★显著" ——
    而 McNemar 低估方差、p 偏小，方向是**偷偷放宽**显著性门槛。
    """

    def test_uniform_differences_are_significant(self):
        self.assertLess(cluster_sign_flip_p([1.0] * 12), 0.01)

    def test_balanced_differences_are_not_significant(self):
        self.assertGreater(cluster_sign_flip_p([1.0, -1.0] * 10), 0.5)

    def test_no_difference_at_all_is_p_one(self):
        self.assertEqual(cluster_sign_flip_p([0.0, 0.0, 0.0]), 1.0)

    def test_the_same_evidence_is_weaker_when_it_comes_from_few_images(self):
        """⚠️⚠️ **这条就是评审举的那个反例，也是整件事的要害。**

        同样是 10 个"只有 a 对"的不一致对：
        - **摊在 10 张图上** —— 每张一个，独立信息有 10 份 → p 很小
        - **挤在 2 张图上** —— 只有 2 份独立证据 → p 大得多

        题目级的 McNemar 对这**两种情形给出同一个 p**（它只看不一致对的个数），
        所以在后一种情形下会严重高估显著性。
        而真实测试集里 **56% 的题与另一题同图**，这不是假想情形。
        """
        spread = cluster_sign_flip_p([1.0] * 10)
        concentrated = cluster_sign_flip_p([5.0, 5.0] + [0.0] * 8)
        self.assertLess(spread, 0.05)
        self.assertGreater(concentrated, 0.2)
        self.assertLess(spread, concentrated)

    def test_same_seed_is_reproducible(self):
        values = [1.0, -1.0, 2.0, 0.0, 3.0]
        self.assertEqual(cluster_sign_flip_p(values), cluster_sign_flip_p(values))


class LatencyTestTest(unittest.TestCase):
    """延迟的次要分析（协议 §6 承诺的 Wilcoxon + Hodges-Lehmann）。"""

    def test_all_positive_differences_are_significant(self):
        self.assertLess(wilcoxon_signed_rank_p([10.0] * 20), 0.001)

    def test_symmetric_differences_are_not_significant(self):
        self.assertGreater(wilcoxon_signed_rank_p([10.0, -10.0] * 10), 0.9)

    def test_all_zero_is_p_one(self):
        self.assertEqual(wilcoxon_signed_rank_p([0.0, 0.0]), 1.0)

    def test_hodges_lehmann_matches_a_hand_computed_case(self):
        # [1,2,3] 的 Walsh 平均是 1, 1.5, 2, 2, 2.5, 3 → 中位数 2
        self.assertAlmostEqual(hodges_lehmann([1.0, 2.0, 3.0]), 2.0)

    def test_hodges_lehmann_resists_a_single_extreme_value(self):
        self.assertLess(hodges_lehmann([1.0, 2.0, 1000.0]), 100.0)


class ClusterBootstrapTest(unittest.TestCase):
    def test_same_seed_gives_identical_results(self):
        clusters = {"a": ["1", "2"], "b": ["3", "4"], "c": ["5", "6"]}
        stat = lambda ids: sum(1 for i in ids if int(i) % 2) / len(ids)  # noqa: E731
        first = cluster_bootstrap(clusters, stat, rounds=200, seed=1)
        second = cluster_bootstrap(clusters, stat, rounds=200, seed=1)
        self.assertEqual(first, second)

    def test_resamples_whole_clusters_not_individual_items(self):
        """⚠️ 这是整套分析的关键：重抽样单位必须是**图片**，不是题目。

        若退化成题目级重抽样，同图题目会被打散，方差被低估、p 值偏小。

        构造：两张图，各自内部答案完全一致（img1 全对、img2 全错），每张 3 题。
        抽两次（簇数=2），于是"对的题数"只能是 **0、3 或 6** —— 永远是簇大小的整数倍。
        **1/3 和 2/3 这些中间值永远不会出现**，那正是题目级重抽样的特征。
        """
        clusters = {"img1": ["a1", "a2", "a3"], "img2": ["b1", "b2", "b3"]}
        answer = {"a1": 1, "a2": 1, "a3": 1, "b1": 0, "b2": 0, "b3": 0}
        stat = lambda ids: sum(answer[i] for i in ids) / len(ids)  # noqa: E731

        values = set(cluster_bootstrap(clusters, stat, rounds=300, seed=3))

        self.assertTrue(values <= {0.0, 0.5, 1.0}, f"出现了非法的中间值：{values}")
        self.assertNotIn(1 / 3, values)
        self.assertNotIn(2 / 3, values)


class DataTest(unittest.TestCase):
    def make(self, rows):
        return Data(rows)

    def test_accuracy_counts_failures_as_incorrect(self):
        """ITT：报错的题算不正确，不能从分母里悄悄剔掉。"""
        rows = [
            record("i1", "direct", file="f1", correct=True),
            record("i2", "direct", file="f2", correct=False),
            record("i3", "direct", file="f3", correct=None, error="超时"),
        ]
        data = self.make(rows)
        self.assertAlmostEqual(data.accuracy(list(data.by_item), "direct"), 1 / 3)

    def test_risk_diff_is_paired(self):
        """只比较**两条路线都有记录**的题目 —— 配对的要点。"""
        rows = [
            record("i1", "a", file="f1", correct=True),
            record("i1", "b", file="f1", correct=False),
            record("i2", "a", file="f2", correct=False),
            record("i2", "b", file="f2", correct=False),
            # i3 只有 a —— 不该进配对比较
            record("i3", "a", file="f3", correct=True),
        ]
        data = self.make(rows)
        self.assertAlmostEqual(data.risk_diff(list(data.by_item), "a", "b"), 0.5)

    def test_discordant_counts_both_directions(self):
        rows = [
            record("i1", "a", file="f1", correct=True),
            record("i1", "b", file="f1", correct=False),   # 只有 a 对
            record("i2", "a", file="f2", correct=False),
            record("i2", "b", file="f2", correct=True),    # 只有 b 对
            record("i3", "a", file="f3", correct=True),
            record("i3", "b", file="f3", correct=True),    # 一致，不计
        ]
        data = self.make(rows)
        self.assertEqual(data.discordant("a", "b"), (1, 1))

    def test_clusters_group_items_by_image(self):
        rows = [
            record("i1", "a", file="same.jpg"),
            record("i2", "a", file="same.jpg"),
            record("i3", "a", file="other.jpg"),
        ]
        data = self.make(rows)
        self.assertEqual(sorted(data.clusters), ["other.jpg", "same.jpg"])
        self.assertEqual(len(data.clusters["same.jpg"]), 2)
        self.assertEqual(data.reused, 1)

    def test_cluster_contributions_keep_every_image(self):
        """⚠️ 贡献为 0 的图**也要留在表里**。

        符号翻转是在"每张图"上重抽的 —— 丢掉贡献为 0 的图等于少抽一批观测，
        置换分布会变窄，p 偏小（又是同一个方向的错：让结果显得更显著）。
        """
        rows = [
            record("i1", "a", file="f1", correct=True),
            record("i1", "b", file="f1", correct=False),   # f1 贡献 +1
            record("i2", "a", file="f2", correct=True),
            record("i2", "b", file="f2", correct=True),    # f2 贡献 0
            record("i3", "a", file="f3", correct=False),
            record("i3", "b", file="f3", correct=False),   # f3 贡献 0
        ]
        data = self.make(rows)
        contributions = data.cluster_contributions("a", "b")
        self.assertEqual(len(contributions), 3, "三张图都要在，包括贡献为 0 的")
        self.assertAlmostEqual(sum(contributions), 1.0)

    def test_cluster_contributions_skip_unpaired_items(self):
        """只有一条路线有记录的题**不能凑进配对贡献**。"""
        rows = [
            record("i1", "a", file="f1", correct=True),
            record("i1", "b", file="f1", correct=False),
            record("i2", "a", file="f1", correct=True),   # i2 没有 b
        ]
        data = self.make(rows)
        self.assertAlmostEqual(sum(data.cluster_contributions("a", "b")), 1.0)

    def test_paired_returns_only_complete_tuples(self):
        rows = [
            record("i1", "a", file="f1", latency_ms=100),
            record("i1", "b", file="f1", latency_ms=140),
            record("i2", "a", file="f2", latency_ms=90),   # i2 没有 b
        ]
        data = self.make(rows)
        self.assertEqual(data.paired("a", "b", "latency_ms"), [(100.0, 140.0)])

    def test_missing_file_field_is_flagged(self):
        """老结果文件没有 file 字段时，必须**喊出来**而不是默默退化。"""
        rows = [{"id": "i1", "route": "a", "tier": "counting", "correct": True}]
        data = self.make(rows)
        self.assertTrue(data.missing_file)


class DesignEffectTest(unittest.TestCase):
    """设计效应 = 聚类方差 ÷ 独立假设下的方差。

    这一组用两个极端构造验证**估计器方向是对的** ——
    报告要拿这个数字说明"为什么不能用普通比例检验"。
    """

    def test_independent_data_gives_about_one(self):
        rows = []
        for index in range(60):
            # 每张图一道题 → 没有聚类，设计效应应当接近 1
            rows.append(record(f"i{index}", "a", file=f"f{index}.jpg", correct=index % 2 == 0))
        data = Data(rows)
        self.assertAlmostEqual(data.design_effect("a"), 1.0, delta=0.35)

    def test_clustered_data_gives_much_more_than_one(self):
        rows = []
        for image_index in range(10):
            outcome = image_index < 5  # 整张图的题答案一致 —— 完全聚类
            for item_index in range(5):
                rows.append(
                    record(
                        f"i{image_index}_{item_index}", "a",
                        file=f"f{image_index}.jpg", correct=outcome,
                    )
                )
        data = Data(rows)
        self.assertGreater(data.design_effect("a"), 2.0)


if __name__ == "__main__":
    unittest.main()
