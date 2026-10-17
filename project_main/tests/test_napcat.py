"""`saya/napcat.py`（QQ 侧状态与启停）的测试。

⚠️ **一条都不碰真进程、真端口。** `pgrep` / `ps` / `kill` / socket / `Popen`
全部打桩 —— 测试跑一百遍也不该动这台机器上的 QQ。

重点在判据：**「就绪」只看 3000 端口**。
2026-10-17 的故障就是"进程在、端口没开"，旧代码用 `pgrep` 判活，一路绿灯。
"""

from __future__ import annotations

import contextlib
import io
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_MAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_MAIN / "src"))

from saya import napcat  # noqa: E402


def state(
    *,
    installed: bool = True,
    pids: tuple[int, ...] = (),
    onebot: bool = False,
    webui: bool = True,
    elapsed: int | None = None,
) -> napcat.State:
    return napcat.State(
        installed=installed, pids=pids, onebot=onebot, webui=webui, elapsed=elapsed
    )


class ProbeTest(unittest.TestCase):
    def test_missing_binary_is_not_installed(self) -> None:
        with mock.patch.object(napcat, "find_pids", return_value=()), mock.patch.object(
            napcat, "port_open", return_value=False
        ), mock.patch.object(Path, "exists", return_value=False):
            found = napcat.probe(Path("/nonexistent"))
        self.assertFalse(found.installed)
        self.assertFalse(found.running)

    def test_running_but_port_closed_is_not_ready(self) -> None:
        """⚠️ 这就是 2026-10-17 那一刻的状态：进程在，QQ 没登录。"""
        with mock.patch.object(napcat, "find_pids", return_value=(100, 101)), mock.patch.object(
            napcat, "elapsed", return_value=600
        ), mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            napcat, "port_open", return_value=False
        ):
            found = napcat.probe(Path("/opt"))
        self.assertTrue(found.running)
        self.assertFalse(found.ready)
        self.assertFalse(found.starting, "跑了 10 分钟还这样，不该算启动中")

    def test_port_open_means_ready(self) -> None:
        with mock.patch.object(napcat, "find_pids", return_value=(100,)), mock.patch.object(
            napcat, "elapsed", return_value=600
        ), mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            napcat, "port_open", return_value=True
        ):
            found = napcat.probe(Path("/opt"))
        self.assertTrue(found.ready)

    def test_just_started_counts_as_starting(self) -> None:
        with mock.patch.object(napcat, "find_pids", return_value=(100,)), mock.patch.object(
            napcat, "elapsed", return_value=5
        ), mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            napcat, "port_open", return_value=False
        ):
            found = napcat.probe(Path("/opt"))
        self.assertTrue(found.starting)

    def test_unknown_elapsed_is_not_treated_as_fresh(self) -> None:
        """拿不到时长时**不能**当成刚启动 —— 那会把"没登录"说成"再等等"。"""
        with mock.patch.object(napcat, "find_pids", return_value=(100,)), mock.patch.object(
            napcat, "elapsed", return_value=None
        ), mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            napcat, "port_open", return_value=False
        ):
            found = napcat.probe(Path("/opt"))
        self.assertFalse(found.starting)


class DescribeTest(unittest.TestCase):
    def lines(self, found: napcat.State) -> str:
        return "\n".join(napcat.describe(found, napcat_dir=Path("/opt")))

    def test_ready(self) -> None:
        self.assertIn("就绪", self.lines(state(pids=(100,), onebot=True)))

    def test_not_installed(self) -> None:
        self.assertIn("未安装", self.lines(state(installed=False)))

    def test_not_running(self) -> None:
        self.assertIn("未运行", self.lines(state(installed=True)))

    def test_not_logged_in_points_at_webui(self) -> None:
        text = self.lines(state(installed=True, pids=(100,), elapsed=600, webui=True))
        self.assertIn("未就绪", text)
        self.assertIn("未登录", text)
        self.assertIn("6099", text)

    def test_starting_is_not_reported_as_logged_out(self) -> None:
        text = self.lines(state(installed=True, pids=(100,), elapsed=3))
        self.assertIn("启动中", text)
        self.assertNotIn("未登录", text)

    def test_no_colloquialisms(self) -> None:
        """屏幕上的话不许出现改过的口语词。见 tests/test_output_style.py。"""
        for found in (
            state(pids=(100,), onebot=True),
            state(pids=(100,), elapsed=600),
            state(pids=(100,), elapsed=3),
            state(installed=False),
            state(),
        ):
            text = self.lines(found)
            for bad in ("敲", "盯着", "拉起", "崩了", "闭嘴"):
                self.assertNotIn(bad, text)


class StartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_missing_binary_raises(self) -> None:
        with mock.patch.object(napcat, "probe", return_value=state(installed=False)):
            with self.assertRaises(FileNotFoundError):
                napcat.start("12345", napcat_dir=self.base, wait=0)

    def test_does_not_spawn_a_second_instance(self) -> None:
        """进程在、端口没开 → 只等，不再起一个。

        ⚠️ 两个实例会抢同一份 QQ 数据目录（`~/.config/QQ`），
        轻则互相踢登录，重则把配置写坏。
        """
        running = state(pids=(100,), onebot=False, elapsed=600)
        with mock.patch.object(napcat, "probe", return_value=running), mock.patch.object(
            napcat.subprocess, "Popen"
        ) as popen:
            found = napcat.start("12345", napcat_dir=self.base, wait=0)
        popen.assert_not_called()
        self.assertFalse(found.ready)

    def test_spawns_when_nothing_is_running(self) -> None:
        before = state(pids=(), onebot=False)
        ready = state(pids=(200,), onebot=True)
        with mock.patch.object(napcat, "probe", side_effect=[before, ready]), mock.patch.object(
            napcat.subprocess, "Popen"
        ) as popen:
            found = napcat.start("12345", napcat_dir=self.base, wait=1)
        self.assertTrue(found.ready)
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertIn("--no-sandbox", argv)
        self.assertEqual(argv[-2:], ["-q", "12345"])
        self.assertTrue((self.base / "log" / napcat.LOG_NAME).exists(), "日志文件该先建好")

    def test_fresh_start_waits_for_the_port(self) -> None:
        """一启动就判定"没登录"是错的 —— 适配器要几秒才加载完。"""
        before = state(pids=(), onebot=False)
        starting = state(pids=(200,), onebot=False, elapsed=1)
        ready = state(pids=(200,), onebot=True, elapsed=4)
        with mock.patch.object(
            napcat, "probe", side_effect=[before, starting, ready]
        ), mock.patch.object(napcat.subprocess, "Popen"), mock.patch.object(
            napcat.time, "sleep"
        ):
            found = napcat.start("12345", napcat_dir=self.base, wait=30)
        self.assertTrue(found.ready)


class StopTest(unittest.TestCase):
    def test_not_running_is_a_no_op(self) -> None:
        with mock.patch.object(napcat, "find_pids", return_value=()), mock.patch.object(
            napcat, "probe", return_value=state()
        ), mock.patch.object(napcat.os, "kill") as kill:
            napcat.stop(napcat_dir=Path("/opt"))
        kill.assert_not_called()

    def test_term_then_kill(self) -> None:
        with mock.patch.object(napcat, "_can_signal", return_value=True), mock.patch.object(
            napcat, "find_pids", return_value=(100,)
        ), mock.patch.object(napcat, "probe", return_value=state()), mock.patch.object(
            napcat, "_wait_gone", side_effect=[False, True]
        ), mock.patch.object(napcat.os, "kill") as kill:
            napcat.stop(napcat_dir=Path("/opt"))
        sent = [call.args[1] for call in kill.call_args_list]
        self.assertEqual(sent, [signal.SIGTERM, napcat._SIGKILL])

    def test_stops_after_first_signal_when_it_complies(self) -> None:
        with mock.patch.object(napcat, "_can_signal", return_value=True), mock.patch.object(
            napcat, "find_pids", return_value=(100,)
        ), mock.patch.object(napcat, "probe", return_value=state()), mock.patch.object(
            napcat, "_wait_gone", return_value=True
        ), mock.patch.object(napcat.os, "kill") as kill:
            napcat.stop(napcat_dir=Path("/opt"))
        self.assertEqual(kill.call_count, 1)

    def test_refuses_to_signal_on_windows(self) -> None:
        """Windows 上 `os.kill` 会真的终止进程 —— 那边一律不动手。"""
        with mock.patch.object(napcat, "_can_signal", return_value=False), mock.patch.object(
            napcat, "find_pids", return_value=(100,)
        ), mock.patch.object(napcat.os, "kill") as kill:
            with self.assertRaises(OSError):
                napcat.stop(napcat_dir=Path("/opt"))
        kill.assert_not_called()


class MainTest(unittest.TestCase):
    def run_main(self, argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return napcat.main(argv)

    def test_exit_zero_only_when_ready(self) -> None:
        with mock.patch.object(napcat, "probe", return_value=state(pids=(1,), onebot=True)):
            self.assertEqual(self.run_main(["status"]), 0)
        with mock.patch.object(napcat, "probe", return_value=state(pids=(1,), onebot=False)):
            self.assertEqual(self.run_main(["status"]), 1)

    def test_start_without_qq_is_an_error(self) -> None:
        with mock.patch.object(napcat, "_qq_from_env", return_value=""), mock.patch.object(
            napcat, "start"
        ) as start, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(napcat.main(["start"]), 2)
        start.assert_not_called()

    def test_unrecognised_action_is_rejected_by_argparse(self) -> None:
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            napcat.main(["teleport"])


if __name__ == "__main__":
    unittest.main()
