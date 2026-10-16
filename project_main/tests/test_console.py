"""控制台编码的回归测试（2026-10-16 加的）。

⚠️ 这里守的是一个**一天踩了三次**的坑：中文 Windows 的 `sys.stdout.encoding` 是 gbk，
输出里出现 GBK 编不出的字符（emoji）就会 `UnicodeEncodeError` 把程序打断。

三次分别是：`run.py --check` 崩在 `⚠️`、实验脚本的 `✓`/`✗`、
以及**我用来排查这个问题的脚本自己也崩了**（因为它把那个字符打了出来）。

光加兜底不够 —— 兜底只把字符变成 `?`，**信息还是丢了**。
所以这一组测试直接扫源码：**控制台输出里不许出现 GBK 编不出的字符**。
"""

from __future__ import annotations

import ast
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.console import keep_console_from_crashing  # noqa: E402

PROJECT_MAIN = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_MAIN.parent


def unencodable(text: str, encoding: str = "gbk") -> list[int]:
    """返回 text 里编不进 encoding 的字符码位。"""
    bad = []
    for char in text:
        try:
            char.encode(encoding)
        except UnicodeEncodeError:
            bad.append(ord(char))
    return bad


def printed_strings(path: Path) -> list[tuple[int, str]]:
    """用 AST 找出所有 `print(...)` 里的字符串字面量。

    按文本行匹配是不够的：`print(` 和它要打印的字符常常不在同一行
    （`--check` 那次崩溃就跨了三行）。AST 不受换行影响。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "print"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                found.append((inner.lineno, inner.value))
    return found


def source_files() -> list[Path]:
    """`project_main/` 与**仓库根的 `tools/`**。

    ⚠️ `tools/` 是 2026-10-16 补进来的：原来只扫 `project_main/`，
    于是**我在 `tools/config.py` 里写 `print("⚠️ …")` 时它一声不吭** ——
    而那个 `print` 在管道里会直接崩（截图里就撞到了）。
    规则要成立就得覆盖**所有能被跑起来的地方**，不能只管一半。
    """
    roots = [PROJECT_MAIN, REPO_ROOT / "tools"]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts and ".venv" not in path.parts
        )
    return sorted(found)


class ConsoleOutputIsEncodableTest(unittest.TestCase):
    """源码扫描：能打印出来的字符串必须编得进 GBK。"""

    def test_no_emoji_in_print_statements(self):
        offenders: list[str] = []
        for path in source_files():
            for lineno, text in printed_strings(path):
                codes = unencodable(text)
                if codes:
                    where = path.relative_to(PROJECT_MAIN)
                    offenders.append(f"{where}:{lineno} -> {[hex(c) for c in codes]}")

        self.assertEqual(
            offenders,
            [],
            "控制台输出里有 GBK 编不出的字符（会崩，或退化成 ?）：\n  "
            + "\n  ".join(offenders)
            + "\n换成 GBK 里有的符号：√ × · — 【】 [!]",
        )

    def test_the_scanner_would_actually_catch_one(self):
        """⚠️ 防的是"测试自己写错了"：扫描器必须真能抓到才说明上面那条有意义。"""
        self.assertEqual(unencodable("正常中文"), [])
        self.assertEqual(unencodable("√ × ·"), [])
        self.assertTrue(unencodable("⚠️"))
        self.assertTrue(unencodable("✓✗▶⟦⟧"))


class ConsoleTextFromHelpersTest(unittest.TestCase):
    """⚠️ 静态扫描**够不着**的一类：函数返回的字符串，最后被 print 出来。

    真实撞到过：`thinking_banner()` 返回的横幅里有 emoji。按 `print(...)` 扫字面量
    扫不到它（`⚠️` 在 `return` 里，不在 `print` 的参数里），于是干跑时输出成了 `??`。

    静态分析跟不了"这个字符串最后会流去哪"，所以这类就**直接调用看结果**。
    """

    @staticmethod
    def _config(**overrides):
        from dataclasses import replace

        from saya.config import Config

        base = Config.from_env(dotenv_path=Path("不存在的文件.env"))
        return replace(base, **overrides)

    @staticmethod
    def _banner(config) -> str:
        sys.path.insert(0, str(PROJECT_MAIN / "experiment"))
        try:
            from run_experiment import thinking_banner
        finally:
            sys.path.pop(0)
        return thinking_banner(config)

    def test_thinking_banner_is_encodable(self):
        banner = self._banner(self._config(reasoning_effort="high"))
        self.assertEqual(unencodable(banner), [], f"横幅里有 GBK 编不出的字符：{banner!r}")
        self.assertIn("temperature", banner)

    def test_non_thinking_banner_is_encodable(self):
        banner = self._banner(self._config(reasoning_effort="none"))
        self.assertEqual(unencodable(banner), [], f"横幅里有 GBK 编不出的字符：{banner!r}")

    def test_policy_help_is_encodable(self):
        """⚠️ 又是一条"函数返回、最后被打印"的路（见 ADR-021）。

        `POLICY_HELP` 在 `manage.py` 里，静态扫描只看 `print(...)` 的字面量，
        够不着它 —— 实测在真机上就打印成了 `?? 尚未实现`。
        **凡是会流到控制台的字符串，加进这一类测试里。**
        """
        sys.path.insert(0, str(PROJECT_MAIN / "src"))
        try:
            from saya.manage import POLICY_HELP, USAGE, policy_list
        finally:
            sys.path.pop(0)

        for name, text in POLICY_HELP.items():
            self.assertEqual(unencodable(text), [], f"POLICY_HELP[{name!r}] 编不出：{text!r}")
        for label, text in (("policy_list", policy_list()), ("USAGE", USAGE)):
            self.assertEqual(unencodable(text), [], f"{label} 里有编不出的字符")


class GuardTest(unittest.TestCase):
    """兜底本身：只改 errors，不改 encoding。"""

    def test_sets_replace_and_leaves_encoding_alone(self):
        class Recorder:
            def __init__(self) -> None:
                self.kwargs: dict | None = None

            def reconfigure(self, **kwargs):
                self.kwargs = kwargs

        out, err = Recorder(), Recorder()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            keep_console_from_crashing()

        self.assertEqual(out.kwargs, {"errors": "replace"})
        self.assertEqual(err.kwargs, {"errors": "replace"})

    def test_survives_a_stream_without_reconfigure(self):
        """不是 TextIOWrapper（比如测试里换成了 StringIO）也不能炸。"""
        with mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", object()):
            keep_console_from_crashing()

    def test_survives_reconfigure_raising(self):
        class Detached:
            def reconfigure(self, **kwargs):
                raise ValueError("underlying buffer has been detached")

        with mock.patch.object(sys, "stdout", Detached()), mock.patch.object(sys, "stderr", Detached()):
            keep_console_from_crashing()


if __name__ == "__main__":
    unittest.main()
