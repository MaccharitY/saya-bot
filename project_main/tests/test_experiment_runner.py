"""对照实验执行器的单元测试 —— 只测纯逻辑，**不发任何 API 请求**。

为什么值得单独测：判分与汇总是**报告里数字的唯一来源**，
这里错了不会报错，只会静默地把报告污染掉。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiment"))

import run_experiment as runner  # noqa: E402


class GradeTest(unittest.TestCase):
    def test_all_keywords_must_appear(self):
        self.assertTrue(runner.grade("图里有 3 只猫", ["3", "猫"]))
        self.assertFalse(runner.grade("图里有 3 只狗", ["3", "猫"]))

    def test_case_insensitive(self):
        self.assertTrue(runner.grade("A dog and a Cat", ["DOG", "cat"]))

    def test_no_keywords_means_ungraded(self):
        self.assertIsNone(runner.grade("随便什么", []))

    def test_empty_answer_fails_when_keywords_expected(self):
        self.assertFalse(runner.grade("", ["猫"]))

    # ---- ⚠️ 子串冲突：全表扫出来的**真实**跨题冲突，不是假想 ----

    def test_a_number_inside_a_bigger_number_does_not_count(self):
        """⚠️ `'3' ⊂ '13'`：7 条题期望 `3`、4 条期望 `13`。

        朴素子串匹配会让**期望 3 的题答「13」被判对** —— counting 层的
        准确率被系统性抬高。
        """
        rivals = {"3", "13", "4"}
        self.assertTrue(runner.grade("图里有 3 只猫", ["3"], rivals=rivals))
        self.assertFalse(runner.grade("图里有 13 只猫", ["3"], rivals=rivals))
        self.assertTrue(runner.grade("图里有 13 只猫", ["13"], rivals=rivals))
        self.assertTrue(runner.grade("3 只", ["3"], rivals=rivals), "两侧不是数字，照样算中")

    def test_a_character_inside_a_longer_word_does_not_count(self):
        """⚠️ `'马' ⊂ '斑马'`：fine 层 1 条期望「马」、2 条期望「斑马」。

        期望「马」的题答「斑马」会被判对 —— 而**那正是 fine 层要测的细粒度错误**，
        于是那一层虚高，还偏向回答更啰嗦的路线。
        """
        rivals = {"马", "斑马"}
        self.assertTrue(runner.grade("图里有一匹马", ["马"], rivals=rivals))
        self.assertFalse(runner.grade("图里有三只斑马", ["马"], rivals=rivals))
        self.assertTrue(runner.grade("图里有三只斑马", ["斑马"], rivals=rivals))

    def test_the_longer_keyword_is_never_hurt_by_the_shorter_one(self):
        rivals = {"马", "斑马"}
        self.assertTrue(runner.grade("斑马", ["斑马"], rivals=rivals))

    def test_the_number_boundary_rule_does_not_need_rivals(self):
        """数字那类冲突不依赖 rivals —— 它是边界规则，任何时候都该生效。

        （中文那类才需要 rivals 才知道"哪个词更长"。）
        """
        self.assertTrue(runner.grade("图里有 3 只猫", ["3"]))
        self.assertFalse(runner.grade("图里有 13 只猫", ["3"]))
        self.assertTrue(runner.grade("答案是 3.0", ["3"]), "小数点不算数字")


class ReadProgressTest(unittest.TestCase):
    """⚠️ 断点续跑。一次正式实验是两三百次**真实计费**的调用 ——
    重跑一遍等于把钱再花一次。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "results.jsonl"

    def write(self, text: str) -> None:
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def test_reads_records_and_completed_pairs(self):
        self.write(
            json.dumps({"kind": "run_meta", "model": "m"}) + "\n"
            + json.dumps({"id": "i1", "route": "direct"}) + "\n"
            + json.dumps({"id": "i1", "route": "delegated"}) + "\n"
        )
        kept, records, done, dropped = runner._read_progress(self.path)
        self.assertEqual(len(kept), 3)
        self.assertEqual(len(records), 2, "run_meta 不是数据记录")
        self.assertEqual(done, {("i1", "direct"), ("i1", "delegated")})
        self.assertEqual(dropped, 0)

    def test_a_half_written_last_line_is_dropped(self):
        """⚠️ 边跑边落盘，被强杀时正好会留下一条半截的记录。

        **不清掉的话就毁了整个文件**：后面追加的内容会和这半行连成一个
        跨两行的坏 JSON，而文件里装着已经花掉的钱换来的数据。
        """
        self.write(
            json.dumps({"id": "i1", "route": "direct"}) + "\n" + '{"id": "i2", "rou'
        )
        kept, _, done, dropped = runner._read_progress(self.path)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(done, {("i1", "direct")})

    def test_the_repaired_file_is_valid_jsonl_again(self):
        """把留下的行写回去之后，每一行都还能解析 —— 追加才是安全的。"""
        self.write('{"id": "i1", "route": "direct"}\n{"id": "i2", "rou')
        kept, _, _, _ = runner._read_progress(self.path)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            for line in kept:
                handle.write(line + "\n")
        for line in self.path.read_text(encoding="utf-8").splitlines():
            json.loads(line)

    def test_blank_lines_are_ignored(self):
        self.write('\n{"id": "i1", "route": "direct"}\n\n')
        _, records, done, dropped = runner._read_progress(self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(dropped, 0)


class PercentileTest(unittest.TestCase):
    def test_empty_list_is_zero(self):
        self.assertEqual(runner.percentile([], 0.5), 0.0)

    def test_single_value(self):
        self.assertEqual(runner.percentile([7.0], 0.95), 7.0)

    def test_p50_and_p95(self):
        values = [float(n) for n in range(1, 101)]  # 1..100
        self.assertAlmostEqual(runner.percentile(values, 0.5), 50, delta=1)
        self.assertGreaterEqual(runner.percentile(values, 0.95), 95)

    def test_unsorted_input_is_handled(self):
        self.assertEqual(runner.percentile([9.0, 1.0, 5.0], 0.5), 5.0)


class LoadManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, text: str) -> Path:
        path = self.dir / "manifest.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_json_array_form(self):
        path = self._write(json.dumps([{"id": "a", "file": "a.jpg", "question": "?"}]))
        self.assertEqual(len(runner.load_manifest(path)), 1)

    def test_jsonl_form(self):
        path = self._write(
            json.dumps({"id": "a", "file": "a.jpg", "question": "?"})
            + "\n"
            + json.dumps({"id": "b", "file": "b.jpg", "question": "?"})
        )
        self.assertEqual(len(runner.load_manifest(path)), 2)

    def test_missing_field_is_a_loud_error(self):
        path = self._write(json.dumps([{"id": "a", "question": "?"}]))
        with self.assertRaises(SystemExit) as ctx:
            runner.load_manifest(path)
        self.assertIn("file", str(ctx.exception))

    def test_empty_manifest_is_a_loud_error(self):
        with self.assertRaises(SystemExit):
            runner.load_manifest(self._write(""))


def make_record(route: str, *, correct=None, tier: str = "common", latency=1000,
                tokens_in=250, tokens_out=10, error="", cost=0.0) -> dict:
    return {
        "id": f"{route}-{tier}-{latency}",
        "route": route,
        "tier": tier,
        "answer": "答案",
        "correct": correct,
        "error": error,
        "latency_ms": latency,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
    }


class SummarizeTest(unittest.TestCase):
    def test_reports_accuracy_per_route(self):
        text = runner.summarize(
            [
                make_record("direct", correct=True),
                make_record("direct", correct=False),
                make_record("delegated", correct=True),
                make_record("delegated", correct=True),
            ]
        )
        self.assertIn("路线 direct", text)
        self.assertIn("1/2 = 50%", text)
        self.assertIn("路线 delegated", text)
        self.assertIn("2/2 = 100%", text)

    def test_ungraded_items_count_as_incorrect(self):
        """⚠️ **口径必须是 ITT**：没判分的题算错，分母是全部样本。

        这条以前钉的是**反过来的**行为（分母剔掉未判分的题），于是
        **现场屏幕上看到的准确率比报告里的高** —— 同一批数据两个数字，
        照现场那个引用就错了。报告用的 `analyze.py` 一直是 ITT
        （`_correct` 里 `None → 0`），两边必须一致（pre/13 §5）。
        """
        text = runner.summarize(
            [make_record("direct", correct=True), make_record("direct", correct=None)]
        )
        self.assertIn("1/2 = 50%", text, "未判分的那条要留在分母里")
        self.assertIn("ITT", text)

    def test_errors_count_as_incorrect_too(self):
        """失败 / 报错同样计入分母 —— 否则"跑崩了"反而会提高准确率。"""
        text = runner.summarize(
            [
                make_record("direct", correct=True),
                make_record("direct", correct=True),
                make_record("direct", error="超时", correct=None),
            ]
        )
        self.assertIn("2/3", text)

    def test_failures_are_counted_and_excluded_from_latency(self):
        text = runner.summarize(
            [
                make_record("hybrid", correct=True, latency=500),
                make_record("hybrid", error="boom", latency=99999),
            ]
        )
        self.assertIn("样本 2", text)
        self.assertIn("失败 1", text)
        self.assertNotIn("99999", text, "失败的样本不该污染延迟统计")

    def test_latency_is_reported_as_percentiles_not_just_mean(self):
        text = runner.summarize([make_record("direct", latency=n) for n in (100, 200, 300, 4000)])
        self.assertIn("P50", text)
        self.assertIn("P95", text)

    def test_per_tier_breakdown(self):
        text = runner.summarize(
            [
                make_record("direct", correct=True, tier="counting"),
                make_record("direct", correct=False, tier="counting"),
                make_record("direct", correct=True, tier="ocr"),
            ]
        )
        self.assertIn("counting", text)
        self.assertIn("1/2", text)
        self.assertIn("ocr", text)

    def test_empty_input_does_not_crash(self):
        self.assertEqual(runner.summarize([]), "")


if __name__ == "__main__":
    unittest.main()
