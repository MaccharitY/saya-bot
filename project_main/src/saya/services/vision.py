"""看图（FR-5）。

**接口是稳定的**：`Vision.describe(image, question, route=...)` 返回统一的 `VisionResult`。
现在只实现路线 A（主模型多模态直接看图）；
`delegated`（路线 B）与 `hybrid`（路线 C）会**明确报"未实现"**，但接口与 meta 字段已经留好 ——
将来补上时，pipeline 与埋点一行都不用改。

路线定义见 `pre/12-experiment-design.md`（pre 已暂停，定义仍然有效）。
"""

from __future__ import annotations

import base64
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from saya.domain import ImageRef
from saya.services.cv_tools import CvTools, CvToolsError
from saya.services.llm import JSON, ChatResult, LLMClient, image_block, text_block

ROUTE_DIRECT = "direct"
ROUTE_DELEGATED = "delegated"
ROUTE_HYBRID = "hybrid"

ALL_ROUTES: tuple[str, ...] = (ROUTE_DIRECT, ROUTE_DELEGATED, ROUTE_HYBRID)
# 三条都实现了。但后两条**需要配套的附属**（专用 CV 模型），没配会在调用时报错。
IMPLEMENTED_ROUTES: tuple[str, ...] = ALL_ROUTES
NEEDS_TOOLS: tuple[str, ...] = (ROUTE_DELEGATED, ROUTE_HYBRID)

# 官方支持 JPEG / PNG / GIF / WebP，且**按文件实际内容判断**，不看扩展名。
# 请求体上限 48 MiB，base64 会放大约 1/3，所以原图留到这个数就够安全。
MAX_IMAGE_BYTES = 32 * 1024 * 1024
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) saya-bot/0.1"

Fetcher = Callable[[str, float], bytes]


class VisionError(RuntimeError):
    """看图失败。信息要能直接看懂原因（没实现 / 格式不支持 / 下载失败）。"""


@dataclass(frozen=True, slots=True)
class VisionResult:
    answer: str
    route: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost: float

    @classmethod
    def from_chat(cls, result: ChatResult, *, route: str) -> VisionResult:
        return cls(
            answer=result.text,
            route=route,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
            cost=result.cost,
        )


def sniff_mime(data: bytes) -> str | None:
    """按**文件头**判断图片格式。官方就是按内容判的，我们也照做。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def to_data_url(path: Path, *, max_bytes: int = MAX_IMAGE_BYTES) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise VisionError(
            f"图片太大（{len(data) // 1024 // 1024} MiB），超过 {max_bytes // 1024 // 1024} MiB 上限"
        )
    mime = sniff_mime(data)
    if mime is None:
        raise VisionError(f"不支持的图片格式：{path.name}（官方支持 JPEG / PNG / GIF / WebP）")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def download_image(
    url: str,
    *,
    cache_dir: Path,
    fetcher: Fetcher | None = None,
    timeout: float = 30.0,
) -> Path:
    """把图片存到本地缓存并返回路径。

    **QQ 的图片链接是临时链、会过期**，所以收到消息后要尽快下载。
    缓存键是 URL 的哈希，同一张图不会被下两次。
    """
    target = _cache_path(url, cache_dir)
    if target.is_file() and target.stat().st_size > 0:
        return target

    fetch = fetcher or _http_get
    data = fetch(url, timeout)
    if not data:
        raise VisionError(f"下载到的图片是空的：{url}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _cache_path(url: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    suffix = Path(urllib.parse.urlparse(url).path).suffix[:8] or ".img"
    return cache_dir / f"{digest}{suffix}"


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise VisionError(f"下载图片失败 HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise VisionError(f"下载图片连不上：{exc.reason}（{url}）") from exc


class Vision:
    def __init__(
        self,
        client: LLMClient,
        *,
        cache_dir: Path,
        route: str = ROUTE_DIRECT,
        temperature: float = 0.2,
        max_image_bytes: int = MAX_IMAGE_BYTES,
        fetcher: Fetcher | None = None,
        tools: CvTools | None = None,
    ) -> None:
        self._client = client
        self._cache_dir = Path(cache_dir)
        self._route = route
        self._temperature = temperature
        self._max_image_bytes = max_image_bytes
        self._fetcher = fetcher
        self._tools = tools

    @property
    def route(self) -> str:
        return self._route

    def describe(
        self,
        image: ImageRef,
        question: str,
        *,
        route: str | None = None,
        system_prompt: str | None = None,
    ) -> VisionResult:
        """单张图的便捷入口。"""
        return self.describe_many([image], question, route=route, system_prompt=system_prompt)

    def describe_many(
        self,
        images: Sequence[ImageRef],
        question: str,
        *,
        route: str | None = None,
        system_prompt: str | None = None,
    ) -> VisionResult:
        """一张或多张图。群聊里一条消息常带好几张图，所以核心实现是多图的。"""
        if not images:
            raise VisionError("没有提供任何图片")

        chosen = route or self._route
        if chosen == ROUTE_DIRECT:
            return self._describe_direct(list(images), question, system_prompt=system_prompt)
        if chosen == ROUTE_DELEGATED:
            return self._describe_delegated(list(images), question, system_prompt=system_prompt)
        if chosen == ROUTE_HYBRID:
            return self._describe_hybrid(list(images), question, system_prompt=system_prompt)
        raise VisionError(f"未知的看图路线：{chosen}")

    def _require_tools(self, route: str) -> CvTools:
        if self._tools is None:
            raise VisionError(
                f"路线「{route}」需要配套的附属（专用 CV 模型），但没配置。"
                f"请在 .env 里设 SAYA_CV_MCP_COMMAND 指向 cv_mcp/server.py。"
            )
        return self._tools

    def _describe_delegated(
        self, images: Sequence[ImageRef], question: str, *, system_prompt: str | None
    ) -> VisionResult:
        """路线 B：**图片完全不进主模型**，附属把图变成文字，主模型只读文字。"""
        tools = self._require_tools(ROUTE_DELEGATED)
        evidence = self._collect_evidence(tools, images)

        messages: list[JSON] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"{question}\n\n"
                    "以下是对图片的分析结果。**你并没有直接看到这张图**，"
                    "只能依据这些结果回答；结果里没有的信息就是不知道，不要脑补。\n"
                    f"{evidence}"
                ),
            }
        )

        result = self._client.chat(messages, temperature=self._temperature)
        return VisionResult.from_chat(result, route=ROUTE_DELEGATED)

    def _collect_evidence(self, tools: CvTools, images: Sequence[ImageRef]) -> str:
        sections: list[str] = []
        for index, image in enumerate(images, start=1):
            local = self._ensure_local(image)
            label = f"第 {index} 张图" if len(images) > 1 else "图片"
            lines = [f"【{label}】"]
            for title, call in (("目标检测", tools.detect), ("文字识别", tools.ocr)):
                try:
                    lines.append(f"{title}：\n{call(local)}")
                except CvToolsError as exc:
                    # 附属出错要说出来，不能装作"没检测到" —— 两者含义完全不同
                    lines.append(f"{title}：调用失败（{exc}）")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    def _describe_hybrid(
        self, images: Sequence[ImageRef], question: str, *, system_prompt: str | None
    ) -> VisionResult:
        """路线 C：附属出**带编号的结构化产物**，主模型基于 [标注图 + 编号说明] 推理。

        与 Set-of-Mark（arXiv 2310.11441）的做法一致：先用现成模型出候选，
        再把带标记的图交给多模态模型。
        """
        tools = self._require_tools(ROUTE_HYBRID)
        blocks: list[JSON] = [text_block(question)]
        notes: list[str] = []

        for index, image in enumerate(images, start=1):
            local = self._ensure_local(image)
            annotated = self._cache_dir / "annotated" / f"{local.stem}.marked{index}.png"
            note = ""
            try:
                note = tools.annotate(local, annotated)
            except CvToolsError as exc:
                note = f"第 {index} 张图标注失败（{exc}），退回原图"

            source = annotated if annotated.is_file() else local
            blocks.append(image_block(to_data_url(source, max_bytes=self._max_image_bytes)))
            notes.append(note)

        if notes:
            blocks.append(
                text_block("附属在图上标出的编号与类别（编号画在框的左上角）：\n" + "\n".join(notes))
            )

        messages: list[JSON] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": blocks})

        result = self._client.chat(messages, temperature=self._temperature)
        return VisionResult.from_chat(result, route=ROUTE_HYBRID)

    def _describe_direct(
        self, images: Sequence[ImageRef], question: str, *, system_prompt: str | None
    ) -> VisionResult:
        blocks = [text_block(question)]
        for image in images:
            local = self._ensure_local(image)
            blocks.append(image_block(to_data_url(local, max_bytes=self._max_image_bytes)))

        messages: list[JSON] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": blocks})

        result = self._client.chat(messages, temperature=self._temperature)
        return VisionResult.from_chat(result, route=ROUTE_DIRECT)

    def _ensure_local(self, image: ImageRef) -> Path:
        if image.local_path:
            path = Path(image.local_path)
            if not path.is_file():
                raise VisionError(f"本地图片不存在：{path}")
            return path
        return download_image(
            image.url, cache_dir=self._cache_dir, fetcher=self._fetcher
        )


def build_tool_result_content(parts: Sequence[str]) -> str:
    """把附属（CV 工具）的输出拼成回灌给模型的文本。路线 B/C 会用到，先放着。"""
    return "\n".join(part for part in parts if part)


__all__ = [
    "IMPLEMENTED_ROUTES",
    "ROUTE_DELEGATED",
    "ROUTE_DIRECT",
    "ROUTE_HYBRID",
    "Vision",
    "VisionError",
    "VisionResult",
    "build_tool_result_content",
    "download_image",
    "sniff_mime",
    "to_data_url",
]
