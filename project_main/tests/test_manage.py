"""`saya/manage.py`（命令语义）与 `saya/core/console.py`（终端命令台）的测试。

⚠️ 这一组守的是**一条会把机器人搞停的路径**：命令台住在机器人的主进程里，
一条手滑的命令、一个建不起来的配置、甚至一次回调异常，
都**不许**把正在群里说话的机器人拖下水。

用**合成 `.env`**，不读也不写真文件 —— 那是装着 API key 的东西。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_MAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_MAIN / "src"))

from saya.core.console import Console  # noqa: E402
from saya.envfile import EnvFile  # noqa: E402
from saya.manage import MAX_ADMINS, run  # noqa: E402

SAMPLE = (
    "SAYA_LLM_API_KEY=sk-not-a-real-key\n"
    "SAYA_OWNER_ID=1001\n"
    "SAYA_ADMIN_IDS=\n"
    "SAYA_GROUP_POLICIES=111:mention\n"
    "SAYA_GROUP_DEFAULT_POLICY=mention\n"
    "SAYA_TRIGGER_KEYWORDS=\n"
    "SAYA_REPLY_PROBABILITY=0.1\n"
    "SAYA_RATE_LIMIT_PER_GROUP_PER_MIN=5\n"
)


class EnvFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / ".env"
        # newline="" —— 不然 write_text 会把 LF 翻成 CRLF，行尾测试全是假失败
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(SAMPLE)

    def env(self) -> EnvFile:
        return EnvFile(self.path)

    def apply(self, line: str):
        """**照前端的做法**跑一条命令：changed 就落盘。

        ⚠️ `manage.run()` 本身**不动文件** —— 落盘是前端的职责
        （终端命令台这么干，离线 CLI 也这么干）。测试必须照做，
        否则会误以为命令没生效。
        """
        env = self.env()
        result = run(env, line)
        if result.changed and result.ok:
            env.save()
        return result


class CommandTest(EnvFixture):
    """命令语义 —— 这是**前端无关**的那一层，web 以后接的就是它。"""

    def test_show_lists_the_settings(self):
        result = self.apply("show")
        self.assertTrue(result.ok)
        text = result.text
        for expected in ("主人", "管理员", "白名单", "keyword", "111"):
            self.assertIn(expected, text)

    def test_show_changes_nothing(self):
        self.assertFalse(self.apply("show").changed)

    def test_add_admin(self):
        result = self.apply("admin add 20001")
        self.assertTrue(result.ok and result.changed)
        self.assertEqual(self.env().get("SAYA_ADMIN_IDS"), "20001")

    def test_bad_qq_is_rejected_and_not_saved(self):
        result = self.apply("admin add 不是数字")
        self.assertFalse(result.ok)
        self.assertFalse(result.changed, "参数不对就一个字节都不该写")

    def test_admin_limit_is_enforced(self):
        for index in range(MAX_ADMINS):
            self.apply(f"admin add 2000{index}")
        result = self.apply("admin add 99999")
        self.assertFalse(result.ok)
        self.assertIn("满", result.text)

    def test_group_policy_set_and_remove(self):
        self.assertTrue(self.apply("group 222 silent").changed)
        self.assertEqual(self.env().get("SAYA_GROUP_POLICIES"), "111:mention,222:silent")
        self.apply("group 111 -")
        self.assertEqual(self.env().get("SAYA_GROUP_POLICIES"), "222:silent")

    def test_unknown_policy_lists_valid_ones(self):
        result = self.apply("group 222 乱写的")
        self.assertFalse(result.ok)
        self.assertIn("mention", result.text)

    def test_rate_zero_warns_but_is_allowed(self):
        result = self.apply("rate 0")
        self.assertTrue(result.ok and result.changed)
        self.assertIn("永远不开口", result.text)

    def test_prob_out_of_range_is_rejected(self):
        self.assertFalse(self.apply("prob 1.5").ok)

    def test_unknown_command_says_so(self):
        result = self.apply("foobar 1 2")
        self.assertFalse(result.ok)
        self.assertIn("help", result.text)

    def test_help_lists_every_command(self):
        text = self.apply("help").text
        for name in ("show", "group", "owner", "admin", "keyword", "rate"):
            self.assertIn(name, text)


class ConsoleTest(EnvFixture):
    """命令台 —— 它住在机器人主进程里，所以**出错绝不能冒出去**。"""

    def make(self, apply=None) -> Console:
        return Console(env_path=self.path, apply=apply, output=lambda _: None)

    def test_change_is_persisted_and_applied(self):
        called = []
        console = self.make(apply=lambda: called.append(1) or [])
        result = console.handle("admin add 20001")
        self.assertTrue(result.ok)
        self.assertEqual(called, [1], "改完必须让内核换配置 —— 这才是「立刻生效」")
        self.assertEqual(self.env().get("SAYA_ADMIN_IDS"), "20001")

    def test_bad_command_does_not_touch_the_file_or_the_core(self):
        called = []
        before = self.path.read_text(encoding="utf-8")
        console = self.make(apply=lambda: called.append(1) or [])
        result = console.handle("admin add 不是数字")
        self.assertFalse(result.ok)
        self.assertEqual(called, [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_apply_failure_is_reported_not_raised(self):
        """⚠️ 换配置失败时**文件已经写好了**，这个不一致必须说出来，不能吞掉。"""

        def boom():
            raise RuntimeError("重建 Config 失败")

        result = self.make(apply=boom).handle("admin add 20001")
        self.assertTrue(result.ok)
        self.assertIn("没能当场生效", result.text)
        self.assertIn("重启", result.text)

    def test_missing_env_file_is_an_error_not_a_crash(self):
        console = Console(env_path=Path(self.dir.name) / "没有这个.env", output=lambda _: None)
        result = console.handle("show")
        self.assertFalse(result.ok)

    def test_serve_reads_until_eof(self):
        """读到 EOF 就安静退出 —— 后台跑（nohup / 重定向）时就是这个情形。"""
        output: list[str] = []
        console = Console(env_path=self.path, apply=lambda: [], output=output.append)
        console.serve(io.StringIO("show\nadmin add 20001\n\n"))
        self.assertTrue(any("主人" in line for line in output))
        self.assertEqual(self.env().get("SAYA_ADMIN_IDS"), "20001")

    def test_serve_survives_a_command_that_throws(self):
        output: list[str] = []
        console = Console(env_path=self.path, apply=lambda: [], output=output.append)
        console._apply = lambda: (_ for _ in ()).throw(RuntimeError("炸了"))  # noqa: SLF001
        console.serve(io.StringIO("admin add 20001\nshow\n"))
        # 第一条虽然出事，第二条照样处理
        self.assertTrue(any("主人" in line for line in output))


class RuntimeStatusTest(EnvFixture):
    """`status` 只有**机器人自己那个终端**有 —— 外面的控制台问不到进程运行时长。"""

    def make(self, status=None) -> Console:
        return Console(
            env_path=self.path, apply=lambda: [], status=status, output=lambda _: None
        )

    def test_status_comes_from_the_provider(self):
        console = self.make(status=lambda: ["== 运行 ==", "  已运行    3 分 5 秒"])
        result = console.handle("status")
        self.assertTrue(result.ok)
        self.assertIn("已运行", result.text)

    def test_status_is_not_a_manage_command(self):
        """没给 provider 时（比如离线 CLI）不该假装认识它。"""
        result = self.make().handle("status")
        self.assertFalse(result.ok)
        self.assertIn("help", result.text)

    def test_status_provider_blowing_up_does_not_escape(self):
        def boom():
            raise RuntimeError("取状态失败")

        result = self.make(status=boom).handle("status")
        self.assertFalse(result.ok)
        self.assertIn("取运行状态时出错", result.text)

    def test_supervisor_only_commands_point_elsewhere(self):
        """⚠️ 在机器人自己的终端里敲 `start`，只说"没这个命令"会让人以为功能没做。

        实测就这么撞上的：用户敲了三次 `status`，因为没人告诉他命令在哪。

        ⚠️ 而且指路要指**使用者认识的东西** —— 跟他说双击
        `saya_bot_controller.bat`，不是仓库里的 `tools/panel.py`
        （那个路径对他等于没说）。
        """
        console = self.make()
        for command in ("start", "restart"):
            result = console.handle(command)
            self.assertFalse(result.ok)
            self.assertIn("saya_bot_controller", result.text, f"{command} 该指路到那个双击文件")

    def test_help_mentions_status(self):
        console = self.make(status=lambda: ["x"])
        self.assertIn("status", console.handle("help").text)


if __name__ == "__main__":
    unittest.main()
