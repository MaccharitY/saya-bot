"""把记忆拼成**要注入的那一小块**上下文。

核心约束：**记忆再多也不能全进提示词。** 所以这里只拼三样，
而且每样都有长度上限：

    1. 最近几条群聊        —— 让对话接得上（最贵，但最有用）
    2. 当前话题            —— 一句话，省得把 20 个话题全塞进去
    3. **正在说话的那个人** —— 只查他一个，不是把 300 人的名单倒进去

⚠️ 产出的是**群友说过的话**，注入前必须过 `guard.wrap_history`。
"""

from __future__ import annotations

from datetime import datetime

from saya.core.memory import Memory
from saya.domain import IncomingMessage

MAX_CHARS = 1500  # 整块硬上限，超了就砍（token 是真金白银）


def _ago(when: datetime, now: datetime) -> str:
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def build_history(
    memory: Memory,
    message: IncomingMessage,
    *,
    bot_qq: int | None = None,
    bot_name: str = "",
    now: datetime | None = None,
) -> str:
    """返回要注入的历史上下文；没有可注入的内容时返回空串。"""
    now = now or datetime.now()
    sections: list[str] = []

    if message.source == "group" and message.group_id is not None:
        sections.extend(
            _group_sections(memory, message, now=now, bot_qq=bot_qq, bot_name=bot_name)
        )

    speaker = _speaker_section(memory, message, now=now)
    if speaker:
        sections.append(speaker)

    block = "\n\n".join(section for section in sections if section)
    if len(block) > MAX_CHARS:
        block = block[:MAX_CHARS] + "\n…（记忆太长，已截断）"
    return block


def _group_sections(
    memory: Memory,
    message: IncomingMessage,
    *,
    now: datetime,
    bot_qq: int | None,
    bot_name: str,
) -> list[str]:
    assert message.group_id is not None
    sections: list[str] = []

    recent = memory.recent(message.group_id)
    if recent:
        lines = ["【群里最近说过的话】"]
        for item in recent:
            who = bot_name if (bot_qq is not None and item.qq == bot_qq) else item.name or str(item.qq)
            text = item.text.replace("\n", " ")[:80]
            lines.append(f"{who}：{text}")
        sections.append("\n".join(lines))

    topic = memory.current_topic(message.group_id)
    if topic is not None:
        started = topic.started_at.strftime("%m-%d %H:%M")
        head = topic.title or "（没起名）"
        parts = [f"【当前话题】{head}（从 {started} 开始）"]
        if topic.summary:
            parts.append(f"话题概述：{topic.summary}")
        sections.append("\n".join(parts))

    return sections


def _speaker_section(memory: Memory, message: IncomingMessage, *, now: datetime) -> str:
    """只查**正在说话的那个人** —— 这是"记忆 300 人却不炸上下文"的关键。"""
    info = memory.member(message.user_id)
    if info is None:
        return ""

    lines = [
        f"【正在说话的人】{info.name or message.user_id}"
        f"（活跃度 {info.activity}，上次说话 {_ago(info.last_seen, now)}）"
    ]
    if info.note:
        lines.append(f"你记得他：{info.note}")
    return "\n".join(lines)
