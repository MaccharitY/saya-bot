"""Adapter 统一接口 —— 内核只依赖这个抽象。

OneBot v11 / 官方 QQ 开放平台 / Mock 都是它的实现。见 docs/01 ADR-001。

接口是**同步**的（ADR-009）：真后端走 OneBot v11 的 HTTP 模式，
并发交给线程，不引入 asyncio。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from saya.domain import IncomingMessage, OutgoingMessage


class Adapter(ABC):
    @abstractmethod
    def events(self) -> Iterator[IncomingMessage]:
        """阻塞地产出入站消息，直到 `stop()` 被调用。实现为生成器。"""
        raise NotImplementedError

    @abstractmethod
    def send(self, message: OutgoingMessage) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        """让 `events()` 结束。默认无操作。"""
        return None

    def close(self) -> None:
        """释放连接等资源。默认无操作。"""
        return None
