"""`tools/panel.py`（常驻控制台）的测试。

⚠️ 这一组**绝不真的启停机器人** —— `subprocess.Popen` 全被替换掉，
端口探测也被打桩。真去跑 `start_saya.sh` 会拉起 NapCat、抢端口、
影响正在跑的实例，而测试就该是"跑一百遍也不动你机器上任何东西"。

重点在**归属**（ADR-026）：前端可以活着而机器人停着，
**但不允许机器人在跑、前端没了** —— 那就成了无头后端。
"""

from __future__ import annotations

import io
import os
import signal
import subprocess
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
from saya import cache, napcat  # noqa: E402

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
        # ⚠️ QQ 侧同样打桩。不打的话 `status` 会去 `pgrep`、连 3000/6099，
        # 测试就变成"看这台机器上恰好有没有在跑 NapCat"。
        self.napcat = napcat.State(installed=True, pids=(), onebot=False, webui=False)
        patcher = mock.patch("panel.napcat.probe", side_effect=lambda *a, **k: self.napcat)
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
        self.assertIn("bot 同时终止", self.sup.handle("quit").question)

    def test_status_when_not_running(self):
        self.assertIn("未运行", "\n".join(self.sup.handle("status").lines))

    def test_empty_line_is_a_no_op(self):
        self.assertEqual(self.sup.handle("   ").lines, ())


class NapCatTest(Fixture):
    """QQ 侧的启停与状态。

    ⚠️ 2026-10-17 的事故：QQ 进程活着、账号掉线，机器人一条消息都收不到，
    而控制台只报「运行中」。所以 `status` 必须**两行一起报**。
    """

    def test_status_reports_both_sides(self):
        text = "\n".join(self.sup.handle("status").lines)
        self.assertIn("未运行", text)  # 机器人
        self.assertIn("NapCat", text)  # QQ 侧

    def test_status_shows_qq_side_separately_from_the_bot(self):
        self.napcat = napcat.State(
            installed=True, pids=(900,), onebot=False, webui=True, elapsed=600
        )
        text = "\n".join(self.sup.handle("status").lines)
        self.assertIn("未就绪", text)
        self.assertIn("未登录", text)

    def test_napcat_command_reports_state(self):
        self.napcat = napcat.State(
            installed=True, pids=(900,), onebot=True, webui=True, elapsed=60
        )
        text = "\n".join(self.sup.handle("napcat").lines)
        self.assertIn("就绪", text)

    def test_stop_and_restart_need_confirmation(self):
        for command, action in (
            ("napcat stop", "napcat-stop"),
            ("napcat restart", "napcat-restart"),
        ):
            outcome = self.sup.handle(command)
            self.assertEqual(outcome.confirm, action)
            self.assertIn("[y/N]", outcome.question)

    def test_restart_warns_about_logging_in_again(self):
        """重启不保证自动登录 —— 必须提前说，不然人以为只是重启一下。"""
        self.assertIn("重新登录", self.sup.handle("napcat restart").question)

    def test_unknown_subcommand_is_rejected(self):
        outcome = self.sup.handle("napcat teleport")
        self.assertFalse(outcome.ok)
        self.assertIn("status", "\n".join(outcome.lines))

    def test_start_without_qq_number_refuses(self):
        outcome = self.sup.handle("napcat start")
        self.assertFalse(outcome.ok)
        self.assertIn("SAYA_SELF_ID", "\n".join(outcome.lines))

    def test_start_uses_the_qq_number_from_env(self):
        with mock.patch.object(self.sup, "self_qq", return_value="123456"):
            with mock.patch(
                "panel.napcat.start",
                return_value=napcat.State(
                    installed=True, pids=(900,), onebot=True, webui=True
                ),
            ) as started:
                outcome = self.sup.handle("napcat start")
        started.assert_called_once()
        self.assertEqual(started.call_args.args[0], "123456")
        self.assertTrue(outcome.ok)

    def test_confirmed_stop_calls_napcat_stop(self):
        with mock.patch(
            "panel.napcat.stop",
            return_value=napcat.State(installed=True, pids=(), onebot=False, webui=False),
        ) as stopped:
            outcome = self.sup.confirmed("napcat-stop")
        stopped.assert_called_once()
        self.assertIn("未运行", "\n".join(outcome.lines))

    def test_qq_side_is_not_tied_to_the_panel(self):
        """退出控制台只带走机器人 —— NapCat 留着（它揣着登录态）。"""
        with mock.patch("panel.napcat.stop") as napcat_stop:
            outcome = self.sup.confirmed("quit")
        self.assertTrue(outcome.stop)
        napcat_stop.assert_not_called()


class CacheTest(Fixture):
    """清缓存 —— 聊天记录与图片，**长期记忆与设置不动**。"""

    def test_cache_only_previews(self):
        with mock.patch("panel.cache_store.purge") as purge:
            text = "\n".join(self.sup.handle("cache").lines)
        purge.assert_not_called()
        self.assertIn("没有可清理的内容", text)

    def test_clear_needs_confirmation(self):
        outcome = self.sup.handle("cache clear")
        self.assertEqual(outcome.confirm, "cache-clear")
        self.assertIn("[y/N]", outcome.question)

    def test_the_question_names_what_is_kept(self):
        """⚠️ 不清的那部分必须写在问句里，不然人不敢按 y。"""
        self.assertIn("长期记忆与设置不受影响", self.sup.handle("cache clear").question)

    def test_confirmed_clear_actually_purges(self):
        with mock.patch(
            "panel.cache_store.purge", return_value=cache.Inventory(messages=3)
        ) as purge:
            outcome = self.sup.confirmed("cache-clear")
        purge.assert_called_once()
        self.assertIn("已清空", "\n".join(outcome.lines))

    def test_unknown_action_is_rejected(self):
        outcome = self.sup.handle("cache nuke")
        self.assertFalse(outcome.ok)
        self.assertIn("clear", "\n".join(outcome.lines))

    def test_help_mentions_cache(self):
        self.assertIn("cache", "\n".join(self.sup.handle("help").lines))


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
        """⚠️ 撞过的那一幕：面板说"未运行"，可端口上是上次留下的实例，
        于是 start 被 start_saya.sh 挡回来。这里必须**说出来**。"""
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837):
            text = "\n".join(self.sup.status().lines)
        self.assertIn("未托管", text)
        self.assertIn("16837", text)


class ManagementPassThroughTest(Fixture):
    """机器人没在跑时，设置命令直接写 `.env`。"""

    def test_settings_go_to_the_env_file(self):
        outcome = self.sup.handle("admin add 20001")
        self.assertTrue(outcome.ok)
        self.assertEqual(self.env_value("SAYA_ADMIN_IDS"), "20001")
        self.assertIn("bot 未运行", "\n".join(outcome.lines))

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
        self.assertIn("运行中", "\n".join(again.lines))

    def test_stop_uses_sigint_so_the_bot_can_clean_up(self):
        """⚠️ 发 SIGINT 而不是 SIGTERM：机器人接住 KeyboardInterrupt 会走
        finally 里的 pipeline.stop()（关接入、落记忆）。SIGTERM 会跳过清理。"""
        import signal as signal_module

        child = FakeChild()
        self.sup._child = child  # noqa: SLF001
        with mock.patch.object(child, "send_signal") as send_signal:
            outcome = self.sup.stop()
        self.assertIn("已停止", "\n".join(outcome.lines))
        self.assertEqual(send_signal.call_args.args[0], signal_module.SIGINT)

    def test_stop_when_not_running_is_a_no_op(self):
        self.assertIn("未运行", "\n".join(self.sup.stop().lines))

    def test_status_counts_elapsed_time(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start()
            outcome = self.sup.status()
        self.assertIn("运行中", "\n".join(outcome.lines))


class ServeTest(Fixture):
    """启动流程 + 交互循环 + **收尾**。"""

    def run_it(self, script: str) -> str:
        serve(self.sup, stream=io.StringIO(script), output=self.out.append)
        return self.text()

    def test_banner_and_a_status_report_on_startup(self):
        """⚠️ 启动就报到一次 —— 不用先敲一下才知道现在什么状态。"""
        self.run_it("")
        self.assertIn("控制台", self.text())
        self.assertIn("未运行", self.text())

    def test_refuses_to_start_when_another_panel_holds_the_lock(self):
        self.lock_path.write_text("4242", encoding="ascii")
        with mock.patch("panel._pid_alive", return_value=True):
            self.run_it("show\n")
        text = self.text()
        self.assertIn("已存在运行中的控制台", text)
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
        self.assertIn("未托管", text)
        self.assertIn("--restart", popen.call_args.args[0], "确认后才允许接管")

    def test_declining_the_takeover_leaves_it_alone(self):
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("subprocess.Popen") as popen:
            self.run_it("n\n")
        self.assertIn("已取消", self.text())
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
        self.assertNotIn("已取消", self.text())

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
        self.assertEqual(text.count("未托管"), 1, text)
        self.assertNotIn("可接管", text, "报到就说一次，别再补一句")

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
        self.assertIn("已取消", self.text())

    def test_quit_leaves_the_console(self):
        self.run_it("quit\ny\nstatus\n")
        text = self.text()
        self.assertIn("已退出", text)
        self.assertNotIn("未运行", text.split("已退出")[-1], "退出之后不该再处理后面的行")


class RegressionsFromReviewTest(ServeTest):
    """⚠️ 全部来自 2026-10-16 的**独立评审**，每一条都是真能出事的。

    这里专门补的是原来**没测到**的路径。评审员指出：`FakeChild` 的
    `send_signal` / `terminate` / `kill` 一律立刻成功，于是 `stop()` 的
    **超时升级分支从来没被执行过** —— 那类"发了信号收不了尾"的缺陷
    可以让整套测试全绿。
    """

    # ---- 归属锁 ----

    def test_zombie_panel_does_not_block_the_lock(self):
        """⚠️ **僵尸进程对 `os.kill(pid, 0)` 是"活着"的。**

        面板被强杀后如果父进程还没回收，它会以僵尸形态赖着，而锁里记的正是那个号
        —— 新面板于是判出"已存在运行中的控制台"、自己退出，**永远进不去**，
        界面上没有任何提示，唯一出路是手工删 `project_main/data/panel.lock`。

        （同一个坑 `watchdog.py` 那边修好了，`panel.py` 这边只修了一半。）
        """
        self.lock_path.write_text("4242", encoding="ascii")
        with mock.patch("panel.pid_is_alive", return_value=False):
            self.assertIsNone(self.sup.other_panel())

    def test_claim_lock_is_atomic(self):
        """⚠️ 以前是"先查一遍、再写一遍" —— 查和写之间别的进程能插进来，
        于是**两个面板都以为自己独占**。现在用 `O_EXCL` 建文件，抢不到就是抢不到。"""
        self.lock_path.write_text("4242", encoding="ascii")
        with mock.patch("panel._pid_alive", return_value=True):
            self.assertFalse(self.sup.claim_lock())
        self.assertEqual(self.lock_path.read_text(encoding="ascii"), "4242")

    def test_claim_lock_replaces_a_stale_lock(self):
        """锁文件在、但里面那个进程没了 = 上次没收干净 —— 该清掉重抢。"""
        self.lock_path.write_text("4242", encoding="ascii")
        with mock.patch("panel._pid_alive", return_value=False):
            self.assertTrue(self.sup.claim_lock())
        self.assertEqual(self.lock_path.read_text(encoding="ascii"), str(os.getpid()))

    def test_release_lock_leaves_someone_elses_lock_alone(self):
        """⚠️ 以前是**无条件 unlink**：抢锁失败的那个面板一退出就把别人的锁删了，
        于是第三个面板能进来，对着第一个的机器人"接管"。"""
        self.lock_path.write_text("4242", encoding="ascii")
        self.sup.release_lock()
        self.assertTrue(self.lock_path.is_file())
        self.assertEqual(self.lock_path.read_text(encoding="ascii"), "4242")

    # ---- restart 不能把提问吞掉 ----

    def test_restart_keeps_the_takeover_question(self):
        """⚠️ `restart()` 以前只把 `start()` 的 `lines` 拼起来，把
        `confirm` / `question` 丢掉了 —— 端口上有未托管实例时，屏幕上只出现
        提问的**上半句**（断在破折号上），既没有问句也没有"已取消"，
        敲完 y 什么都不会发生。"""
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("panel._looks_like_our_bot", return_value=True):
            outcome = self.sup.restart()
        self.assertEqual(outcome.confirm, "takeover")
        self.assertTrue(outcome.question)

    def test_a_foreign_process_on_the_port_is_not_offered_for_takeover(self):
        """⚠️ **端口号不算身份。** 8080 上坐着谁都可能，而"接管"的动作是
        **杀进程** —— 把别的程序叫成"未托管实例"，人就会去点那个 y。"""
        with mock.patch("panel._port_open", return_value=True), \
             mock.patch("panel._pid_on_port", return_value=16837), \
             mock.patch("panel._looks_like_our_bot", return_value=False):
            outcome = self.sup.takeover_prompt(16837)
        self.assertIsNone(outcome.confirm)
        self.assertFalse(outcome.ok)
        self.assertIn("不是本机器人", "\n".join(outcome.lines))

    # ---- 循环不能死 ----

    def test_one_bad_command_does_not_kill_the_console(self):
        """⚠️ 敲错一句命令最坏的后果是"报个错"，**不是"控制台消失 + 机器人被停"**。

        以前 `_loop` 裸调 `handle`：`manage` 抛异常 → 穿出 `_loop` →
        `serve` 的 finally 收尾、**把机器人停掉** → 窗口带着一屏栈消失。
        """
        with mock.patch.object(self.sup, "handle", side_effect=RuntimeError("模拟的意外")):
            self.run_it("show\n")
        self.assertIn("出错了", self.text())

    def test_an_unrecognized_answer_cancels_but_still_runs_the_line(self):
        """⚠️ 在 `[y/N]` 下敲一条命令，以前会被**静默咽掉**。

        `end` 之后顺手敲 `status`：只回一句"已取消。"，那条命令凭空消失
        —— 正是所有者撞过的"把命令吃掉"。现在明确说取消，**并把它当命令再跑一遍**。
        """
        self.run_it("end\nstatus\n")
        text = self.text()
        self.assertIn("已取消", text)
        self.assertIn("未运行", text, "被咽掉的那条命令应该照跑")

    def test_empty_answer_takes_the_default_no(self):
        """`[y/N]` 的默认是 N —— 直接回车就是"否"，而且要有回音。"""
        self.run_it("end\n\n")
        self.assertIn("已取消", self.text())

    # ---- 命令台没开的时候 ----

    def test_forward_says_so_when_the_console_is_off(self):
        """⚠️ `SAYA_CONSOLE_ENABLED=false` 时机器人**根本没起那个线程** ——
        往它的 stdin 写字是**白写**（管道那头没人读）。

        以前 `forward` 只看 `self.running`，于是命令进了管道、面板**一个字都不回**、
        `.env` 没写、内核也没换，而用户以为改好了。更糟的是行为不一致：
        机器人**停着**时同一条命令反而会正常打印"已写入 .env"。
        """
        with self.env_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(SAMPLE + "SAYA_CONSOLE_ENABLED=false\n")
        child = FakeChild()
        self.sup._child = child  # noqa: SLF001
        outcome = self.sup.forward("owner 10001")
        self.assertIn("没开命令台", "\n".join(outcome.lines))
        self.assertEqual(child.forwarded(), [], "不该往没人读的管道里写字")
        self.assertEqual(self.env_value("SAYA_OWNER_ID"), "10001", "该走离线那条路")

    def test_forward_uses_stdin_when_the_console_is_on(self):
        child = FakeChild()
        self.sup._child = child  # noqa: SLF001
        self.assertEqual(self.sup.forward("owner 10001").lines, ())
        self.assertEqual(child.forwarded(), ["owner 10001"])

    # ---- 发信号收不了尾 ----

    def test_stop_escalates_when_the_bot_ignores_sigint(self):
        """⚠️ 评审指出这条**从来没被执行过**：桩子里的 `wait()` 立刻返回，
        所以超时升级那两段是死代码。这里让它真的超时一次。

        而且升级时也要走 `_signal_group` —— 脚本 `exec` 之前 PID 还归 bash，
        只发单进程的话机器人根本收不到。
        """
        child = FakeChild()
        child.wait = mock.Mock(  # type: ignore[method-assign]
            side_effect=[subprocess.TimeoutExpired("x", 10), 0]
        )
        self.sup._child = child  # noqa: SLF001
        with mock.patch("panel._signal_group") as send:
            outcome = self.sup.stop()
        self.assertEqual(
            [call.args[1] for call in send.call_args_list],
            [signal.SIGINT, signal.SIGTERM],
        )
        self.assertIn("已停止", "\n".join(outcome.lines))

    def test_stop_gives_up_with_sigkill(self):
        """温柔的两下都不理，就只剩强杀 —— 也不能在这里卡住。

        ⚠️ 断言的是 `panel._SIGKILL` 而不是 `signal.SIGKILL`：**后者在 Windows 上
        根本不存在**，直接引用会在求值那一刻就 `AttributeError`
        （写这个测试的时候就是这么栽的 —— 面板只在 WSL 跑，但测试跑在 Windows 上）。
        """
        import panel as panel_module

        child = FakeChild()
        child.wait = mock.Mock(  # type: ignore[method-assign]
            side_effect=[
                subprocess.TimeoutExpired("x", 10),
                subprocess.TimeoutExpired("x", 5),
                0,
            ]
        )
        self.sup._child = child  # noqa: SLF001
        with mock.patch("panel._signal_group") as send:
            self.sup.stop()
        self.assertEqual(
            [call.args[1] for call in send.call_args_list],
            [signal.SIGINT, signal.SIGTERM, panel_module._SIGKILL],
        )


if __name__ == "__main__":
    unittest.main()
