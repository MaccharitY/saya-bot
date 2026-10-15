"""DNS 缓存 —— 绕开本机 WSL 里那个抽风的 DNS。

**背景（实测）**：本机 WSL 的域名解析在 1 秒和 10 秒之间**严格交替**
（Windows 上多个网卡各配了不同 DNS，其中一个不响应，轮到它就得等 10 秒超时）。
而我们的模型客户端**每条消息都要新建连接、都要解析一次域名**，
于是每条回复都被随机抽走 1~11 秒。真机第一次回复花了 12.2 秒，几乎全是这个税。

**这里做的事**：给 `socket.getaddrinfo` 包一层带 TTL 的缓存 ——
同一个域名在 TTL 内只解析一次，把"每条消息一次"降到"每个 TTL 一次"。

⚠️ 这是**对环境问题的绕行**，不是架构选择。系统的 DNS 修好后
（见 `docs/04-napcat-deployment.md`）它仍然有益（少一次解析就少一点延迟），
但不再是必需品；把它设成 0 即可关闭。
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

Resolver = Callable[..., list[Any]]


class DnsCache:
    """按 (host, port, 其余参数) 缓存解析结果，带 TTL 与线程安全。"""

    def __init__(self, resolver: Resolver, ttl_seconds: float) -> None:
        self._resolver = resolver
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[tuple, tuple[float, list[Any]]] = {}

    def getaddrinfo(self, host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        key = (host, port, args, tuple(sorted(kwargs.items())))
        now = time.monotonic()

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now - entry[0] < self._ttl:
                return entry[1]

        # 故意在锁外解析：解析可能慢，不该卡住别的线程
        result = self._resolver(host, port, *args, **kwargs)

        with self._lock:
            self._entries[key] = (now, result)
        return result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_installed_cache: DnsCache | None = None


def install(ttl_seconds: float = 300.0) -> DnsCache | None:
    """把 `socket.getaddrinfo` 换成带缓存的版本。重复调用无副作用。

    ttl_seconds <= 0 表示不装。返回装上的缓存对象（便于测试与排查）。
    """
    global _installed_cache
    if ttl_seconds <= 0:
        return None
    if _installed_cache is not None:
        return _installed_cache

    cache = DnsCache(socket.getaddrinfo, ttl_seconds)

    def cached_getaddrinfo(host, port, *args, **kwargs):
        return cache.getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = cached_getaddrinfo  # type: ignore[assignment]
    _installed_cache = cache
    return cache


def clear() -> None:
    """清空缓存。

    **在连接失败时调用** —— 万一解析到的 IP 已经失效（CDN 轮换），
    清一次让下次重新解析，比一直撞墙好。
    """
    if _installed_cache is not None:
        _installed_cache.clear()
