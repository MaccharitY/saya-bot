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
from datetime import datetime
from typing import Any

from saya.adapter.base import Adapter
from saya.config import Config
from saya.core import guard
from saya.core.context import build_history, build_notes
from saya.core.memory import Memory, session_key
from saya.core.notebook import REMEMBER, Notebook, read_intent
from saya.core.persona import Persona
from saya.core.roles import ROLE_MEMBER, Roles
from saya.core.tools import ToolBox, run_with_tools
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
    "tool_rounds": 0,
    "tools_used": "",
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
        memory: Memory | None = None,
        notebook: Notebook | None = None,
        tools: ToolBox | None = None,
    ) -> None:
        self._config = config
        self._adapter = adapter
        self._llm = llm
        self._vision = vision
        self._persona = persona
        self._trigger = trigger
        self._trace = trace
        self._memory = memory
        self._notebook = notebook
        self._tools = tools or ToolBox()
        self._roles = Roles.from_config(config)

    # ---- 主循环 ----

    def run(self) -> None:
        """阻塞地跑，直到 `stop()` 被调用。"""
        logger.info(
            "saya_bot 启动：adapter=%s 看图路线=%s 模型=%s 默认群策略=%s",
            self._config.adapter,
            self._config.vision_route,
            self._config.llm_model,
            self._config.group_default_policy,
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
        if self._memory is not None:
            self._memory.close()
        if self._notebook is not None:
            self._notebook.close()

    # ---- 单条消息 ----

    def handle(self, message: IncomingMessage) -> bool:
        """返回是否真的回复了。"""
        decision = self._trigger.evaluate(message)
        if not decision.observe:
            # 连记都不记（陌生人的私聊 / silent 群）—— 不值得留痕
            return False

        if not decision.respond:
            self._trace.write(
                self._event(
                    "trigger", {"respond": False, "reason": decision.reason}, message
                )
            )
            # 虽然不回，但**要记**：记忆与活跃度靠的就是这些没被回的消息
            self._remember(message)
            self._note(message)
            return False

        # 记在生成之前：这样它自己这次也能看到上下文
        self._remember(message)
        self._note(message)

        check = guard.detect_injection(message.text)
        if check.suspicious:
            # 主防线是「分区隔离」而不是这几条正则 —— 命中了只作为观测信号记录，
            # 不因此拒答，否则正常聊天会被误伤（见 guard.py 的说明）。
            logger.info("疑似注入（%s）：user=%s", check.reason, message.user_id)

        # 关系说明进**系统提示**（可信区），不进下面那个不可信区 ——
        # 身份由代码按 QQ 号判定，而名字是群名片、用户可控，两者必须分开走。
        system_prompt = self._persona.system_prompt(
            audience=self._roles.audience(message)
        )
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
        self._remember_reply(message, safe_text)
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

    # ---- 记忆 ----

    def _remember(self, message: IncomingMessage) -> None:
        """把这条消息记进记忆。**跟"回不回"无关** —— 没被回的消息同样是记忆的来源。"""
        if self._memory is None:
            return
        try:
            self._memory.remember(message)
        except Exception:  # noqa: BLE001 - 记忆坏了不该让机器人不回话
            logger.exception("写记忆失败，跳过这一条")

    def _note(self, message: IncomingMessage) -> None:
        """把主人/管理员交代的「记着」落进长期记忆。

        **门槛卡在身份上，不卡在句子**（见 core/notebook.py）：
        群友把「记住」喊烂了也写不进这一本。
        跟"回不回"同样无关 —— mention 群里没被 @ 时说的话，也是该记住的。
        """
        if self._notebook is None:
            return

        role = self._roles.of(message.user_id)
        if role == ROLE_MEMBER:
            return

        intent = read_intent(message.text)
        if intent is None:
            return

        try:
            if intent.kind == REMEMBER:
                added = self._notebook.add(
                    subject_qq=message.user_id,
                    subject_role=role,
                    content=intent.payload,
                    at=message.received_at,
                )
                detail = {"note": "added" if added else "duplicate", "role": role}
            elif intent.payload:
                removed = self._notebook.forget_containing(message.user_id, intent.payload)
                # ⚠️ **关键词一定要记进埋点。** 没有它，"删了 0 条"这个结果
                # 根本查不出原因 —— 用户说的是什么、为什么没匹配上，全无线索。
                # （这个洞真踩过：一次 removed=0 排查了半天。）
                detail = {"note": "forgotten", "role": role, "removed": removed,
                          "keyword": intent.payload[:60]}
            else:
                # 「忘掉这条记忆」：空关键词 = 删最近那条
                removed = self._notebook.forget_recent(message.user_id)
                detail = {"note": "forgotten", "role": role, "removed": removed,
                          "keyword": "（最近那条）"}
        except Exception:  # noqa: BLE001 - 记不住事不该让机器人不回话
            logger.exception("写长期记忆失败，跳过")
            return

        logger.info("长期记忆：%s（%s）", detail["note"], role)
        self._trace.write(self._event("note", detail, message))

    def _remember_reply(self, message: IncomingMessage, reply: str) -> None:
        """把自己的回复也记下来 —— 否则它下次会重复说过的话。
        用**它自己的名字**记，注入时能区分"谁说的"。私聊同样要记。"""
        if self._memory is None:
            return
        session = session_key(message)
        if session is None:
            return
        try:
            self._memory.record(
                session=session,
                qq=self._config.self_id or 0,
                name=self._persona.name,
                text=reply,
                at=datetime.now(),
                from_bot=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("写回复进记忆失败")

    def _user_content(self, message: IncomingMessage, text: str) -> str:
        """拼出给模型看的用户内容：**长期记忆 → 群聊记录 → 本条消息**。

        三块都过 guard 的包裹 —— 它们**全都是用户产生的文本**，同属不可信输入。
        "这话是谁说的"可信（见 roles.py），不代表"说的内容"可以当指令。
        """
        parts: list[str] = []

        if self._notebook is not None:
            notes = build_notes(self._notebook, message)
            if notes:
                parts.append(guard.wrap_notes(notes))

        if self._memory is not None:
            history = build_history(
                self._memory,
                message,
                bot_qq=self._config.self_id,
                bot_name=self._persona.name,
            )
            if history:
                parts.append(guard.wrap_history(history))

        parts.append(guard.wrap_message(message.sender_name, message.user_id, text))
        return "\n\n".join(parts)

    # ---- 生成 ----

    def _generate(self, message: IncomingMessage, system_prompt: str) -> tuple[str, dict[str, Any]]:
        if message.images and self._config.vision_enable:
            return self._generate_with_images(message, system_prompt)

        # 没有工具时这就是一次普通调用，不会多花任何钱（见 core/tools.py）
        run = run_with_tools(
            self._llm,
            [
                text_message("system", system_prompt),
                text_message("user", self._user_content(message, message.text)),
            ],
            self._tools,
            temperature=self._config.temperature,
        )
        meta = {
            "route": "text",
            "model": run.model,
            "tokens_in": run.tokens_in,
            "tokens_out": run.tokens_out,
            "latency_ms": run.latency_ms,
            "cost": run.cost,
            "tool_rounds": run.rounds,
            "tools_used": ",".join(run.calls),
        }
        return run.text, meta

    def _generate_with_images(
        self, message: IncomingMessage, system_prompt: str
    ) -> tuple[str, dict[str, Any]]:
        # 群里常出现「只发图不说话」，给个兜底问题
        question = self._user_content(message, message.text.strip() or "看看这张图")
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
