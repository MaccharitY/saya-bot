"""最小 MCP 客户端（**标准库实现**）。

**为什么自己写而不是用 `mcp` 官方包**：机器人主体一直刻意保持零第三方依赖
（clone 下来就能跑、单测不用摆环境）。而 MCP 的 stdio 传输本质就是
"一行一个 JSON-RPC 消息"，自己实现约一百行，代价可控 ——
而且自己实现过一遍协议，讲报告时底气完全不同。

**支持**：initialize 握手、tools/list、tools/call。
**不支持**：HTTP/SSE 传输、采样、资源订阅、通知处理 —— 我们不需要。

⚠️ 与官方 SDK 的差别：不做完整的版本协商（我们声明一个版本，接受服务端回的任意版本），
不处理并发请求（调用串行化）。对"机器人调几个 CV 工具"这个场景足够。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

# 我们声明支持的协议版本。服务端回别的版本也照用 —— 见模块开头的取舍说明。
PROTOCOL_VERSION = "2025-11-25"

_READ_TIMEOUT_SENTINEL = None


class McpError(RuntimeError):
    """MCP 调用失败：启动不了、超时、server 退出、工具报错。"""


@dataclass(frozen=True, slots=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class McpStdioClient:
    """通过 stdin/stdout 跟一个 MCP server 说话。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout: float = 120.0,
        client_name: str = "saya_bot",
        client_version: str = "0.1",
    ) -> None:
        self._command = list(command)
        self._timeout = timeout
        self._client_name = client_name
        self._client_version = client_version

        self._process: subprocess.Popen[str] | None = None
        self._queue: Queue[Any] = Queue()
        self._call_lock = threading.Lock()
        self._next_id = 0
        self.server_info: dict[str, Any] = {}

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._process is not None:
            return
        env = os.environ.copy()
        # ⚠️ MCP stdio 规定消息是 **UTF-8**，但 Windows 上的 Python 子进程**默认按 GBK 输出**
        # （实测：中文变成乱码，两边解出来不一样）。所以强制它改用 UTF-8 ——
        # 这里**覆盖**而不是 respects 用户设置，因为协议正确性优先。
        # 非 Python 的 server 看到这两个变量也无害。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                # ⚠️ 必须容错：MCP 协议本身是 UTF-8，但 server 往 stdout 打的**日志**
                # 未必是（Windows 上子进程默认就是 GBK）。不允许替换的话，一行日志
                # 就会让读线程抛 UnicodeDecodeError 直接死掉，之后所有调用全部超时。
                errors="replace",
                bufsize=1,  # 行缓冲：MCP stdio 是行分隔的
                env=env,
            )
        except OSError as exc:
            raise McpError(f"启动 MCP server 失败：{exc}（命令：{' '.join(self._command)}）") from exc

        threading.Thread(target=self._read_loop, name="mcp-reader", daemon=True).start()
        self._handshake()

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return

        try:
            if process.stdin is not None:
                process.stdin.close()  # 先关 stdin，让 server 知道该收工了
        except OSError:
            pass

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # 关掉剩下的管道 —— 不关的话解释器退出时会报 ResourceWarning，
        # 那种噪音会盖住测试里真正的问题。
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def __enter__(self) -> McpStdioClient:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 协议 ----

    def list_tools(self) -> list[McpTool]:
        result = self._request("tools/list", {})
        tools = []
        for raw in result.get("tools", []) or []:
            tools.append(
                McpTool(
                    name=str(raw.get("name", "")),
                    description=str(raw.get("description", "")),
                    input_schema=raw.get("inputSchema") or {},
                )
            )
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用工具，返回它输出的纯文本。

        工具内部报错时**抛 McpError 而不是静默返回空** ——
        调用方（路线 B/C）需要知道"附属挂了"和"附属说没检测到"是两件事。
        """
        result = self._request("tools/call", {"name": name, "arguments": arguments})

        parts: list[str] = []
        for block in result.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "\n".join(part for part in parts if part)

        if result.get("isError"):
            raise McpError(f"工具 {name} 报错：{text[:300]}")
        return text

    # ---- 内部 ----

    def _handshake(self) -> None:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self._client_name, "version": self._client_version},
            },
        )
        self.server_info = result.get("serverInfo") or {}
        self._notify("notifications/initialized")

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        # 串行化：这套实现一次只等一个响应，并发发请求会串号
        with self._call_lock:
            self._next_id += 1
            request_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

            deadline = time.monotonic() + self._timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"{method} 超时（{self._timeout:.0f}s）")

                try:
                    message = self._queue.get(timeout=remaining)
                except Empty:
                    raise McpError(f"{method} 超时（{self._timeout:.0f}s）") from None

                if message is _READ_TIMEOUT_SENTINEL:
                    raise McpError(f"MCP server 已退出，{method} 未完成")

                if message.get("id") != request_id:
                    continue  # 通知或过期响应，跳过
                if "error" in message:
                    raise McpError(f"{method} 失败：{message['error']}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpError("MCP server 尚未启动")
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise McpError(f"向 MCP server 写入失败：{exc}") from exc

    def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._queue.put(json.loads(line))
                except json.JSONDecodeError:
                    # 有些 server 会把日志打到 stdout —— 不是协议消息，丢掉即可
                    continue
        finally:
            # 无论如何都要放结束哨兵：宁可让调用方收到"server 已退出"，
            # 也不要让它一直等到超时。
            self._queue.put(_READ_TIMEOUT_SENTINEL)
