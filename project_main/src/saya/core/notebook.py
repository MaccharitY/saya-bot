"""关系记忆 —— 独立于会话记忆的那一本。

会话记忆是缓存：满了就裁、旧了就忘，这是设计如此（见 memory.py）。
但主人/管理员特意交代过的事不该被裁掉 —— 「我不吃辣」「下周三考试」
「以后叫我老板」这类，忘一次就白记了。所以单独存一本，**不参与裁剪**，
也不跟群聊消息混在一起。

**门槛卡在身份上，不卡在内容上**：只有主人与管理员说的话可能被写进来。
所以群友喊一万遍「记住」也进不了这一本 —— 靠的是身份判定（roles.py），
不是靠猜句子。相应地，**这里不需要做得像防注入那样小心翼翼地去理解文本**：
写进来的东西随后会裹进 `guard.wrap_notes` 才进提示词，源头脏不了。

触发看**话里的提示词**（记性好的说法），不调用模型、不花钱：
显式的记忆提示命中就落一行。宁可漏记也不要乱记 —— 所以线索取得保守。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_NOTES_INJECT = 12  # 注入提示词时最多带几条
MAX_CONTENT_CHARS = 300  # 单条记忆存多长
MAX_LINE_CHARS = 120  # 注入时单条显示多长

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_qq   INTEGER NOT NULL,
    subject_role TEXT    NOT NULL DEFAULT '',
    content      TEXT    NOT NULL,
    noted_at     TEXT    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_notes_subject ON notes(subject_qq, id DESC);
"""

# 显式的记忆提示。"记住" 各种说法都收，但**不收**含糊的（比如光出现"记"字）。
#
# ⚠️ 两条防误伤的门槛，都是被具体反例逼出来的：
#   1. **提示词后面必须还有内容** —— 「记住了」「别忘了」单说一句通常是应答。
#      真正要记的东西就在提示词后头，所以这条门槛同时也是"记什么"的定位。
#   2. **「我记住…」是回话，「帮我/给我记住…」是交代** —— 差的只有前面那两个字，
#      所以后顾断言要排除"我/咱"，同时把「帮我/给我/替我」放行。
#      （一开始只写排除，结果最常见的「帮我记一下」反被挡掉了。）
_REMEMBER_CUES = (
    re.compile(r"(?:(?<![我咱])|(?<=帮我)|(?<=给我)|(?<=替我))记(?:住|一下|下|着|好)"),
    re.compile(r"别忘(?:了|记)"),
    re.compile(r"不要忘(?:了|记)"),
    re.compile(r"从今(?:天|以后|往后)"),
    re.compile(r"以后(?:都|就|别|不要|不用|叫我|喊我|称我|要)"),
    re.compile(r"\bremember\b", re.I),
    re.compile(r"覚えて"),
)

# ⚠️ 顺序要紧：「别忘了」里也有个"忘"字，别把它当成"忘记"。
# 用定宽的后顾断言把「别/不要/勿 + 忘记」排除掉。
_FORGET_CUES = (
    re.compile(r"(?<!别)(?<!不要)(?<!勿)(?:忘(?:掉|记)|别再记)"),
    re.compile(r"(?:删|去掉)(?:掉)?(?:那|这)(?:条|个)记(?:忆|录)"),
    re.compile(r"\bforget\b", re.I),
)

# 剥壳用的标点。**别把"的/记/忆"这类实字放进来** —— 会把正经内容削掉。
_PUNCT = "：:，,。.、！!？?~～ 　\t"

MIN_TAIL_CHARS = 2  # 提示词后面剩这么少，就当作是应答而不是交代

# 退一步匹配时要削掉的尾巴虚词（「猫的事」→「猫」）
_TRAILING_PARTICLES = "的了着把事啊吧呢嘛哦呀"

REMEMBER = "remember"
FORGET = "forget"


@dataclass(frozen=True, slots=True)
class Intent:
    """从一条消息里读出的记忆意图。"""

    kind: str
    payload: str


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    subject_qq: int
    subject_role: str
    content: str
    noted_at: datetime


def read_intent(text: str) -> Intent | None:
    """看看这句话是不是在交代「记着」或者「别记了」。读不出就返回 None。

    记的内容是**整条原话**，不是提示词后面那截：拿掉提示词会把上下文一起削掉，
    而上下文（谁、什么语气、什么场合）恰恰是以后用得上的部分。
    后头那截只用来判断"是不是真在交代事情"。
    """
    text = text.strip()
    if not text:
        return None

    for pattern in _FORGET_CUES:
        match = pattern.search(text)
        if not match:
            continue
        keyword = pattern.sub("", text, count=1).strip().strip(_PUNCT).strip()
        # 没说清楚忘什么就什么都不做 —— 删记忆这种事宁可少做
        if keyword:
            return Intent(FORGET, keyword)
        return None

    for pattern in _REMEMBER_CUES:
        match = pattern.search(text)
        if not match:
            continue
        if len(text[match.end() :].strip(_PUNCT).strip()) >= MIN_TAIL_CHARS:
            return Intent(REMEMBER, text)
        # 命中但后面没东西（「记住了」）：当应答处理，换下一个模式碰运气
        continue

    return None


class Notebook:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self._path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- 写入 ----

    def add(
        self,
        *,
        subject_qq: int,
        subject_role: str,
        content: str,
        at: datetime | None = None,
    ) -> int | None:
        """记一条。**同一人说过一模一样的话就不重复存**，只把时间往前挪。

        返回新记录的 id；命中重复时返回 None（没新增）。
        """
        content = content.strip()[:MAX_CONTENT_CHARS]
        if not content:
            return None
        stamp = (at or datetime.now()).isoformat(timespec="seconds")

        with self._lock:
            row = self._db.execute(
                "SELECT id FROM notes WHERE subject_qq = ? AND content = ? AND active = 1",
                (subject_qq, content),
            ).fetchone()
            if row is not None:
                self._db.execute(
                    "UPDATE notes SET noted_at = ? WHERE id = ?", (stamp, row["id"])
                )
                self._db.commit()
                return None

            cursor = self._db.execute(
                "INSERT INTO notes (subject_qq, subject_role, content, noted_at)"
                " VALUES (?, ?, ?, ?)",
                (subject_qq, subject_role, content, stamp),
            )
            self._db.commit()
            return int(cursor.lastrowid or 0)

    def forget_containing(self, subject_qq: int, keyword: str) -> int:
        """把某人说的话里**提到这个关键词的**记忆停用。返回停用了几条。

        用 LIKE 而不是正则：这里的输入是主人自己打的字，不是攻击面，
        简单可靠优先。

        ⚠️ 会做一次**退一步的尝试**：人说的是「忘掉猫的事」，而记下来的原话是
        「我养了一只猫」—— 整段关键词对不上。所以精确匹配落空时，
        把尾巴上的虚词削掉再试一次（「猫的事」→「猫」）。
        **只在精确匹配一条都没中的时候才退这一步**，免得误伤。
        """
        for candidate in _keyword_candidates(keyword):
            removed = self._forget_like(subject_qq, candidate)
            if removed:
                return removed
        return 0

    def _forget_like(self, subject_qq: int, keyword: str) -> int:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            cursor = self._db.execute(
                "UPDATE notes SET active = 0"
                " WHERE subject_qq = ? AND active = 1 AND content LIKE ? ESCAPE '\\'",
                (subject_qq, f"%{escaped}%"),
            )
            self._db.commit()
            return int(cursor.rowcount or 0)

    def forget(self, note_id: int) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE notes SET active = 0 WHERE id = ? AND active = 1", (note_id,)
            )
            self._db.commit()
            return bool(cursor.rowcount)

    # ---- 读取 ----

    def for_prompt(self, subject_qq: int, limit: int = DEFAULT_NOTES_INJECT) -> list[Note]:
        """注入用：**正在说话的那个人的**排前面，其余按新到旧补上。

        数量本来就少（只有主人和管理员写得进来），不需要检索那一套。
        """
        rows = self._db.execute(
            "SELECT * FROM notes WHERE active = 1"
            " ORDER BY (subject_qq = ?) DESC, id DESC LIMIT ?",
            (subject_qq, limit),
        ).fetchall()
        return [_note(row) for row in rows]

    def all_active(self, limit: int = 200) -> list[Note]:
        rows = self._db.execute(
            "SELECT * FROM notes WHERE active = 1 ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_note(row) for row in rows]

    def count(self, *, active_only: bool = True) -> int:
        sql = "SELECT COUNT(*) AS n FROM notes"
        if active_only:
            sql += " WHERE active = 1"
        return int(self._db.execute(sql).fetchone()["n"])


def _keyword_candidates(keyword: str):
    """先给原样的关键词，再给削掉尾巴虚词的版本。"""
    yield keyword
    trimmed = keyword.rstrip(_TRAILING_PARTICLES)
    if trimmed and trimmed != keyword:
        yield trimmed


def _note(row: sqlite3.Row) -> Note:
    return Note(
        id=int(row["id"]),
        subject_qq=int(row["subject_qq"]),
        subject_role=str(row["subject_role"]),
        content=str(row["content"]),
        noted_at=datetime.fromisoformat(row["noted_at"]),
    )
