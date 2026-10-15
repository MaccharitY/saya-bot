"""埋点（FR-8）：每次调用的全量指标。

这是 pre 实验数据的**唯一来源**，所以从内核第一天就要写。落 JSONL，一行一事件，
pandas 可直接读。

⚠️ trace 含真实 QQ 号与群号，**不提交**（见 .gitignore）。进报告的数据必须脱敏后
另存到 `pre/experiments/`。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TraceEvent:
    kind: str  # trigger | llm_call | vision_call | reply | error
    experiment_tag: str
    route: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost: float = 0.0
    at: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.at:
            object.__setattr__(self, "at", datetime.now().isoformat(timespec="seconds"))


class TraceWriter:
    def __init__(self, path: Path | str, *, enabled: bool = True) -> None:
        self._path = Path(path)
        self._enabled = enabled
        if self._enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: TraceEvent) -> None:
        if not self._enabled:
            return
        line = json.dumps(asdict(event), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        """读回全部事件。实验聚合脚本与测试都用它。"""
        if not self._path.is_file():
            return []
        text = self._path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]
