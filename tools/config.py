#!/usr/bin/env python3
"""**离线**改机器人的设置：群策略、主人 / 管理员 / 白名单、触发参数。

⚠️ **改完要重启才生效** —— 配置是启动时读一次的。
   不重启也能改的东西在**机器人自己那个终端**里（`core/console.py` 的命令台）：
   在那里敲同样的命令，改完**立刻生效**。

**为什么需要这个离线的**：机器人没在跑的时候也能改；脚本化、备份前、
或者就想在另一个窗口里看当前设置。两者**共用同一套命令**
（`saya/manage.py`），语义和校验只有一份。

用法::

    python tools/config.py show                       # 看现在是什么样
    python tools/config.py group 123456789 mention    # 设某个群的策略
    python tools/config.py group 123456789 -          # 移除某个群
    python tools/config.py default silent             # 没列出的群默认怎么办
    python tools/config.py owner 10001                # 主人（只能一个）
    python tools/config.py admin add 20001            # 管理员（最多 3 个）
    python tools/config.py friend add 30001           # 白名单（最多 15 个）
    python tools/config.py keyword set saya,咲弥       # keyword 模式用的词
    python tools/config.py prob 0.15                  # random 模式的概率
    python tools/config.py rate 3                     # 每群每分钟最多回几条
    python tools/config.py path                       # 只打印 .env 的路径

零第三方依赖。命令实现见 `saya/manage.py`，文件编辑见 `saya/envfile.py`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / "project_main" / ".env"

sys.path.insert(0, str(REPO_ROOT / "project_main" / "src"))

from saya.console import prepare_console  # noqa: E402
from saya.envfile import EnvFile  # noqa: E402
from saya.manage import Result, run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/config.py",
        description="改机器人的设置（与机器人终端里的命令台是同一套命令）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "命令：\n"
            "  show                            看现在是什么样\n"
            "  group <群号> <策略>              设某个群的策略（策略写 - 就移出表）\n"
            "  default <策略>                   没列出的群默认怎么办\n"
            "  owner <QQ>                       主人（只有一个）\n"
            "  admin add|del <QQ>               管理员（最多 3 个）\n"
            "  friend add|del <QQ>              白名单（最多 15 个）\n"
            "  keyword set|add|del <词>          keyword 模式用的词\n"
            "  prob <0~1>                       random 模式的概率\n"
            "  rate <n>                         每群每分钟最多回几条\n"
            "  help                             命令与策略的完整列表\n"
            "\n"
            "策略：silent / mention / keyword / random / always / selective\n"
            "\n"
            "⚠️ 改完要重启才生效：bash tools/start_saya.sh --restart\n"
            "   不想重启就在机器人自己那个终端里敲同样的命令。"
        ),
    )
    parser.add_argument("--env", help=".env 的路径（默认 project_main/.env）")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="要执行的命令")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.env).resolve() if args.env else DEFAULT_ENV

    if args.command and args.command[0] == "path":
        print(path)
        return 0

    if not args.command:
        print("没给命令。`--help` 看有哪些。")
        return 2

    env = EnvFile(path)
    result: Result = run(env, " ".join(args.command))
    if result.lines:
        print(result.text)

    if not result.ok:
        return 2
    if result.changed:
        risky = env.save()
        print(f"√ 已写入 {path.name}（备份 {path.name}.bak）")
        if risky:
            print("[!] 注意：这个文件没被 git 忽略，而它装着 API key —— 别提交上去")
        print("[!] 要让改动生效，重启：bash tools/start_saya.sh --restart")
    return 0


if __name__ == "__main__":
    prepare_console()
    raise SystemExit(main())
