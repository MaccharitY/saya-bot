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
        """指标都走 `detail` —— 这就是流水线真实的写法。"""
        writer = TraceWriter(self.path)
        writer.write(
            TraceEvent(
                kind="reply",
                experiment_tag="baseline",
                detail={
                    "route": "direct",
                    "model": "deepseek-flash",
                    "tokens_in": 1234,
                    "tokens_out": 56,
                    "cost": 0.0012,
                    "备注": "中文要能原样往返",
                },
            )
        )

        records = writer.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "reply")
        self.assertEqual(records[0]["experiment_tag"], "baseline")
        self.assertEqual(records[0]["detail"]["tokens_in"], 1234)
        self.assertEqual(records[0]["detail"]["备注"], "中文要能原样往返")

    def test_at_is_filled_automatically(self):
        self.assertTrue(TraceEvent(kind="trigger", experiment_tag="t").at)

    def test_records_append_one_line_each(self):
        writer = TraceWriter(self.path)
        for index in range(3):
            writer.write(
                TraceEvent(kind="trigger", experiment_tag="t", detail={"序号": index})
            )

        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[2])["detail"]["序号"], 2)

    def test_disabled_writer_writes_nothing(self):
        writer = TraceWriter(self.path, enabled=False)
        writer.write(TraceEvent(kind="trigger", experiment_tag="t"))
        self.assertFalse(self.path.exists())
        self.assertEqual(writer.read_all(), [])

    def test_read_all_on_missing_file_is_empty(self):
        self.assertEqual(TraceWriter(self.path).read_all(), [])


class RotationTest(unittest.TestCase):
    """埋点会轮转（2026-10-16 加的）。

    机器人是长期跑的，埋点只增不减，不管它迟早长成一个巨型文件。

    ⚠️ **轮转是有损的**：只留一代，转第二次就覆盖上一个 `.1`。
    这是取舍 —— 另一条路是无限留文件，那迟早把磁盘塞满，更糟。
    默认上限 10 MB，正常用碰不到；真要长留就定期把 `.1` 拷走。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "trace.jsonl"

    def event(self, kind: str = "reply") -> TraceEvent:
        return TraceEvent(kind=kind, experiment_tag="t")

    def one_event_size(self) -> int:
        probe = TraceWriter(self.path, max_bytes=0)
        probe.write(self.event("probe"))
        size = self.path.stat().st_size
        self.path.unlink()
        return size

    def rotate_once(self, count: int = 9) -> tuple[TraceWriter, int]:
        """写到刚好轮转**一次**为止。

        上限设成 5 个事件的大小：前 5 条落在原文件里，第 6 条之前触发轮转，
        剩下的落进新文件。`count` 必须 < 10，否则新文件也会顶到上限、再转一次
        （那就成了"有损轮转"那个用例的场景了）。
        """
        size = self.one_event_size()
        writer = TraceWriter(self.path, max_bytes=size * 5)
        for index in range(count):
            writer.write(self.event(f"e{index}"))
        return writer, count

    def test_file_rotates_once_it_hits_the_cap(self):
        writer, _ = self.rotate_once()
        self.assertTrue(writer.rotated_path.is_file(), "超上限后旧文件应该被挪走")
        self.assertLess(
            self.path.stat().st_size,
            writer.rotated_path.stat().st_size,
            "轮转后的新文件应该是从头开始的那份",
        )

    def test_a_single_rotation_loses_nothing(self):
        """⚠️ 实验聚合就靠这个 —— 轮转一次不该悄悄吞掉事件。"""
        writer, count = self.rotate_once()
        self.assertEqual(len(writer.read_all()), count)

    def test_read_all_is_in_chronological_order(self):
        writer, count = self.rotate_once()
        kinds = [item["kind"] for item in writer.read_all()]
        self.assertEqual(kinds, [f"e{i}" for i in range(count)])

    def test_rotation_can_be_turned_off(self):
        writer = TraceWriter(self.path, max_bytes=0)
        for index in range(50):
            writer.write(self.event(f"e{index}"))

        self.assertFalse(writer.rotated_path.exists())
        self.assertEqual(len(writer.read_all()), 50)

    def test_only_one_generation_is_kept(self):
        """上限设得极小、写一大堆时，旧的会被覆盖 —— 刻意的，别指望它是归档。"""
        writer = TraceWriter(self.path, max_bytes=300)
        for index in range(200):
            writer.write(self.event(f"e{index}"))

        self.assertFalse(self.path.with_name(self.path.name + ".2").exists())
        self.assertLess(len(writer.read_all()), 200, "多代轮转下有损，这是设计如此")

    def test_default_cap_is_generous(self):
        """默认上限要足够大，让正常使用根本碰不到轮转。"""
        from saya.obs.trace import DEFAULT_MAX_BYTES

        self.assertGreaterEqual(DEFAULT_MAX_BYTES, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
