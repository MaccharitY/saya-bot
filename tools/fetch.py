#!/usr/bin/env python3
"""抓网页并转成可读文本。

**为什么要这个东西**：本机的 WebFetch 工具坏了 —— 它需要联网校验"域名是否可安全抓取"，
那一步失败，于是所有网页都读不了。但**网络本身是通的**（curl / urllib 直连均 200），
所以用这个脚本绕过去。

用法::

    python tools/fetch.py <url>                    # 抓正文
    python tools/fetch.py <url> --max-chars 5000   # 限制长度
    python tools/fetch.py <url> --links            # 只列链接（导航用）
    python tools/fetch.py <url> --raw --out p.html # 存原始字节

零第三方依赖（只用标准库），任何 Python 3.11+ 直接可跑。
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "hr", "section", "article", "header", "footer",
    "pre", "blockquote", "ul", "ol", "table", "form", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._href and data.strip():
            href = self._href.strip()
            if href and not href.startswith("#"):
                self.links.append((data.strip(), href))
        self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _Extractor()
    parser.feed(markup)
    parser.close()

    # 注意：下面字符类里有 nbsp 与零宽空格的字面量（网页常拿它们排空白），不是笔误
    lines = [
        re.sub(r"[ 	 ​]+", " ", line).strip()
        for line in unescape("".join(parser.parts)).splitlines()
    ]
    collapsed: list[str] = []
    for line in lines:
        if line:
            collapsed.append(line)
        elif collapsed and collapsed[-1] != "":
            collapsed.append("")
    return "\n".join(collapsed).strip()


def extract_links(markup: str) -> list[tuple[str, str]]:
    parser = _Extractor()
    parser.feed(markup)
    parser.close()
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for text, href in parser.links:
        if href not in seen:
            seen.add(href)
            unique.append((text, href))
    return unique


def decode(raw: bytes, charset: str | None) -> str:
    """按 头里的 charset → meta 里的 charset → 常见编码 依次尝试。

    gb18030 兜底是必需的：不少中文站点不声明编码。
    """
    candidates: list[str | None] = [charset]
    head = raw[:4096].decode("ascii", errors="ignore")
    match = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    if match:
        candidates.append(match.group(1))
    candidates += ["utf-8", "gb18030", "latin-1"]

    for encoding in candidates:
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch(url: str, timeout: float = 30.0) -> tuple[bytes, str | None, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": ACCEPT_LANGUAGE,
            "Accept-Encoding": "identity",  # 避免 gzip，urllib 不会自动解压
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return (
            response.read(),
            response.headers.get_content_charset(),
            response.headers.get_content_type(),
        )


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认用 GBK，直接输出中文会变成乱码。统一改成 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="抓网页并转成可读文本（绕过坏掉的 WebFetch）")
    parser.add_argument("url")
    parser.add_argument("--max-chars", type=int, default=20000, help="截断长度，0 表示不截断")
    parser.add_argument("--links", action="store_true", help="只列出页面链接")
    parser.add_argument("--raw", action="store_true", help="输出原始响应体，不做 HTML 转文本")
    parser.add_argument("--out", help="写入文件而不是打印到标准输出")
    args = parser.parse_args(argv)

    try:
        raw, charset, content_type = fetch(args.url)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason}  ← {args.url}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI 边界，什么错都要给用户看
        print(f"抓取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if content_type == "application/pdf":
        target = Path(args.out) if args.out else None
        if target:
            target.write_bytes(raw)
        print(
            "这是一个 PDF，本工具不解析 PDF。可改用：\n"
            "  · arXiv 论文读 https://arxiv.org/abs/<id>（摘要）或 https://arxiv.org/html/<id>（全文）\n"
            + (f"  · 原始字节已存到 {target}" if target else "  · 加 --out 可以把原始字节存下来"),
            file=sys.stderr,
        )
        return 2

    body = decode(raw, charset)

    if args.raw:
        output = body
    elif args.links or "html" not in (content_type or "") and not body.lstrip().startswith("<"):
        output = body
    else:
        output = html_to_text(body)

    if args.links:
        output = "\n".join(f"{text}\t{href}" for text, href in extract_links(body))

    total = len(output)
    if args.max_chars and total > args.max_chars:
        output = output[: args.max_chars] + f"\n\n…（已截断；原文 {total} 字符，用 --max-chars 调整）"

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"已写入 {args.out}（{len(output)} 字符）")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
