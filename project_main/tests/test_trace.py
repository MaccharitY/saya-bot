"""埋点写入与读回的单元测试。零依赖，标准库 unittest。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.obs.trace import TraceEvent, TraceWriter  # noqa: E402


class TraceWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "nested" / "trace.jsonl"

    def test_creates_parent_directory(self):
        TraceWriter(self.path)
        self.assertTrue(self.path.parent.is_dir())

    def test_write_then_read_round_trip(self):
        writer = TraceWriter(self.path)
        event = TraceEvent(
            kind="vision_call",
            experiment_tag="baseline",
            route="direct_mllm",
            model="deepseek-vl",
            tokens_in=1234,
            tokens_out=56,
            latency_ms=890,
            cost=0.0012,
            detail={"分类": "猫", "备注": "中文要能原样往返"},
        )
        writer.write(event)

        records = writer.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["route"], "direct_mllm")
        self.assertEqual(records[0]["tokens_in"], 1234)
        self.assertEqual(records[0]["detail"]["备注"], "中文要能原样往返")

    def test_at_is_filled_automatically(self):
        self.assertTrue(TraceEvent(kind="trigger", experiment_tag="t").at)

    def test_records_append_one_line_each(self):
        writer = TraceWriter(self.path)
        for index in range(3):
            writer.write(TraceEvent(kind="trigger", experiment_tag="t", latency_ms=index))

        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[2])["latency_ms"], 2)

    def test_disabled_writer_writes_nothing(self):
        writer = TraceWriter(self.path, enabled=False)
        writer.write(TraceEvent(kind="trigger", experiment_tag="t"))
        self.assertFalse(self.path.exists())
        self.assertEqual(writer.read_all(), [])

    def test_read_all_on_missing_file_is_empty(self):
        self.assertEqual(TraceWriter(self.path).read_all(), [])


if __name__ == "__main__":
    unittest.main()
