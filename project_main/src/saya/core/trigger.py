"""触发判定：这条消息**该不该记**、**该不该回**。

判定顺序 —— 先否决，再匹配：

    去重（所有来源通用）
      → 私聊：不在白名单里直接不理（结构性防注入）
      → 群：查该群的策略 → silent 直接结束
      → 限流 → 策略匹配

**感知与回复是两件事**（`TriggerDecision.observe` / `.respond`）：
`mention` 策略的群里，机器人**记录所有消息**（供记忆与活跃度用），
但**只在被 @ 时开口**。否则它只在被叫到时才"看见世界"，
记忆与活跃度根本无从统计。

私聊白名单（主人 / 管理 / 特别白名单）是**结构性**防注入：
私聊是陌生人能直接对机器人下指令的地方，白名单把这块攻击面直接关掉。
它比正则检测可靠得多 —— 正则会被绕过，白名单不会。

时钟可注入，测试不依赖真实时间。
"""

from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from saya.config import Config
from saya.core.roles import Roles
from saya.domain import IncomingMessage

WINDOW_SECONDS = 60.0
DEDUP_MEMORY = 256


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    """两个独立的开关。reason 进埋点，用于事后调参。"""

    observe: bool
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
        self._roles = Roles.from_config(config)
        self._rng = rng or random.Random()
        self._clock = clock
        self._recent: dict[int, deque[float]] = {}
        self._answered: deque[str] = deque(maxlen=DEDUP_MEMORY)
        self._answered_set: set[str] = set()
        # 关键词命中后的"跟随窗口"：(群号, 用户) → (还剩几条, 最后一次活动时刻)
        self._following: dict[tuple[int, int], tuple[int, float]] = {}

    def reconfigure(self, config: Config) -> None:
        """换配置（群策略 / 关键词 / 概率 / 限流都从这里读）。

        ⚠️ `Roles` 也要重建 —— 群策略里 `mention` 的判定用得上身份。
        由 `Pipeline.reconfigure` 统一调用，别处不要单独调。
        """
        self._config = config
        self._roles = Roles.from_config(config)

    # ---- 判定 ----

    def evaluate(self, message: IncomingMessage) -> TriggerDecision:
        if message.message_id is not None and message.message_id in self._answered_set:
            return TriggerDecision(False, False, "duplicate")

        if message.source == "private":
            return self._judge_private(message)

        return self._judge_group(message)

    def _judge_private(self, message: IncomingMessage) -> TriggerDecision:
        if not self.is_allowed_private(message.user_id):
            # 不说话，但**也不记录**：陌生人的私聊内容没必要留
            return TriggerDecision(False, False, "private_not_allowed")
        return TriggerDecision(True, True, "private_allowed")

    def _judge_group(self, message: IncomingMessage) -> TriggerDecision:
        group_id = message.group_id
        if group_id is None:
            return TriggerDecision(False, False, "no_group_id")

        policy = self.group_policy(group_id)
        if policy == "silent":
            # 不记不回 —— "这个群当我没在"
            return TriggerDecision(False, False, "group_silent")

        # 到这儿就说明要记录（感知），回不回另说
        if self._rate_limited(group_id):
            return TriggerDecision(True, False, "rate_limited")

        matched, tag = self._match(message, policy)
        return TriggerDecision(True, matched, f"{'hit' if matched else 'skip'}:{tag}")

    def _match(self, message: IncomingMessage, policy: str) -> tuple[bool, str]:
        config = self._config
        if policy == "mention":
            return message.mentioned_self, "mention"
        if policy == "keyword":
            if any(word in message.text for word in config.trigger_keywords):
                self._open_follow_window(message)
                return True, "keyword"
            # 关键词没中，但可能是**刚才叫过它的人在接着说话**
            if self._within_follow_window(message):
                return True, "keyword-follow"
            return False, "keyword"
        if policy == "random":
            return self._rng.random() < config.reply_probability, "random"
        if policy == "always":
            return True, "always"
        if policy == "selective":
            # ⚠️ 尚未实现。**降级成 mention 而不是静默** ——
            # 静默会让人以为"它坏了"，降级至少还能用，且 reason 里写明了。
            return message.mentioned_self, "selective→mention（未实现，已降级）"
        raise ValueError(f"未知的群策略：{policy}")

    # ---- 关键词的"跟随窗口" ----
    #
    # 为什么需要：叫人只用叫一次。说了「saya 这个怎么弄」之后，紧接着的
    # 「那这个呢」「还是不行」不该再要求他每条都带上名字。
    #
    # ⚠️ **按人记、按会话隔离**：窗口挂在 (群号, 用户) 上，
    # 所以群里别人说话不会消耗它，也不会蹭到别人的窗口。
    # ⚠️ **只有 keyword 策略用得上**：mention 每次 @ 都会回，
    # random / always 本来就会回，都不需要这个。

    def _open_follow_window(self, message: IncomingMessage) -> None:
        count = self._config.keyword_followup
        if count > 0 and message.group_id is not None:
            self._following[(message.group_id, message.user_id)] = (count, self._clock())

    def _within_follow_window(self, message: IncomingMessage) -> bool:
        if message.group_id is None:
            return False
        limit = self._config.keyword_followup
        seconds = self._config.keyword_followup_seconds
        if limit <= 0 or seconds <= 0:
            return False

        key = (message.group_id, message.user_id)
        state = self._following.get(key)
        if state is None:
            return False

        remaining, last_seen = state
        now = self._clock()
        # ⚠️ 超时也要清 —— 早上叫过它，晚上回来随口说五句话不该被它接话。
        if remaining <= 0 or now - last_seen > seconds:
            del self._following[key]
            return False

        self._following[key] = (remaining - 1, now)
        return True

    # ---- 配置查询 ----

    def group_policy(self, group_id: int) -> str:
        return self._config.group_policies.get(group_id, self._config.group_default_policy)

    def is_allowed_private(self, user_id: int) -> bool:
        """判定**只有一份**，在 roles.py 里 —— 别处不要再写一遍，
        否则「谁能私聊」和「模型以为谁能私聊」迟早对不上。"""
        return self._roles.may_dm(user_id)

    # ---- 状态 ----

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
