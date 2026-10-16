"""`tools/panel.py`（常驻控制台）的测试。

⚠️ 这一组**绝不真的启停机器人** —— `subprocess.Popen` 全被替换掉，
端口探测也被打桩。真去跑 `start_saya.sh` 会拉起 NapCat、抢端口、
影响正在跑的实例，而测试就该是"跑一百遍也不动你机器上任何东西"。

重点在**归属**（ADR-026）：前端可以活着而机器人停着，
**但不允许机器人在跑、前端没了** —— 那就成了无头后端。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_MAIN = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_MAIN.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(PROJECT_MAIN / "src"))

# ⚠️ 这个脚本**不能叫 saya.py** —— 会和 src/saya/ 这个包同名，
#    于是 `from saya import ...` 导到的是包，不是它。实测撞过。
from panel import Supervisor, serve  # noqa: E402

SAMPLE = (
    "SAYA_LLM_API_KEY=sk-not-a-real-key\n"
    "SAYA_ONEBOT_LISTEN_PORT=8080\n"
    "SAYA_OWNER_ID=1001\n"
    "SAYA_ADMIN_IDS=\n"
    "SAYA_GROUP_POLICIES=111:mention\n"
    "SAYA_GROUP_DEFAULT_POLICY=mention\n"
)


class FakeChild:
    """装成 Popen：`poll()` 返回 None 就是在跑，stdin 收下的行记下来。"""

    def __init__(self, *, alive: bool = True) -> None:
        self.pid = 4242
        self.stdin = io.StringIO()
        self.stdout = iter(())
        self._alive = alive

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def send_signal(self, sig):
        self._alive = False

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False

    def forwarded(self) -> list[str]:
        return [line for line in self.stdin.getvalue().splitlines() if line]


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env_path = Path(self.dir.name) / ".env"
        with self.env_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(SAMPLE)
        self.lock_path = Path(self.dir.name) / "panel.lock"
        self.out: list[str] = []
        # 端口探测默认打桩成"没人听" —— 测试不该去连真实端口
        patcher = mock.patch("panel._port_open", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sup = Supervisor(
            env_path=self.env_path,
            lock_path=self.lock_path,
            output=self.out.append,
        )

    def env_value(self, key: str) -> str:
        from saya.envfile import EnvFile

        return EnvFile(self.env_path).get(key)

    def text(self) -> str:
        return "\n".join(self.out)


class DispatchTest(Fixture):
    def test_help_lists_the_process_commands(self):
        text = "\n".join(self.sup.handle("help").lines)
        for name in ("start", "end", "restart", "status", "quit", "show"):
            self.assertIn(name, text)

    def test_end_and_restart_and_quit_need_confirmation(self):
        """⚠️ 这三个都不能一条命令就悔棋 —— 必须问一句。"""
        for command in ("end", "restart", "quit"):
            outcome = self.sup.handle(command)
            self.assertEqual(outcome.confirm, command)
            self.assertIn("[y/N]", outcome.question)

    def test_quit_says_the_bot_goes_too(self):
        """退出控制台**会带走机器人** —— 这句话必须写出来，不然是暗坑。"""
        self.assertIn("机器人也会一起停", self.sup.handle("quit").question)

    def test_status_when_not_running(self):
        self.assertIn("没在跑", "\n".join(self.sup.handle("status").lines))

    def test_empty_line_is_a_no_op(self):
        self.assertEqual(self.sup.handle("   ").lines, ())


class OwnershipTest(Fixture):
    """归属锁：谁在管机器人。"""

    def test_no_lock_means_no_other_panel(self):
        self.assertIsNone(self.sup.other_panel())

    def test_claim_and_release(self):
        self.sup.claim_lock()
        self.assertTrue(self.lock_path.is_file())
        self.sup.release_lock()
        self.assertFalse(self.lock_path.is_file())

    def test_release_is_safe_when_never_claimed(self):
        self.sup.release_lock()  # 不该炸

    def test_stale_lock_from_a_dead_panel_is_ignored(self):
        """锁文件还在但那个进程早没了 = 上次没收干净，**不算**有人在跑。"""
        self.lock_path.write_text("999999999", encoding="ascii")
        with mock.patch("panel._pid_alive", return_value=False):
            self.assertIsNone(self.sup.other_panel())

    def test_live_lock_means_another_panel(self):
        """⚠️ 这里**必须打桩**，不能拿真实 pid 去试。

        `_pid_alive` 用的是 `os.kill(pid, 0)`，而它在 Windows 上**不是**检查存在 ——
        会真的去 `TerminateProcess`。测试跑在 Windows 上，拿真 pid 试等于开枪。
        """
        self.lock_path.write_text("4242", encoding="ascii")
        with mock.patch("panel._pid_alive", return_value=True):
            self.assertEqual(self.sup.other_panel(), 4242)

    def test_own_pid_is_not_another_panel(self):
        import os

        self.lock_path.write_text(str(os.getpid()), encoding="ascii")
        self.assertIsNone(self.sup.other_panel())


class StrayBotTest(Fixture):
    """无头后端：端口被占，但不是我们子进程 —— 上次控制台被关掉留下的孤儿。"""

    def test_none_when_the_port_is_free(self):
        self.assertIsNone(self.sup.stray_bot())

    def test_found_with_pid(self):
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837):
            self.assertEqual(self.sup.stray_bot(), 16837)

    def test_found_without_pid_still_counts(self):
        """拿不到 PID 也要报 —— 不能因为查不到就当没有。"""
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=None):
            self.assertEqual(self.sup.stray_bot(), 0)

    def test_our_own_child_is_not_stray(self):
        self.sup._child = FakeChild()  # noqa: SLF001
        with mock.patch("panel._port_open", return_value=True):
            self.assertIsNone(self.sup.stray_bot())

    def test_status_points_at_the_stray(self):
        """⚠️ 撞过的那一幕：面板说"本来就没在跑"，可端口上是上次留下的实例，
        于是 start 被 start_saya.sh 挡回来。这里必须**说出来**。"""
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837):
            text = "\n".join(self.sup.status().lines)
        self.assertIn("没人管", text)
        self.assertIn("16837", text)


class ManagementPassThroughTest(Fixture):
    """机器人没在跑时，设置命令直接写 `.env`。"""

    def test_settings_go_to_the_env_file(self):
        outcome = self.sup.handle("admin add 20001")
        self.assertTrue(outcome.ok)
        self.assertEqual(self.env_value("SAYA_ADMIN_IDS"), "20001")
        self.assertIn("机器人没在跑", "\n".join(outcome.lines))

    def test_bad_command_is_reported_and_writes_nothing(self):
        before = self.env_path.read_text(encoding="utf-8")
        outcome = self.sup.handle("admin add 不是数字")
        self.assertFalse(outcome.ok)
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before)

    def test_commands_are_forwarded_to_a_running_bot(self):
        """⚠️ **在跑的时候必须送进它的 stdin** —— 只改 .env 等于"我改了但它没变"。"""
        child = FakeChild()
        self.sup._child = child  # noqa: SLF001  故意装作机器人正在跑

        outcome = self.sup.handle("admin add 20001")

        self.assertEqual(child.forwarded(), ["admin add 20001"])
        self.assertEqual(outcome.lines, (), "结果应该由子进程的输出回来，这里不该自己再打一遍")

    def test_falls_back_when_the_bot_dies_mid_command(self):
        child = FakeChild()
        child.stdin = None  # 类型上不可能，但真会遇到"刚好在这时候退了"
        self.sup._child = child
        outcome = self.sup.handle("admin add 20001")
        self.assertTrue(outcome.ok)
        self.assertEqual(self.env_value("SAYA_ADMIN_IDS"), "20001")


class LifecycleTest(Fixture):
    def test_start_uses_the_projects_own_script(self):
        """别在这儿重写 NapCat / 自检那一套 —— 走 start_saya.sh。"""
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            outcome = self.sup.start()
        self.assertIn("启动中", "\n".join(outcome.lines))
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "bash")
        self.assertTrue(argv[1].endswith("start_saya.sh"))
        self.assertNotIn("--restart", argv, "没确认过就不该动别人的实例")

    def test_take_over_passes_restart(self):
        """接管无头实例才允许 --restart（那会杀掉正在跑的那个）。"""
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start(take_over=True)
        self.assertIn("--restart", popen.call_args.args[0])

    def test_start_asks_for_unbuffered_output(self):
        """⚠️ 子进程的 stdout 是**管道**，Python 会因此块缓冲 ——
        机器人命令台的输出全卡在缓冲区里，面板一个字都看不到。
        （表现就是"日志在滚、敲命令没反应"：日志走 stderr，一直是逐行的。）
        """
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start()
        self.assertEqual(popen.call_args.kwargs["env"]["PYTHONUNBUFFERED"], "1")

    def test_start_twice_says_already_running(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start()
            again = self.sup.start()
        self.assertIn("已经在跑", "\n".join(again.lines))

    def test_stop_uses_sigint_so_the_bot_can_clean_up(self):
        """⚠️ 发 SIGINT 而不是 SIGTERM：机器人接住 KeyboardInterrupt 会走
        finally 里的 pipeline.stop()（关接入、落记忆）。SIGTERM 会跳过清理。"""
        import signal as signal_module

        child = FakeChild()
        self.sup._child = child  # noqa: SLF001
        with mock.patch.object(child, "send_signal") as send_signal:
            outcome = self.sup.stop()
        self.assertIn("已停", "\n".join(outcome.lines))
        self.assertEqual(send_signal.call_args.args[0], signal_module.SIGINT)

    def test_stop_when_not_running_is_a_no_op(self):
        self.assertIn("本来就没在跑", "\n".join(self.sup.stop().lines))

    def test_status_counts_elapsed_time(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start()
            outcome = self.sup.status()
        self.assertIn("在跑", "\n".join(outcome.lines))


class ServeTest(Fixture):
    """启动流程 + 交互循环 + **收尾**。"""

    def run_it(self, script: str) -> str:
        serve(self.sup, stream=io.StringIO(script), output=self.out.append)
        return self.text()

    def test_banner_and_a_status_report_on_startup(self):
        """⚠️ 启动就报到一次 —— 不用先敲一下才知道现在什么状态。"""
        self.run_it("")
        self.assertIn("控制台", self.text())
        self.assertIn("没在跑", self.text())

    def test_refuses_to_start_when_another_panel_holds_the_lock(self):
        self.lock_path.write_text("4242", encoding="ascii")
        with mock.patch("panel._pid_alive", return_value=True):
            self.run_it("show\n")
        text = self.text()
        self.assertIn("已经有一个控制台在跑", text)
        self.assertNotIn("主人", text, "自己退出了，就不该再处理命令")

    def test_claims_the_lock_while_running(self):
        with mock.patch.object(self.sup, "release_lock") as release:
            self.run_it("")
        release.assert_called_once()

    def test_quit_stops_the_bot_and_releases_the_lock(self):
        """⚠️ ADR-026：前端退出**一定带走机器人**，否则就是无头后端。"""
        self.sup._child = FakeChild()  # noqa: SLF001
        with mock.patch.object(self.sup, "stop", wraps=self.sup.stop) as stop:
            self.run_it("quit\ny\n")
        stop.assert_called_once()
        self.assertFalse(self.lock_path.is_file(), "锁必须放掉")

    def test_eof_also_stops_the_bot(self):
        """关窗口 / 管道断开都会走到 EOF —— 这条路同样要收尾。"""
        self.sup._child = FakeChild()  # noqa: SLF001
        with mock.patch.object(self.sup, "stop", wraps=self.sup.stop) as stop:
            self.run_it("")
        stop.assert_called_once()

    def test_offers_to_take_over_a_stray_bot(self):
        """⚠️ 撞过的那一幕：上次控制台留下的实例占着端口，start 起不来。"""
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.run_it("y\n")

        text = self.text()
        self.assertIn("没人管", text)
        self.assertIn("--restart", popen.call_args.args[0], "确认后才允许接管")

    def test_declining_the_takeover_leaves_it_alone(self):
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("subprocess.Popen") as popen:
            self.run_it("n\n")
        self.assertIn("取消了", self.text())
        popen.assert_not_called()

    def test_takeover_prompt_accepts_the_command_it_teaches(self):
        """⚠️ 真撞过的一版：提示语教用户"敲 restart 接管"，可 `restart` 这一答
        不在 YES 里，于是**被当成"否"静默吃掉** —— 用户只看到"那就不动它"，
        完全不知道自己的命令去哪了。

        所以接管那一问要多认 restart / start。
        """
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.run_it("restart\n")

        self.assertIn("--restart", popen.call_args.args[0])
        self.assertNotIn("取消了", self.text())

    def test_startup_does_not_say_the_same_thing_twice(self):
        """⚠️ 另一处真撞过的地方：原来先打一遍 `status`（它已经把无头实例
        描述了一遍），紧接着**提问又把同一件事说了一遍** —— 两段叠在一起，
        所有者直接说"我没看懂这是什么交互逻辑"。

        有一个无头实例时，**那个问题本身就是报到**，不要再打 status。
        """
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837):
            self.run_it("n\n")
        text = self.text()
        self.assertEqual(text.count("没人管的"), 1, text)
        self.assertNotIn("敲 restart（或 start）可以接管它", text)

    def test_start_later_also_asks_before_taking_over(self):
        """启动时答了"否"之后，再敲 start 还是要有个说法 ——
        不然只会被 start_saya.sh 挡回来一句"已经有一个在跑了"。
        """
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.run_it("n\nstart\ny\n")
        self.assertIn("--restart", popen.call_args.args[0])

    def test_answer_n_cancels(self):
        self.run_it("end\nn\n")
        self.assertIn("取消了", self.text())

    def test_quit_leaves_the_console(self):
        self.run_it("quit\ny\nstatus\n")
        text = self.text()
        self.assertIn("再见", text)
        self.assertNotIn("没在跑", text.split("再见")[-1], "退出之后不该再处理后面的行")


if __name__ == "__main__":
    unittest.main()
