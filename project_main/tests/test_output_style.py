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
SCREEN_MODULES = (
    "project_main/src/saya/manage.py",
    "project_main/src/saya/core/console.py",
    "project_main/src/saya/__main__.py",
    "project_main/experiment/analyze.py",
    "project_main/experiment/run_experiment.py",
    "tools/panel.py",
    "tools/config.py",
)

# markdown 语法：终端里不会渲染，只会原样打出符号
MARKDOWN = re.compile(r"\*\*|`")
# 内部索引：给维护者查资料用的，不是给用的人看的
INTERNAL = re.compile(r"(docs/\d|pre/\d|ADR-0|§|第\s*\d+\s*节)")

# 会产出"给人看的文本"的调用
TEXT_CALLS = {"print", "Result", "Outcome", "_say", "_fail"}
# 模块级常量里装的也是一整段界面文本
TEXT_CONSTANTS = {"BANNER", "USAGE"}


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
            if name in TEXT_CALLS:
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
        for name in SCREEN_MODULES:
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            for lineno, text in screen_strings(path.read_text(encoding="utf-8")):
                if MARKDOWN.search(text):
                    line = next(
                        (l for l in text.splitlines() if MARKDOWN.search(l)), text
                    )
                    offenders.append(f"{name}:{lineno}  {line.strip()[:70]}")
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
        for name in SCREEN_MODULES:
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            for lineno, text in screen_strings(path.read_text(encoding="utf-8")):
                if INTERNAL.search(text):
                    line = next(
                        (l for l in text.splitlines() if INTERNAL.search(l)), text
                    )
                    offenders.append(f"{name}:{lineno}  {line.strip()[:70]}")
        self.assertEqual(
            offenders,
            [],
            "屏幕上打出了内部文档索引（那是给维护者查资料的）：\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
