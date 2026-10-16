"""知识源（萌娘百科 / Arcaea 维基）的单元测试 —— 全程离线，假 transport。

这里守的两个行为都是**实测撞出来的**：
- 萌娘百科禁掉了全文搜索，只能用 opensearch 做前缀匹配
- Arcaea 维基的 HTTPS 大概每两次断一次，不重试等于没抓
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.services.wiki import (  # noqa: E402
    LocalCorpus,
    MediaWiki,
    WikiError,
    build_knowledge_tools,
    strip_wikitext,
)

MOEGIRL = "https://zh.moegirl.org.cn/api.php"


class FakeFetch:
    """假 transport：按理好的 action 返回预设内容，并记下每次请求的参数。

    键可以是 `action`（如 `"query"`），也可以是 `"action:generator"`
    （如 `"query:search"`）—— 后者优先，用来区分同一 action 下的不同搜索模式。
    """

    def __init__(self, **by_action) -> None:
        self.by_action = by_action
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, timeout: float) -> dict:
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.calls.append(params)

        action = params.get("action", "")
        generator = params.get("generator")
        key = f"{action}:{generator}" if generator else action

        for candidate in (key, action):
            if candidate in self.by_action:
                value = self.by_action[candidate]
                if isinstance(value, Exception):
                    raise value
                if callable(value):
                    return value(params)
                return value
        raise AssertionError(f"这个用例没预设 {key!r} 的响应")


def wiki(fetch, *, retries: int = 2) -> MediaWiki:
    return MediaWiki(MOEGIRL, label="测试维基", retries=retries, min_interval=0, fetch=fetch)


def generated(*pages: tuple[str, str]) -> dict:
    """generator 搜索的返回形状：标题 + `extract`，并带 `index` 表示相关性顺序。"""
    return {
        "query": {
            "pages": [
                {"pageid": i, "ns": 0, "title": title, "index": i, "extract": extract}
                for i, (title, extract) in enumerate(pages, start=1)
            ]
        }
    }


def extracts(title: str, text: str) -> dict:
    return {
        "batchcomplete": True,
        "query": {"pages": [{"pageid": 1, "ns": 0, "title": title, "extract": text}]},
    }


def wikitext(title: str, text: str) -> dict:
    return {
        "query": {
            "pages": [
                {"pageid": 1, "ns": 0, "title": title,
                 "revisions": [{"slots": {"main": {"content": text}}}]}
            ]
        }
    }


class SearchTest(unittest.TestCase):
    def test_uses_full_text_search_with_content_in_one_request(self):
        """⚠️ **回归：以前只用前缀匹配，于是长一点的查询必然落空。**

        实测：机器人换来换去查了六次全是空（每次只返回 24 字"没找到"），
        把工具轮次耗光。根因是 `opensearch` 只做前缀匹配 ——
        「小米15 eSIM」对不上任何标题前缀，而「eSIM」会匹配到
        「Eliminator 小枫」这种毫不相干的东西。

        `list=search` 是被萌娘禁了，但 **`generator=search` 没禁** —— 那是真全文检索。
        """
        fetch = FakeFetch(
            **{"query:search": generated(("初音未来", "正文"), ("我去，初音未来！", "梗条目"))}
        )
        hits = wiki(fetch).search("初音未来")

        self.assertEqual([h.title for h in hits], ["初音未来", "我去，初音未来！"])
        self.assertEqual(fetch.calls[0]["generator"], "search")
        self.assertNotIn("list", fetch.calls[0], "别用被禁的 list=search")

    def test_falls_back_to_prefix_search(self):
        """全文搜没结果时，退回前缀匹配再试一次。"""
        fetch = FakeFetch(
            **{
                "query:search": generated(),
                "query:prefixsearch": generated(("小米10至尊纪念版娘", "正文")),
            }
        )
        hits = wiki(fetch).search("小米15")

        self.assertEqual([h.title for h in hits], ["小米10至尊纪念版娘"])
        self.assertEqual(
            [c.get("generator") for c in fetch.calls], ["search", "prefixsearch"]
        )

    def test_no_results_at_all(self):
        fetch = FakeFetch(**{"query:search": generated(), "query:prefixsearch": generated()})
        self.assertEqual(wiki(fetch).search("不存在的东西"), ())

    def test_pages_are_ordered_by_the_index_field(self):
        """带 generator 时返回的页序不保证，得按 `index` 排 —— 那才是相关性顺序。"""
        response = generated(("第一", "a"), ("第二", "b"), ("第三", "c"))
        response["query"]["pages"].reverse()
        fetch = FakeFetch(**{"query:search": response})

        self.assertEqual([h.title for h in wiki(fetch).search("x")], ["第一", "第二", "第三"])


class ExtractTest(unittest.TestCase):
    def test_prefers_the_extracts_extension(self):
        fetch = FakeFetch(
            query=lambda params: extracts("条目", "正文正文")
            if params["prop"] == "extracts"
            else wikitext("条目", "'''别的'''"),
        )
        self.assertEqual(wiki(fetch).extract("条目"), "正文正文")

    def test_falls_back_to_raw_wikitext(self):
        """不是每个维基都装了 TextExtracts 扩展 —— 取不到就改抓 wikitext。"""
        def query(params):
            if params["prop"] == "extracts":
                return {"query": {"pages": [{"title": "条目", "missing": True}]}}
            return wikitext("条目", "== 标题 ==\n'''粗体'''正文[[链接|显示]]")

        fetch = FakeFetch(query=query)
        text = wiki(fetch).extract("条目")

        self.assertIn("正文", text)
        self.assertIn("显示", text)
        self.assertNotIn("'''", text)
        self.assertNotIn("==", text)

    def test_missing_page_returns_empty_string(self):
        fetch = FakeFetch(
            query=lambda params: {"query": {"pages": [{"title": "x", "missing": True}]}}
        )
        self.assertEqual(wiki(fetch).extract("x"), "")

    def test_long_text_is_trimmed(self):
        fetch = FakeFetch(query=lambda params: extracts("条目", "啊" * 5000))
        self.assertLessEqual(len(wiki(fetch).extract("条目", max_chars=100)), 110)


class FailureTest(unittest.TestCase):
    def test_transient_disconnect_is_retried(self):
        """⚠️ 回归：Arcaea 维基大约每两次断一次。

        把单次失败当成"条目不存在"，会让整批抓取静默地漏掉一半。
        """
        state = {"n": 0}

        def flaky(params):
            state["n"] += 1
            if state["n"] == 1:
                raise urllib.error.URLError("SSL: UNEXPECTED_EOF_WHILE_READING")
            return generated(("条目", "正文"))

        fetch = FakeFetch(**{"query:search": flaky})
        hits = wiki(fetch, retries=3).search("条目")

        self.assertEqual([hit.title for hit in hits], ["条目"])
        self.assertEqual(state["n"], 2, "第一次失败后应该自动重试")

    def test_giving_up_says_how_many_tries_were_made(self):
        down = urllib.error.URLError("断连")
        fetch = FakeFetch(**{"query:search": down, "query:prefixsearch": down})
        with self.assertRaises(WikiError) as caught:
            wiki(fetch, retries=3).search("条目")

        self.assertIn("3 次", str(caught.exception))

    def test_blocked_api_is_reported_as_such(self):
        """被禁用的接口要一眼看出来是"权限"问题，而不是"网络"问题 ——
        否则会朝着完全错误的方向排查。"""
        blocked = {"error": {"code": "action-notallowed", "info": "Unauthorized API call"}}
        fetch = FakeFetch(**{"query:search": blocked, "query:prefixsearch": blocked})

        with self.assertRaises(WikiError) as caught:
            wiki(fetch).search("测试")

        message = str(caught.exception)
        self.assertIn("action-notallowed", message)
        self.assertIn("不允许", message)


class LookupTest(unittest.TestCase):
    def test_formats_hits_into_blocks(self):
        fetch = FakeFetch(**{"query:search": generated(("咲弥", "咲弥是……"))})
        text = wiki(fetch).lookup("咲弥")

        self.assertIn("《咲弥》", text)
        self.assertIn("咲弥是……", text)
        self.assertEqual(len(fetch.calls), 1, "正文跟着搜索一起回来，一次请求就够")

    def test_says_so_when_nothing_is_found(self):
        fetch = FakeFetch(**{"query:search": generated(), "query:prefixsearch": generated()})
        self.assertIn("没有找到", wiki(fetch).lookup("不存在"))

    def test_offers_candidate_titles_when_there_is_no_body(self):
        """⚠️ 搜到标题却取不到正文时，**把候选给出去**。

        以前这种情况回一句"正文没取到"就完了 —— 机器人只能自己硬猜标题，
        猜错又是一轮空手（实测就是连猜六次）。
        """
        fetch = FakeFetch(**{"query:search": generated(("初音未来", ""), ("镜音铃", ""))})
        text = wiki(fetch).lookup("初音")

        self.assertIn("《初音未来》", text)
        self.assertIn("《镜音铃》", text)
        self.assertIn("准确的标题", text)


class StripWikitextTest(unittest.TestCase):
    def test_removes_markup_but_keeps_words(self):
        raw = (
            "{{Infobox|名字=甲}}\n"
            "'''甲'''是[[作品]]里的[[角色|一个人物]]。<!-- 注释 -->\n"
            "* 第一条\n"
            "== 章节 ==\n"
            "<ref>引用</ref>正文"
        )
        text = strip_wikitext(raw)

        self.assertIn("甲", text)
        self.assertIn("一个人物", text)
        self.assertIn("正文", text)
        for junk in ("{{", "}}", "[[", "]]", "'''", "<!--", "<ref", "=="):
            self.assertNotIn(junk, text)


class LocalCorpusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write_corpus(self) -> None:
        (self.dir / "01-ch1.txt").write_text("咲弥与对立的第一次相遇。", encoding="utf-8")
        (self.dir / "02-ch2.txt").write_text("关于光与碎片的记述。", encoding="utf-8")
        (self.dir / "index.json").write_text(
            json.dumps(
                [
                    {"title": "第一章", "file": "01-ch1.txt", "summary": "相遇", "keywords": "咲弥,对立"},
                    {"title": "第二章", "file": "02-ch2.txt", "summary": "碎片", "keywords": "光,碎片"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_not_available_without_an_index(self):
        corpus = LocalCorpus(self.dir)
        self.assertFalse(corpus.available)
        self.assertEqual(corpus.search("咲弥"), [])

    def test_available_once_the_index_exists(self):
        self.write_corpus()
        self.assertTrue(LocalCorpus(self.dir).available)

    def test_search_matches_keywords_and_reads_the_file(self):
        self.write_corpus()
        results = LocalCorpus(self.dir).search("对立")

        self.assertEqual(len(results), 1)
        doc, body = results[0]
        self.assertEqual(doc["title"], "第一章")
        self.assertIn("咲弥", body)

    def test_search_ranks_the_exact_hit_first(self):
        self.write_corpus()
        results = LocalCorpus(self.dir).search("碎片")
        self.assertEqual(results[0][0]["title"], "第二章")

    def test_unmatched_query_returns_nothing(self):
        self.write_corpus()
        self.assertEqual(LocalCorpus(self.dir).search("完全无关的词"), [])

    def test_read_refuses_to_escape_the_directory(self):
        """语料文件名来自索引文件，仍然是**外部输入** —— 不许越出目录。"""
        (self.dir.parent / "secret.txt").write_text("机密", encoding="utf-8")
        self.addCleanup(lambda: (self.dir.parent / "secret.txt").unlink(missing_ok=True))

        self.assertEqual(LocalCorpus(self.dir).read("../secret.txt"), "")

    def test_search_finds_terms_that_only_appear_in_the_body(self):
        """⚠️ 回归：以前只搜摘要与关键词，**正文里的词一个也搜不到**。

        实测就是靠这条把「无垢花园」这类只在正文出现的词捞回来的 ——
        而那恰恰是最常被问到的那些词。
        """
        (self.dir / "01.txt").write_text("正文里提到了无垢花园这个词。", encoding="utf-8")
        (self.dir / "index.json").write_text(
            json.dumps(
                [{"title": "第一章", "file": "01.txt", "summary": "无关摘要", "keywords": "无关"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        results = LocalCorpus(self.dir).search("无垢花园")

        self.assertEqual(len(results), 1)
        self.assertIn("无垢花园", results[0][1])

    def test_excerpt_jumps_to_the_match_when_it_is_deep_in_the_text(self):
        """命中点在很后面时要从命中处截 —— 否则模型拿到开头，找不到它问的东西。"""
        (self.dir / "01.txt").write_text("铺垫" * 800 + "关键的那句话" + "尾声" * 800, encoding="utf-8")
        (self.dir / "index.json").write_text(
            json.dumps([{"title": "第一章", "file": "01.txt", "summary": "", "keywords": ""}],
                       ensure_ascii=False),
            encoding="utf-8",
        )

        _, excerpt = LocalCorpus(self.dir, max_chars=300).search("关键的那句话")[0]

        self.assertIn("关键的那句话", excerpt)

    def test_duplicate_chapters_prefer_the_chinese_only_version(self):
        """同一章有母本与中英对照两版时只留一份。

        对照版篇幅约为母本的三倍，给模型看一眼的用途上又贵又没必要；
        两版都返回也会让结果显得乱七八糟。
        """
        (self.dir / "cn.txt").write_text("中文母本：咲弥在这里。", encoding="utf-8")
        (self.dir / "bi.txt").write_text("Saya is here. 咲弥在这里。", encoding="utf-8")
        (self.dir / "index.json").write_text(
            json.dumps(
                [
                    {"title": "18-1", "file": "cn.txt", "summary": "", "keywords": "",
                     "source": "剧情母本（中文）", "chars": 10},
                    {"title": "18-1", "file": "bi.txt", "summary": "", "keywords": "",
                     "source": "B站 5.9 主线（中英对照）", "chars": 26},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        results = LocalCorpus(self.dir).search("咲弥")

        self.assertEqual(len(results), 1, "同一个章节号不该出现两次")
        self.assertEqual(results[0][0]["file"], "cn.txt")

    def test_broken_index_degrades_quietly(self):
        (self.dir / "index.json").write_text("{不是 JSON", encoding="utf-8")
        corpus = LocalCorpus(self.dir)
        self.assertEqual(corpus.search("咲弥"), [])


class BuildToolsTest(unittest.TestCase):
    def test_exposes_both_lookups(self):
        box = build_knowledge_tools(moegirl_api=MOEGIRL, corpus_dir=Path("不存在的语料目录"))
        self.assertEqual(set(box.names), {"arcaea_lookup", "moegirl_lookup"})

    def test_missing_query_is_answered_not_raised(self):
        box = build_knowledge_tools(moegirl_api=MOEGIRL, corpus_dir=Path("不存在的语料目录"))
        self.assertIn("没给", box.call("moegirl_lookup", "{}"))

    def test_arcaea_falls_back_to_moegirl_without_a_corpus(self):
        """⚠️ Arcaea 官方中文维基已放弃（Anubis 反爬，不绕过）。

        所以没有本地语料时，这一路必须能**退到萌娘百科**，而不是抛异常或返回空 ——
        否则模型会以为"查过了，什么都没有"。
        """
        fetch = FakeFetch(**{"query:search": generated(("咲弥", "咲弥是 Arcaea 的角色。"))})
        box = build_knowledge_tools(
            moegirl_api=MOEGIRL, corpus_dir=Path("不存在的语料目录"), fetch=fetch
        )

        result = box.call("arcaea_lookup", '{"query": "咲弥"}')

        self.assertIn("咲弥是 Arcaea 的角色。", result)
        self.assertEqual(fetch.calls[0]["action"], "query", "退路走的是萌娘百科")
        self.assertEqual(fetch.calls[0]["generator"], "search")

    def test_local_corpus_wins_over_the_online_lookup(self):
        """有本地语料就先查本地 —— 又快，又能翻到百科里没有的剧情原文。"""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "ch1.txt").write_text("咲弥站在碎片之前。", encoding="utf-8")
            (directory / "index.json").write_text(
                json.dumps(
                    [{"title": "第一章", "file": "ch1.txt", "summary": "", "keywords": "咲弥"}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            fetch = FakeFetch(**{"query:search": AssertionError("不该联网")})  # type: ignore[arg-type]

            box = build_knowledge_tools(
                moegirl_api=MOEGIRL, corpus_dir=directory, fetch=fetch
            )
            result = box.call("arcaea_lookup", '{"query": "咲弥"}')

        self.assertIn("本地剧情文本", result)
        self.assertIn("碎片之前", result)
        self.assertEqual(fetch.calls, [], "有本地语料时不该发起任何网络请求")


if __name__ == "__main__":
    unittest.main()
