"""看图模块的单元测试。全部离线：假 fetcher + 假 transport，不发真请求。

测试用的 1×1 透明 PNG（合法文件，不是随便几个字节）。
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.domain import ImageRef  # noqa: E402
from saya.services.cv_tools import CvToolsError, split_command  # noqa: E402
from saya.services.llm import LLMClient  # noqa: E402
from saya.services.vision import (  # noqa: E402
    NEEDS_TOOLS,
    ROUTE_DELEGATED,
    ROUTE_DIRECT,
    ROUTE_HYBRID,
    Vision,
    VisionError,
    download_image,
    sniff_mime,
    to_data_url,
)

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF_MAGIC = b"GIF89a" + b"\x00" * 16
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8


class SniffMimeTest(unittest.TestCase):
    def test_recognises_supported_formats(self):
        self.assertEqual(sniff_mime(PNG_1PX), "image/png")
        self.assertEqual(sniff_mime(JPEG_MAGIC), "image/jpeg")
        self.assertEqual(sniff_mime(GIF_MAGIC), "image/gif")
        self.assertEqual(sniff_mime(WEBP_MAGIC), "image/webp")

    def test_rejects_unknown(self):
        self.assertIsNone(sniff_mime(b"not an image at all"))

    def test_riff_that_is_not_webp_is_rejected(self):
        self.assertIsNone(sniff_mime(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8))


class DataUrlTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_builds_png_data_url(self):
        url = to_data_url(self._write("a.bin", PNG_1PX))
        self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_extension_is_ignored_content_wins(self):
        """官方按内容判格式，我们也一样：扩展名撒谎没关系。"""
        url = to_data_url(self._write("fake.txt", PNG_1PX))
        self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_unsupported_format_raises(self):
        with self.assertRaises(VisionError):
            to_data_url(self._write("a.txt", b"hello"))

    def test_oversized_image_raises(self):
        path = self._write("big.png", PNG_1PX)
        with self.assertRaises(VisionError):
            to_data_url(path, max_bytes=4)

    def test_round_trips_bytes(self):
        url = to_data_url(self._write("a.png", PNG_1PX))
        payload = base64.b64decode(url.split(",", 1)[1])
        self.assertEqual(payload, PNG_1PX)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "cache"

    def test_downloads_and_caches(self):
        calls: list[str] = []

        def fetcher(url: str, timeout: float) -> bytes:
            calls.append(url)
            return PNG_1PX

        first = download_image("https://example.com/a.jpg", cache_dir=self.cache, fetcher=fetcher)
        second = download_image("https://example.com/a.jpg", cache_dir=self.cache, fetcher=fetcher)

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, "同一 URL 不该下载两次")
        self.assertEqual(first.read_bytes(), PNG_1PX)

    def test_different_urls_get_different_files(self):
        fetcher = lambda url, timeout: PNG_1PX  # noqa: E731
        one = download_image("https://example.com/a", cache_dir=self.cache, fetcher=fetcher)
        two = download_image("https://example.com/b", cache_dir=self.cache, fetcher=fetcher)
        self.assertNotEqual(one, two)

    def test_extension_comes_from_content_not_url(self):
        """回归用例：QQ 图片链接没有可用后缀，缓存成 .img 会让下游工具拒读。

        实测撞过：Ultralytics 靠扩展名判断，直接报 "No images found"。
        """
        path = download_image(
            "https://multimedia.nt.qq.com.cn/download?fileid=abc",
            cache_dir=self.cache,
            fetcher=lambda url, timeout: PNG_1PX,
        )
        self.assertEqual(path.suffix, ".png")

    def test_cached_file_is_reused_across_calls(self):
        calls: list[str] = []
        fetcher = lambda url, timeout: (calls.append(url), PNG_1PX)[1]  # noqa: E731
        first = download_image("https://x/y", cache_dir=self.cache, fetcher=fetcher)
        second = download_image("https://x/y", cache_dir=self.cache, fetcher=fetcher)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_empty_response_raises(self):
        with self.assertRaises(VisionError):
            download_image(
                "https://example.com/x", cache_dir=self.cache, fetcher=lambda u, t: b""
            )


class FakeTransport:
    def __init__(self, text: str = "图里有一只猫") -> None:
        self.text = text
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> dict:
        self.calls.append(payload)
        return {
            "model": "deepseek-flash",
            "choices": [{"message": {"role": "assistant", "content": self.text}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 30},
        }


class VisionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.image_path = self.dir / "cat.png"
        self.image_path.write_bytes(PNG_1PX)
        self.transport = FakeTransport()
        self.client = LLMClient(
            base_url="https://api.deepseek.com",
            api_key="test-key",
            model="deepseek-flash",
            transport=self.transport,
        )

    def _vision(self, **overrides) -> Vision:
        options = {"client": self.client, "cache_dir": self.dir / "cache"}
        options.update(overrides)
        return Vision(**options)

    def test_direct_route_sends_image_block(self):
        vision = self._vision()
        result = vision.describe(ImageRef(url="http://x/a.jpg", local_path=str(self.image_path)), "这是什么")

        self.assertEqual(result.answer, "图里有一只猫")
        self.assertEqual(result.route, ROUTE_DIRECT)
        self.assertEqual(result.tokens_in, 900)

        content = self.transport.calls[0]["messages"][0]["content"]
        kinds = [block["type"] for block in content]
        self.assertEqual(kinds, ["text", "image_url"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_system_prompt_is_prepended_when_given(self):
        vision = self._vision()
        vision.describe(
            ImageRef(url="http://x/a.jpg", local_path=str(self.image_path)),
            "这是什么",
            system_prompt="你是莎夜",
        )
        messages = self.transport.calls[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")

    def test_no_system_prompt_means_no_system_message(self):
        self._vision().describe(
            ImageRef(url="http://x/a.jpg", local_path=str(self.image_path)), "这是什么"
        )
        self.assertEqual(len(self.transport.calls[0]["messages"]), 1)

    def test_missing_local_file_raises(self):
        with self.assertRaises(VisionError):
            self._vision().describe(ImageRef(url="http://x/a.jpg", local_path="不存在.png"), "hi")

    def test_routes_needing_tools_say_so_when_unconfigured(self):
        """路线 B/C 需要附属；没配时要明确说是缺什么，而不是莫名其妙地失败。"""
        vision = self._vision()
        image = ImageRef(url="http://x/a.jpg", local_path=str(self.image_path))
        for route in NEEDS_TOOLS:
            with self.subTest(route=route):
                with self.assertRaises(VisionError) as ctx:
                    vision.describe(image, "hi", route=route)
                self.assertIn("附属", str(ctx.exception))
                self.assertIn("SAYA_CV_MCP_COMMAND", str(ctx.exception))

    def test_multiple_images_all_included(self):
        vision = self._vision()
        image = ImageRef(url="http://x/a.jpg", local_path=str(self.image_path))
        vision.describe_many([image, image], "哪张更好看")

        content = self.transport.calls[0]["messages"][0]["content"]
        kinds = [block["type"] for block in content]
        self.assertEqual(kinds, ["text", "image_url", "image_url"])

    def test_no_images_raises(self):
        with self.assertRaises(VisionError):
            self._vision().describe_many([], "hi")

    def test_unknown_route_raises(self):
        with self.assertRaises(VisionError):
            self._vision().describe(
                ImageRef(url="http://x/a.jpg", local_path=str(self.image_path)), "hi", route="乱写"
            )

    def test_route_can_be_overridden_per_call(self):
        vision = self._vision()
        result = vision.describe(
            ImageRef(url="http://x/a.jpg", local_path=str(self.image_path)),
            "hi",
            route=ROUTE_DIRECT,
        )
        self.assertEqual(result.route, ROUTE_DIRECT)


class FakeCvTools:
    """假的附属。记录调用，可配置输出、可让它失败、可不写标注图。"""

    def __init__(
        self,
        *,
        detect: str = "检测到 2 个目标（1 类），耗时 30 ms：\n  猫 ×2",
        ocr: str = "识别到 1 行文字（耗时 20 ms）：\n你好",
        annotate: str = "已在图上标出 2 个目标：\n  1: 猫\n  2: 猫",
        fail: str = "",
        write_annotated: bool = True,
    ) -> None:
        self._detect = detect
        self._ocr = ocr
        self._annotate = annotate
        self._fail = fail
        self._write_annotated = write_annotated
        self.calls: list[str] = []

    def _maybe_fail(self, tool: str) -> None:
        self.calls.append(tool)
        if self._fail == tool:
            raise CvToolsError(f"{tool} 炸了")

    def detect(self, image_path, *, detail: str = "count") -> str:
        self._maybe_fail("detect")
        return self._detect

    def ocr(self, image_path) -> str:
        self._maybe_fail("ocr")
        return self._ocr

    def annotate(self, image_path, output_path) -> str:
        self._maybe_fail("annotate")
        if self._write_annotated:
            from pathlib import Path as _Path

            target = _Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(PNG_1PX)
        return self._annotate


class DelegatedRouteTest(unittest.TestCase):
    """路线 B：图片**完全不进主模型**，只进附属产出的文字。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.image = self.dir / "cat.png"
        self.image.write_bytes(PNG_1PX)
        self.image_ref = ImageRef(url="http://x/cat.png", local_path=str(self.image))

    def _vision(self, tools) -> tuple[Vision, FakeTransport]:
        transport = FakeTransport(text="图里有两只猫")
        client = LLMClient(
            base_url="https://api.deepseek.com",
            api_key="k",
            model="deepseek-flash",
            transport=transport,
        )
        return Vision(client, cache_dir=self.dir / "cache", route=ROUTE_DELEGATED, tools=tools), transport

    def test_image_never_reaches_the_model(self):
        vision, transport = self._vision(FakeCvTools())
        result = vision.describe(self.image_ref, "图里有几只猫")

        self.assertEqual(result.route, ROUTE_DELEGATED)
        content = transport.calls[0]["messages"][-1]["content"]
        self.assertIsInstance(content, str, "路线 B 里 content 必须是纯文本，不能有图片块")
        self.assertNotIn("image_url", json.dumps(transport.calls[0], ensure_ascii=False))

    def test_evidence_is_in_the_prompt(self):
        vision, transport = self._vision(FakeCvTools())
        vision.describe(self.image_ref, "图里有几只猫")

        content = transport.calls[0]["messages"][-1]["content"]
        self.assertIn("猫 ×2", content)
        self.assertIn("你好", content)
        self.assertIn("你并没有直接看到这张图", content, "必须告诉模型它没看到图，防止脑补")

    def test_tool_failure_is_stated_not_hidden(self):
        """附属挂了要在证据里写明 —— 和"没检测到"完全是两回事。"""
        vision, transport = self._vision(FakeCvTools(fail="detect"))
        vision.describe(self.image_ref, "图里有几只猫")

        content = transport.calls[0]["messages"][-1]["content"]
        self.assertIn("调用失败", content)


class HybridRouteTest(unittest.TestCase):
    """路线 C：附属出带编号的标注图，主模型看图 + 编号说明做推理。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.image = self.dir / "cat.png"
        self.image.write_bytes(PNG_1PX)
        self.image_ref = ImageRef(url="http://x/cat.png", local_path=str(self.image))

    def _vision(self, tools) -> tuple[Vision, FakeTransport]:
        transport = FakeTransport(text="两只猫")
        client = LLMClient(
            base_url="https://api.deepseek.com", api_key="k", model="m", transport=transport
        )
        return Vision(client, cache_dir=self.dir / "cache", route=ROUTE_HYBRID, tools=tools), transport

    def test_annotated_image_and_legend_are_sent(self):
        tools = FakeCvTools()
        vision, transport = self._vision(tools)
        result = vision.describe(self.image_ref, "图里有什么")

        self.assertEqual(result.route, ROUTE_HYBRID)
        content = transport.calls[0]["messages"][-1]["content"]
        kinds = [block["type"] for block in content]
        self.assertIn("image_url", kinds, "路线 C 必须把图交给主模型")
        self.assertIn("text", kinds)
        legend = content[-1]
        self.assertIn("编号", legend["text"])
        self.assertIn("1: 猫", legend["text"])
        self.assertEqual(tools.calls, ["annotate"], "路线 C 只该调标注，不该调 detect/ocr")

    def test_falls_back_to_original_when_annotation_fails(self):
        vision, transport = self._vision(FakeCvTools(fail="annotate"))
        result = vision.describe(self.image_ref, "图里有什么")

        self.assertEqual(result.route, ROUTE_HYBRID)
        content = transport.calls[0]["messages"][-1]["content"]
        self.assertIn("image_url", [b["type"] for b in content], "标注失败也要把原图给模型")
        self.assertIn("标注失败", content[-1]["text"])

    def test_falls_back_when_annotation_writes_no_file(self):
        """附属没写出文件（比如没检测到目标）—— 不能把不存在的路径发给模型。"""
        vision, transport = self._vision(FakeCvTools(write_annotated=False))
        vision.describe(self.image_ref, "图里有什么")

        url = transport.calls[0]["messages"][-1]["content"][1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/"), "退回应是原图的 data URL")


class SplitCommandTest(unittest.TestCase):
    """⚠️ `.env` 里的命令行要能在 Windows 与 POSIX 两边都拆对。

    实测两种模式各有各的坑：

        posix=True   `C:\\Users\\me\\...`  →  `C:Usersme...`        反斜杠被当转义符吃掉
        posix=False  `"C:\\Program Files"` →  `'"C:\\Program Files"'`  引号被原样保留

    而**这两边都会遇到** —— `.env` 是人在 Windows 上编辑的，机器人跑在 WSL。
    """

    def test_posix_paths(self):
        argv = split_command("/home/me/venv/bin/python /mnt/d/bot/cv_mcp/server.py", posix=True)
        self.assertEqual(
            argv, ["/home/me/venv/bin/python", "/mnt/d/bot/cv_mcp/server.py"]
        )

    def test_posix_paths_with_spaces_keep_quotes_stripped(self):
        argv = split_command('"/home/me/my venv/bin/python" "/mnt/d/a b/server.py"', posix=True)
        self.assertEqual(argv, ["/home/me/my venv/bin/python", "/mnt/d/a b/server.py"])

    def test_windows_backslashes_are_not_eaten(self):
        argv = split_command(
            r"C:\Users\me\venv\Scripts\python.exe C:\bot\cv_mcp\server.py", posix=False
        )
        self.assertEqual(
            argv,
            [r"C:\Users\me\venv\Scripts\python.exe", r"C:\bot\cv_mcp\server.py"],
        )

    def test_windows_quoted_path_loses_its_quotes(self):
        """非 posix 模式下 shlex 会把引号留在 token 里 —— 必须剥掉，否则系统认不出这个路径。"""
        argv = split_command(
            r'"C:\Program Files\py\python.exe" C:\bot\cv_mcp\server.py', posix=False
        )
        self.assertEqual(argv[0], r"C:\Program Files\py\python.exe")
        self.assertNotIn('"', argv[0])

    def test_unbalanced_quotes_give_a_readable_error(self):
        with self.assertRaises(CvToolsError):
            split_command('"/home/me/venv/bin/python /tmp/x.py', posix=True)


if __name__ == "__main__":
    unittest.main()
