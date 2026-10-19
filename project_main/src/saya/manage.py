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
from saya.envfile import EnvFile, EnvValueError

# 这两个上限写在 .env.example 里，改这里之前先改那边
MAX_ADMINS = 3
MAX_FRIENDS = 15

POLICY_HELP = {
    "silent": "不记录不回复",
    "mention": "记录所有消息，仅在被 @ 时回复（推荐）",
    "keyword": "记录所有消息，命中关键词时回复",
    "random": "记录所有消息，按概率回复",
    "always": "全部回复（仅适用于调试）",
    "selective": "选择性插话 —— [!] 尚未实现，降级为 mention",
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


def parse_policies(raw: str) -> tuple[dict[str, str], list[str]]:
    """解析 `群号:策略,群号:策略`。返回 `(认得的, 认不得的原样片段)`。

    和 `saya.config._parse_group_policies` **同一套严格度** —— 那边是**报错**
    的，这边要是"悄悄跳过"，`show` 就会显示出一个和实际加载结果不一致的视图，
    而 `_write_policies` 按解析结果整串重写时还会把那一截**无声抹掉**。

    ⚠️ 所以认不得的片段**必须原样留下来**给前端看，而不是丢掉。
    """
    out: dict[str, str] = {}
    bad: list[str] = []
    for part in split_csv(raw):
        group_raw, _, policy_raw = part.partition(":")
        number = parse_qq(group_raw.strip())
        policy = policy_raw.strip()
        if number is not None and policy in GROUP_POLICIES:
            out[str(number)] = policy
        else:
            bad.append(part)
    return out, bad


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
    lines.append(f"  未列出群     {default:<10} {POLICY_HELP.get(default, '')}")
    policies, unreadable = parse_policies(env.get("SAYA_GROUP_POLICIES"))
    for group_id, name in sorted(policies.items(), key=lambda pair: int(pair[0])):
        lines.append(f"  {group_id:<12} {name:<10} {POLICY_HELP.get(name, '')}")
    if unreadable:
        lines.append(f"  [!] 读不出来：{','.join(unreadable)}")
        lines.append("      这几条会让机器人启动失败，先用 group 命令改掉或删掉。")

    lines.append("")
    lines.append("== 可私聊用户 ==")
    owner = env.get("SAYA_OWNER_ID").strip()
    admins = split_csv(env.get("SAYA_ADMIN_IDS"))
    friends = split_csv(env.get("SAYA_FRIEND_ALLOWLIST"))
    lines.append(f"  主人         {owner or '（未配置）'}")
    lines.append(f"  管理员       {','.join(admins) or '（未配置）'}   [{len(admins)}/{MAX_ADMINS}]")
    lines.append(f"  白名单       {','.join(friends) or '（未配置）'}   [{len(friends)}/{MAX_FRIENDS}]")
    if not (owner or admins or friends):
        lines.append("  [!] 未配置 —— 所有私聊均不响应")

    lines.append("")
    lines.append("== 触发参数 ==")
    words = split_csv(env.get("SAYA_TRIGGER_KEYWORDS"))
    lines.append(f"  keyword 词    {','.join(words) or '（未配置）'}")
    lines.append(f"  random 概率   {env.get('SAYA_REPLY_PROBABILITY', '0.1')}")
    rate = env.get("SAYA_RATE_LIMIT_PER_GROUP_PER_MIN", "5").strip()
    warn = "   [!] 0 表示永不回复，而非不限制" if rate == "0" else ""
    lines.append(f"  每分钟上限    {rate}{warn}")

    # ⚠️ **不打印凭据本身** —— 只报"配没配"。密钥长度也是指纹，不该往外报。
    lines.append("")
    lines.append("== 专用工具（检测 / OCR）==")
    lines.append(f"  检测权重     {env.get('SAYA_YOLO_WEIGHTS') or 'yolo11n.pt'}")
    lines.append(f"  检测设备     {env.get('SAYA_CV_DEVICE') or 'auto（有 GPU 就用）'}")
    backend = (env.get("SAYA_CV_OCR_BACKEND") or "auto").strip() or "auto"
    has_cloud = bool(env.get("SAYA_BAIDU_OCR_KEY") and env.get("SAYA_BAIDU_OCR_SECRET"))
    lines.append(f"  OCR 后端     {backend}")
    lines.append(f"  云 OCR 凭据   {'已配置' if has_cloud else '未配置（用本地）'}")
    return Result(lines=tuple(lines))


def cmd_group(env: EnvFile, args: list[str]) -> Result:
    if not args:
        return _fail("用法：group <群号> <策略>（策略写 - 表示把这个群移出表）")
    # ⚠️ **群号必须在这里校验。** 不校验的话 `group abc mention` 会一路写进
    # `.env`，而 `config._parse_group_policies` 读的时候才报错 ——
    # 结果是"改的时候一切正常，重启就起不来"，且那台机器上已经中毒了。
    number = parse_qq(args[0])
    if number is None:
        return _fail(f"群号只能是正整数，但你写的是 {args[0]!r}")
    group_id, rest = str(number), args[1:]
    policies, unreadable = parse_policies(env.get("SAYA_GROUP_POLICIES"))
    if unreadable:
        # 重写是"按解析结果整串生成"的，读不出来的那截会被抹掉 —— 宁可先不改
        return _fail(
            f"SAYA_GROUP_POLICIES 里有读不出来的条目：{','.join(unreadable)}\n"
            f"这次重写会把它抹掉，所以先手工把它改对或删掉。"
        )

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
    # 按**数值**排，不是按字符串 —— 否则 "10" 会排在 "9" 前面
    ordered = sorted(policies.items(), key=lambda pair: int(pair[0]))
    env.set("SAYA_GROUP_POLICIES", ",".join(f"{k}:{v}" for k, v in ordered))


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
        # ⚠️ 这里以前是 `raise AssertionError` —— 全文件**唯一**会抛异常的命令。
        # `panel._loop` 没接异常，于是敲一句少写参数的 `admin` 就能让
        # 控制台带栈消失，并顺手把机器人停掉（`serve` 的 finally 会收尾）。
        # **用法写错是正常输入**，不是断言失败，必须走 `Result` 这条路。
        return _fail(f"用法：{label} add|del <QQ 号>")
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
        lines.append("  （未设置关键词时，keyword 模式不会回复）")
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
        lines.append("[!] 0 表示永不回复，而非不限制。")
        lines.append("   如需静默某个群：group <群号> silent")
    return Result(lines=tuple(lines), changed=True)


def cmd_help(env: EnvFile, args: list[str]) -> Result:
    return Result(lines=(USAGE,))


# ---------------------------------------------------------------- 分发

# 命令表：名字 → (一句话说明, 处理函数)。`help` 就是照着它生成的。
_OCR_BACKENDS = ("auto", "cloud", "local")


def cmd_ocr(env: EnvFile, args: list[str]) -> Result:
    """云 OCR 走哪条路。⚠️ 这里**不收也不发凭据** —— 密钥只走 .env。"""
    backend = (env.get("SAYA_CV_OCR_BACKEND") or "auto").strip() or "auto"
    if not args:
        return Result(lines=(f"OCR 后端    {backend}",))
    value = args[0].strip().lower()
    if value not in _OCR_BACKENDS:
        return _fail(f"OCR 后端只有 {' / '.join(_OCR_BACKENDS)}，你写的是 {args[0]!r}")
    env.set("SAYA_CV_OCR_BACKEND", value)
    return Result(lines=(f"OCR 后端 → {value}",), changed=True)


def cmd_yolo(env: EnvFile, args: list[str]) -> Result:
    """检测用哪个权重。允许简写 `x`，也允许直接写文件名。"""
    current = env.get("SAYA_YOLO_WEIGHTS") or "yolo11n.pt"
    if not args:
        return Result(lines=(f"检测权重    {current}",))
    value = args[0].strip().lower()
    if value in ("n", "s", "m", "l", "x"):
        value = f"yolo11{value}.pt"
    if not value.endswith(".pt"):
        return _fail(f"权重文件名要以 .pt 结尾，你写的是 {args[0]!r}")
    env.set("SAYA_YOLO_WEIGHTS", value)
    return Result(lines=(f"检测权重 → {value}（下次启动加载）",), changed=True)


def cmd_device(env: EnvFile, args: list[str]) -> Result:
    """检测跑在哪。不写 auto：有 GPU 就用 GPU。

    ⚠️ Ultralytics 的默认是 **CPU**，即使 CUDA 可用也不自动上去 ——
    所以"auto"是我们这层自己加的，不是它的默认行为。
    """
    current = env.get("SAYA_CV_DEVICE") or "auto"
    if not args:
        return Result(lines=(f"检测设备    {current}",))
    value = args[0].strip().lower()
    if value not in ("auto", "cpu") and not value.startswith("cuda"):
        return _fail(f"设备只有 auto / cpu / cuda:N，你写的是 {args[0]!r}")
    env.set("SAYA_CV_DEVICE", value)
    return Result(lines=(f"检测设备 → {value}",), changed=True)


COMMANDS: dict[str, tuple[str, object]] = {
    "help": ("查看本列表", cmd_help),
    "show": ("查看当前配置", cmd_show),
    "group": ("group <群号> <策略>；- 表示移出", cmd_group),
    "default": ("default <策略>：未列出群的默认策略", cmd_default),
    "owner": ("owner <QQ>：设置主人（唯一）", cmd_owner),
    "admin": (f"admin add|del <QQ>：管理员（上限 {MAX_ADMINS}）", cmd_admin),
    "friend": (f"friend add|del <QQ>：白名单（上限 {MAX_FRIENDS}）", cmd_friend),
    "keyword": ("keyword set|add|del <词>：keyword 模式关键词", cmd_keyword),
    "prob": ("prob <0~1>：random 概率", cmd_prob),
    "rate": ("rate <n>：每群每分钟回复上限", cmd_rate),
    "ocr": ("ocr auto|cloud|local：云 OCR 开关", cmd_ocr),
    "yolo": ("yolo n|s|m|l|x：检测权重", cmd_yolo),
    "device": ("device auto|cpu|cuda:N：检测跑在哪", cmd_device),
}

USAGE = "\n".join(
    ["命令（无需前缀）："]
    + [f"  {name:<9} {summary}" for name, (summary, _) in COMMANDS.items()]
    + [
        "",
        "策略：",
        policy_list(),
        "",
        "检测权重（yolo）：",
        "    n    最小最快（默认）",
        "    s    小",
        "    m    中",
        "    l    大",
        "    x    最大最准",
        "",
        "OCR 后端（ocr）：",
        "    auto     优先云端，失败退回本地（默认）",
        "    cloud    只用云端",
        "    local    只用本地",
        "",
        "检测设备（device）：",
        "    auto     有 GPU 就用 GPU（默认）",
        "    cpu      只用 CPU",
        "    cuda:N   指定第 N 块 GPU",
        "",
        "更改写入 .env。",
    ]
)


def run(env: EnvFile, line: str) -> Result:
    """把一行命令跑掉。**不认识命令不算错误**，只是提示一下。

    ⚠️ 这里是**唯一**该把异常转成 `Result` 的地方。前端只懂 `Result`
    （终端加前缀、CLI 直接打、web 转 JSON），谁都不接异常 ——
    漏一个出去就是"整块界面消失"。`EnvValueError` 是 `.env` 存不住那个值，
    属于用户输入问题，照样转成一条可读的说明。
    """
    parts = line.strip().split()
    if not parts:
        return Result()
    name, args = parts[0].lower(), parts[1:]
    entry = COMMANDS.get(name)
    if entry is None:
        return _fail(f"没有 {name!r} 这个命令。输入 help 查看命令。")
    try:
        return entry[1](env, args)  # type: ignore[operator]
    except EnvValueError as exc:
        return _fail(str(exc))
