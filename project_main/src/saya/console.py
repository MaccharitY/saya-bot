"""控制台输出的编码兜底。

中文 Windows 上 `sys.stdout.encoding` 是 **gbk**。输出里一旦出现 GBK 编不出的字符
（emoji 最常见），`print` 会抛 `UnicodeEncodeError` **把整个程序打断** ——
管道和重定向时同样会撞，而 `run.py --check > log.txt` 正是常见用法。

2026-10-16 实测撞到：`--check` 打印 `⚠️` 时崩在自检中途；
紧接着**排查脚本自己也崩了**，因为它把那个字符打了出来。
同一个坑一天踩了三次，所以单独拿出来。

⚠️ **这只是兜底。** 真正的规矩是：**控制台输出里不放 emoji**，
用 GBK 里有的符号代替（`√` / `×` / `[!]` 都是安全的，见 `tests/test_console.py`）。
文档注释里随便写，那些不会被打印。
"""

from __future__ import annotations

import sys


def keep_console_from_crashing() -> None:
    """让编不出的字符变成 `?`，而不是让程序崩掉。

    **只改「编不出来怎么办」，不改编码**：硬改成 UTF-8 在 GBK 控制台上反而变乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            # 不是 TextIOWrapper（比如已被替换成别的对象），或者已经 detach 了
            pass
