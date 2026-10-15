"""Mock adapter：回放事件夹具，用于自动化测试与离线开发。

它**不编造**事件格式，而是复用 onebot11.parse_event 走真实 OneBot v11 数据，
因此 QQ 侧出问题时（协议失效、账号被封）开发与测试仍能继续（ADR-008）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from saya.adapter.base import Adapter
from saya.adapter.onebot11 import parse_event
from saya.domain import IncomingMessage, OutgoingMessage

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "onebot11_events.jsonl"
)


class MockAdapter(Adapter):
    def __init__(self, fixture: Path | str = DEFAULT_FIXTURE, self_id: int = 99999) -> None:
        self._fixture = Path(fixture)
        self._self_id = self_id
        self.sent: list[OutgoingMessage] = []

    def load_raw_events(self) -> list[dict]:
        events: list[dict] = []
        with self._fixture.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def events(self) -> Iterator[IncomingMessage]:
        for raw in self.load_raw_events():
            message = parse_event(raw, self_id=self._self_id)
            if message is not None:
                yield message

    def send(self, message: OutgoingMessage) -> None:
        self.sent.append(message)
