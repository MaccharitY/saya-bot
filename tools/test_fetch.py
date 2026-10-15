"""tools/fetch.py 的单元测试 —— 全部离线，不联网。

    python tools/test_fetch.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch import decode, extract_links, html_to_text  # noqa: E402


class HtmlToTextTest(unittest.TestCase):
    def test_strips_script_and_style(self):
        markup = (
            "<html><head><style>p{color:red}</style></head>"
            "<body><p>正文</p><script>var x=1</script></body></html>"
        )
        text = html_to_text(markup)
        self.assertIn("正文", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("var x=1", text)

    def test_block_elements_are_separated_by_a_blank_line(self):
        self.assertEqual(html_to_text("<div>一</div><div>二</div>"), "一\n\n二")

    def test_heading_becomes_its_own_block(self):
        self.assertEqual(html_to_text("<h1>标题</h1><p>正文</p>"), "标题\n\n正文")

    def test_entities_are_unescaped(self):
        self.assertEqual(html_to_text("<p>A&nbsp;&amp;&nbsp;B</p>"), "A & B")

    def test_blank_line_runs_are_collapsed(self):
        self.assertEqual(html_to_text("<p>甲</p><p></p><p></p><p>乙</p>"), "甲\n\n乙")

    def test_empty_input(self):
        self.assertEqual(html_to_text(""), "")


class DecodeTest(unittest.TestCase):
    def test_utf8(self):
        self.assertEqual(decode("中文".encode("utf-8"), None), "中文")

    def test_gb18030_fallback_when_nothing_declared(self):
        """中文站常不声明编码，gb18030 兜底是必需的。"""
        self.assertEqual(decode("中文".encode("gb18030"), None), "中文")

    def test_charset_from_meta_tag_wins(self):
        raw = b'<meta charset="gb2312"><p>\xd6\xd0\xce\xc4</p>'
        self.assertTrue(decode(raw, None).endswith("中文</p>"))

    def test_declared_charset_is_honoured(self):
        self.assertEqual(decode("中文".encode("utf-8"), "utf-8"), "中文")

    def test_undecodable_bytes_do_not_crash(self):
        self.assertIsInstance(decode(b"\xff\xfe\x00garbage", None), str)


class ExtractLinksTest(unittest.TestCase):
    def test_dedupes_and_skips_anchors(self):
        markup = (
            '<a href="/a">甲</a><a href="/a">甲二</a>'
            '<a href="#top">锚</a><a href="/b">乙</a>'
        )
        self.assertEqual([href for _, href in extract_links(markup)], ["/a", "/b"])

    def test_link_without_text_is_skipped(self):
        self.assertEqual(extract_links('<a href="/x"></a>'), [])


if __name__ == "__main__":
    unittest.main()
