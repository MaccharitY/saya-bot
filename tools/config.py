#!/usr/bin/env python3
"""改机器人的设置：群策略、主人 / 管理员 / 白名单、触发参数。

**为什么需要它**：这些设置全在 `project_main/.env` 里，而那个文件
**既有密钥、又有大段说明注释**。手工改很容易改坏 —— 多一个空格、少一个逗号、
或者顺手把注释删了。而改坏的后果分两种，**两种都很难一眼看出来**：

- 值写错了 → 机器人起不来，或者**照旧跑但用的还是旧设置**（更糟：你以为改了）
- 文件写坏了 → API key 一起没了

所以本工具只做一件事：**外科手术式地替换某一行的值**。
其余的行、注释、顺序**原样不动**，写之前先备份一份。

用法::

    python tools/config.py show                        # 看现在是什么样
    python tools/config.py group 123456789 mention     # 设某个群的策略
    python tools/config.py group 123456789 --del       # 移除某个群
    python tools/config.py default silent              # 没列出的群默认怎么办
    python tools/config.py owner 10001                 # 主人（只能一个）
    python tools/config.py admin add 20001             # 加管理员（最多 3 个）
    python tools/config.py admin del 20001
    python tools/config.py friend add 30001            # 加白名单（最多 15 个）
    python tools/config.py keyword set 莎夜,在吗        # keyword 模式用的词
    python tools/config.py prob 0.15                   # random 模式的概率
    python tools/config.py rate 3                      # 每群每分钟最多回几条
    python tools/config.py path                        # 只打印 .env 的路径

⚠️ **改完必须重启**才生效 —— 配置是**启动时读一次**的。
   `bash tools/start_saya.sh --restart`（Windows 双击 `tools/start_saya.bat`）。

零第三方依赖。`replace_key` 可被 import 复用。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / "project_main" / ".env"

sys.path.insert(0, str(REPO_ROOT / "project_main" / "src"))

from saya.config import parse_dotenv  # noqa: E402
from saya.console import keep_console_from_crashing  # noqa: E402
from saya.domain import DEFAULT_GROUP_POLICY, GROUP_POLICIES  # noqa: E402

# 这两个上限写在 .env.example 里，改这里之前先改那边
MAX_ADMINS = 3
MAX_FRIENDS = 15

POLICY_HELP = {
    "silent": "不记不回（「这个群当我没在」）",
    "mention": "记录所有消息，只在被 @ 时回复  ← 最安全的默认",
    "keyword": "记录所有消息，命中关键词才回",
    "random": "记录所有消息，按概率回",
    "always": "全回（只适合调试，会很吵）",
    "selective": "选择性插嘴 —— ⚠️ 尚未实现，会降级成 mention",
}


# ---------------------------------------------------------------- 行编辑

def inline_comment(value: str) -> str:
    """取出值后面的行尾注释（连同前面那串空白）。

    规则必须和 `saya.config._strip_inline_comment` **一致**，否则会出现
    "写进去的值读出来变了"。那边是：没被引号包住时，`#` 前面是空白才算注释。
    """
    if value[:1] in ("'", '"'):
        return ""
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            start = index
            while start > 0 and value[start - 1] in " \t":
                start -= 1
            return value[start:]
    return ""


def replace_key(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    """把 `KEY=` 那一行的值换掉，**行尾注释、其余行、行尾风格都原样保留**。

    返回 (新行列表, 是否找到了这个键)。找不到就**追加到末尾** ——
    照 `.env.example` 抄来的 `.env` 可能缺某些键，追加比报错有用。
    """
    out: list[str] = []
    found = False
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        head, sep, rest = body.partition("=")
        if sep and not body.lstrip().startswith("#") and head.strip() == key:
            indent = body[: len(body) - len(body.lstrip())]
            out.append(f"{indent}{key}={value}{inline_comment(rest)}{ending}")
            found = True
        else:
            out.append(line)

    if not found:
        ending = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        if out and not out[-1].endswith(("\n", "\r")):
            out[-1] += ending
        out.append(f"{key}={value}{ending}")
    return out, found


class EnvFile:
    """`.env` 的可写视图：值是解析后的，行是原始的。"""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise SystemExit(
                f"没有这个文件：{path}\n"
                f"照 project_main/.env.example 复制一份再改。"
            )
        self.path = path
        # ⚠️ **必须 newline=""**：默认的通用换行会把 CRLF 归一成 LF，
        # 写回时 Windows 又把 LF 翻译成 CRLF —— 结果**整个文件每一行都变了**，
        # 明明只改了一个值，diff 却是一片红。
        # 普通 read_text()/write_text() 就会这样，实测撞过。
        with path.open("r", encoding="utf-8", newline="") as handle:
            raw = handle.read()
        self.lines = raw.splitlines(keepends=True)
        self.values = parse_dotenv(raw)
        self._pending: list[tuple[str, str]] = []

    def get(self, key: str, default: str = "") -> str:
        """`key` 是**含 `SAYA_` 的完整键名**，和文件里写的一模一样。"""
        return self.values.get(key, default)

    def set(self, key: str, value: str) -> bool:
        """只改内存。返回这个键原本在不在文件里。

        ⚠️ `key` 要传**完整键名**（`SAYA_OWNER_ID`），不要传 `OWNER_ID`。
        早先这里会自动补 `SAYA_` 前缀，结果调用方传了完整键时
        变成 `SAYA_SAYA_OWNER_ID` —— **原值没动，文件末尾却多了一行**，
        而且是静默的。前缀这种"贴心的隐式约定"在只出现在一侧时最坑人，
        所以直接去掉：文件里长什么样，这里就写什么样。
        """
        self.lines, found = replace_key(self.lines, key, value)
        self.values[key] = value
        return found

    def save(self, *, quiet: bool = False) -> None:
        """先备份再写。`.env` 里有 API key，而且它没进 git —— 弄丢补不回来。

        `quiet=True` 只给测试用：不然一堆提示会把断言结果淹掉。
        """
        backup = self.path.with_name(self.path.name + ".bak")
        shutil.copyfile(self.path, backup)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(self.lines))
        self._warn_if_committable(backup, quiet=quiet)
        if not quiet:
            print(f"√ 已写入 {self.path.name}（备份 {backup.name}）")
            print("[!] **要重启才生效**：bash tools/start_saya.sh --restart")

    def _warn_if_committable(self, backup: Path, *, quiet: bool = False) -> None:
        """⚠️ 这个文件**装着 API key**，被提交上去就是泄密。

        `.gitignore` 里 `.env` / `.env.*` 两条能盖住默认路径，
        但 `--env 别的名字` 就盖不住了 —— 而**告警必须在写盘前后立刻给**，
        不能等谁哪天顺手 `git add -A` 才发现。
        （这条不是假想：写这个工具的当天，我拿真 `.env` 复制出来做测试，
         副本名不匹配忽略规则，差点把 key 提上去。）
        """
        probe = [str(self.path), str(backup)]
        try:
            result = subprocess.run(
                ["git", "-C", str(self.path.parent), "check-ignore", "--", *probe],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return
        # ⚠️ 退出码不是 0/1 就是 git 自己出错了（比如这个目录根本不在仓库里）。
        # 那时 stdout 是空的，直接拿它判断会**把每个临时文件都误报成危险** ——
        # 测试跑在系统临时目录，实测被淹了一屏。0=有被忽略的，1=一个都没忽略。
        if result.returncode not in (0, 1):
            return
        ignored = set(result.stdout.split())
        risky = [item for item in probe if item not in ignored]
        if risky and not quiet:
            print("[!][!] **这些文件没被 git 忽略，里面有 API key**：")
            for item in risky:
                print(f"     {item}")
            print("   → 把它们加进 .gitignore，或者改用默认的 project_main/.env")


# ---------------------------------------------------------------- 校验

def parse_qq(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"QQ 号只能是数字，但你写的是 {raw!r}") from None
    if value <= 0:
        raise SystemExit(f"QQ 号必须是正数，但你写的是 {value}")
    return value


def parse_policy(raw: str) -> str:
    if raw not in GROUP_POLICIES:
        options = "\n".join(
            f"    {name:<10} {POLICY_HELP.get(name, '')}" for name in GROUP_POLICIES
        )
        raise SystemExit(f"没有 {raw!r} 这个策略。合法的有：\n{options}")
    return raw


def split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_policies(raw: str) -> dict[str, str]:
    """和 `saya.config._parse_group_policies` 同样的格式：`群号:策略,群号:策略`。"""
    out: dict[str, str] = {}
    for part in split_csv(raw):
        group_id, _, name = part.partition(":")
        if group_id.strip() and name.strip():
            out[group_id.strip()] = name.strip()
    return out


# ---------------------------------------------------------------- 命令

def cmd_show(env: EnvFile) -> int:
    """打印**现在生效的**设置。值后面跟说明，省得对着键名猜。"""
    print("== 群 ==")
    default = env.get("SAYA_GROUP_DEFAULT_POLICY") or DEFAULT_GROUP_POLICY
    print(f"  没列出的群   {default:<10} {POLICY_HELP.get(default, '')}")
    for group_id, name in sorted(parse_policies(env.get("SAYA_GROUP_POLICIES")).items()):
        print(f"  {group_id:<12} {name:<10} {POLICY_HELP.get(name, '')}")

    print("\n== 谁能私聊它 ==")
    owner = env.get("SAYA_OWNER_ID").strip()
    admins = split_csv(env.get("SAYA_ADMIN_IDS"))
    friends = split_csv(env.get("SAYA_FRIEND_ALLOWLIST"))
    print(f"  主人         {owner or '（没配）'}")
    print(f"  管理员       {','.join(admins) or '（没配）'}   [{len(admins)}/{MAX_ADMINS}]")
    print(f"  白名单       {','.join(friends) or '（没配）'}   [{len(friends)}/{MAX_FRIENDS}]")
    if not (owner or admins or friends):
        print("  [!] 一个都没配 —— **任何人私聊它都会被忽略**")

    print("\n== 触发参数 ==")
    words = split_csv(env.get("SAYA_TRIGGER_KEYWORDS"))
    print(f"  keyword 的词  {','.join(words) or '（没配）'}")
    print(f"  random 概率   {env.get('SAYA_REPLY_PROBABILITY', '0.1')}")
    rate = env.get("SAYA_RATE_LIMIT_PER_GROUP_PER_MIN", "5").strip()
    warn = "   ⚠️ 0 不是「不限制」，是「永远不开口」" if rate == "0" else ""
    print(f"  每分钟上限    {rate}{warn}")
    return 0


def cmd_group(env: EnvFile, group_id: str, name: str | None, *, delete: bool) -> int:
    policies = parse_policies(env.get("SAYA_GROUP_POLICIES"))
    if delete:
        if group_id not in policies:
            print(f"{group_id} 本来就没在表里（它用的是默认策略）")
            return 0
        del policies[group_id]
        print(f"移除了 {group_id} —— 它现在跟其他没列出的群一样，用默认策略")
    else:
        assert name is not None
        print(f"{group_id}: {policies.get(group_id) or '（默认）'} → {name}")
        policies[group_id] = name
    env.set("SAYA_GROUP_POLICIES", ",".join(f"{k}:{v}" for k, v in sorted(policies.items())))
    env.save()
    return 0


def cmd_default(env: EnvFile, name: str) -> int:
    print(f"默认策略 → {name}")
    env.set("SAYA_GROUP_DEFAULT_POLICY", name)
    env.save()
    return 0


def cmd_owner(env: EnvFile, value: int) -> int:
    print(f"主人：{env.get('SAYA_OWNER_ID').strip() or '（没配）'} → {value}")
    env.set("SAYA_OWNER_ID", str(value))
    env.save()
    return 0


def cmd_list(env: EnvFile, key: str, label: str, action: str, value: int, *, limit: int) -> int:
    values = split_csv(env.get(key))
    text = str(value)
    if action == "add":
        if text in values:
            print(f"{text} 已经在{label}里了")
            return 0
        if len(values) >= limit:
            raise SystemExit(
                f"{label}最多只能有 {limit} 个，现在是满的：{','.join(values)}\n"
                f"先 del 掉一个再加。"
            )
        values.append(text)
        print(f"加了 {text} → {','.join(values)}")
    else:
        if text not in values:
            print(f"{text} 本来就不在{label}里")
            return 0
        values.remove(text)
        print(f"从{label}去掉了 {text} → {','.join(values) or '（空了）'}")
    env.set(key, ",".join(values))
    env.save()
    return 0


def cmd_keyword(env: EnvFile, action: str, raw: str) -> int:
    words = split_csv(env.get("SAYA_TRIGGER_KEYWORDS"))
    incoming = split_csv(raw)
    if action == "set":
        words = incoming
    elif action == "add":
        words = words + [word for word in incoming if word not in words]
    else:
        words = [word for word in words if word not in incoming]
    print(f"keyword 的词 → {','.join(words) or '（空了）'}")
    if not words:
        print("  （没有词的话，keyword 模式下它就永远不开口）")
    env.set("SAYA_TRIGGER_KEYWORDS", ",".join(words))
    env.save()
    return 0


def cmd_prob(env: EnvFile, raw: str) -> int:
    try:
        number = float(raw)
    except ValueError:
        raise SystemExit(f"概率得是数字，你写的是 {raw!r}") from None
    if not 0.0 <= number <= 1.0:
        raise SystemExit(f"概率要在 0 和 1 之间，你写的是 {number}")
    print(f"random 概率 → {number}")
    env.set("SAYA_REPLY_PROBABILITY", str(number))
    env.save()
    return 0


def cmd_rate(env: EnvFile, raw: str) -> int:
    try:
        number = int(raw)
    except ValueError:
        raise SystemExit(f"条数得是整数，你写的是 {raw!r}") from None
    if number < 0:
        raise SystemExit("不能是负数")
    if number == 0:
        print("[!] 0 不是「不限制」—— 是「这个群永远不开口」。")
        print("   想让某个群闭嘴，用：python tools/config.py group <群号> silent")
    print(f"每分钟上限 → {number}")
    env.set("SAYA_RATE_LIMIT_PER_GROUP_PER_MIN", str(number))
    env.save()
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="改机器人的设置（群策略 / 主人 / 管理员 / 白名单 / 触发参数）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="⚠️ 改完要重启才生效：bash tools/start_saya.sh --restart",
    )
    parser.add_argument("--env", help=".env 的路径（默认 project_main/.env）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="看现在是什么样")
    sub.add_parser("path", help="只打印 .env 的路径")

    group = sub.add_parser("group", help="设某个群的策略")
    group.add_argument("group_id", help="群号")
    group.add_argument("policy", nargs="?", help=f"策略：{' / '.join(GROUP_POLICIES)}")
    group.add_argument(
        "--del", "--remove", dest="delete", action="store_true",
        help="把这个群从表里移除",
    )

    default = sub.add_parser("default", help="没列出的群默认怎么办")
    default.add_argument("policy", help=f"策略：{' / '.join(GROUP_POLICIES)}")

    owner = sub.add_parser("owner", help="设主人（只能一个）")
    owner.add_argument("qq")

    for name, label, limit in (
        ("admin", "管理员", MAX_ADMINS),
        ("friend", "白名单", MAX_FRIENDS),
    ):
        node = sub.add_parser(name, help=f"{label}（最多 {limit} 个）")
        node.add_argument("action", choices=["add", "del"])
        node.add_argument("qq")

    keyword = sub.add_parser("keyword", help="keyword 模式用的词")
    keyword.add_argument("action", choices=["set", "add", "del"])
    keyword.add_argument("words", help="逗号分隔")

    prob = sub.add_parser("prob", help="random 模式的概率（0~1）")
    prob.add_argument("value")
    rate = sub.add_parser("rate", help="每群每分钟最多回几条")
    rate.add_argument("value")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.env).resolve() if args.env else DEFAULT_ENV

    if args.command == "path":
        print(path)
        return 0

    env = EnvFile(path)
    if args.command == "show":
        return cmd_show(env)
    if args.command == "group":
        if args.delete:
            if args.policy:
                raise SystemExit("--del 和策略不能一起写")
            return cmd_group(env, args.group_id, None, delete=True)
        if not args.policy:
            raise SystemExit(f"要指定策略。合法的有：{' / '.join(GROUP_POLICIES)}")
        return cmd_group(env, args.group_id, parse_policy(args.policy), delete=False)
    if args.command == "default":
        return cmd_default(env, parse_policy(args.policy))
    if args.command == "owner":
        return cmd_owner(env, parse_qq(args.qq))
    if args.command == "admin":
        return cmd_list(env, "SAYA_ADMIN_IDS", "管理员", args.action, parse_qq(args.qq), limit=MAX_ADMINS)
    if args.command == "friend":
        return cmd_list(
            env, "SAYA_FRIEND_ALLOWLIST", "白名单", args.action, parse_qq(args.qq), limit=MAX_FRIENDS
        )
    if args.command == "keyword":
        return cmd_keyword(env, args.action, args.words)
    if args.command == "prob":
        return cmd_prob(env, args.value)
    if args.command == "rate":
        return cmd_rate(env, args.value)
    return 1


if __name__ == "__main__":
    keep_console_from_crashing()
    raise SystemExit(main())
