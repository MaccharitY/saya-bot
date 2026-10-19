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
import logging
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / "project_main" / ".env"
START_SCRIPT = REPO_ROOT / "tools" / "start_saya.sh"
# 放在 data/ 下（整个目录都被 gitignore 覆盖），和机器人的运行期数据作伴
LOCK_PATH = REPO_ROOT / "project_main" / "data" / "panel.lock"

sys.path.insert(0, str(REPO_ROOT / "project_main" / "src"))

from saya import cache as cache_store  # noqa: E402
from saya.console import prepare_console  # noqa: E402
from saya.envfile import EnvFile  # noqa: E402
from saya.manage import run as run_manage  # noqa: E402
from saya import napcat  # noqa: E402
from saya.watchdog import pid_is_alive  # noqa: E402

PREFIX = "  "

logger = logging.getLogger("saya.panel")

# 确认时的"是"。**默认只认这些** —— 在 [y/N] 提示下敲别的东西一律当"否"，
# 这是常识，别自作聪明。
YES = {"y", "yes", "是", "确认", "好", "嗯"}

# ⚠️ **接管那一问例外**：它问的是"要不要用现在这份代码起一个"，
# 用户很自然会敲 `restart` 或 `start` —— 而且提示语本身就该这么说。
# 不认这两个的话，**敲了命令却被当成"否"静默吃掉**，人会一脸问号。
# （实测撞过：所有者就是在 [y/N] 下敲了 `restart`，然后看到"那就不动它"。）
YES_TAKEOVER = YES | {"restart", "start", "接管", "重开"}

# 只有**交互模式**能做的命令。它们要么要起一个子进程并一直看着（start / restart），
# 要么要先问一句再动手（end / quit）—— 非交互模式跑一条就走，做不了这些。
INTERACTIVE_ONLY = frozenset({"start", "end", "restart", "quit"})


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
    """这个 PID 上还有**活着的**进程吗。

    ⚠️ **直接复用看护那边的判定**（`watchdog.pid_is_alive`）。这里以前是
    **自己写的另一份**，而且只处理了两个坑里的一个：

    - 处理了：`os.kill(pid, 0)` 在 Windows 上会**真的把进程杀掉**
    - **漏了**：僵尸进程对 `os.kill` 是"活着"的

    漏掉的那个后果很具体：面板被强杀之后，如果它的父进程还没回收它，
    它会以**僵尸**形态赖在进程表里，锁文件里记的正是这个号 ——
    于是新面板判出"已存在运行中的控制台"，**自己退出，永远进不去**，
    而界面上没有任何提示，唯一出路是手工删 `project_main/data/panel.lock`。
    （同一个坑，`watchdog.py` 那边修好了，这边没修 —— 只修了一半。）

    ⚠️ 默认值和看护那边**相反**：这边判不了就当作"没了"，
    不然锁永远解不开；看护那边判不了当作"活着"，不然会误杀机器人。
    """
    return pid_is_alive(pid)


def _looks_like_our_bot(pid: int) -> bool:
    """这个 PID 是不是**我们自己的**机器人。

    ⚠️ **端口号不算身份。** 8080 上坐着谁都可能，而"接管"的动作是**杀进程** ——
    只凭"它在听那个端口"就把别的程序叫成"未托管实例"，等于拿别人的进程冒险。
    判据取 `/proc/<pid>/cmdline` 里有没有 `run.py`。

    读不到 `/proc`（非 Linux）时**当作是**：宁可保留老行为，
    也不要因为读不到就拒绝接管一个真的是自己的实例。
    """
    if os.name == "nt":  # pragma: no cover - 面板只在 WSL 里跑
        return True
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    return b"run.py" in raw


# ⚠️ `signal.SIGKILL` **在 Windows 上根本不存在** —— 写成 `signal.SIGKILL`
# 会在**求值那一刻**就 `AttributeError`，连 `os.name` 的判断都轮不到。
# 面板只在 WSL 里跑，但测试跑在 Windows 上（写这个的时候就是这么栽的）。
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _signal_group(child: subprocess.Popen, sig: int) -> None:
    """把信号发给**整个进程组**，而不是那个 bash 进程。

    ⚠️ `_child` 起的是 `bash start_saya.sh`，它以 `start_new_session=True`
    自成一个进程组。脚本末尾是 `exec python run.py`，所以**稳态下** PID 就是
    机器人本身，单发也能到 —— 但**在那之前**（脚本前半段的自检，含一次
    真实模型调用，好几秒）PID 还归 bash，而那时跑着的 python 是它的
    **前台子进程**。

    只给 bash 发 SIGINT：机器人收不到，bash 也不替它转发 →
    等满 10 秒超时 → `terminate()` 把 bash 杀掉 → **机器人被孤儿化**，
    而面板照样报"已停止"。发给整个组，两个阶段都覆盖。

    NapCat 是脚本里用 `setsid` 单独开的会话，**不在这个组里**，不会被误伤。
    """
    if os.name == "nt":  # pragma: no cover - 面板只在 WSL 里跑
        child.send_signal(sig)
        return
    try:
        os.killpg(os.getpgid(child.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            child.send_signal(sig)  # 组没了或拿不到组号 —— 退回单发
        except (ProcessLookupError, OSError):
            pass


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
        napcat_dir: Path | None = None,
        napcat_wait: float = napcat.DEFAULT_WAIT,
        output=print,
    ) -> None:
        self.env_path = env_path
        # 可注入是为了**测试**：真跑 start_saya.sh 会拉起 NapCat、抢端口、
        # 动到正在跑的那个实例 —— 测试不该碰用户机器上的任何东西。
        self.start_script = start_script
        self.lock_path = lock_path
        self.napcat_dir = napcat_dir or napcat.default_dir()
        self.napcat_wait = napcat_wait
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

    def claim_lock(self) -> bool:
        """抢锁。**用 `O_EXCL` 建文件**，抢不到返回 False。

        ⚠️ 以前是"先 `other_panel()` 查一遍、再 `write_text`" —— 查和写之间
        别的进程可以插进来，于是**两个面板都以为自己独占**。后果不是"有点乱"：
        后来的那个因为 `self.running` 为假，会把设置命令**直接写 `.env`**，
        而前者的机器人还跑着（改动不会生效），界面却提示"已写入 .env"。

        文件存在但里面那个进程已经没了（上次没收干净）→ 删掉重抢一次。
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                handle_fd = os.open(
                    self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                if self.other_panel() is not None:
                    return False  # 真有人在用
                try:
                    self.lock_path.unlink()  # 陈锁，清掉重来
                except OSError:
                    return False
                continue
            except OSError:
                return False
            with os.fdopen(handle_fd, "w", encoding="ascii") as handle:
                handle.write(str(os.getpid()))
            return True
        return False

    def release_lock(self) -> None:
        """**只删自己的锁。**

        ⚠️ 以前是无条件 `unlink()` —— 抢锁失败的那个面板一退出，就会把
        **别人的**锁删掉，于是第三个面板能进来，对着第一个的机器人"接管"。
        """
        try:
            if self.lock_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                self.lock_path.unlink()
        except (OSError, ValueError):
            pass

    # ---- 无头机器人 ----

    def listen_port(self) -> int:
        try:
            return int(EnvFile(self.env_path).get("SAYA_ONEBOT_LISTEN_PORT") or 8080)
        except (OSError, ValueError):
            return 8080

    def console_enabled(self) -> bool:
        """机器人那个实例开没开命令台（`SAYA_CONSOLE_ENABLED`）。

        ⚠️ **关掉命令台时，往它的 stdin 写字是白写** —— `__main__.py` 根本没起
        那个线程，管道那头没人读。以前 `forward` 只看 `self.running`，
        于是命令进了管道、面板**一个字都不回**、`.env` 没写、内核也没换，
        而用户以为改好了。更糟的是行为不一致：机器人**停着**时同一条命令
        反而会正常打印"已写入 .env"。

        解析规则和 `config.py` 的 `_TRUE` 保持一致 —— 两处不一致的话，
        这里会以为开着、那边实际关着，又回到同一个坑。
        """
        try:
            raw = EnvFile(self.env_path).get("SAYA_CONSOLE_ENABLED", "true")
        except (OSError, FileNotFoundError):
            return True
        return raw.strip().lower() in {"1", "true", "yes", "on"}

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
            return _say(f"运行中（PID {self._child.pid}）。更换代码请用 restart。")

        stray = self.stray_bot()
        if stray is not None and not take_over:
            return self.takeover_prompt(stray)

        # 走项目自己的启动脚本，别在这里重写一遍 NapCat / 自检那套逻辑
        #
        # ⚠️ **子进程的 stdout 是管道，不是终端** —— Python 会因此把 stdout
        # 改成**块缓冲**，于是机器人自己命令台的横幅和结果全卡在缓冲区里，
        # 面板这边一个字都看不到（表现就是"日志在滚、敲命令没反应"）。
        # `PYTHONUNBUFFERED` 让它逐行出来。机器人自己那边也设了
        # `line_buffering`（那才是主修），这里再兜一道。
        #
        # `SAYA_OWNER_PID` 是**反方向的检查**：告诉机器人"养你的是我"，
        # 它就会盯着这个 PID —— 我这个窗口被直接叉掉（finally 跑不到）时，
        # 它会自己退出，不会留下没人管又占着端口的实例。
        env = dict(
            os.environ, PYTHONUNBUFFERED="1", SAYA_OWNER_PID=str(os.getpid())
        )
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
        return _say(f"启动中（PID {self._child.pid}）--开始输出日志。")

    def takeover_prompt(self, stray: int) -> Outcome:
        """问要不要接管那个无头实例。**问题里把后果说清楚。**

        ⚠️ **先确认那真是我们的机器人。** 端口是极弱的身份证据：8080 上
        坐着什么都可能。把别的程序说成"未托管实例"，人就会去点"接管" ——
        而接管的动作是**把它杀掉**。
        """
        if stray and not _looks_like_our_bot(stray):
            return Outcome(
                lines=(
                    f"端口 {self.listen_port()} 被另一个进程占着（PID {stray}），",
                    "它不是本机器人（命令行里没有 run.py）。不会去动它。",
                    "如要换端口：改 .env 里的 SAYA_ONEBOT_LISTEN_PORT。",
                ),
                ok=False,
            )
        who = f"PID {stray}" if stray else "PID 拿不到"
        return Outcome(
            lines=(
                f"端口 {self.listen_port()} 上存在未托管实例（{who}）——",
                "该实例会占用端口，导致新实例无法启动。",
            ),
            confirm="takeover",
            question="接管？将先停止该实例，再以当前代码启动。 [y/N]",
        )

    def stop(self) -> Outcome:
        if not self.running:
            self._child = None
            return _say("未运行。")
        pid = self._child.pid
        # ⚠️ 发 **SIGINT 而不是 SIGTERM**：机器人把 KeyboardInterrupt 接住了，
        # 会走 finally 里的 pipeline.stop()（关接入、落记忆）。直接 SIGTERM
        # 会跳过清理。装了信号处理之前先试温柔的，不行再强杀。
        #
        # ⚠️ 而且要走 `_signal_group`：脚本 `exec` 之前 PID 还归 bash，
        # 只发单进程的话机器人根本收不到（见那个函数的说明）。
        _signal_group(self._child, signal.SIGINT)
        try:
            self._child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _signal_group(self._child, signal.SIGTERM)
            try:
                self._child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_group(self._child, _SIGKILL)
                self._child.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=2)
        self._child = None
        self._started_at = None
        return _say(f"已停止（PID {pid}）。")

    def restart(self) -> Outcome:
        stopped = self.stop() if self.running else _say("（未运行，直接启动）")
        started = self.start()
        # ⚠️ **`confirm` / `question` 要一起带出去**，不能只拼 `lines`。
        # 端口上有未托管实例时 `start()` 会返回一句待确认的提问；只取 `lines`
        # 的话，屏幕上会出现提问的**上半句**（断在破折号上），既没有问句、
        # 也没有"已取消"，敲完 y 什么都不会发生 —— 接管被静默吞掉。
        return replace(started, lines=stopped.lines + started.lines)

    def status(self) -> Outcome:
        """机器人与 QQ 侧**一起报**。

        ⚠️ 2026-10-17 那次事故的教训：只报机器人，会得到"运行中"
        而 QQ 那头其实早就掉线了 —— 机器人活着，但一条消息都收不到。
        两行并排看，这种"半边活着"一眼就能看出来。
        """
        outcome = self._bot_status()
        return replace(outcome, lines=outcome.lines + tuple(self.napcat_lines()))

    def _bot_status(self) -> Outcome:
        if not self.running:
            stray = self.stray_bot()
            if stray is not None:
                who = f"PID {stray}" if stray else "PID 拿不到"
                if stray and not _looks_like_our_bot(stray):
                    return _say(
                        f"未在管理 bot。端口 {self.listen_port()} 被另一个进程占着"
                        f"（{who}，不是本机器人）。",
                        "如要换端口：改 .env 里的 SAYA_ONEBOT_LISTEN_PORT。",
                    )
                return _say(
                    f"未在管理 bot。端口 {self.listen_port()} 上存在未托管实例（{who}）。",
                    "输入 restart 或 start 可接管。",
                )
            return _say("未运行。输入 start 启动。")
        elapsed = int(time.monotonic() - (self._started_at or time.monotonic()))
        minutes, seconds = divmod(elapsed, 60)
        return _say(f"运行中（PID {self._child.pid}），已运行 {minutes} 分 {seconds} 秒。")

    # ---- QQ 侧（NapCat）----
    #
    # ⚠️ **它不跟着这个窗口一起死。** 机器人是"前端走了就跟着走"（ADR-026），
    # NapCat 正相反：它揣着 QQ 登录态，重启之后不保证能自动登录。
    # 所以这里只有人叫它停才停。

    def self_qq(self) -> str:
        """QQ 号。和 `start_saya.sh` 一样，从 `.env` 的 `SAYA_SELF_ID` 读。"""
        try:
            return (EnvFile(self.env_path).get("SAYA_SELF_ID") or "").strip()
        except (OSError, ValueError):
            return ""

    def napcat_state(self) -> napcat.State:
        return napcat.probe(self.napcat_dir)

    def napcat_lines(self) -> list[str]:
        try:
            state = self.napcat_state()
        except Exception:  # noqa: BLE001 - 状态查不出来不该掀翻控制台
            logger.exception("NapCat 状态探测失败")
            return ["NapCat：状态查询失败（详见日志）。"]
        return napcat.describe(state, napcat_dir=self.napcat_dir)

    def napcat_start(self) -> Outcome:
        qq = self.self_qq()
        if not qq:
            return Outcome(
                lines=(".env 内 SAYA_SELF_ID 未赋值，无法启动 NapCat。",), ok=False
            )
        try:
            state = napcat.start(qq, napcat_dir=self.napcat_dir, wait=self.napcat_wait)
        except FileNotFoundError as exc:
            return Outcome(lines=(f"未找到 NapCat：{exc}",), ok=False)
        return Outcome(
            lines=tuple(napcat.describe(state, napcat_dir=self.napcat_dir)),
            ok=state.ready,
        )

    def napcat_stop(self) -> Outcome:
        try:
            state = napcat.stop(napcat_dir=self.napcat_dir)
        except OSError as exc:
            return Outcome(lines=(f"停止失败：{exc}",), ok=False)
        return Outcome(lines=tuple(napcat.describe(state, napcat_dir=self.napcat_dir)))

    def napcat_restart(self) -> Outcome:
        qq = self.self_qq()
        if not qq:
            return Outcome(
                lines=(".env 内 SAYA_SELF_ID 未赋值，无法启动 NapCat。",), ok=False
            )
        try:
            state = napcat.restart(qq, napcat_dir=self.napcat_dir, wait=self.napcat_wait)
        except (FileNotFoundError, OSError) as exc:
            return Outcome(lines=(f"重启失败：{exc}",), ok=False)
        return Outcome(
            lines=tuple(napcat.describe(state, napcat_dir=self.napcat_dir)),
            ok=state.ready,
        )

    def napcat_command(self, args: list[str]) -> Outcome:
        action = args[0].lower() if args else "status"
        if action in ("", "status"):
            return _say(*self.napcat_lines())
        if action == "start":
            return self.napcat_start()
        if action == "stop":
            return Outcome(
                confirm="napcat-stop",
                question="确认停止 NapCat？QQ 将下线，重新登录可能需要在 WebUI 扫码。 [y/N]",
            )
        if action == "restart":
            return Outcome(
                confirm="napcat-restart",
                question="确认重启 NapCat？重启后可能需要在 WebUI 重新登录。 [y/N]",
            )
        return Outcome(
            lines=(f"napcat 支持 status / start / stop / restart。",),
            ok=False,
        )

    def _pump(self) -> None:
        """把子进程的输出原样搬到我们这屏。**读到 EOF 就结束**（进程没了）。"""
        child = self._child
        if child is None or child.stdout is None:
            return
        for line in child.stdout:
            self._output(line.rstrip("\n"))

    # ---- 清缓存 ----
    #
    # ⚠️ **直接开库清，不转给机器人**。配置是"进程里的一份对象"（所以要转进去），
    # 而聊天记录只在磁盘上 —— 机器人每次读都走 SQLite，删掉就没了。
    # 机器人跑着也照清：它下一次读自然是空的。

    def cache_dir(self) -> Path:
        return cache_store.data_dir_for(self.env_path)

    def cache_command(self, args: list[str]) -> Outcome:
        action = args[0].lower() if args else ""
        if action in ("", "show", "status"):
            return _say(*cache_store.describe(cache_store.inventory(self.cache_dir())))
        if action == "clear":
            return Outcome(
                confirm="cache-clear",
                question=(
                    "确认清空聊天记录与收到的图片？长期记忆与设置不受影响。 [y/N]"
                ),
            )
        return Outcome(
            lines=("cache 支持 show / clear。",), ok=False
        )

    def cache_clear(self) -> Outcome:
        try:
            snap = cache_store.purge(self.cache_dir())
        except OSError as exc:
            return Outcome(lines=(f"清缓存失败：{exc}",), ok=False)
        return Outcome(lines=cache_store.describe(snap, cleaned=True))

    # ---- 设置命令 ----

    def forward(self, line: str) -> Outcome:
        """把一行设置命令送到该去的地方。

        ⚠️ **机器人跑着的时候必须送进它的 stdin**，不能只改 `.env` ——
        配置是启动时读一次的，只改文件等于"我改了但它没变"。
        改动生效后它的 console 会把结果打到自己 stdout，我们这屏看得见。

        ⚠️ 但**只有它开了命令台才送得进去**（见 `console_enabled`）。
        送不进去的时候要**说清楚**，不能既没改到、又让人以为改到了。
        """
        if self.running and self._child is not None and self._child.stdin is not None:
            if self.console_enabled():
                try:
                    self._child.stdin.write(line + "\n")
                    self._child.stdin.flush()
                    return Outcome()  # 结果由子进程的输出回来
                except (BrokenPipeError, ValueError):
                    pass  # 刚好在这时候退了，走下面离线那条
            else:
                return self._write_env(
                    line,
                    note="运行中的实例没开命令台，命令送不进去。改动已写入 .env，"
                         "重启后生效（end 再 start）。",
                )

        return self._write_env(line, note="bot 未运行，已写入 .env（下次启动生效）")

    def _write_env(self, line: str, *, note: str) -> Outcome:
        """离线那条路：直接改 `.env`。"""
        try:
            env = EnvFile(self.env_path)
        except FileNotFoundError as exc:
            return Outcome(lines=(str(exc),), ok=False)

        result = run_manage(env, line)
        lines = list(result.lines)
        if result.changed and result.ok:
            risky = env.save()
            lines.append(f"{PREFIX}{note}")
            if risky:
                lines.append(f"{PREFIX}[!] 这个 .env 没被 git 忽略，而它有 API key")
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
                confirm="end", question="确认停止 bot？停止期间不响应消息。 [y/N]"
            )
        if name == "restart":
            return Outcome(
                confirm="restart", question="确认重启？重启期间的消息不会处理。 [y/N]"
            )
        if name == "status":
            return self.status()
        if name == "napcat":
            return self.napcat_command(args)
        if name == "cache":
            return self.cache_command(args)
        if name == "quit":
            return Outcome(
                confirm="quit",
                question="退出控制台？bot 同时终止。 [y/N]",
            )

        # 其余全归管理命令
        return self.forward(line)

    def confirmed(self, action: str) -> Outcome:
        if action == "end":
            return self.stop()
        if action == "restart":
            return self.restart()
        if action == "quit":
            return Outcome(lines=("已退出。",), stop=True)
        if action == "takeover":
            # 用户确认过要接管那个无头实例
            return self.start(take_over=True)
        if action == "napcat-stop":
            return self.napcat_stop()
        if action == "napcat-restart":
            return self.napcat_restart()
        if action == "cache-clear":
            return self.cache_clear()
        return Outcome()


USAGE = "\n".join(
    [
        "可用命令：",
        "  start            启动 bot（日志输出至本终端）",
        "  end              停止 bot（需确认）",
        "  restart          重启（使用当前代码，需确认）",
        "  status           查看运行状态（bot 与 NapCat）",
        "  quit             退出控制台（bot 同时终止，需确认）",
        "",
        "NapCat：",
        "  napcat           查看状态（进程 / 登录 / 端口）",
        "  napcat start     启动（运行中则等待就绪）",
        "  napcat stop      停止（需确认）",
        "  napcat restart   重启（需确认，可能需要在 WebUI 重新登录）",
        "",
        "缓存：",
        "  cache            查看将清理的内容",
        "  cache clear      清空聊天记录与收到的图片（需确认）",
        "",
        "[!] bot 会在此终端退出时同时终止。",
        "[!] NapCat 不随控制台退出。",
        "[!] cache clear 不影响长期记忆与设置。",
        "",
        "设置命令（与 bot 终端内命令一致）：",
        "  show                       查看当前配置",
        "  group <群号> <策略>         设置群策略（- 表示移出）",
        "  default <策略>              未列出群的默认策略",
        "  owner <QQ>                  设置主人",
        "  admin add|del <QQ>          管理员（上限 3）",
        "  friend add|del <QQ>         白名单（上限 15）",
        "  keyword set|add|del <词>    keyword 模式关键词",
        "  prob <0~1> / rate <n>       random 概率 / 每分钟回复上限",
        "  ocr auto|cloud|local        云 OCR 开关",
        "  yolo n|s|m|l|x              检测权重",
        "  device auto|cpu|cuda:N      检测跑在哪",
        "",
        "策略：silent / mention / keyword / random / always / selective",
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
        output(f"{PREFIX}已存在运行中的控制台（PID {other}）。")
        output(f"{PREFIX}本控制台退出。")
        return

    # ⚠️ 查过之后还得**真正抢一次**：查和抢之间别的进程可以插进来。
    # 抢锁用的是 `O_EXCL` 建文件，那一步是原子的。
    if not sup.claim_lock():
        output(f"{PREFIX}已存在运行中的控制台。")
        output(f"{PREFIX}本控制台退出。")
        return
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
        # ⚠️ QQ 侧照报 —— "机器人在跑"和"QQ 通着"是两件事，
        # 2026-10-17 就是只看见前一件才没发现后一件已经断了。
        for line in sup.napcat_lines():
            output(f"{PREFIX}{line}")
        return prompt.confirm
    for line in sup.status().lines:
        output(f"{PREFIX}{line}")
    return None


def _run(sup: Supervisor, line: str) -> Outcome:
    """跑一条命令。**一条命令不许掀翻整个控制台。**

    ⚠️ 以前这里是裸调用。`manage` 抛一个异常出去（当时 `admin` 少写参数就会，
    见 `manage.cmd_list`）→ 一路穿到 `_loop` 外面 → `serve` 的 finally 收尾、
    **把机器人停掉** → 整个窗口带着一屏栈消失。

    敲错一句命令，机器人在群里下线 —— 代价和"报个错"完全不成比例。
    """
    try:
        return sup.handle(line)
    except Exception:  # noqa: BLE001 - 前端必须活下来，这是它的全部职责
        logger.exception("命令处理异常：%r", line)
        return Outcome(lines=("这条命令出错了（已忽略，详见日志）。",), ok=False)


def _loop(sup: Supervisor, stream, output, *, pending: str | None = None) -> None:
    for raw in stream:
        line = raw.strip()

        if pending is not None:
            if not line:
                # `[y/N]` 里的 N 是默认值 —— 直接回车就是"否"
                pending = None
                output(f"{PREFIX}已取消。")
                continue
            action, pending = pending, None
            if is_yes(action, line):
                try:
                    outcome = sup.confirmed(action)
                except Exception:  # noqa: BLE001
                    logger.exception("确认动作异常：%r", action)
                    outcome = Outcome(lines=("这个动作出错了（已忽略，详见日志）。",), ok=False)
            else:
                # ⚠️ **不认识的答复不能把这一行咽掉。** 以前无论敲什么
                # 都只回一句"已取消。"，于是 `end` 之后顺手敲的 `status`
                # 就凭空消失了 —— 正是所有者撞过的"把命令静默吃掉"。
                # 现在明确说取消，**并把它当普通命令再跑一遍**。
                output(f"{PREFIX}已取消。")
                outcome = _run(sup, line)
        else:
            if not line:
                continue
            outcome = _run(sup, line)

        for text in outcome.lines:
            output(f"{PREFIX}{text}")
        if outcome.confirm:
            output(f"{PREFIX}{outcome.question}")
            pending = outcome.confirm
        if outcome.stop:
            return


BANNER = """
─────────────────────────────────────────────
 saya 控制台--输入 help 查看命令。
 start / end / restart / quit / status
 napcat / cache
 [!] bot 会在此终端退出时同时终止
 [!] NapCat 不随控制台退出
─────────────────────────────────────────────"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="saya_bot 控制台：启停 + 改设置")
    parser.add_argument("--env", help=".env 的路径（默认 project_main/.env）")
    parser.add_argument(
        "--start-script", help="换个启动脚本（测试用）"
    )
    parser.add_argument(
        "--napcat-dir", help="NapCat 安装目录（默认 $NAPCAT_DIR 或 ~/Napcat）"
    )
    parser.add_argument(
        "command", nargs="*", help="不给就进交互模式；给了就跑一条命令然后退出"
    )
    args = parser.parse_args(argv)

    sup = Supervisor(
        env_path=Path(args.env).resolve() if args.env else DEFAULT_ENV,
        start_script=Path(args.start_script).resolve() if args.start_script else START_SCRIPT,
        napcat_dir=Path(args.napcat_dir).resolve() if args.napcat_dir else None,
    )

    # 非交互：跑一条就走（给脚本和测试用）。**不碰机器人、也不占锁** ——
    # 一条命令不该把别人的窗口踢掉。
    if args.command:
        name = args.command[0].lower()
        if name in INTERACTIVE_ONLY:
            # ⚠️ 这条以前是真的会做坏事的：`panel.py start` 会**真的 Popen
            # 一个机器人**，然后这个进程立刻退出 —— 机器人的 stdout 管道
            # 没人读了（它第一个 print 就 BrokenPipe），而 `SAYA_OWNER_PID`
            # 指向的那个进程已经死了，看护线程 5 秒内把它杀掉。
            # 屏幕上却写着"启动中"，退出码还是 0。
            print(f"{name} 只能在交互模式下做：python3 tools/panel.py")
            return 2
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
