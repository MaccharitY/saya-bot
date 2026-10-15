"""MCP 客户端的测试 —— 起真的子进程说话，但服务端是本地假实现，不联网。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saya.services.mcp_client import McpError, McpStdioClient  # noqa: E402

FAKE_SERVER = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"


def make_client(timeout: float = 10.0) -> McpStdioClient:
    return McpStdioClient([sys.executable, str(FAKE_SERVER)], timeout=timeout)


class HandshakeTest(unittest.TestCase):
    def test_start_handshakes_and_records_server_info(self):
        with make_client() as client:
            self.assertEqual(client.server_info.get("name"), "fake-cv")

    def test_start_is_idempotent(self):
        with make_client() as client:
            client.start()  # 再来一次不该炸
            self.assertTrue(client.list_tools())

    def test_missing_command_raises_clear_error(self):
        client = McpStdioClient(["这个命令不存在-saya-test"], timeout=5.0)
        with self.assertRaises(McpError) as ctx:
            client.start()
        self.assertIn("启动", str(ctx.exception))


class ListToolsTest(unittest.TestCase):
    def test_lists_tools_with_schema(self):
        with make_client() as client:
            tools = {tool.name: tool for tool in client.list_tools()}

        self.assertIn("echo", tools)
        self.assertIn("boom", tools)
        self.assertTrue(tools["echo"].description)
        self.assertEqual(tools["echo"].input_schema["type"], "object")


class CallToolTest(unittest.TestCase):
    def test_text_result_is_returned(self):
        with make_client() as client:
            self.assertEqual(client.call_tool("echo", {"text": "你好"}), "echo: 你好")

    def test_tool_error_raises_not_silently_empty(self):
        """工具报错必须抛出来 —— "附属挂了"和"附属说没检测到"是两件事。"""
        with make_client() as client:
            with self.assertRaises(McpError) as ctx:
                client.call_tool("boom", {})
        self.assertIn("炸了", str(ctx.exception))

    def test_unknown_tool_raises_with_message(self):
        with make_client() as client:
            with self.assertRaises(McpError) as ctx:
                client.call_tool("不存在的工具", {})
        self.assertIn("没有这个工具", str(ctx.exception))

    def test_server_exiting_mid_call_raises_instead_of_hanging(self):
        with make_client() as client:
            with self.assertRaises(McpError) as ctx:
                client.call_tool("die", {})
        self.assertIn("退出", str(ctx.exception))

    def test_hanging_tool_times_out(self):
        client = make_client(timeout=1.0)
        self.addCleanup(client.close)
        client.start()
        with self.assertRaises(McpError) as ctx:
            client.call_tool("hang", {})
        self.assertIn("超时", str(ctx.exception))

    def test_non_json_stdout_noise_is_ignored(self):
        """有些 server 会往 stdout 打日志 —— 不能被它呛住。"""
        with make_client() as client:
            # notifications/initialized 之后服务端会打一行非 JSON，
            # 如果客户端处理不当，这次调用就会读到那行并出错
            self.assertEqual(client.call_tool("echo", {"text": "还行"}), "echo: 还行")

    def test_non_utf8_stdout_log_does_not_kill_the_reader(self):
        """回归用例：假 server 会吐一行**GBK 字节**。

        曾经因为读线程按 UTF-8 硬解、抛 UnicodeDecodeError 后线程直接死掉，
        导致之后**所有**调用都超时 —— 一行日志瘫痪整条链路。
        """
        with make_client(timeout=5.0) as client:
            tools = client.list_tools()
            self.assertTrue(tools, "解码容错后仍应能正常通信")
            self.assertEqual(client.call_tool("echo", {"text": "活着"}), "echo: 活着")


class LifecycleTest(unittest.TestCase):
    def test_close_is_safe_to_call_twice(self):
        client = make_client()
        client.start()
        client.close()
        client.close()

    def test_calling_after_close_raises(self):
        client = make_client()
        client.start()
        client.close()
        with self.assertRaises(McpError):
            client.call_tool("echo", {"text": "x"})


if __name__ == "__main__":
    unittest.main()
