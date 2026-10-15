"""DNS 缓存的单元测试 —— 全部离线，用假解析器。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.services.net import DnsCache  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, host, port, *args, **kwargs):
        self.calls.append((host, port, args, kwargs))
        return [(2, 1, 6, "", (f"10.0.0.{len(self.calls)}", port))]


def make_cache(ttl: float = 300.0, clock: FakeClock | None = None):
    resolver = FakeResolver()
    cache = DnsCache(resolver, ttl)
    if clock is not None:
        # 用假时钟替换 time.monotonic，让 TTL 测试不依赖真实时间
        import saya.services.net as net_module

        original = net_module.time.monotonic
        net_module.time.monotonic = clock
        cache._restore = lambda: setattr(net_module.time, "monotonic", original)
    return cache, resolver


class DnsCacheTest(unittest.TestCase):
    def test_first_call_resolves_and_second_is_cached(self):
        cache, resolver = make_cache()
        first = cache.getaddrinfo("api.deepseek.com", 443)
        second = cache.getaddrinfo("api.deepseek.com", 443)

        self.assertEqual(len(resolver.calls), 1, "同一域名不该解析两次")
        self.assertEqual(first, second)

    def test_different_hosts_are_cached_separately(self):
        cache, resolver = make_cache()
        cache.getaddrinfo("a.example", 443)
        cache.getaddrinfo("b.example", 443)
        self.assertEqual(len(resolver.calls), 2)

    def test_different_ports_are_cached_separately(self):
        cache, resolver = make_cache()
        cache.getaddrinfo("a.example", 443)
        cache.getaddrinfo("a.example", 80)
        self.assertEqual(len(resolver.calls), 2)

    def test_different_kwargs_are_cached_separately(self):
        cache, resolver = make_cache()
        cache.getaddrinfo("a.example", 443, type=1)
        cache.getaddrinfo("a.example", 443, type=2)
        self.assertEqual(len(resolver.calls), 2)

    def test_ttl_expiry_triggers_resolve_again(self):
        clock = FakeClock()
        cache, resolver = make_cache(ttl=60.0, clock=clock)
        self.addCleanup(cache._restore)

        cache.getaddrinfo("a.example", 443)
        clock.now += 59.0
        cache.getaddrinfo("a.example", 443)
        self.assertEqual(len(resolver.calls), 1, "TTL 内应命中缓存")

        clock.now += 2.0
        cache.getaddrinfo("a.example", 443)
        self.assertEqual(len(resolver.calls), 2, "超过 TTL 应重新解析")

    def test_clear_forces_resolution(self):
        cache, resolver = make_cache()
        cache.getaddrinfo("a.example", 443)
        cache.clear()
        cache.getaddrinfo("a.example", 443)
        self.assertEqual(len(resolver.calls), 2)

    def test_len_reports_cached_entries(self):
        cache, _ = make_cache()
        self.assertEqual(len(cache), 0)
        cache.getaddrinfo("a.example", 443)
        cache.getaddrinfo("b.example", 443)
        self.assertEqual(len(cache), 2)

    def test_refresh_forces_resolution_even_when_fresh(self):
        """refresh 要绕开缓存去真解析 —— 否则后台保活就成了空转。"""
        cache, resolver = make_cache()
        cache.getaddrinfo("a.example", 443)
        cache.refresh("a.example", 443)
        self.assertEqual(len(resolver.calls), 2)

        cache.getaddrinfo("a.example", 443)
        self.assertEqual(len(resolver.calls), 2, "刷新后缓存应是新鲜的")

    def test_refresh_without_prior_entry(self):
        cache, resolver = make_cache()
        cache.refresh("new.example", 443)
        self.assertEqual(len(resolver.calls), 1)
        self.assertEqual(len(cache), 1)


class KeepWarmTest(unittest.TestCase):
    def test_returns_none_when_cache_not_installed(self):
        import saya.services.net as net_module

        original = net_module._installed_cache
        self.addCleanup(lambda: setattr(net_module, "_installed_cache", original))
        net_module._installed_cache = None

        self.assertIsNone(net_module.keep_warm("a.example"))

    def test_refreshes_periodically_in_background(self):
        import time

        import saya.services.net as net_module

        cache, resolver = make_cache(ttl=1000.0)
        original = net_module._installed_cache
        self.addCleanup(lambda: setattr(net_module, "_installed_cache", original))
        net_module._installed_cache = cache

        thread = net_module.keep_warm("a.example", 443, interval=0.05)
        self.assertIsNotNone(thread)
        self.assertTrue(thread.daemon, "保活线程必须是守护线程，不能拖住进程退出")

        time.sleep(0.3)
        self.assertGreaterEqual(len(resolver.calls), 1, "后台应定期刷新")


class InstallTest(unittest.TestCase):
    def test_install_is_idempotent_and_disableable(self):
        import socket

        import saya.services.net as net_module

        original = socket.getaddrinfo
        self.addCleanup(lambda: setattr(socket, "getaddrinfo", original))
        net_module._installed_cache = None

        self.assertIsNone(net_module.install(0), "ttl=0 表示不安装")

        first = net_module.install(120)
        second = net_module.install(120)
        self.assertIs(first, second, "重复安装应返回同一个缓存")
        self.assertIsNot(socket.getaddrinfo, original, "安装后应被替换")

        net_module._installed_cache = None


if __name__ == "__main__":
    unittest.main()
