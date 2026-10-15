"""OneBot v11 协议解析：外部事件 → 内部领域模型。

M1 会在这里补上 WebSocket 客户端；解析逻辑先落地，是为了让 Mock 夹具与真后端
共用同一份翻译代码 —— 这样夹具喂的数据与 NapCat 喂的一模一样（ADR-008）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from saya.domain import ImageRef, IncomingMessage

_TEXT = "text"
_IMAGE = "image"
_AT = "at"
_FACE = "face"


def parse_event(raw: dict[str, Any], self_id: int | None = None) -> IncomingMessage | None:
    """把一条 OneBot v11 事件翻译成 IncomingMessage。

    非消息事件（心跳等 meta_event、通知、请求）返回 None，由调用方丢弃。
    """
    if raw.get("post_type") != "message":
        return None

    message_type = raw.get("message_type")
    if message_type not in ("group", "private"):
        return None

    me = str(self_id if self_id is not None else raw.get("self_id", ""))

    text_parts: list[str] = []
    images: list[ImageRef] = []
    mentioned = False

    segments = raw.get("message")
    if isinstance(segments, str):
        # 部分实现把 message 直接给成 CQ 码字符串，退化处理
        text_parts.append(segments)
    elif isinstance(segments, list):
        for seg in segments:
            seg_type = seg.get("type")
            data = seg.get("data") or {}
            if seg_type == _TEXT:
                text_parts.append(str(data.get("text", "")))
            elif seg_type == _IMAGE:
                url = data.get("url") or data.get("file")
                if url:
                    images.append(ImageRef(url=str(url)))
                text_parts.append("[图片]")
            elif seg_type == _AT:
                qq = str(data.get("qq", ""))
                if me and qq == me:
                    mentioned = True
                    text_parts.append("[@我]")
                else:
                    text_parts.append(f"[@{qq}]")
            elif seg_type == _FACE:
                text_parts.append("[表情]")
    elif raw.get("raw_message"):
        text_parts.append(str(raw["raw_message"]))

    timestamp = raw.get("time")
    received_at = datetime.fromtimestamp(int(timestamp)) if timestamp else None

    message_id = raw.get("message_id")

    return IncomingMessage(
        source=message_type,
        group_id=raw.get("group_id"),
        user_id=int(raw.get("user_id", 0)),
        text="".join(text_parts).strip(),
        images=tuple(images),
        mentioned_self=mentioned,
        message_id=str(message_id) if message_id is not None else None,
        received_at=received_at,
        raw=raw,
    )
