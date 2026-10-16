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

import shutil
import subprocess
from pathlib import Path

from saya.config import parse_dotenv


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
            raise FileNotFoundError(
                f"没有这个文件：{path}（照 project_main/.env.example 复制一份再改）"
            )
        self.path = path
        # ⚠️ **必须 newline=""**：默认的通用换行会把 CRLF 归一成 LF，
        # 写回时 Windows 又把 LF 翻译成 CRLF —— 结果**整个文件每一行都变了**，
        # 明明只改了一个值，diff 却是一片红。实测撞过（原 .env 是 LF）。
        with path.open("r", encoding="utf-8", newline="") as handle:
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
        """
        backup = self.path.with_name(self.path.name + ".bak")
        shutil.copyfile(self.path, backup)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(self.lines))
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
