"""`.bat` 启动器的内容约束 —— 这个格式的坑特别多，全靠测试钉住。

`.bat` 是**唯一一个不能靠"跑一遍"来验证的格式**：它只在 Windows 的 cmd 下、
双击这种交互场景里才暴露问题。所以这里改成**查内容**：

1. **纯 ASCII** —— cmd.exe 解析含中文的 UTF-8 `.bat` 时会**从行中间劈开**，
   把后半句当命令执行。实测报过
   `'…双击就没用了。）' is not recognized as an internal or external command`。
   中文提示一律交给 WSL 那侧的 Python 输出。
2. **CRLF 行尾** —— cmd 读 `.bat` 要 CRLF。
3. **不许有 `for /f`** —— ⚠️ 这条是 2026-10-16 实测撞出来的：
   反引号形式的 `for /f`（``for /f ... in (`wsl ...`)``）**会吞掉控制台的 stdin**。
   表现是：启动了、横幅也打了，**但敲进去的命令一条都没反应**。
   控制台现在就靠 stdin 收命令，所以这个构造在启动器里是致命的。
   替代办法：WSL 会**继承 cmd 的当前目录**，一句 `cd /d "%~dp0"` 就够了，
   根本不需要把 Windows 路径转成 WSL 路径。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def bat_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.bat")
        if ".git" not in path.parts and "node_modules" not in path.parts
    )


def sh_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.sh")
        if ".git" not in path.parts and "node_modules" not in path.parts
    )


class ShFileTest(unittest.TestCase):
    """`.sh` 必须是 **LF**。

    ⚠️ 2026-10-16 真撞过：改 `start_saya.sh` 里两个字符串时用了
    `Path.read_text()` / `write_text()` —— 它们在 Windows 上**默认做换行转换**
    （读时 CRLF→LF，写时 LF→os.linesep），于是整个脚本变成 CRLF，
    机器人在面板里起不来：

        start_saya.sh: line 17: set: pipefail: invalid option name
        start_saya.sh: line 32: syntax error near unexpected token `$'in\\r''

    `.gitattributes` 里 `*.sh text eol=lf` 是对的，但**它管的是 git 的进出**——
    绕过 git 直接写文件（脚本、编辑器、另一个工具）就漏过去了。
    所以这条只能靠**查工作区的真实字节**。
    """

    def test_there_are_some(self):
        self.assertGreaterEqual(len(sh_files()), 1)

    def test_lf_only(self):
        for path in sh_files():
            raw = path.read_bytes()
            crlf = raw.count(b"\r\n")
            if crlf:
                self.fail(
                    f"{path.name} 有 {crlf} 个 CRLF —— bash 会报 "
                    f"`\\r: command not found`，脚本直接跑不起来。\n"
                    f"多半是某个工具用默认换行写回文件了："
                    f"读写时要用 newline=\"\"（Python）或直接按字节处理。"
                )


class BatFileTest(unittest.TestCase):
    def test_there_are_some(self):
        """先确认扫描器真的找到了东西 —— 不然下面全是空转。"""
        self.assertGreaterEqual(len(bat_files()), 2)

    def test_ascii_only(self):
        for path in bat_files():
            raw = path.read_bytes()
            bad = [i for i, b in enumerate(raw) if b > 127]
            if bad:
                # ⚠️ 提示语要**在出错分支里**拼 —— 写成 assertEqual 的参数会**先求值**，
                # 于是通过的时候反而去取 bad[0] 越界。这种"断言消息自己炸了"的
                # 写法很容易把一条通过的测试变成报错。
                self.fail(
                    f"{path.name} 里有非 ASCII 字节（第一个在第 {bad[0]} 字节）。"
                    f"cmd.exe 会把这种 .bat 从行中间劈开。中文交给 WSL 侧输出。"
                )

    def test_crlf_line_endings(self):
        for path in bat_files():
            raw = path.read_bytes()
            lone_lf = raw.count(b"\n") - raw.count(b"\r\n")
            self.assertEqual(
                lone_lf, 0, f"{path.name} 有 {lone_lf} 个裸 LF；cmd 读 .bat 要 CRLF。"
            )

    def test_no_for_f_swallows_stdin(self):
        """⚠️ 见模块开头第 3 条 —— 这是真撞过的那一个。"""
        for path in bat_files():
            text = path.read_text(encoding="ascii")
            hits = [
                line.strip()
                for line in text.splitlines()
                if re.match(r"\s*for\s+/f\b", line, re.IGNORECASE)
            ]
            if hits:
                self.fail(
                    f"{path.name} 里有 for /f —— 反引号形式会吞掉控制台 stdin，"
                    f"命令台就收不到你敲的字了。改用 cd /d \"%~dp0\"（WSL 继承 cwd）。\n"
                    + "\n".join(hits)
                )


if __name__ == "__main__":
    unittest.main()
