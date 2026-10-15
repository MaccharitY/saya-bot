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

    def test_ungraded_items_are_excluded_from_accuracy(self):
        text = runner.summarize(
            [make_record("direct", correct=True), make_record("direct", correct=None)]
        )
        self.assertIn("1/1", text)
        self.assertNotIn("1/2", text)

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
