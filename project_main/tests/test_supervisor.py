"""`tools/panel.py`（常驻控制台）的测试。

⚠️ 这一组**绝不真的启停机器人** —— `subprocess.Popen` 全被替换掉。
真去跑 `start_saya.sh` 会拉起 NapCat、抢端口、影响正在跑的实例，
而测试就该是"跑一百遍也不动你机器上任何东西"。
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
        self.out: list[str] = []
        self.sup = Supervisor(env_path=self.env_path, output=self.out.append)

    def env_value(self, key: str) -> str:
        from saya.envfile import EnvFile

        return EnvFile(self.env_path).get(key)


class DispatchTest(Fixture):
    def test_help_lists_the_process_commands(self):
        text = "\n".join(self.sup.handle("help").lines)
        for name in ("start", "end", "restart", "status", "quit", "show"):
            self.assertIn(name, text)

    def test_end_and_restart_need_confirmation(self):
        """⚠️ 这两个都不能一条命令就悔棋 —— 必须问一句。"""
        for command in ("end", "restart"):
            outcome = self.sup.handle(command)
            self.assertEqual(outcome.confirm, command)
            self.assertIn("[y/N]", outcome.question)

    def test_status_when_not_running(self):
        self.assertIn("没在跑", "\n".join(self.sup.handle("status").lines))

    def test_empty_line_is_a_no_op(self):
        self.assertEqual(self.sup.handle("   ").lines, ())


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

    def test_start_twice_says_already_running(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start()
            again = self.sup.start()
        self.assertIn("已经在跑", "\n".join(again.lines))

    def test_stop_uses_sigint_so_the_bot_can_clean_up(self):
        """⚠️ 发 SIGINT 而不是 SIGTERM：机器人接住 KeyboardInterrupt 会走
        finally 里的 pipeline.stop()（关接入、落记忆）。SIGTERM 会跳过清理。"""
        child = FakeChild()
        self.sup._child = child  # noqa: SLF001
        with mock.patch.object(child, "send_signal") as send_signal:
            outcome = self.sup.stop()
        self.assertIn("已停", "\n".join(outcome.lines))
        self.assertEqual(send_signal.call_args.args[0], __import__("signal").SIGINT)

    def test_stop_when_not_running_is_a_no_op(self):
        self.assertIn("本来就没在跑", "\n".join(self.sup.stop().lines))

    def test_status_counts_elapsed_time(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = FakeChild()
            self.sup.start()
            outcome = self.sup.status()
        self.assertIn("在跑", "\n".join(outcome.lines))


class ServeTest(Fixture):
    """交互循环：特别是 **y/n 那一步**。"""

    def run_it(self, script: str) -> str:
        serve(self.sup, stream=io.StringIO(script), output=self.out.append)
        return "\n".join(self.out)

    def test_banner_is_shown(self):
        self.run_it("")
        self.assertIn("控制台", "\n".join(self.out))

    def test_answer_n_cancels(self):
        self.run_it("end\nn\n")
        self.assertIn("取消了", "\n".join(self.out))
        self.assertTrue(self.sup.handle("status").lines, "取消之后控制台还在")

    def test_answer_y_executes(self):
        self.run_it("end\ny\n")
        self.assertIn("本来就没在跑", "\n".join(self.out))

    def test_unknown_string_is_not_taken_as_yes(self):
        self.run_it("end\n随便打个字\n")
        self.assertIn("取消了", "\n".join(self.out))

    def test_quit_leaves_the_console(self):
        self.run_it("quit\ny\nstatus\n")
        joined = "\n".join(self.out)
        self.assertIn("再见", joined)
        self.assertNotIn("没在跑", joined, "退出之后不该再处理后面的行")


if __name__ == "__main__":
    unittest.main()
