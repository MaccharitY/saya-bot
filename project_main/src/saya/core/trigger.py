"""触发判定：这条消息该不该回（FR-2）。

判定顺序 —— 先否决，再匹配：
    去重（同一条消息不回两次，所有来源通用）→ 私聊直接回 →
    群黑名单 → 群白名单 → 限流 → 触发模式

去重必须排在私聊之前：OneBot 在连接闪断后会重投递，私聊同样会重复。

随机数与时钟都可注入，测试不依赖真实时间和真实随机。
"""

from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from saya.config import Config
from saya.domain import IncomingMessage

WINDOW_SECONDS = 60.0
DEDUP_MEMORY = 256


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    """respond 决定要不要回；reason 进埋点，用于事后调参。"""

    respond: bool
    reason: str


class Trigger:
    def __init__(
        self,
        config: Config,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._rng = rng or random.Random()
        self._clock = clock
        self._recent: dict[int, deque[float]] = {}
        self._answered: deque[str] = deque(maxlen=DEDUP_MEMORY)
        self._answered_set: set[str] = set()

    def should_respond(self, message: IncomingMessage) -> TriggerDecision:
        if message.message_id is not None and message.message_id in self._answered_set:
            return TriggerDecision(False, "duplicate")

        if message.source == "private":
            return TriggerDecision(True, "private")

        group_id = message.group_id
        if group_id is None:
            return TriggerDecision(False, "no_group_id")

        if group_id in self._config.group_denylist:
            return TriggerDecision(False, "group_denied")

        allowlist = self._config.group_allowlist
        if allowlist and group_id not in allowlist:
            return TriggerDecision(False, "group_not_allowed")

        if self._rate_limited(group_id):
            return TriggerDecision(False, "rate_limited")

        mode = self._config.trigger_mode
        if mode == "mention":
            matched = message.mentioned_self
        elif mode == "keyword":
            keywords = self._config.trigger_keywords
            matched = any(word in message.text for word in keywords)
        elif mode == "random":
            matched = self._rng.random() < self._config.reply_probability
        elif mode == "always":
            matched = True
        else:
            raise ValueError(f"未知的触发模式：{mode}")

        return TriggerDecision(matched, f"{'hit' if matched else 'skip'}:{mode}")

    def note_answered(self, message: IncomingMessage) -> None:
        """记录"这条已经回了"。限流与去重都依赖它，回复成功后必须调用。"""
        if message.message_id is not None:
            if len(self._answered) == self._answered.maxlen:
                self._answered_set.discard(self._answered[0])
            self._answered.append(message.message_id)
            self._answered_set.add(message.message_id)

        if message.source == "group" and message.group_id is not None:
            self._recent.setdefault(message.group_id, deque()).append(self._clock())

    def _rate_limited(self, group_id: int) -> bool:
        limit = self._config.rate_limit_per_group_per_min
        if limit <= 0:
            return True

        history = self._recent.get(group_id)
        if not history:
            return False

        now = self._clock()
        while history and now - history[0] > WINDOW_SECONDS:
            history.popleft()
        return len(history) >= limit
