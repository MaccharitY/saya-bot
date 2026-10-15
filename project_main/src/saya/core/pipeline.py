"""消息处理流水线（docs/01 第 3 节）。

    adapter 收到事件
      → ① 触发判定（不该回就记一条 skip 埋点后结束）
      → ② 组装上下文（人格系统提示 + 本条消息；图片按路线 A 直接进 prompt）
      → ③ 护栏（群友内容包裹成不可信区）
      → ④ 生成
      → ⑤ 输出过滤
      → ⑥ 发送
      → ⑦ 埋点

**任何一步失败都不许把机器人搞崩**：单条消息出错就记一条 error 埋点然后跳过，
主循环继续跑。群里的机器人崩一次，别人就再也不会用它了。
"""

from __future__ import annotations

import logging
from typing import Any

from saya.adapter.base import Adapter
from saya.config import Config
from saya.core import guard
from saya.core.persona import Persona
from saya.core.trigger import Trigger
from saya.domain import IncomingMessage, OutgoingMessage
from saya.obs.trace import TraceEvent, TraceWriter
from saya.services.llm import LLMClient, LLMError, text_message
from saya.services.vision import Vision, VisionError

logger = logging.getLogger("saya.pipeline")

_EMPTY_META: dict[str, Any] = {
    "route": "",
    "model": "",
    "tokens_in": 0,
    "tokens_out": 0,
    "latency_ms": 0,
    "cost": 0.0,
}


class Pipeline:
    def __init__(
        self,
        *,
        config: Config,
        adapter: Adapter,
        llm: LLMClient,
        vision: Vision,
        persona: Persona,
        trigger: Trigger,
        trace: TraceWriter,
    ) -> None:
        self._config = config
        self._adapter = adapter
        self._llm = llm
        self._vision = vision
        self._persona = persona
        self._trigger = trigger
        self._trace = trace

    # ---- 主循环 ----

    def run(self) -> None:
        """阻塞地跑，直到 `stop()` 被调用。"""
        logger.info(
            "saya_bot 启动：adapter=%s 看图路线=%s 模型=%s 触发模式=%s",
            self._config.adapter,
            self._config.vision_route,
            self._config.llm_model,
            self._config.trigger_mode,
        )
        for message in self._adapter.events():
            try:
                self.handle(message)
            except Exception:  # noqa: BLE001 - 主循环必须活下来
                logger.exception("处理消息时崩了，跳过这一条")
                self._trace.write(
                    self._event("error", {"error": "未捕获的异常，详见日志"}, message)
                )

    def stop(self) -> None:
        self._adapter.stop()
        self._adapter.close()

    # ---- 单条消息 ----

    def handle(self, message: IncomingMessage) -> bool:
        """返回是否真的回复了。"""
        decision = self._trigger.should_respond(message)
        if not decision.respond:
            self._trace.write(self._event("trigger", {"respond": False, "reason": decision.reason}, message))
            return False

        check = guard.detect_injection(message.text)
        if check.suspicious:
            # 主防线是「分区隔离」而不是这几条正则 —— 命中了只作为观测信号记录，
            # 不因此拒答，否则正常聊天会被误伤（见 guard.py 的说明）。
            logger.info("疑似注入（%s）：user=%s", check.reason, message.user_id)

        system_prompt = self._persona.system_prompt()
        try:
            reply, meta = self._generate(message, system_prompt)
        except (LLMError, VisionError) as exc:
            logger.warning("生成失败：%s", exc)
            self._trace.write(self._event("error", {"error": str(exc)}, message))
            return False

        safe_text, blocked = guard.screen_output(reply, system_prompt=system_prompt)
        if blocked:
            logger.warning("输出被拦下（%s）", blocked)
        if not safe_text.strip():
            logger.info("生成结果为空，不回")
            self._trace.write(self._event("error", {"error": "生成结果为空"}, message))
            return False

        self._adapter.send(
            OutgoingMessage(
                source=message.source,
                target_id=message.group_id if message.source == "group" else message.user_id,
                text=safe_text,
            )
        )
        self._trigger.note_answered(message)
        logger.info(
            "已回复 %s %s：%s",
            message.source,
            message.group_id if message.source == "group" else message.user_id,
            safe_text if len(safe_text) <= 60 else safe_text[:60] + "…",
        )

        detail = dict(_EMPTY_META)
        detail.update(meta)
        detail["injection"] = check.reason if check.suspicious else "safe"
        detail["output_blocked"] = blocked or ""
        self._trace.write(self._event("reply", detail, message))
        return True

    # ---- 生成 ----

    def _generate(self, message: IncomingMessage, system_prompt: str) -> tuple[str, dict[str, Any]]:
        if message.images and self._config.vision_enable:
            return self._generate_with_images(message, system_prompt)

        result = self._llm.chat(
            [
                text_message("system", system_prompt),
                text_message("user", guard.wrap_untrusted(message.text)),
            ],
            temperature=self._config.temperature,
        )
        meta = {
            "route": "text",
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "latency_ms": result.latency_ms,
            "cost": result.cost,
        }
        return result.text, meta

    def _generate_with_images(
        self, message: IncomingMessage, system_prompt: str
    ) -> tuple[str, dict[str, Any]]:
        # 群里常出现「只发图不说话」，给个兜底问题
        question = guard.wrap_untrusted(message.text.strip() or "看看这张图")
        result = self._vision.describe_many(
            list(message.images),
            question,
            system_prompt=system_prompt,
        )
        meta = {
            "route": result.route,
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "latency_ms": result.latency_ms,
            "cost": result.cost,
        }
        return result.answer, meta

    # ---- 埋点 ----

    def _event(self, kind: str, detail: dict[str, Any], message: IncomingMessage) -> TraceEvent:
        enriched = {
            "source": message.source,
            "group_id": message.group_id,
            "user_id": message.user_id,
            "message_id": message.message_id,
        }
        enriched.update(detail)
        return TraceEvent(kind=kind, experiment_tag=self._config.experiment_tag, detail=enriched)
