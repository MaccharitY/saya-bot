"""NapCat（QQ 侧）的状态与启停。

⚠️⚠️ **判据是端口，不是进程。**

2026-10-17 实测撞过一次：QQ 进程好好活着，但账号在 10:21 掉线，
NapCat 的 OneBot 适配器（HTTP 服务端 `:3000`）根本没起来 ——
机器人一条消息也收不到。而当时 `start_saya.sh` 用
`pgrep -f "opt/QQ/qq"` 判活，看见进程在就报「运行中」，一路绿灯，
没人发现 QQ 那头已经断了。

所以「就绪」在这里只有一条判据：**3000 端口上有人在听**。
进程在、端口没开 = 没登录（或适配器没加载）。

⚠️ **面板退出不停它。** 机器人是"前端走了就跟着走"（ADR-026），
NapCat 正好相反 —— 它揣着 QQ 登录态，重启之后**不保证**能自动登录
（2026-10-17 10:56 那次重启就没登回去，一直等到人扫码）。
所以这里是"人叫它停才停"。

进程操作只走 POSIX（`pgrep` / `kill`）。本模块在 Windows 上一律返回
"问不出来"，不会真的动手 —— 见 `watchdog.pid_is_alive` 里那条：
`os.kill` 在 Windows 上是 `TerminateProcess`，不是"检查存在"。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ONEBOT_PORT = 3000
WEBUI_PORT = 6099

# 命令行里认它用这个串。`xvfb-run` 那层也含它，所以会被一并算进来 ——
# 那正是我们要的：停的时候连外壳一起停。
QQ_HINT = "opt/QQ/qq"

LOG_NAME = "manual-start.log"

# 进程起了多久之内还算「启动中」。超过这个数、端口还没开，就按没登录处理。
STARTUP_GRACE = 90

# 等端口的默认上限。够 NapCat 把适配器加载完，又不会让人干等。
DEFAULT_WAIT = 20.0

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def default_dir() -> Path:
    """NapCat 装在哪。`$NAPCAT_DIR` 优先（和 `start_saya.sh` 同一个约定）。"""
    raw = os.environ.get("NAPCAT_DIR", "").strip()
    return Path(raw) if raw else Path.home() / "Napcat"


@dataclass(frozen=True, slots=True)
class State:
    """查一次看到的事实。**不含判断** —— 判断留给显示层，免得两处口径不一致。"""

    installed: bool
    pids: tuple[int, ...]
    onebot: bool
    webui: bool
    elapsed: int | None = None  # 主进程跑了多少秒，拿不到就是 None

    @property
    def running(self) -> bool:
        return bool(self.pids)

    @property
    def ready(self) -> bool:
        """机器人能不能往 QQ 发东西。**只看 3000。**"""
        return self.onebot

    @property
    def starting(self) -> bool:
        """刚起来、还没就绪 —— 这时候催人去扫码是错的。"""
        return self.running and not self.ready and (
            self.elapsed is not None and self.elapsed < STARTUP_GRACE
        )


def binary(napcat_dir: Path | None = None) -> Path:
    return (napcat_dir or default_dir()) / "opt" / "QQ" / "qq"


def find_pids() -> tuple[int, ...]:
    """QQ 侧的进程号。拿不到就返回空 —— 不代表没在跑。"""
    if os.name == "nt":  # pragma: no cover - 只在 Windows 上才走到
        return ()
    try:
        done = subprocess.run(
            ["pgrep", "-f", QQ_HINT],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(sorted({int(token) for token in done.stdout.split() if token.isdigit()}))


def elapsed(pid: int) -> int | None:
    """这个进程跑了多少秒。拿不到返回 None（**不拿 0 代替** —— 0 会被当成刚启动）。"""
    if os.name == "nt":  # pragma: no cover
        return None
    try:
        done = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = done.stdout.strip()
    return int(text) if text.isdigit() else None


def port_open(port: int) -> bool:
    """端口上有没有人在听。纯 socket，不依赖 `ss` 这类外部工具。"""
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def probe(napcat_dir: Path | None = None) -> State:
    base = napcat_dir or default_dir()
    pids = find_pids()
    return State(
        installed=binary(base).exists(),
        pids=pids,
        onebot=port_open(ONEBOT_PORT),
        webui=port_open(WEBUI_PORT),
        elapsed=elapsed(pids[0]) if pids else None,
    )


def wait_ready(seconds: float, *, napcat_dir: Path | None = None) -> State:
    """等 3000 开起来。超时返回最后一次探测的结果，**不报错** ——
    没登录的情况等多久都不会好，报错没有意义，让调用方去看状态。"""
    deadline = time.monotonic() + max(0.0, seconds)
    state = probe(napcat_dir)
    while not state.ready and time.monotonic() < deadline:
        time.sleep(0.5)
        state = probe(napcat_dir)
    return state


def start(
    qq: str,
    *,
    napcat_dir: Path | None = None,
    wait: float = DEFAULT_WAIT,
) -> State:
    """确保有一个在跑，并等它把 3000 开起来。

    ⚠️ **进程在、端口没开时不再起一个**：两个实例会抢同一份 QQ 数据目录
    （`~/.config/QQ`），轻则互相踢登录，重则把配置写坏。这种情况只等、只报。
    """
    base = napcat_dir or default_dir()
    state = probe(base)
    if state.ready:
        return state
    if not state.installed:
        raise FileNotFoundError(str(binary(base)))

    if not state.running:
        log = base / "log" / LOG_NAME
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as handle:
            subprocess.Popen(
                ["xvfb-run", "-a", str(binary(base)), "--no-sandbox", "-q", qq],
                cwd=str(base),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    return wait_ready(wait, napcat_dir=base)


def stop(*, napcat_dir: Path | None = None, timeout: float = 10.0) -> State:
    """停掉。先 SIGTERM，不理再 SIGKILL。"""
    base = napcat_dir or default_dir()
    pids = find_pids()
    if not pids:
        return probe(base)
    if not _can_signal():
        raise OSError("停止 NapCat 仅支持 POSIX。")

    _signal(pids, signal.SIGTERM)
    if _wait_gone(timeout):
        return probe(base)
    _signal(find_pids() or pids, _SIGKILL)
    _wait_gone(5.0)
    return probe(base)


def restart(
    qq: str, *, napcat_dir: Path | None = None, wait: float = DEFAULT_WAIT
) -> State:
    stop(napcat_dir=napcat_dir)
    return start(qq, napcat_dir=napcat_dir, wait=wait)


def _can_signal() -> bool:
    """能不能发信号。Windows 上 `os.kill` 是 `TerminateProcess`，不是"通知"，
    所以那边一律不动手（同 `watchdog.pid_is_alive` 的理由）。
    单独拎出来是为了让测试能替换它 —— 测试跑在 Windows 上，但测的是 POSIX 的逻辑。
    """
    return os.name != "nt"


def _signal(pids: tuple[int, ...], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _wait_gone(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_pids():
            return True
        time.sleep(0.25)
    return not find_pids()


# ---------------------------------------------------------------- 显示


def describe(state: State, *, napcat_dir: Path | None = None) -> list[str]:
    """给人看的状态行。说明书体：书面、陈述事实，不口语、不拟人、不用「你」。

    续行缩进 7 格，对在「NapCat：」后面 —— 两个前端都只在行首加固定前缀，
    所以这个对齐在两边都成立。
    """
    base = napcat_dir or default_dir()
    if not state.installed and not state.running:
        return [f"NapCat：未安装（{binary(base)}）。"]

    if not state.running:
        return ["NapCat：未运行。"]

    pid = state.pids[0]
    if state.ready:
        return [f"NapCat：就绪（PID {pid}，OneBot 端口 {ONEBOT_PORT}）。"]

    if state.starting:
        return [
            f"NapCat：启动中（PID {pid}，{state.elapsed} 秒）"
            f"—— OneBot 端口 {ONEBOT_PORT} 未就绪。"
        ]

    lines = [
        f"NapCat：未就绪（PID {pid}）"
        f"—— OneBot 端口 {ONEBOT_PORT} 未监听，账号可能未登录。"
    ]
    if state.webui:
        lines.append(f"       WebUI：http://127.0.0.1:{WEBUI_PORT}/webui")
    else:
        lines.append("       WebUI 未监听，NapCat 可能尚未启动完成。")
    return lines


# ---------------------------------------------------------------- 命令行


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NapCat（QQ 侧）状态与启停")
    parser.add_argument("--napcat-dir", help="NapCat 安装目录（默认 $NAPCAT_DIR 或 ~/Napcat）")
    parser.add_argument("--qq", help="QQ 号（默认从 .env 的 SAYA_SELF_ID 读）")
    parser.add_argument(
        "--wait", type=float, default=DEFAULT_WAIT, help=f"等端口的上限秒数（默认 {DEFAULT_WAIT:.0f}）"
    )
    parser.add_argument("command", nargs="?", default="status",
                        choices=["status", "start", "stop", "restart", "ensure"])
    args = parser.parse_args(argv)

    base = Path(args.napcat_dir) if args.napcat_dir else None
    qq = args.qq or _qq_from_env()

    if args.command in ("start", "restart", "ensure"):
        if not qq:
            print("未设置 QQ 号：填写 .env 里的 SAYA_SELF_ID，或用 --qq 指定。", file=sys.stderr)
            return 2

    if args.command == "stop":
        state = stop(napcat_dir=base)
    elif args.command == "restart":
        state = restart(qq, napcat_dir=base, wait=args.wait)
    elif args.command in ("start", "ensure"):
        state = start(qq, napcat_dir=base, wait=args.wait)
    else:
        state = probe(base)

    for line in describe(state, napcat_dir=base):
        print(line)
    return 0 if state.ready else 1


def _qq_from_env() -> str:
    """从机器人那份 `.env` 里取 QQ 号。读不到就返回空串。"""
    try:
        from saya.envfile import EnvFile
    except ImportError:  # pragma: no cover
        return ""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        return (EnvFile(env_path).get("SAYA_SELF_ID") or "").strip()
    except (OSError, ValueError):
        return ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
