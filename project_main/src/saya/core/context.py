"""把记忆拼成**要注入的那一小块**上下文。

核心约束：**记忆再多也不能全进提示词。** 所以这里只拼三样，
而且每样都有长度上限：

    1. 最近几条群聊        —— 让对话接得上（最贵，但最有用）
    2. 当前话题            —— 一句话，省得把 20 个话题全塞进去
    3. **正在说话的那个人** —— 只查他一个，不是把 300 人的名单倒进去

⚠️ **三块的预算分法是刻意的**，不是按先后顺序整体截断：

    先算出「当前话题」和「说话人是谁」（这两块有硬上限，谁也不挤），
    剩下的预算**全部给最近消息**，并且**从最新往回装、装不下就停**。

反过来做（先把最近消息拼好、再整体从头砍）会先砍掉话题与说话人 ——
而这两块恰恰是**每一轮都该在**的：它总得知道「刚才在聊什么」和「对面是谁」，
少看两条最近消息却无所谓。这个顺序以前是反的，2026-10-16 修正。

**窗口大小不是固定的条数**：一次多取候选（`RECENT_FETCH`），
最终带几条由字数预算决定 —— 短消息就多带几条，长消息就少带几条。
固定"最近 10 条"在热闹的群里只覆盖几分钟，在冷清的群里却浪费预算。

⚠️ 产出的是**群友说过的话**，注入前必须过 `guard.wrap_history`。
"""

from __future__ import annotations

from datetime import datetime

from saya.core.memory import Memory, session_key
from saya.core.notebook import MAX_LINE_CHARS, Notebook
from saya.core.roles import ROLE_LABELS
from saya.domain import IncomingMessage

MAX_CHARS = 3000  # 整块硬上限，超了就砍（token 是真金白银）
NOTES_MAX_CHARS = 600  # 长期记忆那一块的上限

# ⚠️ **这三个是一组的，动一个要考虑另外两个**（2026-10-17）：
#
#     RECENT_FETCH              取多少条**候选**（下面这个）
#     memory.DEFAULT_MESSAGES_PER_SIDE   存储**每一侧**各留多少条（现在 50，合计上限 100）
#     MAX_CHARS                 最后按字数装多少（这个）
#
# 实测过：把 `MAX_CHARS` 从 1500 翻到 3000，装进去的从 27 行涨到 45 行；
# **再翻到 4500 一点不涨** —— 因为候选只有那么多，预算再大也没东西可装。
# 所以想让她记得更多，要**存储上限、取候选、预算一起抬**，只动一个会白动。
#
# ⚠️ 这里取 110 = **两倍存储上限**，也就是"把留着的全取回来，让预算去裁"。
# 存储上限改了，这里也要跟着改 —— 取少了的话，被取满的一方会把另一方挤掉，
# 而那正是这一轮要修的那个毛病。
RECENT_FETCH = 110  # 先取这么多候选，最终带几条由预算说了算

# ⚠️ **单条上限按说话人分开**（2026-10-17）。
# 原来只有 80 一个字，后果是**她的回复被砍掉的比例是别人的 20 倍**。
# 实测这个项目的库里：
#
#     她自己  52 条：中位 49 字，p90 126，最长 178 → 按 80 切会砍掉 21%
#     别人   118 条：中位  5 字，p90  18，最长  91 → 按 80 切只砍掉 1%
#
# （别人的中位数只有 5 个字 —— 群里大多数话就是「忘掉那条」这么长。）
#
# ⚠️ 后果不是"少看几个字"：**她看到的是自己被切一半的话**，于是没法核对
# "我到底说过什么"。实测撞到过：用户引用她真说过的一句，她回
# **「那句不是我说的」** —— 因为那一整句从来没完整进过上下文。
# 她的话还常常是两段（`\n\n` 分开），切断正好丢掉后半段。
#
# 所以两个上限都**设在实测最长之上**，让"完整"是常态：
# 护栏作用还在（防单条超长消息吃掉整块预算），但正常消息不会再被切。
# ⚠️ 以后数据长什么样会变，**发现又有截断就照实测重新定**，别当成常数。
PER_LINE_CHARS = 100  # 别人的话最多显示多长（实测最长 91）
BOT_LINE_CHARS = 200  # 她自己的话最多显示多长（实测最长 178）


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
    """返回要注入的历史上下文；没有可注入的内容时返回空串。

    ⚠️ **预算分法见模块头**：先留出话题与说话人，剩下的给最近消息。
    """
    now = now or datetime.now()
    session = session_key(message)

    # 这两块有硬上限（话题标题 60 字 + 概述 500，成员摘要 500），谁也挤不掉谁
    topic = ""
    speaker = _speaker_section(memory, message, now=now)
    if session is not None:
        entry = memory.current_topic(session)
        if entry is not None:
            topic = _topic_section(entry)

    budget = MAX_CHARS - len(topic) - len(speaker) - 4  # 4 = 两个分节换行

    recent = ""
    if session is not None and budget > 0:
        recent = _recent_section(
            memory,
            session,
            message,
            bot_qq=bot_qq,
            bot_name=bot_name,
            budget=budget,
        )

    # 展示顺序仍然是「最近消息 → 话题 → 说话人」（从上下文到当下，读着顺）
    return "\n\n".join(part for part in (recent, topic, speaker) if part)


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
    bot_qq: int | None,
    bot_name: str,
    budget: int,
) -> str:
    """最近消息。**两边各占一半额度**，各自从最新往回装。

    ⚠️ **为什么要分额度**（2026-10-17）：原来不分谁的，一律按时间从新往回装。
    话多的一方会把预算占满，话少的一方**整块消失** —— 实测撞到过：
    一个群里的候选**全是别人的**，她一条都没有，于是她完全想不起自己说过什么。
    （所有者原话：「只记得别人的话」。）

    存储侧已经改成两边各留 50 条（见 `memory._prune_messages`），
    但**只改存储不够** —— 注入时按时间装，她的旧话照样被挤出去。所以这里也要分。

    ⚠️ **用不完的那一半让给对方**，不浪费：她话少的时候别人照样能多占。
    """
    recent = memory.recent(session, limit=RECENT_FETCH)
    if not recent:
        return ""

    # 群聊与私聊的措辞分开 —— 让模型知道自己在什么场合说话
    title = "【群里最近说过的话】" if message.source == "group" else "【你们之前聊过】"
    allowance = budget - len(title)
    if allowance <= 0:
        return ""

    mine: list[tuple[int, str]] = []
    theirs: list[tuple[int, str]] = []
    for index, item in enumerate(recent):
        is_bot = bot_qq is not None and item.qq == bot_qq
        who = bot_name if is_bot else item.name or str(item.qq)
        cap = BOT_LINE_CHARS if is_bot else PER_LINE_CHARS
        # ⚠️ 先算好再插进 f-string：3.11 的 f-string 表达式里**不能有反斜杠**
        text = item.text.replace("\n", " ")[:cap]
        (mine if is_bot else theirs).append((index, f"{who}：{text}"))

    half = allowance // 2
    kept_mine = _take_newest(mine, half)
    kept_theirs = _take_newest(theirs, half)

    # 谁那半边没用完，剩下的给**还有存货**的那一边（两边都试，装到预算满为止）。
    # ⚠️ 这里必须**两边都再试一次** —— 只补给对方的话，先装的那一边会漏掉尾巴。
    rest = allowance - _width(kept_mine) - _width(kept_theirs)
    if rest > 0:
        kept_mine = _take_newest(mine, _width(kept_mine) + rest)
        rest = allowance - _width(kept_mine) - _width(kept_theirs)
        if rest > 0:
            kept_theirs = _take_newest(theirs, _width(kept_theirs) + rest)

    picked = sorted(kept_mine + kept_theirs)
    if not picked:
        return ""
    return "\n".join([title, *(line for _, line in picked)])


def _take_newest(items: list[tuple[int, str]], cap: int) -> list[tuple[int, str]]:
    """从**最新**往回装，装不下就停。返回带原序号的 [(序, 行)]。"""
    kept: list[tuple[int, str]] = []
    used = 0
    for entry in reversed(items):
        cost = len(entry[1]) + 1
        if used + cost > cap:
            break
        kept.append(entry)
        used += cost
    return kept


def _width(kept: list[tuple[int, str]]) -> int:
    return sum(len(line) + 1 for _, line in kept)


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
