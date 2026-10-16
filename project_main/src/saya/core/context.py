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

from saya.core.memory import Memory, session_key
from saya.core.notebook import MAX_LINE_CHARS, Notebook
from saya.core.roles import ROLE_LABELS
from saya.domain import IncomingMessage

MAX_CHARS = 1500  # 整块硬上限，超了就砍（token 是真金白银）
NOTES_MAX_CHARS = 600  # 长期记忆那一块的上限


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

    session = session_key(message)
    if session is not None:
        sections.extend(
            _recent_section(memory, session, message, now=now, bot_qq=bot_qq, bot_name=bot_name)
        )
        topic = memory.current_topic(session)
        if topic is not None:
            sections.append(_topic_section(topic))

    speaker = _speaker_section(memory, message, now=now)
    if speaker:
        sections.append(speaker)

    block = "\n\n".join(section for section in sections if section)
    if len(block) > MAX_CHARS:
        block = block[:MAX_CHARS] + "\n…（记忆太长，已截断）"
    return block


def build_notes(notebook: Notebook, message: IncomingMessage) -> str:
    """拼出长期记忆那一块；没有就返回空串。

    **独立成一块，不并进群聊记录**：它跟闲聊的来源不同（经过身份筛选），
    混在一起模型就分不出"随口一提"和"特意交代"。

    ⚠️ 内容仍然是用户打的字，注入前必须过 `guard.wrap_notes`（在 pipeline 里做）。
    """
    notes = notebook.for_prompt(message.user_id)
    if not notes:
        return ""

    title = "【他们让你记着的事】"
    lines = [title]
    used = len(title)
    for note in notes:
        who = ROLE_LABELS.get(note.subject_role, note.subject_role or "某人")
        text = note.content.replace("\n", " ")[:MAX_LINE_CHARS]
        line = f"- {who}（{note.subject_qq}）说过：{text}"
        if used + len(line) > NOTES_MAX_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def _recent_section(
    memory: Memory,
    session: str,
    message: IncomingMessage,
    *,
    now: datetime,
    bot_qq: int | None,
    bot_name: str,
) -> list[str]:
    recent = memory.recent(session)
    if not recent:
        return []

    # 群聊与私聊的措辞分开 —— 让模型知道自己在什么场合说话
    title = "【群里最近说过的话】" if message.source == "group" else "【你们之前聊过】"
    lines = [title]
    for item in recent:
        who = bot_name if (bot_qq is not None and item.qq == bot_qq) else item.name or str(item.qq)
        text = item.text.replace("\n", " ")[:80]
        lines.append(f"{who}：{text}")
    return ["\n".join(lines)]


def _topic_section(topic) -> str:
    started = topic.started_at.strftime("%m-%d %H:%M")
    head = topic.title or "（没起名）"
    parts = [f"【当前话题】{head}（从 {started} 开始）"]
    if topic.summary:
        parts.append(f"话题概述：{topic.summary}")
    return "\n".join(parts)


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
