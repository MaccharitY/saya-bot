#!/usr/bin/env python3
"""把剧情母本与 B 站对照文本切成「一章一文件」的语料库，并生成两套索引。

**数据来源**（`stories/`，均为项目所有者提供，脚本只读不改）：

    Arcaea.txt     剧情母本（纯中文，章节标记单独成行，如 `0-1`、`1-ZR`、`18-III`、
                   `E-1 / 最终之梦`）
    bili-5.9.txt   B 站文集「5.9 主线」，中英对照 + 译者注释，标记形如 `—18-1—`
    bili-6.0.txt   6.0 主线，中英对照，标记形如 `20-1`
    bili-6.8.2.txt 6.8.2 档案故事「Unseeing Eyes」（单篇，无章节标记）
    bili-6.9.txt   6.9 主线，中英对照，标记形如 `23-1`
    bili-7.0.txt   7.0 主线，中英对照，标记形如 `C-1`

**产出**（默认写进 `project_main/persona/corpus/`）：

    NN-ch<标记>.txt        母本的章节，名字全 ASCII，NN 是全局序号
    NN-<tag>ch<标记>.txt   B 站文本的章节（带来源 tag，避免与母本重名）
    index.json             给程序查的索引：title / file / source / section / kind /
                           summary / keywords / chars
    INDEX.md               人看的目录，按 主线 / 支线 / 短篇 分组

**设计要点**

1. 章节顺序**按各自文件里的出现顺序**排（母本里不同分支互相穿插，顺序不等于剧情顺序）。
2. **B 站文章开头都有一份「目录」**，把章节标记又列了一遍 —— 脚本会自动识别这种
   「连续排列的标记群」（彼此间隔 ≤4 行且 ≥3 个）并丢弃，只保留正文里的真标记。
3. **页面杂质**（`cvXXXXXXX`、`分享至`、标签串等）在文件末尾连成一段，脚本从尾部
   逐行剥离；`6.8.2` 这类单篇文件则从正文分隔线之后开始取。
4. 可续跑：已存在的章节文件默认跳过，`--force` 才重写。中断了直接再跑即可。
5. 摘要（summary）优先用 `CURATED_SUMMARIES` 里手写的；其余章节自动摘录正文开头
   （**只摘原文，不编造**）。关键词从固定人名词表筛，单字人名只在「像人名」的上下文里算。
6. `--fetch-url` 是给「以后补料」留的口子：把某个公开页面抓下来当一章存进语料。
   抓取遵守：请求间隔 ≥1 秒、失败最多重试 2 次；**一旦遇到反爬拦截（401/403/412/429）
   就报错退出，不做任何绕过**。

零第三方依赖，只用标准库。用法::

    python tools/fetch_arcaea_corpus.py                 # 默认：切全部来源
    python tools/fetch_arcaea_corpus.py --force         # 全量重切
    python tools/fetch_arcaea_corpus.py --only b70      # 只切某一个来源
    python tools/fetch_arcaea_corpus.py --fetch-url <url> --name 6.0主线

（路径说明：项目约定脚本放仓库根的 `tools/`；本脚本自动向上找含 `project_main/` 的目录
作为仓库根，所以放在 `tools/` 或 `project_main/persona/tools/` 下都能跑。）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── 章节标记 ────────────────────────────────────────────────────────────────
# 母本：数字或短字母前缀 + 短后缀；`E-1 / 最终之梦` 这类带标题的也认。
BARE_MARKER_RE = re.compile(r"^([0-9]{1,2}|[A-Za-z]{1,3})-[0-9A-Za-z]{1,4}(?:\s*/\s*.+)?$")
# B 站 5.9：全角破折号包着的标记，如 `—18-1—`（取出后去掉破折号）。
DASHED_MARKER_RE = re.compile(r"^—\s*[0-9A-Za-z]{1,3}-[0-9A-Za-z]{1,3}\s*—$")

# ── 来源清单 ────────────────────────────────────────────────────────────────
# tag 为空 = 母本；否则进文件名与索引的 source 字段。
SOURCES: tuple[dict, ...] = (
    {
        "path": "stories/Arcaea.txt",
        "label": "剧情母本（中文）",
        "tag": "",
        "marker": BARE_MARKER_RE,
    },
    {
        "path": "stories/bili-5.9.txt",
        "label": "B站 5.9 主线（中英对照）",
        "tag": "b59",
        "marker": DASHED_MARKER_RE,
        "marker_clean": lambda s: s.strip("—").strip(),
    },
    {
        "path": "stories/bili-6.0.txt",
        "label": "B站 6.0 主线（中英对照）",
        "tag": "b60",
        "marker": BARE_MARKER_RE,
    },
    {
        "path": "stories/bili-6.8.2.txt",
        "label": "B站 6.8.2 档案故事（中英对照）",
        "tag": "b682",
        "marker": None,               # 单篇，无章节标记
        "single_title": "Unseeing Eyes",
        "single_kind": "短篇",
        "single_section": "短篇·档案故事",
        "start_after": re.compile(r"^—{5,}$"),  # 正文在第一条长破折号之后
    },
    {
        "path": "stories/bili-6.9.txt",
        "label": "B站 6.9 主线（中英对照）",
        "tag": "b69",
        "marker": BARE_MARKER_RE,
    },
    {
        "path": "stories/bili-7.0.txt",
        "label": "B站 7.0 主线（中英对照）",
        "tag": "b70",
        "marker": BARE_MARKER_RE,
    },
)

# ── 页面杂质 ────────────────────────────────────────────────────────────────
# 出现即视为「正文结束」的行（B 站页脚连成一整段，从尾部逐行剥离即可）。
TAIL_JUNK_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^cv\d+$"),
    re.compile(r"^#.+$"),
    re.compile(r"^arcaea\s+韵律源点.*$", re.I),
    re.compile(r"^(分享至|微信扫一扫分享|投诉或建议|给UP主投上硬币|确定)$"),
    re.compile(r"^[0-9]*硬币$"),
    re.compile(r"^经验值\+\d+（今日\d+/\d+）$"),
)
# 出现在任何位置都直接删掉的行（页头信息，正常正文不会长这样）。
DROP_LINE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(目录|收录于文集|共\d+篇|韵律源点Arcaea)$"),
)

# ── 分类表 ──────────────────────────────────────────────────────────────────
# 章节号 → 大类。依据：母本里的分节标签（「支线」「短篇」）、B 站文集标题
# （5.9 / 6.0 / 6.9 / 7.0 都自称「主线」）与各段内容。
KIND_BY_STORY = {
    "0": "主线", "1": "主线", "2": "主线", "V": "主线", "VS": "主线",
    "F": "主线", "E": "主线", "18": "主线", "20": "主线", "23": "主线", "C": "主线",
    "3": "支线", "4": "支线", "5": "支线", "7": "支线", "9": "支线",
    "10": "支线", "15": "支线", "16": "支线", "17": "支线", "S": "支线",
    "6": "短篇", "8": "短篇", "11": "短篇", "12": "短篇", "13": "短篇", "14": "短篇",
}

# 章节号 → 这一组的别名（来自母本里的分节标签行；18 按内容，20/23/C 按版本号）。
STORY_NAME = {
    "0": "序章", "1": "光", "2": "对立", "V": "聚合", "VS": "决裂",
    "F": "终焉", "E": "结局", "18": "咲弥（5.9）", "20": "6.0", "23": "6.9", "C": "7.0",
    "3": "咲弥", "4": "红", "5": "忘却", "7": "爱丽丝 & 坦尼尔", "9": "拉格兰",
    "10": "爱托 & 露娜", "15": "摩耶", "16": "洞烛", "17": "群愿", "S": "白姬",
    "6": "调", "8": "迷尔", "11": "彩梦", "12": "维塔", "13": "依莉丝", "14": "奈美",
}

# 加进关键词时用的名字（与分节显示名不同的少数情况）
KEYWORD_NAME_OVERRIDE = {"18": "咲弥"}

# 关键词筛选用的固定人名词表（只筛出现的，不做其他推测）
NAME_KEYWORDS = (
    "咲弥", "忘却", "对立", "拉薇妮雅", "多纳文", "维塔", "露娜", "爱托",
    "白姬", "群愿", "摩耶", "彩梦", "依莉丝", "奈美", "迷尔", "拉格兰",
    "爱丽丝", "坦尼尔", "洞烛", "神明", "卡戎", "拉可弥拉", "尼尔",
)

# 单字人名（光 / 红 / 调）直接用子串匹配会撞车（「发光」「调整」……），
# 所以只在**像人名**的上下文里才算命中：加引号、接虚词/标点、或与别的名字并列；
# 再用否定后顾排掉最常见的前缀复合词（目光、时间、鲜红……）。
SINGLE_CHAR_PATTERNS = {
    "光": re.compile(r"[“「]光|(?<![目时月日阳灯电弧曝采感争沾微晨曙波背星])光(?=[，。、：；？！与和说看听道问则&])"),
    "红": re.compile(r"[“「]红|(?<![殷绯鲜通分暗血])红(?=[，。、：；？！与和说看听道问则&])"),
    "调": re.compile(r"[“「]调|调(?=[，。、：；？！与和说看道问则&])"),
}

# 手写摘要：只写真的读过的章节；没有条目的章节自动摘录开头。
CURATED_SUMMARIES: dict[str, str] = {
    "3-0": "咲弥在纯白废墟中苏醒，只有一只眼睛，右眼覆着异物；她立誓解开这个世界的谜。",
    "3-1": "她在回忆中成为宴会上的少女拉薇妮雅，席间有人谈论「用人类的手创造一个世界」。",
    "3-2": "她察觉自己被困在人造的回忆里：外面的世界只是概念，她无处可去。",
    "3-3": "她向宾客发问只得到敷衍，终于用面包刀刺穿未婚夫多纳文的喉咙。",
    "3-4": "伤口里没有血，只有虚空与碎片；她确认这是「毫无价值的梦」，任其崩毁。",
    "3-5": "她得出结论：在那些地方，人类可以成为上帝；她要收集碎片，拼出一个新世界。",
    "18-I": "忘却守在灵魂的池边；咲弥找上门来，要「把这些东西物尽其用」，两人对峙。",
    "18-II": "咲弥点破忘却保留着原来的名字；忘却拒绝她的来意，举起镰刀。",
    "18-III": "咲弥以碎片抵住镰刀：她说自己从不撒谎、得不到任何好处；谈崩，开战。",
    "18-1": "咲弥自述：她由「神明」造于此地，是孤独的愚者，追逐碎片与答案。",
    "18-2": "她回望与忘却的旧账，坚信碎片可以用来创造新的现实。",
    "18-3": "她说自己是「创造」的象征，建造回忆的档案馆；天空裂开，她遇见哭泣的维塔。",
    "18-4": "她收留维塔，带她进入回忆，两个人的旅途就此开始。",
    "18-5": "她说自己只继承了「咲弥」这个名字；终局到来，Arcaea 解体，世界化为虚无。",
    "18-6": "她决定去杀忘却、夺走她的碎片；雨里维塔追问，她只说「躲远点看着」。",
    "18-7": "她与忘却的战斗被一只戴手套的手打断——一位苍白长发的陌生女性让两人跪下。",
    "Unseeing Eyes": "拉格兰在虚无中观测到一团没被编织、也没消散的记忆；她由此谈起维塔的复制体为何半头浅发、一眼异色。",
    "C-1": "维塔视角开场：咲弥教她掌心相贴的约定；画面转向战场——咲弥力竭倒在拉可弥拉面前，被揪住头发逼问维塔的下落。",
    "C-2": "以「世界」的口吻叙述：拉可弥拉掐着咲弥的喉咙，忘却虚弱抗议；声音低语着要维塔打破藏身的承诺。",
    "C-3": "咲弥视角复述：她只想把维塔带离这里；拉可弥拉从天而降，挡在两人之间。",
    "C-4": "洞烛视角：忘却被她打落虚空；咲弥以碎片穿梭与白色火焰之剑迎战，把维塔转移到自认为安全的地方。",
    "C-5": "旁白谈论「爱使人盲目」与 Arcaea 对维塔、咲弥的算计；译者注指出咲弥许下的是 Arcaea 倒数第二个愿望。",
    "C-6": "咲弥与洞烛贴身缠斗：她撕裂洞烛制造的空间、反复逼近；洞烛察觉她已不单为了自己而战。",
    "C-7": "拉可弥拉化为「神」的形态；咲弥被自己那把剑贯穿心脏，同时用花的力量把维塔送出险境。",
    "C-8": "临终：咲弥索要对方的名字，说出唯一的愿望「我祈愿着一片崭新的大地」，与维塔道别。",
    "C-9": "咲弥已死，笑容定格；维塔坠入虚空，拉可弥拉干笑着面对只剩死寂的世界。",
    "C-10": "拉可弥拉吞下「大能」，宣告 Arcaea 的永恒之日将要终结，要清洗这个世界。",
}


# ── 解析 ────────────────────────────────────────────────────────────────────
def strip_tail_junk(lines: list[str]) -> list[str]:
    """从尾部逐行剥掉 B 站页脚（从后往前，遇到第一条正常内容就停）。"""
    end = len(lines)
    while end > 0:
        s = lines[end - 1].strip()
        if not s or any(rx.match(s) for rx in TAIL_JUNK_RES):
            end -= 1
        else:
            break
    return lines[:end]


def _is_label(line: str) -> bool:
    """分节标签：短、没有句读，且不是章节标记（如「光」「支线」「忘却」）。"""
    s = line.strip()
    if not s or len(s) > 14:
        return False
    if BARE_MARKER_RE.match(s) or DASHED_MARKER_RE.match(s):
        return False
    return not re.search(r"[。，！？：；、…—“”\"'《》()（）]", s)


def find_markers(lines: list[str], marker_re: re.Pattern[str],
                 clean=lambda s: s) -> list[tuple[int, str]]:
    """找出真正的章节标记：先收集全部候选，再把「连续排列的标记群」（目录）丢掉。

    目录里的标记彼此只隔 1~4 行且成串出现（≥3 个）；正文里的标记之间隔着成百行。
    """
    cand = [(i, clean(l.strip())) for i, l in enumerate(lines) if marker_re.match(l.strip())]
    if not cand:
        return []

    drop: set[int] = set()
    run_start = 0
    for k in range(1, len(cand) + 1):
        close_run = k == len(cand) or (cand[k][0] - cand[k - 1][0]) > 4
        if close_run:
            run = cand[run_start:k]
            if len(run) >= 3:
                drop.update(i for i, _ in run)
            run_start = k
    return [(i, t) for i, t in cand if i not in drop]


def parse_source(lines: list[str], spec: dict) -> list[dict]:
    """把一个来源切成章节列表（每项含 marker / body / section / kind …）。"""
    lines = strip_tail_junk([l.rstrip("\r\n") for l in lines])

    marker_re = spec["marker"]
    if marker_re is None:  # 单篇文档（6.8.2）
        start = None
        if spec.get("start_after"):
            for i, l in enumerate(lines):
                if spec["start_after"].match(l.strip()):
                    start = i + 1
                    break
        body_lines = lines[start or 0:]
        body_lines = [l for l in body_lines if not any(rx.match(l.strip()) for rx in DROP_LINE_RES)]
        body = _join_body(body_lines)
        return [{
            "marker": spec["single_title"], "title": spec["single_title"],
            "section": spec["single_section"], "kind": spec["single_kind"],
            "body": body, "tags": [],
        }]

    marks = find_markers(lines, marker_re, spec.get("marker_clean", lambda s: s))
    chapters: list[dict] = []
    for k, (i, marker) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)

        # 往上看最多 4 行，拿分节标签（可能有「支线」+「忘却」两个）
        labels: list[str] = []
        back, j = 0, i - 1
        while j >= 0 and back < 4:
            if lines[j].strip():
                if _is_label(lines[j]):
                    labels.insert(0, lines[j].strip())
                else:
                    break
            back += 1
            j -= 1
        labels = labels[-2:]

        story = marker.split("-", 1)[0]
        kind = KIND_BY_STORY.get(story, "")
        for lb in labels:  # 标签里的「支线 / 短篇」优先级更高
            if lb in ("主线", "支线", "短篇"):
                kind = lb
        name = STORY_NAME.get(story, "")
        section = f"{kind}·{name}" if (kind and name) else (name or kind or "未分组")

        body_lines = [l for l in lines[i + 1:end]
                      if not any(rx.match(l.strip()) for rx in DROP_LINE_RES)]
        body = _join_body(body_lines)

        chapters.append({
            "marker": marker, "title": marker, "section": section, "kind": kind,
            "body": body, "tags": [],
        })
    return chapters


def _join_body(lines: list[str]) -> str:
    """去掉首尾空行，压缩连续空行，保留段落分隔。"""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    out: list[str] = []
    for l in lines:
        if l.strip() or (out and out[-1].strip()):
            out.append(l)
    return "\n".join(out)


def classify(ch: dict) -> None:
    """给章节补上 summary / keywords。"""
    text = ch["body"].replace("\n", "")
    keywords = [w for w in NAME_KEYWORDS if w in text]
    keywords += [c for c, pat in SINGLE_CHAR_PATTERNS.items() if pat.search(text)]
    story = ch["title"].split("-", 1)[0]
    kw_name = KEYWORD_NAME_OVERRIDE.get(story, STORY_NAME.get(story, ""))
    if kw_name and kw_name not in keywords:
        keywords.insert(0, kw_name)
    ch["keywords"] = keywords
    ch["summary"] = CURATED_SUMMARIES.get(ch["marker"]) or _auto_summary(ch["body"])


def _auto_summary(body: str, limit: int = 40) -> str:
    """自动摘要 = 正文开头几句的摘录（截断到 limit 字）。**只摘原文，不编造。**"""
    first = next((l.strip() for l in body.splitlines() if l.strip()), "")
    s = first
    if len(s) < 18:  # 第一行太短（如「死寂。」），再取下一行
        rest = [l.strip() for l in body.splitlines() if l.strip()][1:2]
        if rest:
            s = s + rest[0]
    full = s
    s = s[:limit].rstrip("，。！？：；、…— ")
    suffix = "…" if len(full) > limit else ""
    if not suffix and s and s[-1] not in "。！？…」”』":
        suffix = "。"
    return s + suffix


# ── 落盘 ────────────────────────────────────────────────────────────────────
def build(chapters: list[dict], outdir: Path, force: bool) -> tuple[int, int]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for n, ch in enumerate(chapters, start=1):
        tag = ch.get("tag", "")
        mid = re.sub(r"[^0-9A-Za-z\-]", "", ch["title"].split("/")[0].strip()) or "doc"
        fname = f"{n:02d}-{tag + 'ch' if tag else 'ch'}{mid}.txt"
        ch["file"] = fname
        path = outdir / fname
        content = f"{ch['title']}\n\n{ch['body']}\n"
        if path.exists() and not force:
            skipped += 1
            continue
        path.write_text(content, encoding="utf-8")
        written += 1
    return written, skipped


def write_indexes(chapters: list[dict], outdir: Path, sources_desc: list[str]) -> None:
    entries = [
        {
            "title": ch["title"], "file": ch["file"], "source": ch.get("source", ""),
            "section": ch["section"], "kind": ch["kind"], "summary": ch["summary"],
            "keywords": ch["keywords"], "chars": len(ch["body"]),
        }
        for ch in chapters
    ]
    (outdir / "index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    total = sum(e["chars"] for e in entries)
    lines = [
        "# 剧情语料索引",
        "",
        f"> 由 `persona/tools/fetch_arcaea_corpus.py` 自动切分生成，共 {len(entries)} 章、"
        f"{total} 字符。",
        "> 章节顺序 = 各自文件里的出现顺序，**不是**剧情顺序（不同分支互相穿插）。",
        "> 来源：" + "；".join(sources_desc) + "。",
        "",
        "> 归类说明：主线/支线/短篇 依据母本里的分节标签与 B 站文集标题"
        "（5.9 / 6.0 / 6.9 / 7.0 均自称「主线」）；`18` 系列即「5.9 主线」。",
        "> B 站来源的章节是中英对照 + 译者注释版，与母本内容重叠处**两版都保留**"
        "（母本：纯中文；B站：中英对照，可用来核对官方译名）。",
        "",
    ]
    for kind in ("主线", "支线", "短篇", ""):
        group = [e for e in entries if e["kind"] == kind]
        if not group:
            continue
        lines.append(f"## {kind or '未分组'}（{len(group)} 章）")
        lines.append("")
        current_section = None
        for e in group:
            if e["section"] != current_section:
                current_section = e["section"]
                lines.append(f"### {current_section}")
                lines.append("")
            kw = ("　`" + "、".join(e["keywords"]) + "`") if e["keywords"] else ""
            src = f"　—　{e['source']}" if e["source"] and "母本" not in e["source"] else ""
            lines.append(
                f"- **{e['title']}** — {e['summary']}（[{e['file']}]({e['file']})，"
                f"{e['chars']} 字）{src}{kw}"
            )
        lines.append("")
    (outdir / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def fetch_extra(url: str, name: str, outdir: Path) -> None:
    """把一个公开页面抓成语料附加章。遇反爬拦截（401/403/412/429）直接报错退出。"""
    time.sleep(1.0)  # 对人家服务器客气点
    last_err: Exception | None = None
    for _ in range(3):  # 首次 + 最多 2 次重试
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; saya-bot-corpus/0.1)",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 412, 429):
                raise SystemExit(
                    f"被反爬拦截（HTTP {exc.code}），按约定不做绕过。请人工获取后手工放入语料目录。"
                )
            last_err = exc
        except Exception as exc:  # noqa: BLE001 - CLI 边界
            last_err = exc
        time.sleep(1.0)
    else:
        raise SystemExit(f"抓取失败（重试 2 次后放弃）：{last_err}")

    text = raw.decode("utf-8", errors="replace")
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"extra-{re.sub(r'[^0-9A-Za-z-]', '', name)}.txt"
    path.write_text(text, encoding="utf-8")
    print(f"已写入 {path}（{len(text)} 字符）。摘要/关键词请手工补进 index.json。")


def find_repo_root(start: Path) -> Path:
    """向上找含有 `project_main/` 的目录当仓库根。

    这样脚本无论放在仓库根的 `tools/` 还是 `project_main/persona/tools/` 下都能跑。
    """
    for p in (start, *start.parents):
        if (p / "project_main").is_dir():
            return p
    return start.parents[3]  # 兜底：老位置（project_main/persona/tools/）


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    root = find_repo_root(Path(__file__).resolve().parent)  # …/saya_bot
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--outdir", default=str(root / "project_main" / "persona" / "corpus"))
    parser.add_argument("--only", action="append", help="只处理指定来源（按 tag，如 b70；母本用 master）")
    parser.add_argument("--force", action="store_true", help="已存在的章节文件也重写")
    parser.add_argument("--fetch-url", help="（可选）额外抓一个公开页面作为附加语料")
    parser.add_argument("--name", default="extra", help="配合 --fetch-url：附加语料的名字")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir)
    if args.fetch_url:
        fetch_extra(args.fetch_url, args.name, outdir)
        return 0

    wanted = set(args.only or [])
    all_chapters: list[dict] = []
    used_sources: list[str] = []
    problems: list[str] = []

    for spec in SOURCES:
        tag = spec["tag"] or "master"
        if wanted and tag not in wanted:
            continue
        path = root / spec["path"]
        if not path.exists():
            problems.append(f"缺文件：{path}")
            continue
        chapters = parse_source(path.read_text(encoding="utf-8").splitlines(), spec)
        for ch in chapters:
            ch["tag"] = spec["tag"]
            ch["source"] = spec["label"]
            classify(ch)
        all_chapters.extend(chapters)
        used_sources.append(f"{spec['label']}（{len(chapters)} 章）")
        print(f"[{tag}] {spec['label']}：{len(chapters)} 章，"
              f"{sum(len(c['body']) for c in chapters)} 字符")

    if not all_chapters:
        print("没有任何章节——来源文件都缺席吗？", file=sys.stderr)
        return 1

    written, skipped = build(all_chapters, outdir, force=args.force)
    write_indexes(all_chapters, outdir, used_sources)

    total = sum(len(c["body"]) for c in all_chapters)
    kinds: dict[str, int] = {}
    for c in all_chapters:
        kinds[c["kind"] or "未分组"] = kinds.get(c["kind"] or "未分组", 0) + 1
    print(f"\n章节总数 {len(all_chapters)}；新写入 {written}、跳过（已存在）{skipped}")
    print(f"合计 {total} 字符；" + "、".join(f"{k} {v} 章" for k, v in kinds.items()))
    for p in problems:
        print("⚠️ " + p, file=sys.stderr)
    print(f"索引：{outdir / 'index.json'} 与 {outdir / 'INDEX.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
