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
#
# ⚠️ `(?<![我咱])` 同「记住」那边一个道理：**「我忘记了」是在陈述，不是在下令。**
# 少了它，这句日常话会被当成忘记指令（实测：关键词被切成"我了"）。
# 「帮我忘掉…」这种请求仍要放行，所以把「帮我/给我/替我」显式开个口子。
_FORGET_CUES = (
    re.compile(
        r"(?:"
        r"(?<!别)(?<!不要)(?<!勿)(?<![我咱])"  # 「别忘了…」是让记住、「我忘记了」是陈述
        r"|(?<=帮我)|(?<=给我)|(?<=替我)"  # 但这几种是在请求，要放行
        r")(?:忘(?:掉|记)|别再记)"
    ),
    re.compile(r"(?:删|去掉)(?:掉)?(?:那|这)(?:条|个)记(?:忆|录)"),
    re.compile(r"\bforget\b", re.I),
)

# 「忘掉**这条**记忆」—— 没说什么事，但意图明确：删最近那条。
# ⚠️ 必须和上一条分开：**光有"忘记"两个字不算**（「我忘记了」是句正常话），
# 要有「这/那条记忆」这种明确的指代才能当指令。
_FORGET_LATEST = re.compile(r"(?:忘(?:掉|记)|删掉?|去掉)(?:那|这)(?:条|个)记(?:忆|录)")

# 剥壳用的标点。**别把"的/记/忆"这类实字放进来** —— 会把正经内容削掉。
_PUNCT = "：:，,。.、！!？?~～ 　\t"

MIN_TAIL_CHARS = 2  # 提示词后面剩这么少，就当作是应答而不是交代

# ---- 忘记时怎么算"沾边" ----
# ⚠️ 这些阈值是拿真实说法试出来的，不是拍的（见 _related 的说明）
SHORT_KEYWORD_CHARS = 3  # 这么短的关键词就直接子串匹配，别绕
MIN_GRAM_HITS = 2  # 长关键词至少要命中这么多个 2 字片段
MIN_GRAM_RATIO = 0.4  # 而且命中率不能太低

# 削掉尾巴虚词再试一次用的（「猫的事」→「猫」）
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

    # 「忘掉这条记忆」这种：**空的关键词代表"最近那一 条"**（见 forget_recent）
    latest = _FORGET_LATEST.search(text)
    if latest:
        rest = _FORGET_LATEST.sub("", text, count=1).strip().strip(_PUNCT).strip()
        return Intent(FORGET, rest)

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
        """把某人说过的话里**跟这个关键词沾边的**记忆停用。返回停用了几条。

        ⚠️ **匹配必须是模糊的，因为人不会照抄原话。**

        实测：记下来的是「群号…里的用户 aaa大肥鱼 也是Chatbot」，
        而人是这么说的 ——「忘掉那个 Chatbot 的事」「把大肥鱼的事忘掉」。
        拿整串去 `LIKE` **一条都对不上**，用户看到的却是"操作完成了，只是没删掉任何东西"。

        做法：短关键词照旧子串匹配（精确又直观）；长句改按 **2 字片段重合度**
        打分 —— 中文没有词边界，在不引分词器的前提下这是够用又稳的办法。
        """
        keyword = keyword.strip()
        if not keyword:
            return 0

        rows = self._db.execute(
            "SELECT id, content FROM notes WHERE subject_qq = ? AND active = 1",
            (subject_qq,),
        ).fetchall()
        doomed = [int(row["id"]) for row in rows if _related(str(row["content"]), keyword)]
        return self._deactivate(doomed)

    def forget_recent(self, subject_qq: int) -> int:
        """停用**最近记下的那一条**。

        对应「忘掉这条记忆」这种说法 —— 它没说什么事，但意图很明确。
        以前这种句子会被当成"没说清楚要忘什么"而**静默什么都不做**。
        """
        row = self._db.execute(
            "SELECT id FROM notes WHERE subject_qq = ? AND active = 1 ORDER BY id DESC LIMIT 1",
            (subject_qq,),
        ).fetchone()
        return self._deactivate([int(row["id"])] if row is not None else [])

    def _deactivate(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._lock:
            self._db.executemany(
                "UPDATE notes SET active = 0 WHERE id = ?", [(note_id,) for note_id in ids]
            )
            self._db.commit()
        return len(ids)

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


def _related(content: str, keyword: str) -> bool:
    """这条记忆跟这个关键词沾不沾边。

    三路判断，从紧到松：

      1. **整串子串命中** —— 精确，最可信
      2. **削掉尾巴虚词再试** —— 「猫的事」对不上「我养了一只猫」，
         削成「猫」就对上了。真实说法和原话常常就差这么个尾巴
      3. 长关键词（>3 字）再按 **2 字片段重合度**兜底：

        「忘掉那个Chatbot的事」 vs 记忆「…的用户aaa大肥鱼也是Chatbot」
        → 片段 {那个,个C,Ch,ha,at,tb,bo,ot,t的,的事} 命中 6 个 → 沾边 ✓

        「忘记我说过的话」 → 片段命中 0 个 → 不沾边 ✓（这句话本来就没说什么）

    阈值（至少 2 个片段、命中率 ≥ 四成）是拿真实说法试出来的：
    取 1 会误伤（随便一个"记忆"就中），取 3 又会让「把大肥鱼的事忘掉」这种
    只剩两个有效片段的说法落空。
    """
    if not keyword:
        return False
    if keyword in content:
        return True

    trimmed = keyword.rstrip(_TRAILING_PARTICLES)
    if trimmed and trimmed != keyword and trimmed in content:
        return True

    if len(keyword) <= SHORT_KEYWORD_CHARS:
        return False

    grams = _grams(keyword)
    if not grams:
        return False
    hits = sum(1 for gram in grams if gram in content)
    return hits >= MIN_GRAM_HITS and hits / len(grams) >= MIN_GRAM_RATIO


def _grams(text: str) -> list[str]:
    """每 2 个字切一个片段。**不引分词器** —— 中文没有词边界，这样够用且零依赖。

    （`services/wiki.py` 里有一份同名的，用途不同、分层也不同 ——
    core 不该为了四行函数去 import 整个 services 包。）
    """
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) <= 2:
        return [cleaned] if cleaned else []
    return list({cleaned[index : index + 2] for index in range(len(cleaned) - 1)})


def _note(row: sqlite3.Row) -> Note:
    return Note(
        id=int(row["id"]),
        subject_qq=int(row["subject_qq"]),
        subject_role=str(row["subject_role"]),
        content=str(row["content"]),
        noted_at=datetime.fromisoformat(row["noted_at"]),
    )
