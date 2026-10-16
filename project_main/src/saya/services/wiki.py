"""知识源：萌娘百科 + Arcaea 中文维基（FR-6，也就是"接梗"）。

两个站都是 MediaWiki，所以走 **API 而不是解析 HTML** —— 页面结构一改，
解析就废；API 是稳定契约。仍只用标准库（urllib + json）。

⚠️ **两个站各自有脾气，都实测过**（2026-10-16）：

- **萌娘百科**：`list=search`、`action=parse`、取原始 wikitext **全被禁**
  （返回 `action-notallowed: Unauthorized API call`）。能用的是
  `action=opensearch`（找条目）与 `action=query&prop=extracts`（取正文）。
  所以这里的检索只有"按前缀模糊匹配"这一条路，**关键词命中不了就是找不到**。
- **Arcaea 中文维基（`wiki.arcaea.cn`）已经放弃**：它挂在 Anubis 反爬后面
  （PoW 挑战页），实测直接拒连。按项目伦理红线**不做绕过**，详情见下方
  `build_knowledge_tools`。Arcaea 的内容改从萌娘百科取（那边有条目）。

**拿回来的全是外来的、不可信的文本**，进提示词之前照样得裹进不可信区 ——
维基是人人可编辑的，它和群友说的话没有本质区别。
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from saya.core.tools import Tool, ToolBox, object_schema

JSON = dict[str, Any]
logger = logging.getLogger("saya.wiki")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
MIN_INTERVAL_SECONDS = 0.6  # 对人家服务器客气点


class WikiError(RuntimeError):
    """查维基失败。信息要能指向真正的原因（被禁 / 断连 / 没这个条目）。"""


def _ssl_context() -> ssl.SSLContext:
    """容忍"没打招呼就挂断"的连接。

    某些维基（实测 Arcaea 那个）会在握手后直接断连，Python 默认把这当成协议错误。
    对**只读的 GET** 开这个口子没有安全影响：我们不复用连接，也不发任何数据。
    """
    context = ssl.create_default_context()
    if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
        context.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
    return context


def _get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        body = response.read().decode("utf-8", "replace")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise WikiError(f"返回的不是 JSON：{body[:200]}") from None
    # ⚠️ 顶层**未必是对象**：opensearch 返回的就是一个数组。
    if not isinstance(data, (dict, list)):
        raise WikiError(f"返回的 JSON 顶层既不是对象也不是数组：{body[:200]}")
    return data


@dataclass(frozen=True, slots=True)
class WikiHit:
    title: str
    description: str = ""
    url: str = ""


class MediaWiki:
    """一个 MediaWiki 站点的只读客户端。"""

    def __init__(
        self,
        api_url: str,
        *,
        label: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        min_interval: float = MIN_INTERVAL_SECONDS,
        fetch: Any = None,
    ) -> None:
        self.api_url = api_url
        self.label = label or urllib.parse.urlsplit(api_url).hostname or "wiki"
        self._timeout = timeout
        self._retries = max(1, retries)
        self._min_interval = min_interval
        self._fetch = fetch or _get_json
        self._lock = threading.Lock()
        self._last_call = 0.0

    # ---- 底层 ----

    def _call(self, params: JSON) -> Any:
        """发一次 API 请求。返回值的形状**随 action 而变**：
        `query` 系列给对象，`opensearch` 给数组。
        """
        query = {"format": "json", "formatversion": "2", **params}
        url = f"{self.api_url}?{urllib.parse.urlencode(query)}"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

        last: Exception | None = None
        for attempt in range(self._retries):
            self._throttle()
            try:
                data = self._fetch(url, headers, self._timeout)
            except (urllib.error.URLError, ssl.SSLError, OSError, WikiError) as exc:
                last = exc
                # ⚠️ 断连**不代表条目不存在**，所以这里必须重试而不是直接放弃
                logger.debug("%s 第 %d 次请求失败：%s", self.label, attempt + 1, exc)
                continue

            if isinstance(data, dict) and data.get("error"):
                error = data["error"]
                code = str(error.get("code", ""))
                if code == "action-notallowed":
                    raise WikiError(
                        f"{self.label} 不允许这个 API 调用（{code}）——"
                        f"该站限制了匿名访问，需要换一种取法"
                    )
                raise WikiError(f"{self.label} 返回错误：{code} {error.get('info', '')}")
            return data

        raise WikiError(
            f"{self.label} 连试 {self._retries} 次都没连上：{type(last).__name__}: {last}"
        )

    def _throttle(self) -> None:
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def page_url(self, title: str) -> str:
        base = self.api_url.rsplit("/", 1)[0]
        return f"{base}/{urllib.parse.quote(title.replace(' ', '_'))}"

    # ---- 检索 ----

    def search(self, query: str, *, limit: int = 3) -> tuple[WikiHit, ...]:
        """找条目。

        ⚠️ **萌娘百科禁的是 `list=search`，不是 `generator=search`** —— 实测
        `generator=` 那套全放行。这个区别很要紧：

        - `opensearch` / `prefixsearch` 只做**前缀匹配**，于是
          「小米15 eSIM」这种带修饰的查询**必然落空**，而「eSIM」会匹配到
          「Eliminator 小枫」这种毫不相干的东西（拉丁字母前缀毫无意义）。
        - `generator=search` 是**真全文检索**。实测「初音未来」能查回
          条目正文 446 字，还带出「我去，初音未来！」这种梗条目。

        以前只用前缀匹配，机器人换来换去查了六次全是空，把工具轮次耗光。
        """
        return tuple(
            WikiHit(title=title, url=url)
            for title, url, _ in self._search_pages(query, limit=limit)
        )

    def _search_pages(self, query: str, *, limit: int) -> list[tuple[str, str, str]]:
        """先全文搜，没结果再退到前缀匹配。

        ⚠️ **两种模式都失败时，要把错误抛出去。** 全吞掉的话，一个被禁的接口
        会伪装成"没找到这个条目" —— 排查方向就完全错了（要么去改查询词，
        要么以为人家真没这个条目，而真相是接口根本不能用）。
        """
        last: WikiError | None = None
        for mode in ("search", "prefixsearch"):
            try:
                pages = self._pages(query, limit=limit, mode=mode)
            except WikiError as exc:
                last = exc
                continue
            if pages:
                return pages
        if last is not None:
            raise last
        return []

    def _pages(
        self, query: str, *, limit: int, mode: str = "search"
    ) -> list[tuple[str, str, str]]:
        """搜条目，**顺带把正文一起拿回来**（一次请求搞定，对人家服务器也客气）。

        mode: `search`（全文，首选）/ `prefixsearch`（前缀，退路）。
        返回 [(标题, 网页地址, 正文)]，按相关性排序。
        """
        key = "gsrsearch" if mode == "search" else "gpssearch"
        cap = "gsrlimit" if mode == "search" else "gpslimit"
        data = self._call(
            {
                "action": "query",
                "generator": mode,
                key: query,
                cap: str(max(1, limit)),
                "prop": "extracts",
                "explaintext": "1",
                "exintro": "1",
            }
        )

        pages = (data.get("query") or {}).get("pages") or []
        if not isinstance(pages, list):
            return []
        # ⚠️ 带 generator 时返回的页序不保证，得按 `index` 排 —— 那才是相关性顺序
        ordered = sorted(pages, key=lambda page: page.get("index", 99))
        return [
            (
                str(page.get("title", "")),
                self.page_url(str(page.get("title", ""))),
                str(page.get("extract") or "").strip(),
            )
            for page in ordered
            if page.get("title")
        ]

    def _try_extracts(self, title: str, *, max_chars: int) -> str:
        """首选：TextExtracts 扩展。不是每个站都装了这个扩展。"""
        try:
            data = self._call(
                {
                    "action": "query",
                    "titles": title,
                    "prop": "extracts",
                    "explaintext": "1",
                    "redirects": "1",
                }
            )
        except WikiError as exc:
            logger.debug("%s 取正文失败（extracts）：%s", self.label, exc)
            return ""

        for page in (data.get("query") or {}).get("pages") or []:
            extract = str(page.get("extract") or "").strip()
            if extract:
                return _trim(extract, max_chars)
        return ""

    def extract(self, title: str, *, max_chars: int = 1200) -> str:
        """按**准确标题**取正文。取不到就返回空串（调用方自己决定怎么说）。"""
        text = self._try_extracts(title, max_chars=max_chars)
        if text:
            return text
        return self._try_raw_wikitext(title, max_chars=max_chars)

    def lookup(self, query: str, *, hits: int = 2, max_chars: int = 900) -> str:
        """给模型用的成品：查一次，把最相关的几个条目的开头拼成一段文本。

        首选全文检索（连正文一起回来，**一次请求**）；它没结果再退到前缀匹配。
        """
        pages = self._search_pages(query, limit=hits)
        if not pages:
            return (
                f"{self.label}里没有找到「{query}」相关的条目。"
                f"换个说法或换一个更接近条目名的词再试。"
            )

        blocks = [
            f"《{title}》（{url}）\n{_trim(extract, max_chars)}"
            for title, url, extract in pages
            if extract
        ]
        if blocks:
            return "\n\n".join(blocks)

        # 搜到了标题但一条正文都没取到 —— 把候选给出去，让模型拿**准确标题**再查一次，
        # 而不是自己硬猜（猜错就又是一轮空手）
        names = "、".join(f"《{title}》" for title, _, _ in pages)
        return (
            f"{self.label}里有这些相关条目：{names}。"
            f"用其中**准确的标题**再查一次就能拿到正文。"
        )

    def _try_raw_wikitext(self, title: str, *, max_chars: int) -> str:
        """兜底：直接取 wikitext，再粗洗一遍标记。凑合能读，不保证好看。"""
        try:
            data = self._call(
                {
                    "action": "query",
                    "titles": title,
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                    "redirects": "1",
                }
            )
        except WikiError as exc:
            logger.debug("%s 取正文失败（wikitext）：%s", self.label, exc)
            return ""

        for page in (data.get("query") or {}).get("pages") or []:
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            content = ((revisions[0].get("slots") or {}).get("main") or {}).get("content") or ""
            cleaned = strip_wikitext(str(content))
            if cleaned:
                return _trim(cleaned, max_chars)
        return ""

def strip_wikitext(text: str) -> str:
    """把 wikitext 粗洗成能读的文本。够用就行 —— 它是给模型看的，不是拿去发布的。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\|.*?\|\}", "", text, flags=re.S)  # 表格
    text = re.sub(r"\{\{[^{}]*\}\}", "", text, flags=re.S)  # 模板
    text = re.sub(r"\{\{[^{}]*\}\}", "", text, flags=re.S)  # 嵌套一层的再来一遍
    text = re.sub(r"\[\[[^\[\]|]*\|([^\[\]]*)\]\]", r"\1", text)  # [[A|B]] → B
    text = re.sub(r"\[\[([^\[\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"^[=]{2,}.*?[=]{2,}$", "", text, flags=re.M)  # 标题行
    text = re.sub(r"^[*#:;]+", "", text, flags=re.M)  # 列表符号
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _trim(text: str, max_chars: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ---- 本地语料（Arcaea 剧情全文）----


class LocalCorpus:
    """抓下来存在本地的剧情文本，优先于在线查询。

    在线查是**慢且不稳**的，而剧情全文本来就不长，
    存一份在本地又快又能翻得更深。索引由抓取脚本生成。

    **正文也进内存**：整个语料 30 余万字，全读一遍实测约 12 ms ——
    换来的是"能搜正文"而不只是"搜摘要"。这一点很重要：
    搜摘要会漏掉大量只在正文里出现过的词（实测「命运之书」就漏了）。
    """

    def __init__(self, directory: Path | str, *, max_chars: int = 1200) -> None:
        self.directory = Path(directory)
        self.max_chars = max_chars
        self._docs: list[JSON] | None = None
        self._bodies: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return (self.directory / "index.json").is_file()

    def _load(self) -> list[JSON]:
        if self._docs is not None:
            return self._docs

        path = self.directory / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("本地语料索引读不了（%s）：%s", path, exc)
            self._docs = []
            return self._docs

        self._docs = [doc for doc in data if isinstance(doc, dict)] if isinstance(data, list) else []
        for doc in self._docs:
            filename = str(doc.get("file", ""))
            if filename and filename not in self._bodies:
                self._bodies[filename] = self._read_file(filename)
        logger.debug("本地语料已载入：%d 篇", len(self._docs))
        return self._docs

    def search(self, query: str, *, limit: int = 2) -> list[tuple[JSON, str]]:
        """按字面重合度打分。

        三路信号，从强到弱：**整串命中标题/摘要/关键词** > **整串命中正文** >
        **2 字片段命中元数据**。最后那一路是兜底：中文没有词边界，
        整串对不上时（换了说法、多个字）还能靠碎片捞回来。
        """
        docs = self._load()
        query = query.strip()
        if not docs or not query:
            return []

        grams = _grams(query)
        scored: list[tuple[int, JSON]] = []
        for doc in docs:
            meta = " ".join(
                str(doc.get(key, "")) for key in ("title", "summary", "keywords")
            )
            body = self._bodies.get(str(doc.get("file", "")), "")

            score = 0
            if query in meta:
                score += 5
            if query in body:
                score += 3
                score += min(body.count(query), 5)  # 出现得多更相关，但别让长文霸榜
            score += sum(1 for gram in grams if gram in meta)

            if score:
                scored.append((score, doc))

        scored.sort(key=lambda item: item[0], reverse=True)
        picked = _dedupe_by_title(scored)[:limit]
        return [
            (doc, self._excerpt(str(doc.get("file", "")), query)) for _, doc in picked
        ]

    def _excerpt(self, filename: str, query: str) -> str:
        """给模型看的正文片段。

        命中点在开头就直接给开头；命中点在后面（正文被截断掉的那部分），
        **就从命中处截一段** —— 否则模型拿到的是开头，找不到它问的东西。
        """
        body = self._bodies.get(filename, "")
        limit = self.max_chars
        if len(body) <= limit:
            return body

        at = body.find(query)
        if at <= limit // 2:
            return _trim(body, limit)

        start = max(0, at - limit // 4)
        excerpt = body[start : start + limit]
        prefix = "…" if start else ""
        return f"{prefix}{excerpt}…" if len(body) > start + limit else f"{prefix}{excerpt}"

    def read(self, filename: str, *, max_chars: int | None = None) -> str:
        text = self._read_file(filename)
        return _trim(text, max_chars or self.max_chars) if text else ""

    def _read_file(self, filename: str) -> str:
        if not filename:
            return ""
        path = (self.directory / filename).resolve()
        try:
            path.relative_to(self.directory.resolve())
        except ValueError:
            logger.warning("语料文件名越出目录，已拒绝：%s", filename)
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""


def _grams(query: str) -> list[str]:
    cleaned = re.sub(r"\s+", "", query)
    if len(cleaned) <= 2:
        return [cleaned] if cleaned else []
    return list({cleaned[index : index + 2] for index in range(len(cleaned) - 1)})


def _dedupe_by_title(scored: list[tuple[int, JSON]]) -> list[tuple[int, JSON]]:
    """同一个章节号有两个版本时只留一个。

    实测有 7 章（`18-1`～`18-7`）同时存在**中文母本**与 **B 站中英对照版**，
    后者篇幅约为前者的 3.4 倍。给模型看一眼的用途上，双语对照**又贵又没必要**，
    而且同一条内容出现两次也会让结果显得乱七八糟。

    → 留**纯中文那份**。对照版仍在 `corpus/` 里，要核对官方译名时直接去读就行。
    """
    best: dict[str, tuple[int, JSON]] = {}
    for score, doc in scored:
        title = str(doc.get("title", "")) or str(doc.get("file", ""))
        current = best.get(title)
        if current is None or _prefers(doc, current[1]):
            best[title] = (score, doc)
    return sorted(best.values(), key=lambda item: item[0], reverse=True)


def _prefers(candidate: JSON, current: JSON) -> bool:
    """候选该不该取代当前这份。同版本时取更短的那份。"""
    candid_bilingual = "中英" in str(candidate.get("source", ""))
    current_bilingual = "中英" in str(current.get("source", ""))
    if candid_bilingual != current_bilingual:
        return not candid_bilingual
    return int(candidate.get("chars") or 0) < int(current.get("chars") or 0)


# ---- 组装成给模型用的工具 ----


def build_knowledge_tools(
    *,
    moegirl_api: str,
    corpus_dir: Path | str,
    fetch: Any = None,
) -> ToolBox:
    """组装给模型用的查询工具。

    ⚠️ **在线查询只有一个源：萌娘百科。** Arcaea 自己的中文维基
    （`wiki.arcaea.cn`）挂在 Anubis 反爬后面（PoW 挑战），实测已经拒绝我们了。
    按项目的伦理红线，**不做绕过** —— 不实现 PoW 求解、不伪造浏览器指纹、不换 IP。
    好在萌娘百科有完整的 Arcaea 条目（含「咲弥」），够用。

    Arcaea 的**深度**内容靠本地剧情语料（如果抓到了）；没抓到就只有百科那一层。

    `fetch` 是留给测试的注入口（同 `LLMClient` 的 `transport`），生产上走默认实现。
    """
    moegirl = MediaWiki(moegirl_api, label="萌娘百科", fetch=fetch)
    corpus = LocalCorpus(corpus_dir)

    def arcaea_lookup(args: JSON) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "没给要查的词。"

        if corpus.available:
            local = corpus.search(query, limit=2)
            blocks = [
                f"《{doc.get('title', '?')}》（本地剧情文本）\n{body}"
                for doc, body in local
                if body
            ]
            if blocks:
                return "\n\n".join(blocks)

        return moegirl.lookup(query)

    def moegirl_lookup(args: JSON) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "没给要查的词。"
        return moegirl.lookup(query)

    return ToolBox(
        [
            Tool(
                name="arcaea_lookup",
                description=(
                    "查 Arcaea（韵律源点）的世界观、角色、剧情与专有名词。"
                    "被问到 Arcaea 的角色、故事、术语时用它。"
                ),
                parameters=object_schema(
                    {"query": {"type": "string", "description": "要查的词或句子"}},
                    required=("query",),
                ),
                handler=arcaea_lookup,
            ),
            Tool(
                name="moegirl_lookup",
                description=(
                    "查萌娘百科。被问到动画、游戏、角色、网络梗的来历，"
                    "而你不确定时用它。"
                ),
                parameters=object_schema(
                    {"query": {"type": "string", "description": "要查的词或句子"}},
                    required=("query",),
                ),
                handler=moegirl_lookup,
            ),
        ]
    )
