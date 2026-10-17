"""监测终端里的命令台 —— **在机器人自己那个跑着日志的窗口里敲命令**。

为什么是终端而不是另开一个 CLI：设置改完要**立刻生效**。
另开一个进程改的只是 `.env`，而配置是**启动时读一次**的 ——
用户看到的会是"我改了但它没变"。在这里敲，改完当场换掉内核手里的配置。

⚠️ 这同时是 **0.2.0 那个 web 管理页的前置**（所有者 2026-10-16 定的）：
命令的语义和校验全在 `saya/manage.py`，**和前端无关**。
今天的前端是这个终端，将来换成 web，后端一行不用动。
分工是：

    saya/manage.py        命令的语义与校验（唯一一份）
    saya/core/console.py  前端之一：读 stdin、把结果显示出来
    tools/config.py       前端之二：离线 CLI
    （将来）web           前端之三：同一个 manage

⚠️ **本模块不做任何判断**，它只负责读一行、交给 manage、把结果打出来。

**关于"改了立刻生效"**：`Config` 是 `frozen=True` 的，谁都改不动它。
所以做法是**换掉它** —— 重建一个 `Config` 交给 `Pipeline`，
`Pipeline` 再重建 `Roles`（身份判定）并转给 `Trigger`。
冻结字段一个都不用捅，并发上也安全：换的是整个对象的引用。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Iterable, TextIO

from saya.envfile import EnvFile
from saya.manage import Result, run

# 结果行前面加两个空格，和 logging 的输出区隔开
_PREFIX = "  "

# 这些命令只有外面那个控制台（tools/panel.py）能做：
# `start` / `restart` 要**另起或替换进程**，机器人自己做不到；
# `end` / `quit` 是**那个控制台自己的**生命周期动作（它退出时会带走机器人）。
#
# ⚠️ 一开始只收了 `start` / `restart`，于是 `end` / `quit` 落进通用分支，
# 得到一句"没有 'end' 这个命令"，而 `help` 里也根本没有这两个词 ——
# 正是当初为 `start` / `restart` 修掉的那个坑，**只修了一半**。
SUPERVISOR_ONLY = frozenset({"start", "restart", "end", "quit"})

# 每个词给一句**为什么**不在这儿做 —— 只说"不在这儿"等于把人推回去重猜
_SUPERVISOR_REASON = {
    "start": "它要另起一个进程",
    "restart": "它要换掉正在跑的这个进程",
    "end": "停机器人是那个控制台的动作（它退出时会带走机器人）",
    "quit": "退出控制台是那个窗口自己的动作",
}

# ⚠️ 指路要指**使用者认识的东西**（他双击的那个文件），不是仓库内部的路径 ——
# 那对他来说等于没说。
_BAT_NAME = "saya_bot_controller.bat"

BANNER = """
─────────────────────────────────────────────
 命令台就绪--输入 help 查看命令。
 更改后立刻及长期生效（写入 .env）。
─────────────────────────────────────────────"""


# 确认词。`cache clear` 要再加一个这样的词才会真的动手。
_YES = {"yes", "y", "是", "确认"}


class Console:
    """读一行 → 交给 manage → 打印结果。变更时回调 `apply` 让内核换配置。"""

    def __init__(
        self,
        *,
        env_path: Path,
        apply: Callable[[], Iterable[str]] | None = None,
        status: Callable[[], Iterable[str]] | None = None,
        cache_preview: Callable[[], Iterable[str]] | None = None,
        cache_purge: Callable[[], Iterable[str]] | None = None,
        output: Callable[[str], None] = print,
    ) -> None:
        self.env_path = env_path
        # `apply` 返回"换完配置后要补说的话"（比如重建失败了）
        self._apply = apply
        # 运行时长之类的**只有进程自己知道**的信息，由 main 那边提供
        self._status = status
        # 清缓存用**同一个数据库连接**，所以只能在这儿做（见 panel.forward）
        self._cache_preview = cache_preview
        self._cache_purge = cache_purge
        self._output = output

    # ---- 纯逻辑：可单测，不碰 stdin/线程 ----

    def handle(self, line: str) -> Result:
        """跑一行命令。**任何异常都不许冒出去** —— 这里是机器人的主进程，
        一条手滑的命令不该把群里那个机器人搞停。"""
        word = line.strip().split()[0].lower() if line.strip() else ""

        # 有几个命令**只属于外面的控制台**（tools/panel.py）：start / restart
        # 要另起或替换进程，end / quit 是那个窗口自己的生命周期动作，
        # 机器人自己都做不到。这里如果只说"没这个命令"，用户会以为功能没做
        # —— 实测就是这么撞上的，所以直接指路。
        if word in SUPERVISOR_ONLY:
            reason = _SUPERVISOR_REASON.get(word, "机器人自己做不到")
            return Result(
                lines=(
                    f"{word!r} 得在外面那个控制台里输入"
                    f"（双击 {_BAT_NAME} 出来的那个窗口）—— {reason}。",
                ),
                ok=False,
            )

        if word == "cache":
            return self._cache(line)

        if word == "status" and self._status is not None:
            try:
                return Result(lines=tuple(self._status()))
            except Exception as exc:  # noqa: BLE001
                return Result(lines=(f"取运行状态时出错：{exc}",), ok=False)

        try:
            env = EnvFile(self.env_path)
        except FileNotFoundError as exc:
            return Result(lines=(str(exc),), ok=False)

        result = run(env, line)
        if word in ("help", "?"):
            # `status` / `cache` 是**这个前端特有的**，`manage` 那边不认识它们，
            # 所以帮助里得在这儿补上 —— 不补的话用户永远不知道有这些命令
            # （实测就是敲了三次没敲对）。
            extra: list[str] = []
            if self._status is not None:
                extra.append("  status    跑了多久、接在哪儿、用的哪个模型")
            if self._cache_preview is not None:
                extra.append("  cache     清聊天记录与收到的图片（长期记忆与设置不动）")
            if extra:
                result = Result(lines=result.lines + ("", "另外还有：", *extra))
        if not result.changed:
            return result
        if not result.ok:
            return result

        # ① 落盘：下次启动还是这套
        risky = env.save()
        lines = list(result.lines)
        lines.append(_PREFIX + "已生效，并写回 .env")

        # ② 换掉内核手里的配置 —— 这一步才是"立刻生效"
        if self._apply is not None:
            try:
                lines.extend(self._apply())
            except Exception as exc:  # noqa: BLE001
                # ⚠️ 换配置失败**绝不能**把机器人搞停：文件已经写好了，
                # 但内核手里还是旧的 —— 这个不一致必须说出来，不能吞掉。
                lines.append(f"{_PREFIX}[!] 配置已写进文件，但没能当场生效：{exc}")
                lines.append(f"{_PREFIX}    重启一次就好：bash tools/start_saya.sh --restart")
            if risky:
                lines.append(
                    _PREFIX + "[!] 注意：这个 .env 没被 git 忽略，而它有 API key —— "
                    "别提交上去"
                )
        return Result(lines=tuple(lines), changed=True, ok=True)

    def _cache(self, line: str) -> Result:
        """清聊天记录与收到的图片。

        ⚠️ **必须两句**：`cache clear` 只报数，`cache clear yes` 才动手。
        这个终端是一次一行读的，没有"停下来问一句"的地方 ——
        所以确认做在命令本身，而不是做成交互。
        """
        if self._cache_preview is None or self._cache_purge is None:
            return Result(lines=("这个实例没接缓存清理（记忆未启用）。",), ok=False)

        args = line.split()[1:]
        action = args[0].lower() if args else "show"
        if action in ("show", "status"):
            return Result(lines=tuple(self._cache_preview()))
        if action == "clear":
            if len(args) >= 2 and args[1].lower() in _YES:
                return Result(lines=tuple(self._cache_purge()))
            return Result(
                lines=tuple(self._cache_preview())
                + ("确认清空再加一个 yes：cache clear yes",)
            )
        return Result(
            lines=(f"cache 只有 show / clear，没有 {action!r}。",), ok=False
        )

    # ---- 前端：读 stdin ----

    def serve(self, stream: TextIO) -> None:
        """阻塞读。**读到 EOF 就安静退出** —— 后台跑（nohup / 重定向）时就是这样。"""
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                self.emit(self.handle(line))
            except Exception as exc:  # noqa: BLE001  兜底：命令台绝不许拖垮机器人
                self._output(f"{_PREFIX}[!] 这条命令炸了（已忽略）：{exc}")

    def emit(self, result: Result) -> None:
        for line in result.lines:
            self._output(f"{_PREFIX}{line}")

    def start(self, stream: TextIO) -> threading.Thread:
        """在后台线程里跑。daemon=True —— 它绝不该拦着进程退出。"""
        thread = threading.Thread(
            target=self.serve, args=(stream,), name="saya-console", daemon=True
        )
        thread.start()
        return thread
