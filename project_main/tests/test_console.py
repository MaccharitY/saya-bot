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
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.console import prepare_console  # noqa: E402

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
    """挑出**会出现在屏幕上**的字符串字面量。

    按文本行匹配是不够的：`print(` 和它要打印的字符常常不在同一行
    （`--check` 那次崩溃就跨了三行）。AST 不受换行影响。

    ⚠️ **光看 `print(...)` 也不够** —— 2026-10-16 栽了两次：
    `thinking_banner()` 的 emoji 在 `return` 里、`POLICY_HELP` 的在字典里、
    `USAGE` / `BANNER` 的在模块级常量里。它们都不在 `print` 的参数里，
    却照样会打到屏幕上。
    所以这里连**会变成界面文本的调用和常量**一起收。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in _TEXT_CALLS:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        found.append((inner.lineno, inner.value))
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id in _TEXT_CONSTANTS for t in targets):
                for inner in ast.walk(node.value):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        found.append((inner.lineno, inner.value))
    return found


# 会产出"给人看的文本"的调用与常量（见上面那段说明）
_TEXT_CALLS = {"print", "Result", "Outcome", "_say", "_fail"}
_TEXT_CONSTANTS = {"BANNER", "USAGE"}


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
                    # ⚠️ 用 relpath 而不是 `relative_to(PROJECT_MAIN)`：
                    # 扫描范围含 `tools/`（在 PROJECT_MAIN 外面），
                    # `relative_to` 会抛 ValueError —— 于是测试**因为报错而失败**，
                    # 真正的问题（哪个字符编不进 GBK）反而看不见。
                    where = os.path.relpath(path, REPO_ROOT)
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

    def test_the_scanner_covers_more_than_print(self):
        """⚠️ 扫描器不能只看 `print(...)` —— 同类栽过三次了：

        - `thinking_banner()` 的 emoji 在 `return` 里
        - `POLICY_HELP` 的在**字典**里
        - `USAGE` / `BANNER` 的在**模块级常量**里

        它们都不在 `print` 的参数里，却照样打到屏幕上。
        这条拿一段合成源码钉住"常量和界面调用也算"。
        """
        import tempfile

        source = (
            "BANNER = '\\u26a0\\ufe0f 常量里的也得查'\n"
            "def f():\n"
            "    return _say('界面调用里的')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(source, encoding="utf-8")
            hits = printed_strings(path)
        self.assertEqual(len(hits), 2, hits)
        self.assertTrue(any(unencodable(text) for _, text in hits))


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
    """兜底本身。两件事都要做到：**别崩**、**别攒着**。

    第二条是 2026-10-16 补的：stdout 接的不是终端时会变成**块缓冲**，
    于是命令台的输出全卡在缓冲区里 —— 现象是"日志在滚、敲命令没反应"
    （日志走 stderr，一直是逐行的）。实测过：不加 `line_buffering`
    时管道 6 秒后还是空的，加了立刻就有。
    """

    def test_sets_replace_and_leaves_encoding_alone(self):
        class Recorder:
            def __init__(self) -> None:
                self.kwargs: dict | None = None

            def reconfigure(self, **kwargs):
                self.kwargs = kwargs

        out, err = Recorder(), Recorder()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            prepare_console()

        self.assertEqual(out.kwargs, {"errors": "replace", "line_buffering": True})
        self.assertEqual(err.kwargs, {"errors": "replace", "line_buffering": True})

    def test_survives_a_stream_without_reconfigure(self):
        """不是 TextIOWrapper（比如测试里换成了 StringIO）也不能炸。"""
        with mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", object()):
            prepare_console()

    def test_survives_reconfigure_raising(self):
        class Detached:
            def reconfigure(self, **kwargs):
                raise ValueError("underlying buffer has been detached")

        with mock.patch.object(sys, "stdout", Detached()), mock.patch.object(sys, "stderr", Detached()):
            prepare_console()


if __name__ == "__main__":
    unittest.main()
