"""`.env` 的**可写视图**：外科手术式地替换某一行的值。

**为什么单独一层**：`manage.py`（命令语义）和终端/CLI（前端）都要改这个文件，
但谁也不该自己拼字符串去写一个**装着 API key** 的文件。
写坏了的后果分两种，两种都很难一眼看出来 ——
值写错了，机器人**照旧跑但用的还是旧设置**（你以为改了）；文件写坏了，key 一起没了。

所以规矩定在这里：

1. **只动目标那一行** —— 行尾注释、其余行、顺序、**行尾符（LF/CRLF）**全部原样保留
2. **写之前先备份**
3. **写盘时检查目标有没有被 git 忽略** —— 它装着 key，被提交上去就是泄密

本模块**不打印任何东西**：怎么显示是前端的事。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from saya.config import _strip_inline_comment, parse_dotenv


def inline_comment(value: str) -> str:
    """取出值后面的行尾注释（连同前面那串空白）。

    规则必须和 `saya.config._strip_inline_comment` **一致**，否则会出现
    "写进去的值读出来变了"。那边是：没被引号包住时，`#` 前面是空白才算注释。

    ⚠️ **返回的串一定以空白开头**（非空时）。少了这个空格，拼回去的
    `K=新值# 旧注释` 里那个 `#` 前面就不再有空白 —— 同一份文件重读时
    它**不再是注释**，会并进值里。实测撞过：`.env` 里手写的
    `SAYA_OWNER_ID=# 填你的 QQ 号` 被 `owner 10001` 改成
    `SAYA_OWNER_ID=10001# 填你的 QQ 号`，下次启动 `int()` 抛异常。
    （原来的实现是"往前吃空白"，但 `#` 已经在第 0 位时前面没得吃。）
    """
    if value[:1] in ("'", '"'):
        return ""
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            start = index
            while start > 0 and value[start - 1] in " \t":
                start -= 1
            comment = value[start:]
            return comment if comment[:1] in " \t" else " " + comment
    return ""


class EnvValueError(ValueError):
    """这个值按 `.env` 的解析规则存不住。

    `_strip_inline_comment` 不认转义，所以含引号又含 `#` 的值没法安全编码。
    与其写进去一个"读出来是另一个东西"的值，不如当场拒绝。
    """


def encode_value(value: str) -> str:
    """把值编码成**读回来还是它自己**的形式。存不住就抛 `EnvValueError`。

    两件事，都有实测垫底：

    1. **以 `#` 开头、或含 ` #` 的值要加引号。** 不加的话重读时被当成
       行尾注释，值整个消失。`keyword set #话题` 就是这么把关键词弄没的 ——
       提示说"已设置"，文件里也对，只有重启后它不回复。
    2. **加不了引号的直接拒绝。** `_strip_inline_comment` 遇到开头的引号会
       取到**下一个**引号为止，所以值本身含引号时，套引号会把尾部截掉。
       这种值没有安全写法，只能报错。
    """
    if _strip_inline_comment(value) == value and value[:1] not in ("'", '"'):
        return value
    quoted = f'"{value}"'
    if _strip_inline_comment(quoted) == value:
        return quoted
    raise EnvValueError(
        f"「{value}」这个值没法写进 .env —— 它含引号，而 .env 的解析规则"
        f"不认转义（引号里的内容会被读到下一个引号为止）。"
    )


def replace_key(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    """把 `KEY=` 那一行的值换掉，**行尾注释、其余行、行尾风格都原样保留**。

    返回 (新行列表, 是否找到了这个键)。找不到就**追加到末尾** ——
    照 `.env.example` 抄来的 `.env` 可能缺某些键，追加比报错有用。
    """
    out: list[str] = []
    found = False
    encoded = encode_value(value)
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        head, sep, rest = body.partition("=")
        if sep and not body.lstrip().startswith("#") and head.strip() == key:
            indent = body[: len(body) - len(body.lstrip())]
            out.append(f"{indent}{key}={encoded}{inline_comment(rest)}{ending}")
            found = True
        else:
            out.append(line)

    if not found:
        ending = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        if out and not out[-1].endswith(("\n", "\r")):
            out[-1] += ending
        out.append(f"{key}={encoded}{ending}")
    return out, found


class EnvFile:
    """`.env` 的可写视图：值是解析后的，行是原始的。"""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(
                f"没有这个文件：{path}（照仓库根目录的 .env.example 复制一份再改）"
            )
        self.path = path
        # ⚠️ **必须 newline=""**：默认的通用换行会把 CRLF 归一成 LF，
        # 写回时 Windows 又把 LF 翻译成 CRLF —— 结果**整个文件每一行都变了**，
        # 明明只改了一个值，diff 却是一片红。实测撞过（原 .env 是 LF）。
        #
        # ⚠️ **要读 utf-8-sig**：记事本「UTF-8 with BOM」和 PowerShell 5.1 的
        # `Set-Content -Encoding UTF8` 都会在开头写 BOM。用 utf-8 读的话
        # BOM 会并进**第一行的键名**，那一行的键就谁都不认识了 —— 而且完全静默。
        # 读的时候吃 BOM，写回去不带 BOM（utf-8-sig 只在有 BOM 时才剥）。
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            raw = handle.read()
        self.lines = raw.splitlines(keepends=True)
        self.values = parse_dotenv(raw)

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

    def save(self) -> tuple[Path, ...]:
        """先备份再写。返回**没被 git 忽略的文件**（有泄露风险，空元组=正常）。

        `.env` 里有 API key，而且它没进 git —— 弄丢补不回来，所以必留备份。

        ⚠️ **写要过临时文件 + 原子改名。** 直接 `open("w")` 会先把文件截断，
        写到一半被打断（SIGKILL / 断电）就留下一个**半截的 `.env`** ——
        丢掉的可能是末尾那几行，其中就有 API key。改名是原子的：
        要么是旧的、要么是新的，不存在中间态。
        """
        backup = self.path.with_name(self.path.name + ".bak")
        shutil.copyfile(self.path, backup)
        temp = self.path.with_name(self.path.name + ".tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(self.lines))
        os.replace(temp, self.path)
        return tuple(item for item in (self.path, backup) if not _is_ignored(item))


def _is_ignored(path: Path) -> bool:
    """`git check-ignore` 的退出码：0=被忽略，1=没被忽略，其他=git 自己出错。

    第三种情况**当作"安全"放过** —— 判不了就别喊，否则临时目录、
    非仓库目录这些都会刷屏（测试跑在临时目录里，实测淹了一屏）。

    ⚠️ **逐个查、看退出码**，不要一次传一串再拿 stdout 比字符串：
    git 打印的路径格式和传进去的未必一样（Windows 上尤其），
    字符串一比就会**把已经忽略的文件也报成危险** —— 实测在真 `.env` 上误报过。
    告警一旦爱喊，真警报就没人看了。
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path.parent), "check-ignore", "--quiet", "--", path.name],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode != 1
