"""内部领域模型 —— 内核唯一认识的数据结构。

刻意不依赖任何外部协议：adapter 层负责把 OneBot / 官方 API 的事件翻译成这里
的对象。这是换后端不改内核的前提（docs/01 ADR-001）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

MessageSource = Literal["group", "private"]


@dataclass(frozen=True, slots=True)
class ImageRef:
    """一张待识别的图片。

    url 可能是 QQ CDN 的临时链接，**会过期**，要在收到消息后尽快下载。
    """

    url: str
    local_path: str | None = None


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """归一化后的入站消息。

    mentioned_self 只表示"文本里 @ 了我"。私聊消息天然是对机器人说的，
    但不在此处置 True —— 那是触发策略的事，交给 core.trigger 判断。
    """

    source: MessageSource
    user_id: int
    text: str
    group_id: int | None = None
    images: tuple[ImageRef, ...] = ()
    mentioned_self: bool = False
    message_id: str | None = None
    received_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source == "group" and self.group_id is None:
            raise ValueError("群消息必须带 group_id")


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    """出站消息。target_id 是群号（群聊）或用户号（私聊）。"""

    source: MessageSource
    target_id: int
    text: str
    reply_to: str | None = None
