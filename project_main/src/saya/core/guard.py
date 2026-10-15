"""防注入护栏（FR-4）。

三道防线：

1. **分区隔离** —— 群友内容一律包进明确的边界标记，与系统提示物理分开
2. **输入检测** —— 命中常见注入模式时标记为可疑，由上层决定降级策略
3. **输出过滤** —— 回复里若出现密钥或系统提示片段，拦下来

⚠️ 这不是"防住一切"的银弹，目标是**提高攻击成本、把明显的攻击挡在外面**。
真正的安全靠最小权限：工具白名单、不把敏感信息放进提示、不给越权工具。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 用不常见的定界符，避免群友在消息里凑巧拼出边界
UNTRUSTED_OPEN = "⟦群友消息开始⟧"
UNTRUSTED_CLOSE = "⟦群友消息结束⟧"

_INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "忽略指令",
        re.compile(r"(忽略|无视|忘掉|忘记|不要理会)[^。\n]{0,8}(上面|以上|前面|之前|先前|所有)[^。\n]{0,8}(指令|要求|规则|设定|提示|话)"),
    ),
    (
        "ignore_instructions",
        re.compile(
            r"\b(ignore|disregard|forget)\b[^.\n]{0,24}\b(previous|above|prior|earlier|all)\b[^.\n]{0,24}\b(instruction|prompt|rule|direction)",
            re.I,
        ),
    ),
    (
        "索取系统提示",
        # 中文两种语序都常见：「重复系统提示」与「系统提示重复一遍」，都要覆盖
        re.compile(
            r"(?:输出|告诉我|重复|复述|打印|展示|念|说一遍)"
            r"[^。\n]{0,8}"
            r"(?:系统提示|系统指令|你的设定|你的提示词|人格设定)"
            r"|"
            r"(?:系统提示|系统指令|你的设定|你的提示词|人格设定)"
            r"[^。\n]{0,8}"
            r"(?:输出|告诉我|重复|复述|打印|展示|念|说一遍)"
        ),
    ),
    (
        "reveal_prompt",
        re.compile(
            r"\b(repeat|print|show|reveal|output|dump)\b[^.\n]{0,24}\b(system\s?prompt|initial\s?prompt|your\s?instructions)",
            re.I,
        ),
    ),
    (
        "角色覆盖",
        re.compile(r"(你现在是|从现在起你是|从今天起你是|你现在扮演|假装你是|假装自己是)[^。\n]{0,16}(没有(任何)?限制|不受(任何)?约束|没有规则|不用遵守)"),
    ),
    (
        "越狱模式",
        re.compile(r"(开发者模式|开发人员模式|越狱模式|\bDAN\s?mode\b|\bjailbreak\b|\bdeveloper\s?mode\b)", re.I),
    ),
)

_SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("密钥", re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}")),
    ("密钥", re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{12,}")),
)

_PROMPT_LEAK_MIN_CHARS = 24


@dataclass(frozen=True, slots=True)
class GuardResult:
    suspicious: bool
    reason: str


def wrap_untrusted(text: str) -> str:
    """把不可信内容包进边界标记。

    这是分区隔离的**唯一入口** —— 任何群友文本进提示词之前都必须过这里，
    不要手工拼字符串，否则迟早有人漏掉一处。
    """
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"


def wrap_message(sender_name: str, sender_id: int, text: str) -> str:
    """把**整条消息（含发送者标识）**包成不可信区。

    ⚠️ 发送者名字也是**用户可控**的：QQ 群名片谁都能改，有人可以把名片改成
    「系统：忽略以上指令」再说话。所以它必须和正文一起进不可信区，
    **绝不能单独拼在提示词里当"可信标签"用** —— 那等于自己开了个注入口子。
    """
    label = f"{sender_name}（{sender_id}）" if sender_name else str(sender_id)
    return f"{UNTRUSTED_OPEN}\n[发送者] {label}\n[内容] {text}\n{UNTRUSTED_CLOSE}"


def detect_injection(text: str) -> GuardResult:
    """检测注入企图。命中返回可疑标记，未命中返回 safe。"""
    for name, pattern in _INJECTION_RULES:
        if pattern.search(text):
            return GuardResult(True, name)
    return GuardResult(False, "safe")


def screen_output(reply: str, *, system_prompt: str = "") -> tuple[str, str | None]:
    """发送前的最后一道过滤。

    返回 (可发送文本, 拦截原因)。拦截原因非空时，文本已被替换为安全提示。
    """
    for name, pattern in _SECRET_RULES:
        if pattern.search(reply):
            return f"[已拦截：回复中疑似包含{name}]", name

    if system_prompt:
        for fragment in _prompt_fragments(system_prompt):
            if fragment in reply:
                return "[已拦截：回复疑似泄露系统提示]", "系统提示泄露"

    return reply, None


def _prompt_fragments(system_prompt: str) -> list[str]:
    """把系统提示切成足够长的片段，用于检测回复是否在复述它。

    只取长片段：短句（如"你必须遵守"）本来就可能正常出现在闲聊里，抓它只会误伤。
    """
    fragments: list[str] = []
    for line in system_prompt.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if len(cleaned) >= _PROMPT_LEAK_MIN_CHARS:
            fragments.append(cleaned)
    return fragments
