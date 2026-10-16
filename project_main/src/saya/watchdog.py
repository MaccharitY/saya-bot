"""盯住"养我的那个进程"还在不在。没了就自己走。

⚠️ **为什么需要反方向的检查**：`tools/panel.py` 在**正常退出**时会走 `finally`
收尾，顺手把机器人停掉。但**窗口被直接叉掉**时那个 `finally` 根本跑不到 ——
WSL 侧的进程被强杀了，机器人却因为开着 `start_new_session=True`（自己的进程组）
收不到 SIGHUP，于是活得好好的。

结果就是**无头后端**：端口被占、没人看日志、下次启动还被 `start_saya.sh`
挡回来一句"已经有一个在跑了"。这个坑已经撞过两次了（见 ADR-026）。

**光靠前端自觉不够** —— 前端被强杀时它什么都做不了。所以机器人自己也得看着点：
发现"养我的那个进程"没了，就**自己发一个 SIGINT**，走和 Ctrl+C 完全一样的
清理路径（`finally` 里的 `pipeline.stop()`）。

⚠️ **只有被前端拉起时才有这个检查**：面板会把它的 PID 放进环境变量
（`SAYA_OWNER_PID`）。**无头启动**（`start_saya.bat`）时那个变量不存在 ——
那种情况下机器人**本来就该独立活着**，没有任何人盯着它是对的。
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import threading
import time
from typing import Callable

logger = logging.getLogger("saya.watchdog")

OWNER_PID_ENV = "SAYA_OWNER_PID"
DEFAULT_INTERVAL = 5.0


def is_zombie(pid: int) -> bool:
    """⚠️⚠️ **僵尸进程对 `kill(pid, 0)` 是"活着"的。**

    父进程还没 `wait()` 回收时，进程会以僵尸形态赖在进程表里 ——
    `/proc/<pid>` 还在、`kill(pid, 0)` **成功返回**，但它**早就死了**。

    **实测撞过**（2026-10-16）：强杀面板之后，它的父进程还在 sleep、没回收它，
    于是面板成了僵尸，看护永远不触发 —— **机器人一直活着，正是要防的那件事**。
    只查"PID 还在不在"是不够的，得**看一眼状态**。

    Linux 的 `/proc/<pid>/stat` 第三个字段是状态，`Z` 就是僵尸。
    格式是 `pid (comm) STATE ...`，而 `comm` 里可能有空格和括号 ——
    所以从**最后一个右括号**之后取，不能用 split()。
    """
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False  # 没有 /proc（非 Linux）或读不到 —— 交给 kill 去判断
    _, _, tail = stat.rpartition(")")
    fields = tail.split()
    return bool(fields) and fields[0] == "Z"


def owner_is_alive(pid: int) -> bool:
    """那个进程还在吗。

    ⚠️⚠️ 和 `tools/panel.py` 里那个一样：`os.kill(pid, 0)` **只在 POSIX 上**
    是"检查存在"。Windows 上它会走 `TerminateProcess` **真把那个进程杀掉**。
    机器人跑在 WSL 里，正常走不到 Windows 这条 —— 但测试跑在 Windows 上，
    所以护栏必须有。
    """
    if os.name == "nt":  # pragma: no cover - 只在 Windows 上才走到
        return True  # 判不了就当作活着，**绝不在这里动手**
    if is_zombie(pid):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在，只是不归我们管
    except OSError:
        return False
    return True


def watch_owner(
    owner_pid: int,
    *,
    interval: float = DEFAULT_INTERVAL,
    on_lost: Callable[[], None] | None = None,
) -> threading.Thread:
    """开一个守护线程盯着 `owner_pid`。没了就调 `on_lost`（默认直接退出）。

    ⚠️⚠️ **不能靠 `signal.raise_signal(SIGINT)`** —— 这条是实测撞出来的，
    而且第一版就是那么写的，结果**机器人一直不退**：

        `raise_signal` 把信号发给**调用它的那个线程**（看护线程），
        而主线程正阻塞在事件循环的 `select()` 里 ——
        **信号送到别的线程，主线程的 syscall 不会被打断**，
        于是那个 `KeyboardInterrupt` 永远不抛。

    （Ctrl+C 好使是因为信号发给**整个进程**，内核会挑一个线程送。）

    所以默认动作是**在本地收尾然后 `os._exit`** —— 我们本来就要走了，
    没必要跟主线程客气。`on_lost` 留给调用方接自己的清理。
    """

    def loop() -> None:
        while True:
            time.sleep(interval)
            if not owner_is_alive(owner_pid):
                logger.warning(
                    "拉起我的那个进程（PID %d）已经不在了 —— 机器人跟着退出，"
                    "免得留下没人管、又占着端口的实例",
                    owner_pid,
                )
                try:
                    (on_lost or _exit_now)()
                finally:
                    _exit_now()
                return

    thread = threading.Thread(target=loop, name="saya-watchdog", daemon=True)
    thread.start()
    logger.info("已开始盯着拉起我的进程（PID %d）", owner_pid)
    return thread


def _exit_now() -> None:
    """把缓冲刷掉再立刻结束整个进程。

    ⚠️ 用 `os._exit` 而不是 `sys.exit`：`sys.exit` 在非主线程里只结束**那个线程**，
    主线程照旧卡在事件循环里 —— 那等于什么都没做。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(0)


def start_if_owned(
    *,
    interval: float = DEFAULT_INTERVAL,
    on_lost: Callable[[], None] | None = None,
) -> threading.Thread | None:
    """环境变量里有 PID 才盯 —— 没有就是无头启动，不管。"""
    raw = os.environ.get(OWNER_PID_ENV, "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        logger.warning("%s 不是数字（%r），不启用看护", OWNER_PID_ENV, raw)
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    return watch_owner(pid, interval=interval, on_lost=on_lost)
