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

from analyze import Data, cluster_bootstrap, holm, mcnemar_exact_p  # noqa: E402


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
