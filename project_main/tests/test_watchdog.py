"""`saya/watchdog.py` —— 机器人自己盯着"养它的那个进程"。

⚠️ 这一组**绝不碰真实进程**：`owner_is_alive` 全被打桩。
真实的 `os.kill(pid, 0)` 在 Windows 上会**真的杀进程**（见下面那条护栏），
测试跑在 Windows 上，拿真 pid 试等于开枪。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_MAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_MAIN / "src"))

from saya import watchdog  # noqa: E402


class OwnerCheckTest(unittest.TestCase):
    def test_windows_never_touches_kill(self):
        """⚠️⚠️ 这条是护栏，不是功能。

        `os.kill(pid, 0)` **只在 POSIX 上**是"检查存在"；
        Windows 上它会走 `TerminateProcess` —— **真把那个进程杀掉**。
        机器人跑在 WSL 里走不到这儿，但测试跑在 Windows 上。
        所以 Windows 上必须**直接返回、连 os.kill 都不调**。
        """
        with mock.patch.object(watchdog.os, "name", "nt"), \
             mock.patch("os.kill") as kill:
            self.assertTrue(watchdog.owner_is_alive(12345))
        kill.assert_not_called()

    def test_missing_process_is_not_alive(self):
        # ⚠️ `is_zombie` 也得打桩：打了 `os.name` 之后 pathlib 会换实现，
        # 而真实文件系统是 Windows —— 那是测试自己制造出来的矛盾，不是代码问题。
        with mock.patch.object(watchdog.os, "name", "posix"), \
             mock.patch.object(watchdog, "is_zombie", return_value=False), \
             mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertFalse(watchdog.owner_is_alive(12345))

    def test_permission_error_counts_as_alive(self):
        """不归我们管 ≠ 不存在。"""
        with mock.patch.object(watchdog.os, "name", "posix"), \
             mock.patch.object(watchdog, "is_zombie", return_value=False), \
             mock.patch("os.kill", side_effect=PermissionError):
            self.assertTrue(watchdog.owner_is_alive(1))

    def test_a_zombie_counts_as_dead(self):
        """⚠️⚠️ **僵尸进程对 `kill(pid, 0)` 是"活着"的。**

        父进程没回收时，进程会以僵尸形态赖在进程表里 ——
        `/proc/<pid>` 还在、`kill` 成功返回，但它早死了。

        **实测撞过**（2026-10-16）：强杀面板之后，它的父进程还在 sleep、
        没回收它，于是面板成了僵尸，看护**永远不触发** ——
        机器人一直不退，正是要防的那件事。
        """
        with mock.patch.object(watchdog.os, "name", "posix"), \
             mock.patch.object(watchdog, "is_zombie", return_value=True), \
             mock.patch("os.kill") as kill:
            self.assertFalse(watchdog.owner_is_alive(12345))
        kill.assert_not_called()

    def test_zombie_detection_reads_the_state_field(self):
        """`/proc/<pid>/stat` 第三个字段是状态，`Z` 就是僵尸。

        ⚠️ 格式是 `pid (comm) STATE ...`，而 `comm` **可能带空格和括号** ——
        所以要从**最后一个右括号**之后取，不能直接 split。
        """

        def fake_path(text: str):
            class FakePath:
                def __init__(self, _raw: str) -> None:
                    pass

                def read_text(self, **_kwargs) -> str:
                    return text

            return FakePath

        cases = [
            ("4242 (my (weird) prog) Z 1 2 3", True),
            ("4242 (prog with spaces) S 1 2 3", False),
            ("4242 (p) Z 1 2 3", True),
        ]
        for line, expected in cases:
            with mock.patch.object(watchdog.pathlib, "Path", fake_path(line)):
                self.assertEqual(watchdog.is_zombie(4242), expected, line)

    def test_unreadable_proc_falls_back_to_kill(self):
        """没有 `/proc`（非 Linux）或读不到 —— 别在这儿瞎下结论，交给 kill。"""
        with mock.patch.object(watchdog.pathlib, "Path", side_effect=OSError):
            self.assertFalse(watchdog.is_zombie(4242))


class WatchTest(unittest.TestCase):
    def test_calls_on_lost_when_the_owner_disappears(self):
        called: list[int] = []
        with mock.patch.object(watchdog, "owner_is_alive", return_value=False), \
             mock.patch.object(watchdog, "_exit_now"):  # 不然测试进程会被 os._exit 干掉
            watchdog.watch_owner(12345, interval=0.01, on_lost=lambda: called.append(1))
            time.sleep(0.1)
        self.assertEqual(called, [1])

    def test_exits_even_if_on_lost_explodes(self):
        """⚠️ 收尾失败也得走 —— 留在那儿当无头后端比什么都糟。"""
        with mock.patch.object(watchdog, "owner_is_alive", return_value=False), \
             mock.patch.object(watchdog, "_exit_now") as exit_now:
            watchdog.watch_owner(
                12345, interval=0.01, on_lost=lambda: (_ for _ in ()).throw(RuntimeError("炸了"))
            )
            time.sleep(0.1)
        exit_now.assert_called()

    def test_says_nothing_while_the_owner_is_alive(self):
        called: list[int] = []
        with mock.patch.object(watchdog, "owner_is_alive", return_value=True), \
             mock.patch.object(watchdog, "_exit_now") as exit_now:
            watchdog.watch_owner(12345, interval=0.01, on_lost=lambda: called.append(1))
            time.sleep(0.1)
        self.assertEqual(called, [])
        exit_now.assert_not_called()

    def test_does_not_use_signals(self):
        """⚠️⚠️ 这条守的是**第一版踩过的坑**，而且第一版就是这么写的：

        用 `signal.raise_signal(SIGINT)` 让主线程自己收尾 —— 那个信号发给的是
        **调用它的线程**（看护线程），而主线程正阻塞在事件循环的 `select()` 里，
        **不会被打断**，于是 KeyboardInterrupt 永远不抛，机器人一直不退。

        Ctrl+C 好使是因为信号发给**整个进程**，内核会挑一个线程送。

        ⚠️ 查的是 **AST 里的真实调用**，不是文本 —— 文档字符串里正解释着
        "为什么不用它"，按文本查会把自己绊倒（第一版就这么写错了）。
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(watchdog))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "raise_signal":
                self.fail("又用回 raise_signal 了 —— 看护线程发的信号唤醒不了主线程")
            if isinstance(node, ast.Import) and any(a.name == "signal" for a in node.names):
                self.fail("watchdog 不该再依赖 signal 模块")


class StartIfOwnedTest(unittest.TestCase):
    def test_no_env_var_means_headless(self):
        """⚠️ **无头启动时什么都不做** —— `start_saya.bat` 起的机器人
        **本来就该独立活着**，没有任何人盯着它是对的。"""
        with mock.patch.dict(watchdog.os.environ, {}, clear=True):
            self.assertIsNone(watchdog.start_if_owned())

    def test_bad_value_is_ignored(self):
        with mock.patch.dict(watchdog.os.environ, {watchdog.OWNER_PID_ENV: "不是数字"}):
            self.assertIsNone(watchdog.start_if_owned())

    def test_zero_and_self_are_ignored(self):
        for bad in ("0", str(watchdog.os.getpid())):
            with mock.patch.dict(watchdog.os.environ, {watchdog.OWNER_PID_ENV: bad}):
                self.assertIsNone(watchdog.start_if_owned())

    def test_starts_watching_when_given_a_pid(self):
        with mock.patch.dict(watchdog.os.environ, {watchdog.OWNER_PID_ENV: "4242"}), \
             mock.patch.object(watchdog, "watch_owner") as watch:
            watchdog.start_if_owned()
        watch.assert_called_once()
        self.assertEqual(watch.call_args.args[0], 4242)


class PanelPassesItsPidTest(unittest.TestCase):
    """面板要把自己的 PID 传下去，否则机器人无从知道该盯谁。

    这条查**源码文本**而不是行为：真起一个面板要把整个 WSL 链路拉起来，
    而这里要守的只是"那行还在不在"（删了它，反方向的检查就静默失效了 ——
    不报错、也没有任何征兆，正是最难发现的那种）。
    """

    def test_panel_sets_the_env_var(self):
        source = (PROJECT_MAIN.parent / "tools" / "panel.py").read_text(encoding="utf-8")
        self.assertIn("SAYA_OWNER_PID", source)
        self.assertIn("str(os.getpid())", source)

    def test_the_bot_actually_reads_it(self):
        source = (PROJECT_MAIN / "src" / "saya" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("start_if_owned", source)


if __name__ == "__main__":
    unittest.main()
