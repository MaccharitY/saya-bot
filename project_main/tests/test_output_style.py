"""**说给使用者听的话**里不许出现：markdown 语法、内部文档引用。

⚠️ 这条规矩是所有者点出来的，而且他说得比"去掉星号"更深一层：

> **对用户（现在）的回复** 和 **写下来给使用者的文本** 是不一样的。

具体到这个项目，至少有三层**不同的语域**，混用就会出洋相：

| 语域 | 该长什么样 |
|---|---|
| **运行时的控制台输出** | 纯文本，给人当场看的。**不能有 markdown**（`**粗体**` 在终端里就是四个星号），也不能有 `见 docs/01 ADR-026` 这种**内部索引** —— 那是给维护者查资料的 |
| **写进仓库的文档 / README** | markdown —— 那里星号本来就该渲染成粗体 |
| **提示词**（`persona.py` / `roles.py` / 工具返回给模型的文本） | 给**模型**读的，强调语法有用，不算问题 |

实测撞到的：控制台里原样打出 `改完**立刻生效**`，还有
`（所以它同时是监视器和控制器，见 docs/01 ADR-026）` 整句 ——
**那是我的笔记，不是他的界面。**

所以这里只扫**会出现在屏幕上的那些字符串**：`print(...)` 的参数、
`Outcome(...)` / `Result(...)` / `_say(...)` 里的文本、以及 `BANNER` / `USAGE` 常量。
**不扫**提示词、SQL、和生成 markdown 文件的模板 —— 它们本来就该是那样。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

PROJECT_MAIN = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_MAIN.parent

# 这些模块的字符串**会出现在屏幕上**
def screen_modules() -> list[Path]:
    """**全部** `.py` —— 因为日志散布在各个模块里。

    ⚠️ 原来这里是一份**手写的 7 个文件名**，而那个名单是错的：
    2026-10-16 要改的日志在 `watchdog.py` / `pipeline.py` / `core/tools.py` /
    `services/llm.py` —— **一个都不在名单里**。
    于是"加了防护"的那一轮其实**什么都没扫到**，而所有者是从屏幕上
    先看见那句 `已开始盯着拉起我的进程` 的。

    放宽到全部文件是安全的：`screen_strings()` 只挑**会出现在屏幕上的字符串**
    （`print` / `logger.*` / `Outcome` / `Result` / `BANNER` / `USAGE`），
    给模型的提示词、生成的 markdown 模板都不会被误伤。
    """
    roots = [PROJECT_MAIN / "src", REPO_ROOT / "tools", PROJECT_MAIN / "experiment"]
    return sorted(
        path
        for root in roots
        if root.is_dir()
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def short(path: Path) -> str:
    """报告里用相对仓库根的短路径，方便一眼看出是哪个文件。"""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)

# markdown 语法：终端里不会渲染，只会原样打出符号
MARKDOWN = re.compile(r"\*\*|`")
# 内部索引：给维护者查资料用的，不是给用的人看的
INTERNAL = re.compile(r"(docs/\d|pre/\d|ADR-0|§|第\s*\d+\s*节)")

# 会产出"给人看的文本"的调用
TEXT_CALLS = {"print", "Result", "Outcome", "_say", "_fail"}
# 模块级常量里装的也是一整段界面文本
TEXT_CONSTANTS = {"BANNER", "USAGE"}
# ⚠️ **日志也算"屏幕上的文本"** —— 用户天天在看它。
# 这条是 2026-10-16 补的：上一版只收了 print / Outcome / BANNER，
# 于是 `已开始盯着拉起我的进程（PID 18300）` 这种句子漏了过去 ——
# 而所有者正是**从屏幕上看见它**才提的。
LOG_METHODS = {"info", "warning", "error", "debug", "exception", "critical"}

# ⚠️ **已经改掉的口语词黑名单** —— 别再回来。
# 机器判不了"是不是口语"（那要靠人照 `docs/style.md` 过一遍），
# 但**这几个词是我们逐个改过的**，回潮是真实风险，所以拦一道。
# 加新词之前先想清楚：这是"这次改掉的具体词"，不是"所有口语词"的清单。
_COLLOQUIAL = {
    "盯着": "监视",
    "拉起": "启动",
    "崩了": "异常",
    "摘掉": "移除",
    "兜住": "拦截",
    "收一次尾": "收尾",
    "闭嘴": "静默",
    "不开口": "静默",
    "看图路线": "视图模式",
}


def module_docstring_ids(tree: ast.AST) -> set[int]:
    """文档字符串不算 —— 那才是 markdown 该待的地方。"""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
               and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def screen_strings(source: str) -> list[tuple[int, str]]:
    """挑出**会出现在屏幕上**的字符串字面量。"""
    tree = ast.parse(source)
    skip = module_docstring_ids(tree)
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # ① print(...) / Result(...) / Outcome(...) 之类调用里的文本
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in TEXT_CALLS or name in LOG_METHODS:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str) \
                       and id(inner) not in skip:
                        found.append((inner.lineno, inner.value))

        # ② 模块级的 BANNER / USAGE 常量
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in TEXT_CONSTANTS:
                    for inner in ast.walk(node.value):
                        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                            found.append((inner.lineno, inner.value))

    return found


class ScreenTextStyleTest(unittest.TestCase):
    def test_the_scanner_finds_anything(self):
        """先确认扫描器真能找到东西 —— 不然下面两条是空转。"""
        source = 'print("**粗**")\n'
        self.assertTrue(screen_strings(source))

    def test_the_scanner_ignores_docstrings(self):
        """文档字符串是 markdown 该待的地方，不能误伤。"""
        source = 'def f():\n    """**这**是文档"""\n    return 1\n'
        self.assertEqual(screen_strings(source), [])

    def test_no_markdown_on_screen(self):
        offenders: list[str] = []
        for path in screen_modules():
            for lineno, text in screen_strings(path.read_text(encoding="utf-8")):
                if MARKDOWN.search(text):
                    line = next(
                        (l for l in text.splitlines() if MARKDOWN.search(l)), text
                    )
                    offenders.append(f"{short(path)}:{lineno}  {line.strip()[:70]}")
        self.assertEqual(
            offenders,
            [],
            "屏幕上会原样打出 markdown 符号（终端不渲染它）：\n  "
            + "\n  ".join(offenders),
        )

    def test_no_internal_references_on_screen(self):
        """`见 docs/01 ADR-026`、`（pre/13 第 6 节）` 这些是**维护者的索引**。

        ⚠️ 只拦文档编号，**不拦** `tools/config.py` 这种"让用户去敲的命令"、
        也不拦 `.env` 路径 —— 那些是使用者真要照着做的，得留着。
        """
        offenders: list[str] = []
        for path in screen_modules():
            for lineno, text in screen_strings(path.read_text(encoding="utf-8")):
                if INTERNAL.search(text):
                    line = next(
                        (l for l in text.splitlines() if INTERNAL.search(l)), text
                    )
                    offenders.append(f"{short(path)}:{lineno}  {line.strip()[:70]}")
        self.assertEqual(
            offenders,
            [],
            "屏幕上打出了内部文档索引（那是给维护者查资料的）：\n  "
            + "\n  ".join(offenders),
        )


class ColloquialismTest(unittest.TestCase):
    """⚠️ **已经改掉的口语词别再回来。**

    「书面 vs 口语」**机器判不了** —— 那要靠人照着 `docs/style.md` 过一遍。
    但下面这几个词是我们**逐个改过**的（所有者指出来的），回潮是真实风险。
    ⚠️ 而且这条**连日志一起扫** —— 上一版漏了 `logger.*`，
    那句「已开始盯着拉起我的进程」就是从屏幕上被看见的。
    """

    def test_the_list_is_not_empty(self):
        self.assertTrue(_COLLOQUIAL)

    def test_no_colloquialisms_on_screen(self):
        offenders: list[str] = []
        for path in screen_modules():
            for lineno, text in screen_strings(path.read_text(encoding="utf-8")):
                for bad, good in _COLLOQUIAL.items():
                    if bad in text:
                        first = text.strip().splitlines()[0][:50]
                        offenders.append(f"{short(path)}:{lineno}  「{bad}」→「{good}」  {first}")
        self.assertEqual(
            offenders,
            [],
            "屏幕上又出现了改过的口语词：\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
