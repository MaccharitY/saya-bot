#!/usr/bin/env python3
"""**开一个终端就不关了** —— 在这里启停机器人、改设置。

⚠️ 这个脚本**不能叫 `saya.py`**：会和 `src/saya/` 这个包同名，
   于是 `sys.path` 上谁在前就导到谁，`from saya import ...` 会导到包而不是它。
   实测撞过一次，所以叫 `panel.py`。

为什么需要它：以前每次动一下都要另开一个终端（启动一个、改设置一个、
看日志又一个），而且改设置那个还是**离线**的（改完得重启才生效）。
前后端分离之后（ADR-022）命令语义已经和前端无关了，所以可以有一个
**常驻的控制台**把这些都收进来。

用法（**在 WSL 里跑**，因为机器人也在 WSL 里）::

    python3 tools/panel.py

然后就在这一个窗口里：

    start          启动机器人（它的日志会出现在同一屏）
    end            停掉它            ← 要打 y 确认
    restart        重启（用现在这份代码）← 要打 y 确认
    status         在跑吗、跑了多久
    show / group / admin / friend / keyword / prob / rate / help
                   —— 和机器人终端里、和 `tools/config.py` 是**同一套命令**

**改设置的两种走法**（自动选，不用你操心）：

- 机器人**在跑** → 命令直接送进它的 stdin，**当场生效**（走 `core/console.py`）
- 机器人**没在跑** → 直接写 `.env`，下次启动生效

⚠️ 这个脚本**在 WSL 里跑**：它要调 `tools/start_saya.sh`，那是个 bash 脚本。

零第三方依赖。`Supervisor` 可被 import 复用。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / "project_main" / ".env"
START_SCRIPT = REPO_ROOT / "tools" / "start_saya.sh"

sys.path.insert(0, str(REPO_ROOT / "project_main" / "src"))

from saya.console import prepare_console  # noqa: E402
from saya.envfile import EnvFile  # noqa: E402
from saya.manage import run as run_manage  # noqa: E402

PREFIX = "  "

# 要确认才做的动作 —— 都不能一条命令就悔棋
NEEDS_CONFIRM = {"end", "restart"}
YES = {"y", "yes", "是", "确认", "好", "嗯"}


@dataclass(frozen=True, slots=True)
class Outcome:
    lines: tuple[str, ...] = ()
    # 非空 = 这条命令要确认；值是待执行的动作名。前端负责问 y/n
    confirm: str | None = None
    # 确认时给用户看的问题
    question: str = ""
    ok: bool = True
    # 该退出控制台了（注意：**机器人照跑**，只有控制台退）
    stop: bool = False


def _say(*lines: str) -> Outcome:
    return Outcome(lines=lines)


class Supervisor:
    """管着机器人那个子进程，并把设置命令转给对的地方。"""

    def __init__(
        self,
        *,
        env_path: Path = DEFAULT_ENV,
        start_script: Path = START_SCRIPT,
        output=print,
    ) -> None:
        self.env_path = env_path
        # 可注入是为了**测试**：真跑 start_saya.sh 会拉起 NapCat、抢端口、
        # 动到正在跑的那个实例 —— 测试不该碰用户机器上的任何东西。
        self.start_script = start_script
        self._output = output
        self._child: subprocess.Popen | None = None
        self._started_at: float | None = None
        self._reader: threading.Thread | None = None

    # ---- 机器人进程 ----

    @property
    def running(self) -> bool:
        return self._child is not None and self._child.poll() is None

    def start(self) -> Outcome:
        if self.running:
            return _say(f"已经在跑了（PID {self._child.pid}）。要换代码用 restart。")

        # 走项目自己的启动脚本，别在这里重写一遍 NapCat / 自检那套逻辑
        #
        # ⚠️ **子进程的 stdout 是管道，不是终端** —— Python 会因此把 stdout
        # 改成**块缓冲**，于是机器人自己命令台的横幅和结果全卡在缓冲区里，
        # 面板这边一个字都看不到（表现就是"本体指令正常、原前端指令异常"）。
        # `PYTHONUNBUFFERED=1` 让它逐行出来。ADR-021 那条是"编码"，
        # 这条是"缓冲"，同一类问题：**输出不接终端时也得能用**。
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self._child = subprocess.Popen(
            ["bash", str(self.start_script)],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            # 单独一个进程组：停的时候只停它，不牵连这个控制台
            start_new_session=True,
        )
        self._started_at = time.monotonic()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        return _say(f"启动中（PID {self._child.pid}）。日志会出现在下面。")

    def stop(self) -> Outcome:
        if not self.running:
            self._child = None
            return _say("本来就没在跑。")
        pid = self._child.pid
        # ⚠️ 发 **SIGINT 而不是 SIGTERM**：机器人把 KeyboardInterrupt 接住了，
        # 会走 finally 里的 pipeline.stop()（关接入、落记忆）。直接 SIGTERM
        # 会跳过清理。装了信号处理之前先试温柔的，不行再强杀。
        self._child.send_signal(signal.SIGINT)
        try:
            self._child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._child.terminate()
            try:
                self._child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._child.kill()
                self._child.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=2)
        self._child = None
        self._started_at = None
        return _say(f"已停（PID {pid}）。")

    def restart(self) -> Outcome:
        stopped = self.stop() if self.running else _say("（本来就没在跑，直接启动）")
        started = self.start()
        return Outcome(lines=stopped.lines + started.lines)

    def status(self) -> Outcome:
        if not self.running:
            return _say("没在跑。敲 start 起它。")
        elapsed = int(time.monotonic() - (self._started_at or time.monotonic()))
        minutes, seconds = divmod(elapsed, 60)
        return _say(f"在跑（PID {self._child.pid}），已运行 {minutes} 分 {seconds} 秒。")

    def _pump(self) -> None:
        """把子进程的输出原样搬到我们这屏。**读到 EOF 就结束**（进程没了）。"""
        child = self._child
        if child is None or child.stdout is None:
            return
        for line in child.stdout:
            self._output(line.rstrip("\n"))

    # ---- 设置命令 ----

    def forward(self, line: str) -> Outcome:
        """把一行设置命令送到该去的地方。

        ⚠️ **机器人跑着的时候必须送进它的 stdin**，不能只改 `.env` ——
        配置是启动时读一次的，只改文件等于"我改了但它没变"。
        改动生效后它的 console 会把结果打到自己 stdout，我们这屏看得见。
        """
        if self.running and self._child is not None and self._child.stdin is not None:
            try:
                self._child.stdin.write(line + "\n")
                self._child.stdin.flush()
                return Outcome()  # 结果由子进程的输出回来
            except (BrokenPipeError, ValueError):
                pass  # 刚好在这时候退了，走下面离线那条

        try:
            env = EnvFile(self.env_path)
        except FileNotFoundError as exc:
            return Outcome(lines=(str(exc),), ok=False)

        result = run_manage(env, line)
        lines = list(result.lines)
        if result.changed and result.ok:
            risky = env.save()
            lines.append(PREFIX + "机器人没在跑，已写进 .env（下次启动生效）")
            if risky:
                lines.append(PREFIX + "[!] 这个 .env 没被 git 忽略，而它有 API key")
        return Outcome(lines=tuple(lines), ok=result.ok)

    # ---- 命令分发 ----

    def handle(self, line: str) -> Outcome:
        parts = line.strip().split()
        if not parts:
            return Outcome()
        name, args = parts[0].lower(), parts[1:]

        if name in ("help", "?"):
            return _say(USAGE)
        if name == "start":
            return self.start()
        if name == "end":
            return Outcome(
                confirm="end", question="确定要停掉机器人吗？它会暂时不理人。 [y/N]"
            )
        if name == "restart":
            return Outcome(
                confirm="restart",
                question="确定要重启吗？重启期间群里的消息它会漏掉。 [y/N]",
            )
        if name == "status":
            return self.status()
        if name == "quit":
            return Outcome(confirm="quit", question="退出控制台？（机器人会继续跑）[y/N]")

        # 其余全归管理命令
        return self.forward(line)

    def confirmed(self, action: str) -> Outcome:
        if action == "end":
            return self.stop()
        if action == "restart":
            return self.restart()
        if action == "quit":
            return Outcome(lines=("再见。（机器人还在跑）",), stop=True)
        return Outcome()


USAGE = "\n".join(
    [
        "这台控制台能做什么：",
        "  start            启动机器人（日志就在这一屏）",
        "  end              停掉它            ← 要确认",
        "  restart          重启（用现在这份代码）← 要确认",
        "  status           在跑吗、跑了多久",
        "  quit             退出控制台（机器人继续跑）",
        "",
        "设置命令（和机器人终端里、tools/config.py 是同一套）：",
        "  show                       看现在是什么样",
        "  group <群号> <策略>         设某个群的策略（写 - 就移出表）",
        "  default <策略>              没列出的群默认怎么办",
        "  owner <QQ>                  主人",
        "  admin add|del <QQ>          管理员（最多 3 个）",
        "  friend add|del <QQ>         白名单（最多 15 个）",
        "  keyword set|add|del <词>    keyword 模式用的词",
        "  prob <0~1> / rate <n>       random 概率 / 每分钟上限",
        "",
        "策略：silent / mention / keyword / random / always / selective",
    ]
)


def serve(sup: Supervisor, stream=sys.stdin, output=print) -> None:
    """读一行 → 处理 → 打印。**读到 EOF 就退出**（管道跑的时候会这样）。"""
    pending: str | None = None
    output(BANNER)
    for raw in stream:
        line = raw.strip()
        if not line:
            continue

        if pending is not None:
            action, pending = pending, None
            if line.lower() in YES:
                outcome = sup.confirmed(action)
            else:
                outcome = _say("取消了。")
        else:
            outcome = sup.handle(line)

        for text in outcome.lines:
            output(f"{PREFIX}{text}")
        if outcome.confirm:
            output(f"{PREFIX}{outcome.question}")
            pending = outcome.confirm
        if outcome.stop:
            return


BANNER = """
─────────────────────────────────────────────
 saya 控制台。敲 help 看命令。
 start / end / restart / status + 全部设置命令
 （end 和 restart 要打 y 确认）
─────────────────────────────────────────────"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="saya_bot 控制台：启停 + 改设置")
    parser.add_argument("--env", help=".env 的路径（默认 project_main/.env）")
    parser.add_argument(
        "--start-script", help="换个启动脚本（**只给测试用**，默认 tools/start_saya.sh）"
    )
    parser.add_argument(
        "command", nargs="*", help="不给就进交互模式；给了就跑一条命令然后退出"
    )
    args = parser.parse_args(argv)

    sup = Supervisor(
        env_path=Path(args.env).resolve() if args.env else DEFAULT_ENV,
        start_script=Path(args.start_script).resolve() if args.start_script else START_SCRIPT,
    )

    # 非交互：跑一条就走（给脚本和测试用）
    if args.command:
        outcome = sup.handle(" ".join(args.command))
        for text in outcome.lines:
            print(text)
        if outcome.confirm:
            print(outcome.question)
            return 2  # 需要确认的动作不在非交互模式下做
        return 0 if outcome.ok else 2

    serve(sup)
    return 0


if __name__ == "__main__":
    prepare_console()
    raise SystemExit(main())
