"""内部领域模型 —— 内核唯一认识的数据结构。

刻意不依赖任何外部协议：adapter 层负责把 OneBot / 官方 API 的事件翻译成这里
的对象。这是换后端不改内核的前提（docs/01 ADR-001）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

MessageSource = Literal["group", "private"]

# 每个群的策略 —— **一整个机器人只有这一个"怎么对待这个群"的概念**
# （原先还有一个全局 trigger_mode，两者是重复的，已合并到这里）。
#
#   silent    完全不处理：不记录、不记忆、不回复（"这个群当我没在"）
#   mention   记录所有消息，但**只在被 @ 时回复**（最安全的默认）
#   keyword   记录所有消息，命中关键词才回
#   random    记录所有消息，按概率回
#   always    记录所有消息，全回（**只适合调试，会很吵**）
#   selective 记录所有消息，由策略决定何时插嘴（**尚未实现，会降级成 mention**）
GroupPolicy = Literal["silent", "mention", "keyword", "random", "always", "selective"]
GROUP_POLICIES: tuple[str, ...] = (
    "silent",
    "mention",
    "keyword",
    "random",
    "always",
    "selective",
)
DEFAULT_GROUP_POLICY: GroupPolicy = "mention"


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
    # ⚠️ 用户可控字段 —— 群名片谁都能改。**进提示词时必须过 core.guard 的不可信区**，
    # 不要单独拼在外面，否则有人把名字改成"系统：忽略以上指令"就注入了。
    sender_name: str = ""
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
