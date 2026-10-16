#!/usr/bin/env python3
"""发布前的隐私自检：真的 QQ 号 / 群号 / 密钥有没有漏进要公开的仓库。

**为什么是个脚本，而不是验收清单里的一段命令**：
检查「有没有泄露 X」的命令，本身就写着 X —— 把它写进清单，**清单自己就成了泄露源**。
这个坑真踩过：验收清单的第一版把真群号和 TA 邮箱写进了公开仓库，
最后靠重写 git 历史才擦掉。

所以这个工具的设计前提是：**要查的值一律从 `.env` 读，不写在任何会被提交的地方。**

用法::

    python tools/privacy_scan.py             # 当前文件 + 全历史（发布前跑这个）
    python tools/privacy_scan.py --current   # 只扫当前文件（快，日常用）
    python tools/privacy_scan.py --env <路径>  # 指定 .env（默认 project_main/.env）

退出码：0 = 干净，1 = 有命中。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / "project_main" / ".env"

# 太短的数字到处都是（端口、年份、章节号），只查够长的
MIN_ID_DIGITS = 6

# 密钥类：OpenAI 风格的 sk- 开头
SECRET_PATTERNS = (
    ("疑似密钥", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
)
# 已知的假货（示例占位符、测试里故意放的），不算泄露
SECRET_ALLOWLIST = re.compile(r"x{12,}|abcdefghijklmnop|ShouldNeverAppear")


def files_that_could_be_committed() -> list[Path]:
    """**该扫的是"可能被提交的"文件**，不是磁盘上所有文件。

    直接遍历目录会把 `.env`、埋点日志这些 gitignore 掉的东西也算成泄露，
    满屏假警报 —— 而假警报多了，真警报就没人看了。

    `git ls-files --cached --others --exclude-standard` = 已跟踪的 +
    未跟踪但**没被忽略**的，正好是"将来 `git add .` 会进去的那一批"。
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [REPO_ROOT / line for line in proc.stdout.splitlines() if line.strip()]


def read_ids(env_path: Path) -> dict[str, str]:
    """从 `.env` 里取出**真的**标识符：自己的 QQ 号、主人、群号等。

    只认数字够长的，避免把端口号、超时之类的当成人肉信息去满仓库找。
    """
    if not env_path.is_file():
        print(f"[!] 读不到 {env_path} —— 没有它就没法知道要查什么。")
        print("   指定一个：python tools/privacy_scan.py --env <路径>")
        raise SystemExit(2)

    try:
        text = env_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"[!] {env_path} 不是 UTF-8，读不了。")
        raise SystemExit(2) from None

    ids: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.startswith("SAYA_"):
            continue
        for number in re.findall(r"\d+", value):
            if len(number) >= MIN_ID_DIGITS:
                ids.setdefault(number, key)
    return ids


def scan_text(text: str, needles: dict[str, str]) -> list[tuple[int, str, str]]:
    """在文本里找这些值。返回 [(行号, 命中的值, 行内容)]。"""
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for needle, source in needles.items():
            if needle in line:
                hits.append((lineno, f"{needle}（来自 {source}）", line.strip()[:120]))
    return hits


def scan_secrets(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(line) and not SECRET_ALLOWLIST.search(line):
                hits.append((lineno, f"{label}：{line.strip()[:100]}"))
    return hits


def scan_current(needles: dict[str, str]) -> list[str]:
    findings: list[str] = []
    for path in files_that_could_be_committed():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, what, line in scan_text(text, needles):
            findings.append(f"  {path.relative_to(REPO_ROOT)}:{lineno}  {what}\n      {line}")
        for lineno, what in scan_secrets(text):
            findings.append(f"  {path.relative_to(REPO_ROOT)}:{lineno}  {what}")
    return findings


def scan_history(needles: dict[str, str]) -> list[str]:
    """扫全历史。

    ⚠️ 这一步不能省 —— **改当前文件是不够的**，git 历史会把写过的每个字都带上。
    真群号曾经就藏在一条 commit message 里。
    """
    try:
        proc = subprocess.run(
            ["git", "log", "--all", "-p", "--format=%h %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return [f"  ⚠️ 跑不了 git log：{exc}"]

    if proc.returncode != 0:
        return [f"  ⚠️ git log 失败：{proc.stderr.strip()[:200]}"]

    findings: list[str] = []
    for lineno, what, line in scan_text(proc.stdout, needles):
        findings.append(f"  <历史 第{lineno}行>  {what}\n      {line}")
    for lineno, what in scan_secrets(proc.stdout):
        findings.append(f"  <历史 第{lineno}行>  {what}")
    return findings


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="发布前隐私自检（要查的值从 .env 读）")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help=f".env 路径（默认 {DEFAULT_ENV}）")
    parser.add_argument("--current", action="store_true", help="只扫当前文件，不扫历史（快）")
    args = parser.parse_args(argv)

    needles = read_ids(Path(args.env))
    print(f"从 {args.env} 取到 {len(needles)} 个要查的值（其值本身不会被打出来）")

    problems: list[str] = []

    print("\n① 可能被提交的文件")
    current = scan_current(needles)
    if current:
        problems += current
        print("\n".join(current))
    else:
        print("  √ 干净")

    if not args.current:
        print("\n② 全历史（改当前文件是不够的，历史会带上你写过的每个字）")
        history = scan_history(needles)
        if history:
            problems += history
            print("\n".join(history[:40]))
            if len(history) > 40:
                print(f"  …还有 {len(history) - 40} 条")
        else:
            print("  √ 干净")

    print()
    if problems:
        print(f"× 共 {len(problems)} 处命中 —— 先清干净再推。")
        print("   已经提交过的那份还在历史里，得一起清掉才算干净。")
        return 1

    print("√ 没有发现泄露。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
