#!/usr/bin/env python3
"""**开一个终端就不关了** —— 在这里启停机器人、改设置。

⚠️ 这个脚本**不能叫 `saya.py`**：会和 `src/saya/` 这个包同名，
   于是 `sys.path` 上谁在前就导到谁，`from saya import ...` 会导到包而不是它。
   实测撞过一次，所以叫 `panel.py`。

用法（**在 WSL 里跑**，因为机器人也在 WSL 里）::

    python3 tools/panel.py

然后就在这一个窗口里：

    start          启动机器人（它的日志会出现在同一屏）
    end            停掉它            ← 要打 y 确认
    restart        重启（用现在这份代码）← 要打 y 确认
    status         在跑吗、跑了多久
    show / group / admin / friend / keyword / prob / rate / help
                   —— 和机器人终端里、和 `tools/config.py` 是**同一套命令**

**改设置的两种走法**（自动选）：

- 机器人**在跑** → 命令直接送进它的 stdin，**当场生效**（走 `core/console.py`）
- 机器人**没在跑** → 直接写 `.env`，下次启动生效

## ⚠️ 归属：这个前端**管着**机器人的命（ADR-026）

允许"前端在跑、机器人停着"（`end` 之后就是这样）；
**不允许"机器人在跑、前端没了"** —— 那就成了**无头后端**：
没人看日志、没人接命令，端口还被占着，下次启动还起不来。实测撞过。

所以：

- **前端退出（`quit` / 关窗口 / 读到 EOF）一定带走机器人**
- 启动时检查归属：
  - 有**另一个控制台**在跑 → 提示一句、**自己退出**（两个一起敲会打架）
  - 有**无头机器人**（端口被占但没人管）→ **问一句**，确认后接管（关掉它、起自己的）

> **为什么终端前端要这样，web 不一样**：终端前端是**监视器 + 控制器**，
> 伴随整个生命周期，人和它在同一个窗口里；web 可能被关掉而机器人继续跑。
> 将来 web 存在时，**仍然会有一个终端前端在旁边跑着**。
> （web 也可能从本地路由挪到真正的远程站点，那时更管不着进程。）

零第三方依赖。`Supervisor` 可被 import 复用。
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / "project_main" / ".env"
START_SCRIPT = REPO_ROOT / "tools" / "start_saya.sh"
# 放在 data/ 下（整个目录都被 gitignore 覆盖），和机器人的运行期数据作伴
LOCK_PATH = REPO_ROOT / "project_main" / "data" / "panel.lock"

sys.path.insert(0, str(REPO_ROOT / "project_main" / "src"))

from saya.console import prepare_console  # noqa: E402
from saya.envfile import EnvFile  # noqa: E402
from saya.manage import run as run_manage  # noqa: E402

PREFIX = "  "

# 确认时的"是"。**默认只认这些** —— 在 [y/N] 提示下敲别的东西一律当"否"，
# 这是常识，别自作聪明。
YES = {"y", "yes", "是", "确认", "好", "嗯"}

# ⚠️ **接管那一问例外**：它问的是"要不要用现在这份代码起一个"，
# 用户很自然会敲 `restart` 或 `start` —— 而且提示语本身就该这么说。
# 不认这两个的话，**敲了命令却被当成"否"静默吃掉**，人会一脸问号。
# （实测撞过：所有者就是在 [y/N] 下敲了 `restart`，然后看到"那就不动它"。）
YES_TAKEOVER = YES | {"restart", "start", "接管", "重开"}


@dataclass(frozen=True, slots=True)
class Outcome:
    lines: tuple[str, ...] = ()
    # 非空 = 这条命令要确认；值是待执行的动作名。前端负责问 y/n
    confirm: str | None = None
    # 确认时给用户看的问题
    question: str = ""
    ok: bool = True
    # 该退出控制台了（**会带走机器人**，见模块开头的归属说明）
    stop: bool = False


def _say(*lines: str) -> Outcome:
    return Outcome(lines=lines)


# ---------------------------------------------------------------- 进程探测

def _pid_alive(pid: int) -> bool:
    """这个进程还在吗。

    ⚠️⚠️ **`os.kill(pid, 0)` 只在 POSIX 上是"检查存在"。**
    Windows 上它**不是**空操作 —— 除 `CTRL_C_EVENT` / `CTRL_BREAK_EVENT` 之外的
    任何 sig 都会让 `TerminateProcess` **真的把那个进程杀掉**。
    这个面板只在 WSL 里跑（要调 bash / start_saya.sh），永远不该在 Windows 上执行，
    但**测试是跑在 Windows 上的** —— 所以这道护栏必须有，
    不能指望"它不会被调到"。
    """
    if os.name == "nt":  # pragma: no cover - 只在 Windows 上才走到
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 存在，只是不归我们管 —— 那也算活着
        return True
    except OSError:
        return False
    return True


def _port_open(port: int) -> bool:
    """端口上有没有人在听。纯 socket，不依赖 `ss` 这类外部工具。"""
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _pid_on_port(port: int) -> int | None:
    """谁在听这个端口。拿不到就返回 None（不代表没人听）。"""
    try:
        result = subprocess.run(
            ["ss", "-tlnpH", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"pid=(\d+)", result.stdout)
    return int(found.group(1)) if found else None


class Supervisor:
    """管着机器人那个子进程，并把设置命令转给对的地方。"""

    def __init__(
        self,
        *,
        env_path: Path = DEFAULT_ENV,
        start_script: Path = START_SCRIPT,
        lock_path: Path = LOCK_PATH,
        output=print,
    ) -> None:
        self.env_path = env_path
        # 可注入是为了**测试**：真跑 start_saya.sh 会拉起 NapCat、抢端口、
        # 动到正在跑的那个实例 —— 测试不该碰用户机器上的任何东西。
        self.start_script = start_script
        self.lock_path = lock_path
        self._output = output
        self._child: subprocess.Popen | None = None
        self._started_at: float | None = None
        self._reader: threading.Thread | None = None

    # ---- 归属锁 ----

    def other_panel(self) -> int | None:
        """另一个**还活着**的控制台。锁文件在但进程没了 = 上次没收干净，不算。"""
        try:
            pid = int(self.lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return pid if pid != os.getpid() and _pid_alive(pid) else None

    def claim_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(str(os.getpid()), encoding="ascii")

    def release_lock(self) -> None:
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    # ---- 无头机器人 ----

    def listen_port(self) -> int:
        try:
            return int(EnvFile(self.env_path).get("SAYA_ONEBOT_LISTEN_PORT") or 8080)
        except (OSError, ValueError):
            return 8080

    def stray_bot(self) -> int | None:
        """端口上有没有一个**不是我们子进程**的机器人。拿不到 PID 就返回 0。

        ⚠️ 这就是"无头后端"：上一次控制台被关掉时留下的孤儿。
        它占着端口，于是新的 `start` 会被 start_saya.sh 挡回来
        （"已经有一个在跑了"）—— 实测撞过。
        """
        if self.running:
            return None
        port = self.listen_port()
        return (_pid_on_port(port) or 0) if _port_open(port) else None

    # ---- 机器人进程 ----

    @property
    def running(self) -> bool:
        return self._child is not None and self._child.poll() is None

    def start(self, *, take_over: bool = False) -> Outcome:
        """起机器人。`take_over=True` 时用 `--restart`，顺手清掉占着端口的那个。

        ⚠️ **端口上有别人时先问**，不用 `--restart` 直接上 ——
        那会杀掉一个别人正在用的实例。
        ⚠️ 这里也问一遍（不只在启动时问）是有意的：启动时答了"否"之后
        再敲 `start`，还是要有个说法 —— 否则它就只会被 `start_saya.sh`
        挡回来一句"已经有一个在跑了"，而用户不知道为什么。
        """
        if self.running:
            return _say(f"已经在跑了（PID {self._child.pid}）。要换代码用 restart。")

        stray = self.stray_bot()
        if stray is not None and not take_over:
            return self.takeover_prompt(stray)

        # 走项目自己的启动脚本，别在这里重写一遍 NapCat / 自检那套逻辑
        #
        # ⚠️ **子进程的 stdout 是管道，不是终端** —— Python 会因此把 stdout
        # 改成**块缓冲**，于是机器人自己命令台的横幅和结果全卡在缓冲区里，
        # 面板这边一个字都看不到（表现就是"日志在滚、敲命令没反应"）。
        # `PYTHONUNBUFFERED=1` 让它逐行出来。机器人自己那边也设了
        # `line_buffering`（那才是主修），这里再兜一道。
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        argv = ["bash", str(self.start_script)] + (["--restart"] if take_over else [])
        self._child = subprocess.Popen(
            argv,
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

    def takeover_prompt(self, stray: int) -> Outcome:
        """问要不要接管那个无头实例。**问题里把后果说清楚。**"""
        who = f"PID {stray}" if stray else "PID 拿不到"
        return Outcome(
            lines=(
                f"端口 {self.listen_port()} 上有一个没人管的实例（{who}）——",
                "多半是上次这个窗口被关掉时留下的，它占着端口会让新的起不来。",
            ),
            confirm="takeover",
            question="接管它吗？会先关掉它、再用现在这份代码起一个。 [y/N]",
        )

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
            stray = self.stray_bot()
            if stray is not None:
                who = f"PID {stray}" if stray else "PID 拿不到"
                return _say(
                    f"没在管机器人。端口 {self.listen_port()} 上有一个没人管的实例（{who}）。",
                    "敲 restart（或 start）可以接管它。",
                )
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
                confirm="restart", question="确定要重启吗？重启期间群里的消息它会漏掉。 [y/N]"
            )
        if name == "status":
            return self.status()
        if name == "quit":
            return Outcome(
                confirm="quit",
                question="退出控制台？机器人也会一起停（这个窗口管着它的命）。 [y/N]",
            )

        # 其余全归管理命令
        return self.forward(line)

    def confirmed(self, action: str) -> Outcome:
        if action == "end":
            return self.stop()
        if action == "restart":
            return self.restart()
        if action == "quit":
            return Outcome(lines=("再见。",), stop=True)
        if action == "takeover":
            # 用户确认过要接管那个无头实例
            return self.start(take_over=True)
        return Outcome()


USAGE = "\n".join(
    [
        "这台控制台能做什么：",
        "  start            启动机器人（日志就在这一屏）",
        "  end              停掉它            ← 要确认",
        "  restart          重启（用现在这份代码）← 要确认",
        "  status           在跑吗、跑了多久",
        "  quit             退出控制台（机器人一起停）← 要确认",
        "",
        "[!] 这个窗口管着机器人的命：它一关，机器人就停。",
        "",
        "设置命令（和机器人自己终端里那套完全一样）：",
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


def is_yes(action: str, answer: str) -> bool:
    """这一答算不算"是"。

    ⚠️ 默认只认 `y/yes/是/…`；**接管那一问多认 `restart` / `start`** ——
    它问的就是"要不要起一个"，而提示语本身也在教用户敲 restart。
    第一版没认，结果用户敲 `restart` 被当成"否"**静默吃掉**
    （他只看到"那就不动它"，完全不知道自己的命令去哪了）。
    """
    return answer in (YES_TAKEOVER if action == "takeover" else YES)


def serve(sup: Supervisor, stream=sys.stdin, output=print) -> None:
    """启动检查 → 循环。**退出时一定带走机器人**（ADR-026）。

    ⚠️ 读到 EOF 也算退出 —— 关窗口、管道断开、Ctrl+D 都会走到这儿。
    不在这里收尾的话就会留下无头后端，实测撞过。
    """
    output(BANNER)

    # ① 已经有别的控制台了？**自己退出**，别两个一起敲。
    other = sup.other_panel()
    if other is not None:
        output(f"{PREFIX}已经有一个控制台在跑（PID {other}）。")
        output(f"{PREFIX}两个一起敲会打架，这个先退出了。")
        return

    sup.claim_lock()
    try:
        pending = _report_on_startup(sup, output)
        _loop(sup, stream, output, pending=pending)
    finally:
        # ⚠️ **不管怎么退出的**（quit / EOF / Ctrl+C / 抛异常），都要收尾：
        # 停掉机器人 + 放掉锁。少做一步就是无头后端。
        if sup.running:
            for line in sup.stop().lines:
                output(f"{PREFIX}{line}")
        sup.release_lock()


def _report_on_startup(sup: Supervisor, output) -> str | None:
    """启动就报到一次。返回一个"待确认的动作名"（没有就返回 None）。

    ⚠️ **有一个无头实例时直接问，不先打一遍 status** ——
    `status` 会把同一件事再说一遍，两段话叠在一起很晕（实测撞过：
    所有者看到同一件事说了两遍，然后没看懂在问什么）。
    这时候**那个问题本身就是报到**。
    """
    stray = sup.stray_bot()
    if stray is not None:
        prompt = sup.takeover_prompt(stray)
        for line in prompt.lines:
            output(f"{PREFIX}{line}")
        output(f"{PREFIX}{prompt.question}")
        return prompt.confirm
    for line in sup.status().lines:
        output(f"{PREFIX}{line}")
    return None


def _loop(sup: Supervisor, stream, output, *, pending: str | None = None) -> None:
    for raw in stream:
        line = raw.strip()
        if not line:
            continue

        if pending is not None:
            action, pending = pending, None
            outcome = (
                sup.confirmed(action) if is_yes(action, line) else _say("取消了。")
            )
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
 end / restart / quit 要打 y 确认
 [!] 这个窗口管着机器人的命：它一关，机器人就停。
─────────────────────────────────────────────"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="saya_bot 控制台：启停 + 改设置")
    parser.add_argument("--env", help=".env 的路径（默认 project_main/.env）")
    parser.add_argument(
        "--start-script", help="换个启动脚本（测试用）"
    )
    parser.add_argument(
        "command", nargs="*", help="不给就进交互模式；给了就跑一条命令然后退出"
    )
    args = parser.parse_args(argv)

    sup = Supervisor(
        env_path=Path(args.env).resolve() if args.env else DEFAULT_ENV,
        start_script=Path(args.start_script).resolve() if args.start_script else START_SCRIPT,
    )

    # 非交互：跑一条就走（给脚本和测试用）。**不碰机器人、也不占锁** ——
    # 一条命令不该把别人的窗口踢掉。
    if args.command:
        outcome = sup.handle(" ".join(args.command))
        for text in outcome.lines:
            print(text)
        if outcome.confirm:
            print(outcome.question)
            return 2  # 需要确认的动作不在非交互模式下做
        return 0 if outcome.ok else 2

    try:
        serve(sup)
    except KeyboardInterrupt:
        pass  # finally 里已经收尾了
    return 0


if __name__ == "__main__":
    prepare_console()
    raise SystemExit(main())
