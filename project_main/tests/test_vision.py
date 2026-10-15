"""看图模块的单元测试。全部离线：假 fetcher + 假 transport，不发真请求。

测试用的 1×1 透明 PNG（合法文件，不是随便几个字节）。
"""

from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.domain import ImageRef  # noqa: E402
from saya.services.llm import LLMClient  # noqa: E402
from saya.services.vision import (  # noqa: E402
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

    def test_unimplemented_routes_say_so_clearly(self):
        """路线 B/C 没实现，但报错要说清楚"是没实现"，而不是莫名其妙地失败。"""
        vision = self._vision()
        image = ImageRef(url="http://x/a.jpg", local_path=str(self.image_path))
        for route in (ROUTE_DELEGATED, ROUTE_HYBRID):
            with self.subTest(route=route):
                with self.assertRaises(VisionError) as ctx:
                    vision.describe(image, "hi", route=route)
                self.assertIn("尚未实现", str(ctx.exception))

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


if __name__ == "__main__":
    unittest.main()
