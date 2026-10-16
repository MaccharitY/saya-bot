"""埋点（FR-8）：每次调用的全量指标。

这是 pre 实验数据的**唯一来源**，所以从内核第一天就要写。落 JSONL，一行一事件，
pandas 可直接读。

⚠️ trace 含真实 QQ 号与群号，**不提交**（见 .gitignore）。进报告的数据必须脱敏后
另存到 `pre/experiments/`。

**文件会轮转**：机器人是长期跑的，埋点只增不减，不管它就会长成一个巨型文件。
到 `DEFAULT_MAX_BYTES` 就把旧的挪成 `<名字>.1`，重新开一个。
默认 10 MB，正常用碰不到 —— 它是兜底，不是日常机制。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
ROTATED_SUFFIX = ".1"


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """一次调用的记录。

    ⚠️ **指标全在 `detail` 里**，不要在这里加平铺字段。
    这个类曾经有 `route` / `model` / `tokens_in` / `tokens_out` / `latency_ms` / `cost`
    六个平铺字段，但**从没有任何地方写过它们** —— 所有指标都塞进了 `detail`。
    留着只会让读代码的人以为事件上带着这些值（还会去 `event["tokens_in"]` 取到 0）。
    2026-10-16 删。
    """

    kind: str  # trigger | note | reply | error
    experiment_tag: str
    at: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.at:
            object.__setattr__(self, "at", datetime.now().isoformat(timespec="seconds"))


class TraceWriter:
    def __init__(
        self,
        path: Path | str,
        *,
        enabled: bool = True,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._path = Path(path)
        self._enabled = enabled
        self._max_bytes = max_bytes
        if self._enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def rotated_path(self) -> Path:
        return self._path.with_name(self._path.name + ROTATED_SUFFIX)

    def write(self, event: TraceEvent) -> None:
        if not self._enabled:
            return
        self._rotate_if_needed()
        line = json.dumps(asdict(event), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _rotate_if_needed(self) -> None:
        """超上限就把当前文件挪到 `.1`，重开一个。

        ⚠️ **只留一代** —— 再轮转一次会覆盖上一个 `.1`。
        想长留就定期把它拷走；这里是"别让它无限涨"的兜底，不是归档机制。
        """
        if self._max_bytes <= 0:
            return
        try:
            if self._path.stat().st_size < self._max_bytes:
                return
        except OSError:
            return
        self._path.replace(self.rotated_path)

    def read_all(self) -> list[dict[str, Any]]:
        """读回全部事件（含轮转走的那一份，按时间先后）。实验聚合脚本与测试都用它。"""
        events: list[dict[str, Any]] = []
        for path in (self.rotated_path, self._path):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            events.extend(json.loads(line) for line in text.splitlines() if line.strip())
        return events
