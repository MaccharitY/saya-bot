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

BANNER = """
─────────────────────────────────────────────
 命令台就绪。敲 help 看有哪些命令。
 改完**立刻生效**，同时写回 .env（下次启动还是它）。
 例：show            admin add 10086
     group 123 silent    rate 3
─────────────────────────────────────────────"""


class Console:
    """读一行 → 交给 manage → 打印结果。变更时回调 `apply` 让内核换配置。"""

    def __init__(
        self,
        *,
        env_path: Path,
        apply: Callable[[], Iterable[str]] | None = None,
        output: Callable[[str], None] = print,
    ) -> None:
        self.env_path = env_path
        # `apply` 返回"换完配置后要补说的话"（比如重建失败了）
        self._apply = apply
        self._output = output

    # ---- 纯逻辑：可单测，不碰 stdin/线程 ----

    def handle(self, line: str) -> Result:
        """跑一行命令。**任何异常都不许冒出去** —— 这里是机器人的主进程，
        一条手滑的命令不该把群里那个机器人搞停。"""
        try:
            env = EnvFile(self.env_path)
        except FileNotFoundError as exc:
            return Result(lines=(str(exc),), ok=False)

        result = run(env, line)
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
                lines.append(f"{_PREFIX}[!] 配置已写进文件，但**没能当场生效**：{exc}")
                lines.append(f"{_PREFIX}    重启一次就好：bash tools/start_saya.sh --restart")
            if risky:
                lines.append(
                    _PREFIX + "[!] 注意：这个 .env 没被 git 忽略，而它有 API key —— "
                    "别提交上去"
                )
        return Result(lines=tuple(lines), changed=True, ok=True)

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
