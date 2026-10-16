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
    """假 transport：按理好的 action 返回预设内容，并记下每次请求的参数。"""

    def __init__(self, **by_action) -> None:
        self.by_action = by_action
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, timeout: float) -> dict:
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.calls.append(params)

        action = params.get("action", "")
        if action not in self.by_action:
            raise AssertionError(f"这个用例没预设 action={action!r} 的响应")
        value = self.by_action[action]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(params)
        return value


def wiki(fetch, *, retries: int = 2) -> MediaWiki:
    return MediaWiki(MOEGIRL, label="测试维基", retries=retries, min_interval=0, fetch=fetch)


def opensearch(*titles: str, urls=()) -> list:
    return [
        titles[0] if titles else "",
        list(titles),
        [""] * len(titles),
        list(urls) or [f"https://zh.moegirl.org.cn/{t}" for t in titles],
    ]


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
    def test_reads_opensearch_results(self):
        fetch = FakeFetch(opensearch=opensearch("初音未来", "初音未来(世界计划)"))
        hits = wiki(fetch).search("初音未来")

        self.assertEqual([hit.title for hit in hits], ["初音未来", "初音未来(世界计划)"])
        self.assertTrue(hits[0].url.startswith("https://"))

    def test_no_results_is_an_empty_tuple(self):
        fetch = FakeFetch(opensearch=[[], [], [], []])
        self.assertEqual(wiki(fetch).search("不存在的东西"), ())

    def test_search_goes_through_opensearch_not_list_search(self):
        """⚠️ 回归：萌娘百科禁掉了 `list=search`（返回 action-notallowed）。

        换成它就会在真机上直接报错 —— 这个用例把"用哪个接口"钉死。
        """
        fetch = FakeFetch(opensearch=opensearch("测试"))
        wiki(fetch).search("测试")

        self.assertEqual(fetch.calls[0]["action"], "opensearch")
        self.assertNotIn("list", fetch.calls[0])


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
            return opensearch("条目")

        fetch = FakeFetch(opensearch=flaky)
        hits = wiki(fetch, retries=3).search("条目")

        self.assertEqual([hit.title for hit in hits], ["条目"])
        self.assertEqual(state["n"], 2, "第一次失败后应该自动重试")

    def test_giving_up_says_how_many_tries_were_made(self):
        fetch = FakeFetch(opensearch=urllib.error.URLError("断连"))
        with self.assertRaises(WikiError) as caught:
            wiki(fetch, retries=3).search("条目")

        self.assertIn("3 次", str(caught.exception))

    def test_blocked_api_is_reported_as_such(self):
        """被禁用的接口要一眼看出来是"权限"问题，而不是"网络"问题 ——
        否则会朝着完全错误的方向排查。"""
        blocked = {"error": {"code": "action-notallowed", "info": "Unauthorized API call"}}
        fetch = FakeFetch(opensearch=blocked)

        with self.assertRaises(WikiError) as caught:
            wiki(fetch).search("测试")

        message = str(caught.exception)
        self.assertIn("action-notallowed", message)
        self.assertIn("不允许", message)


class LookupTest(unittest.TestCase):
    def test_formats_hits_into_blocks(self):
        fetch = FakeFetch(
            opensearch=opensearch("咲弥"),
            query=lambda params: extracts("咲弥", "咲弥是……"),
        )
        text = wiki(fetch).lookup("咲弥")

        self.assertIn("《咲弥》", text)
        self.assertIn("咲弥是……", text)

    def test_says_so_when_nothing_is_found(self):
        fetch = FakeFetch(opensearch=opensearch())
        self.assertIn("没有找到", wiki(fetch).lookup("不存在"))

    def test_says_so_when_the_page_has_no_body(self):
        fetch = FakeFetch(
            opensearch=opensearch("空条目"),
            query=lambda params: {"query": {"pages": [{"title": "空条目", "missing": True}]}},
        )
        self.assertIn("正文没取到", wiki(fetch).lookup("空条目"))


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
        fetch = FakeFetch(
            opensearch=opensearch("咲弥"),
            query=lambda params: extracts("咲弥", "咲弥是 Arcaea 的角色。"),
        )
        box = build_knowledge_tools(
            moegirl_api=MOEGIRL, corpus_dir=Path("不存在的语料目录"), fetch=fetch
        )

        result = box.call("arcaea_lookup", '{"query": "咲弥"}')

        self.assertIn("咲弥是 Arcaea 的角色。", result)
        self.assertEqual(fetch.calls[0]["action"], "opensearch", "退路走的是萌娘百科")

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
            fetch = FakeFetch(opensearch=AssertionError("不该联网"))  # type: ignore[arg-type]

            box = build_knowledge_tools(
                moegirl_api=MOEGIRL, corpus_dir=directory, fetch=fetch
            )
            result = box.call("arcaea_lookup", '{"query": "咲弥"}')

        self.assertIn("本地剧情文本", result)
        self.assertIn("碎片之前", result)
        self.assertEqual(fetch.calls, [], "有本地语料时不该发起任何网络请求")


if __name__ == "__main__":
    unittest.main()
