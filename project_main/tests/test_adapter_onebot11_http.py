"""OneBot v11 HTTP 适配器的测试。

大部分用假 poster；另外几个会**真起一个本地 HTTP 服务**（只听 127.0.0.1，
不碰外网），验证"外部 POST → events() 产出消息"这条完整链路。
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.adapter.base import AdapterError  # noqa: E402
from saya.adapter.onebot11_http import OneBot11HttpAdapter  # noqa: E402
from saya.domain import OutgoingMessage  # noqa: E402

GROUP_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "sub_type": "normal",
    "message_id": 1002,
    "group_id": 123456789,
    "user_id": 22233344,
    "self_id": 99999,
    "time": 1760500060,
    "message": [
        {"type": "at", "data": {"qq": "99999"}},
        {"type": "text", "data": {"text": " 这是什么"}},
    ],
}

HEARTBEAT = {"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 99999}


class FakePoster:
    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else {"status": "ok", "retcode": 0}
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url: str, params: dict, timeout: float) -> dict:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def make_adapter(poster: FakePoster | None = None, **overrides) -> OneBot11HttpAdapter:
    options = {
        "api_base": "http://127.0.0.1:3000",
        "listen_port": 0,  # 让系统分配端口，避免测试之间抢端口
        "poster": poster or FakePoster(),
    }
    options.update(overrides)
    return OneBot11HttpAdapter(**options)


def read_events(adapter: OneBot11HttpAdapter, count: int, timeout: float = 5.0) -> list:
    collected: list = []

    def run() -> None:
        for message in adapter.events():
            collected.append(message)
            if len(collected) >= count:
                adapter.stop()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    return collected


def post_event(port: int, payload: dict, *, path: str = "/onebot", token: str = "") -> int:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


class SendTest(unittest.TestCase):
    def test_group_message_uses_send_group_msg(self):
        poster = FakePoster()
        make_adapter(poster).send(
            OutgoingMessage(source="group", target_id=123456789, text="你好")
        )
        call = poster.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:3000/send_group_msg")
        self.assertEqual(call["params"], {"group_id": 123456789, "message": "你好"})

    def test_private_message_uses_send_private_msg(self):
        poster = FakePoster()
        make_adapter(poster).send(OutgoingMessage(source="private", target_id=42, text="hi"))
        call = poster.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:3000/send_private_msg")
        self.assertEqual(call["params"], {"user_id": 42, "message": "hi"})

    def test_api_base_trailing_slash_tolerated(self):
        poster = FakePoster()
        make_adapter(poster, api_base="http://127.0.0.1:3000/").send(
            OutgoingMessage(source="group", target_id=1, text="x")
        )
        self.assertEqual(poster.calls[0]["url"], "http://127.0.0.1:3000/send_group_msg")

    def test_nonzero_retcode_raises_with_detail(self):
        poster = FakePoster({"retcode": 100, "message": "群不存在"})
        with self.assertRaises(AdapterError) as ctx:
            make_adapter(poster).send(OutgoingMessage(source="group", target_id=1, text="x"))
        self.assertIn("100", str(ctx.exception))
        self.assertIn("群不存在", str(ctx.exception))

    def test_network_error_becomes_adapter_error(self):
        poster = FakePoster(error=OSError("connection refused"))
        with self.assertRaises(AdapterError):
            make_adapter(poster).send(OutgoingMessage(source="group", target_id=1, text="x"))


class EventDeliveryTest(unittest.TestCase):
    """真起本地服务，走完整 HTTP 链路。"""

    def test_event_reaches_events(self):
        adapter = make_adapter()
        host, port = adapter.start()
        self.addCleanup(adapter.close)

        self.assertEqual(post_event(port, GROUP_EVENT), 204)
        collected = read_events(adapter, 1)

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].group_id, 123456789)
        self.assertTrue(collected[0].mentioned_self)

    def test_heartbeat_is_ignored_but_real_message_still_arrives(self):
        adapter = make_adapter()
        _, port = adapter.start()
        self.addCleanup(adapter.close)

        post_event(port, HEARTBEAT)
        post_event(port, GROUP_EVENT)
        collected = read_events(adapter, 1)

        self.assertEqual(len(collected), 1, "心跳不该变成消息")

    def test_malformed_body_does_not_crash(self):
        adapter = make_adapter()
        _, port = adapter.start()
        self.addCleanup(adapter.close)

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/onebot", data=b"not json", method="POST"
        )
        with urllib.request.urlopen(request, timeout=5.0) as response:
            self.assertEqual(response.status, 204)

        post_event(port, GROUP_EVENT)
        self.assertEqual(len(read_events(adapter, 1)), 1, "坏请求之后服务应照常工作")

    def test_wrong_path_is_404(self):
        adapter = make_adapter()
        _, port = adapter.start()
        self.addCleanup(adapter.close)
        self.assertEqual(post_event(port, GROUP_EVENT, path="/wrong"), 404)

    def test_custom_event_path(self):
        adapter = make_adapter(event_path="/events")
        _, port = adapter.start()
        self.addCleanup(adapter.close)

        self.assertEqual(post_event(port, GROUP_EVENT, path="/onebot"), 404)
        self.assertEqual(post_event(port, GROUP_EVENT, path="/events"), 204)
        self.assertEqual(len(read_events(adapter, 1)), 1)

    def test_health_check_get(self):
        adapter = make_adapter()
        _, port = adapter.start()
        self.addCleanup(adapter.close)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5.0) as response:
            self.assertEqual(response.status, 200)


class AuthTest(unittest.TestCase):
    def test_missing_token_is_rejected(self):
        adapter = make_adapter(access_token="s3cret")
        _, port = adapter.start()
        self.addCleanup(adapter.close)
        self.assertEqual(post_event(port, GROUP_EVENT), 401)

    def test_wrong_token_is_rejected(self):
        adapter = make_adapter(access_token="s3cret")
        _, port = adapter.start()
        self.addCleanup(adapter.close)
        self.assertEqual(post_event(port, GROUP_EVENT, token="wrong"), 401)

    def test_correct_token_passes(self):
        adapter = make_adapter(access_token="s3cret")
        _, port = adapter.start()
        self.addCleanup(adapter.close)

        self.assertEqual(post_event(port, GROUP_EVENT, token="s3cret"), 204)
        self.assertEqual(len(read_events(adapter, 1)), 1)

    def test_no_token_configured_means_open(self):
        adapter = make_adapter()
        _, port = adapter.start()
        self.addCleanup(adapter.close)
        self.assertEqual(post_event(port, GROUP_EVENT), 204)


class LifecycleTest(unittest.TestCase):
    def test_start_is_idempotent(self):
        adapter = make_adapter()
        self.addCleanup(adapter.close)
        first = adapter.start()
        second = adapter.start()
        self.assertEqual(first, second)

    def test_stop_ends_events_even_if_empty(self):
        adapter = make_adapter()
        self.addCleanup(adapter.close)
        adapter.start()
        adapter.stop()
        self.assertEqual(read_events(adapter, 1, timeout=3.0), [])

    def test_port_in_use_raises_clear_error(self):
        """Windows 上 SO_REUSEADDR 会让"端口被占"静默成功，所以我们在 _EventServer
        里关掉了它 —— 这个测试就是守住那条防线，免得哪天被改回去。"""
        first = make_adapter()
        _, port = first.start()
        self.addCleanup(first.close)

        with self.assertRaises(AdapterError) as ctx:
            make_adapter(listen_port=port).start()
        self.assertIn("端口", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
