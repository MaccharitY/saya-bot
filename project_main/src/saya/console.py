"""让控制台**不管接在哪儿都能用**。

两件事，都有真实事故垫底：

1. **编不出来的字符变成 `?`，而不是让程序崩掉**（ADR-021）。
   中文 Windows 上 `sys.stdout.encoding` 是 **gbk**，输出里出现 emoji 就会
   `UnicodeEncodeError` 把程序打断 —— 管道和重定向时同样会撞，
   而 `run.py --check > log.txt` 正是常见用法。
   **只改 errors，不改编码**：硬改成 UTF-8 在 GBK 控制台上反而变乱码。

2. **逐行 flush**（2026-10-16 实测）。
   ⚠️ stdout 接的**不是终端**（管道 / 重定向 / 被别的程序当子进程拉起）时，
   Python 会把它改成**块缓冲** —— 于是命令台的横幅和结果全卡在缓冲区里，
   外面一个字都看不到。
   实测证据很干净：**日志走 stderr（逐行出），命令台走 stdout（卡住）**，
   现象就是"日志在滚、敲命令没反应"。`logging` 用的是 stderr，
   所以它一直是好的，**反而掩盖**了这个问题。

⚠️ 第 2 条**必须在机器人自己这里修**，不能只靠启动器设 `PYTHONUNBUFFERED` ——
谁把输出接个管道（`| tee`、`> log`、被当子进程拉起）都会撞上。

⚠️ 兜底归兜底，规矩还是：**控制台输出里不放 emoji**，
用 GBK 里有的符号代替（`√` / `×` / `[!]` 都是安全的，见 `tests/test_console.py`）。
文档注释里随便写，那些不会被打印。
"""

from __future__ import annotations

import sys


def prepare_console() -> None:
    """入口都该先调一次。两件事：别崩、别攒着。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            # 不是 TextIOWrapper（比如已被替换成别的对象），或者已经 detach 了
            pass
