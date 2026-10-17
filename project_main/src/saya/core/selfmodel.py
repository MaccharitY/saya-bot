"""她对自己是怎么跑的认识 —— **由能力清单生成，不手写**。

⚠️ **每个数字和开关都来自 `Config` 与实际接上的服务**（ADR-037）。
手写的那种自我介绍，会在某次改动之后**悄悄变成假话**；而"她对自己的描述是假的"
比"她不知道"更糟 —— 她会照着自己写错的那份回答，语气还很确定。

⚠️ 这一节**只说事实**：不劝、不解释动机、不评价。它进系统提示，给模型读。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Capabilities:
    """当前这一份配置下，她实际拥有的东西。"""

    memory_enabled: bool = False
    messages_per_side: int = 0
    topics_per_session: int = 0
    notebook_enabled: bool = False
    vision_enabled: bool = False
    vision_route: str = ""
    wiki_enabled: bool = False
    corpus_available: bool = False
    tool_names: tuple[str, ...] = ()


def build(caps: Capabilities) -> str:
    lines = ["—— 你的记忆与能力（这一节给你自己看，不要对群友复述）——", ""]
    lines.extend(_memory(caps))
    lines.append("")
    lines.extend(_abilities(caps))
    lines.append("")
    lines.append(
        "问到办不到的事，直接说办不到。不要猜「也许可以」，"
        "也不要暗示已经被清掉的内容还能找回来 —— 找不回来。"
    )
    return "\n".join(lines)


def _memory(caps: Capabilities) -> list[str]:
    if not caps.memory_enabled:
        return ["会话记忆未启用：看不到之前的聊天内容，也不记得谁说过什么。"]

    out = [
        "你的记忆分两部分：",
        f"· 会话记忆：每个会话里，你和别人各留最近 {caps.messages_per_side} 条消息；",
        "  更早的会被删掉，你看不到，也没法恢复。",
    ]
    if caps.topics_per_session:
        out.append(f"· 话题概述：每个会话另存 {caps.topics_per_session} 条话题摘要。")
    out.append(
        "· 长期记忆：主人和管理员明确让你记住的事，单独保存，不会被自动清理。"
        if caps.notebook_enabled
        else "· 长期记忆：未启用。"
    )
    out.extend(
        [
            "",
            "清缓存会删除会话记忆和收到的图片。删除后无法恢复；"
            "长期记忆和设置不受影响。",
        ]
    )
    return out


def _abilities(caps: Capabilities) -> list[str]:
    out = ["你的能力："]
    if caps.vision_enabled:
        out.append(f"· 看图：可以（当前视图模式 {caps.vision_route}）。")
    else:
        out.append("· 看图：未启用。")

    sources = []
    if caps.wiki_enabled:
        sources.append("萌娘百科")
    if caps.corpus_available:
        sources.append("本地剧情语料")
    out.append("· 查资料：" + ("、".join(sources) if sources else "未接入") + "。")

    if caps.tool_names:
        out.append("· 可调用的工具：" + "、".join(caps.tool_names) + "。")
    else:
        out.append("· 可调用的工具：没有。")
    out.append("· 没有的能力：语音、视频、文件、按时间主动发言。")
    return out
