"""管理命令 —— **与前端无关**的一层。

终端里的命令台、离线的 `tools/config.py`、将来 0.2.0 之后的 web 管理页，
**都调这里**。各自只负责"怎么把命令读进来 / 怎么把结果显示出去"，
命令的**语义和校验只有这一份**。

> 这条分层是 2026-10-16 定的（所有者提的）：先把前后端分开，
> 前端现在用监测终端顶着，web 以后接同一个后端。
> 顺带解决了 `Config` 是 `frozen=True` 的问题 ——
> **不改它，换掉它**：改完设置重建一个 `Config` 交给 `Pipeline`，不必去捅冻结字段。

⚠️ **本模块不打印任何东西。** 每个命令返回一个 `Result`，
前端想怎么渲染都行（终端加前缀、CLI 直接打、web 转成 JSON）。
"""

from __future__ import annotations

from dataclasses import dataclass

from saya.domain import DEFAULT_GROUP_POLICY, GROUP_POLICIES
from saya.envfile import EnvFile

# 这两个上限写在 .env.example 里，改这里之前先改那边
MAX_ADMINS = 3
MAX_FRIENDS = 15

POLICY_HELP = {
    "silent": "不记不回（「这个群当我没在」）",
    "mention": "记录所有消息，只在被 @ 时回复  ← 最安全的默认",
    "keyword": "记录所有消息，命中关键词才回",
    "random": "记录所有消息，按概率回",
    "always": "全回（只适合调试，会很吵）",
    "selective": "选择性插嘴 —— [!] 尚未实现，会降级成 mention",
}


@dataclass(frozen=True, slots=True)
class Result:
    """一条命令的结果。前端只认这三个字段，不认识具体命令。"""

    lines: tuple[str, ...] = ()
    # 配置被改了吗 —— 改了才需要**落盘 + 重新装配**（重建 Config 交给内核）
    changed: bool = False
    # 命令本身有没有错（参数不对之类）。错的时候 lines 是给人看的说明
    ok: bool = True

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _fail(message: str) -> Result:
    return Result(lines=(message,), ok=False)


# ---------------------------------------------------------------- 取值助手

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


def parse_qq(raw: str) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_policy(raw: str) -> str | None:
    return raw if raw in GROUP_POLICIES else None


def policy_list() -> str:
    return "\n".join(
        f"    {name:<10} {POLICY_HELP.get(name, '')}" for name in GROUP_POLICIES
    )


# ---------------------------------------------------------------- 命令

def cmd_show(env: EnvFile, args: list[str]) -> Result:
    """打印**现在生效的**设置。值后面跟说明，省得对着键名猜。"""
    lines = ["== 群 =="]
    default = env.get("SAYA_GROUP_DEFAULT_POLICY") or DEFAULT_GROUP_POLICY
    lines.append(f"  没列出的群   {default:<10} {POLICY_HELP.get(default, '')}")
    for group_id, name in sorted(parse_policies(env.get("SAYA_GROUP_POLICIES")).items()):
        lines.append(f"  {group_id:<12} {name:<10} {POLICY_HELP.get(name, '')}")

    lines.append("")
    lines.append("== 谁能私聊它 ==")
    owner = env.get("SAYA_OWNER_ID").strip()
    admins = split_csv(env.get("SAYA_ADMIN_IDS"))
    friends = split_csv(env.get("SAYA_FRIEND_ALLOWLIST"))
    lines.append(f"  主人         {owner or '（没配）'}")
    lines.append(f"  管理员       {','.join(admins) or '（没配）'}   [{len(admins)}/{MAX_ADMINS}]")
    lines.append(f"  白名单       {','.join(friends) or '（没配）'}   [{len(friends)}/{MAX_FRIENDS}]")
    if not (owner or admins or friends):
        lines.append("  [!] 一个都没配 —— 任何人私聊它都会被忽略")

    lines.append("")
    lines.append("== 触发参数 ==")
    words = split_csv(env.get("SAYA_TRIGGER_KEYWORDS"))
    lines.append(f"  keyword 的词  {','.join(words) or '（没配）'}")
    lines.append(f"  random 概率   {env.get('SAYA_REPLY_PROBABILITY', '0.1')}")
    rate = env.get("SAYA_RATE_LIMIT_PER_GROUP_PER_MIN", "5").strip()
    warn = "   [!] 0 不是「不限制」，是「永远不开口」" if rate == "0" else ""
    lines.append(f"  每分钟上限    {rate}{warn}")
    return Result(lines=tuple(lines))


def cmd_group(env: EnvFile, args: list[str]) -> Result:
    if not args:
        return _fail("用法：group <群号> <策略>（策略写 - 表示把这个群移出表）")
    group_id, rest = args[0], args[1:]
    policies = parse_policies(env.get("SAYA_GROUP_POLICIES"))

    if rest and rest[0] in ("-", "del", "remove"):
        if group_id not in policies:
            return Result(lines=(f"{group_id} 本来就没在表里（它用的是默认策略）",))
        del policies[group_id]
        _write_policies(env, policies)
        return Result(
            lines=(f"移除了 {group_id} —— 它现在跟其他没列出的群一样，用默认策略",),
            changed=True,
        )

    if len(rest) != 1:
        return _fail("用法：group <群号> <策略>（策略写 - 表示把这个群移出表）")
    name = parse_policy(rest[0])
    if name is None:
        return _fail(f"没有 {rest[0]!r} 这个策略。合法的有：\n{policy_list()}")

    before = policies.get(group_id) or "（默认）"
    policies[group_id] = name
    _write_policies(env, policies)
    return Result(lines=(f"{group_id}: {before} → {name}",), changed=True)


def _write_policies(env: EnvFile, policies: dict[str, str]) -> None:
    env.set("SAYA_GROUP_POLICIES", ",".join(f"{k}:{v}" for k, v in sorted(policies.items())))


def cmd_default(env: EnvFile, args: list[str]) -> Result:
    if len(args) != 1:
        return _fail(f"用法：default <策略>\n{policy_list()}")
    name = parse_policy(args[0])
    if name is None:
        return _fail(f"没有 {args[0]!r} 这个策略。合法的有：\n{policy_list()}")
    env.set("SAYA_GROUP_DEFAULT_POLICY", name)
    return Result(lines=(f"默认策略 → {name}",), changed=True)


def cmd_owner(env: EnvFile, args: list[str]) -> Result:
    if len(args) != 1:
        return _fail("用法：owner <QQ 号>")
    value = parse_qq(args[0])
    if value is None:
        return _fail(f"QQ 号只能是正整数，但你写的是 {args[0]!r}")
    before = env.get("SAYA_OWNER_ID").strip() or "（没配）"
    env.set("SAYA_OWNER_ID", str(value))
    return Result(lines=(f"主人：{before} → {value}",), changed=True)


def cmd_list(env: EnvFile, args: list[str], *, key: str, label: str, limit: int) -> Result:
    if len(args) != 2 or args[0] not in ("add", "del"):
        raise AssertionError(f"{label} 的用法是 add|del <QQ>")
    action, raw = args
    value = parse_qq(raw)
    if value is None:
        return _fail(f"QQ 号只能是正整数，但你写的是 {raw!r}")

    values = split_csv(env.get(key))
    text = str(value)
    if action == "add":
        if text in values:
            return Result(lines=(f"{text} 已经在{label}里了",))
        if len(values) >= limit:
            return _fail(
                f"{label}最多只能有 {limit} 个，现在是满的：{','.join(values)}\n"
                f"先 del 掉一个再加。"
            )
        values.append(text)
        summary = f"加了 {text} → {','.join(values)}"
    else:
        if text not in values:
            return Result(lines=(f"{text} 本来就不在{label}里",))
        values.remove(text)
        summary = f"从{label}去掉了 {text} → {','.join(values) or '（空了）'}"

    env.set(key, ",".join(values))
    return Result(lines=(summary,), changed=True)


def cmd_admin(env: EnvFile, args: list[str]) -> Result:
    return cmd_list(env, args, key="SAYA_ADMIN_IDS", label="管理员", limit=MAX_ADMINS)


def cmd_friend(env: EnvFile, args: list[str]) -> Result:
    return cmd_list(env, args, key="SAYA_FRIEND_ALLOWLIST", label="白名单", limit=MAX_FRIENDS)


def cmd_keyword(env: EnvFile, args: list[str]) -> Result:
    if len(args) != 2 or args[0] not in ("set", "add", "del"):
        return _fail("用法：keyword set|add|del <词>（多个词用逗号分隔）")
    action, raw = args
    words = split_csv(env.get("SAYA_TRIGGER_KEYWORDS"))
    incoming = split_csv(raw)
    if action == "set":
        words = incoming
    elif action == "add":
        words = words + [word for word in incoming if word not in words]
    else:
        words = [word for word in words if word not in incoming]

    env.set("SAYA_TRIGGER_KEYWORDS", ",".join(words))
    lines = [f"keyword 的词 → {','.join(words) or '（空了）'}"]
    if not words:
        lines.append("  （没有词的话，keyword 模式下它就永远不开口）")
    return Result(lines=tuple(lines), changed=True)


def cmd_prob(env: EnvFile, args: list[str]) -> Result:
    if len(args) != 1:
        return _fail("用法：prob <0 到 1 之间的小数>")
    try:
        number = float(args[0])
    except ValueError:
        return _fail(f"概率得是数字，你写的是 {args[0]!r}")
    if not 0.0 <= number <= 1.0:
        return _fail(f"概率要在 0 和 1 之间，你写的是 {number}")
    env.set("SAYA_REPLY_PROBABILITY", str(number))
    return Result(lines=(f"random 概率 → {number}",), changed=True)


def cmd_rate(env: EnvFile, args: list[str]) -> Result:
    if len(args) != 1:
        return _fail("用法：rate <正整数>")
    try:
        number = int(args[0])
    except ValueError:
        return _fail(f"条数得是整数，你写的是 {args[0]!r}")
    if number < 0:
        return _fail("不能是负数")
    env.set("SAYA_RATE_LIMIT_PER_GROUP_PER_MIN", str(number))
    lines = [f"每分钟上限 → {number}"]
    if number == 0:
        lines.append("[!] 0 不是「不限制」—— 是「这个群永远不开口」。")
        lines.append("   想让某个群闭嘴，用：group <群号> silent")
    return Result(lines=tuple(lines), changed=True)


def cmd_help(env: EnvFile, args: list[str]) -> Result:
    return Result(lines=(USAGE,))


# ---------------------------------------------------------------- 分发

# 命令表：名字 → (一句话说明, 处理函数)。`help` 就是照着它生成的。
COMMANDS: dict[str, tuple[str, object]] = {
    "help": ("看这个列表", cmd_help),
    "show": ("看现在是什么样", cmd_show),
    "group": ("group <群号> <策略>；策略写 - 就移出表", cmd_group),
    "default": ("default <策略>：没列出的群默认怎么办", cmd_default),
    "owner": ("owner <QQ>：主人（只有一个）", cmd_owner),
    "admin": (f"admin add|del <QQ>：管理员（最多 {MAX_ADMINS} 个）", cmd_admin),
    "friend": (f"friend add|del <QQ>：白名单（最多 {MAX_FRIENDS} 个）", cmd_friend),
    "keyword": ("keyword set|add|del <词>：keyword 模式用的词", cmd_keyword),
    "prob": ("prob <0~1>：random 模式的概率", cmd_prob),
    "rate": ("rate <n>：每群每分钟最多回几条", cmd_rate),
}

USAGE = "\n".join(
    ["命令（直接敲，不用加前缀）："]
    + [f"  {name:<9} {summary}" for name, (summary, _) in COMMANDS.items()]
    + ["", "策略：", policy_list(), "", "改完立刻生效，同时写回 .env。"]
)


def run(env: EnvFile, line: str) -> Result:
    """把一行命令跑掉。**不认识命令不算错误**，只是提示一下。"""
    parts = line.strip().split()
    if not parts:
        return Result()
    name, args = parts[0].lower(), parts[1:]
    entry = COMMANDS.get(name)
    if entry is None:
        return _fail(f"没有 {name!r} 这个命令。敲 help 看有哪些。")
    return entry[1](env, args)  # type: ignore[operator]
