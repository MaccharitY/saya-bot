"""Adapter 统一接口 —— 内核只依赖这个抽象。

OneBot v11 / 官方 QQ 开放平台 / Mock 都是它的实现。见 docs/01 ADR-001。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from saya.domain import IncomingMessage, OutgoingMessage


class Adapter(ABC):
    @abstractmethod
    def events(self) -> AsyncIterator[IncomingMessage]:
        """异步产出入站消息（实现为 async generator）。"""
        raise NotImplementedError

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        """释放连接等资源。默认无操作。"""
        return None
