"""一个最小的 MCP server，**只用来测客户端**，不依赖任何第三方包。

故意做得"不干净"：会往 stdout 打非 JSON 的日志、有会报错的工具、有会直接退出的工具、
还有永远不回应的工具 —— 用来验证客户端在这些情况下都不会崩。
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "把输入原样返回",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
    {"name": "boom", "description": "总是报错", "inputSchema": {"type": "object"}},
    {"name": "die", "description": "直接退出进程", "inputSchema": {"type": "object"}},
    {"name": "hang", "description": "永远不回应", "inputSchema": {"type": "object"}},
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-cv", "version": "0.0"},
                    },
                }
            )
        elif method == "notifications/initialized":
            # 故意打一行**非 UTF-8、也非 JSON** 的日志。
            # 真实世界里 server 的日志编码常常不是 UTF-8（Windows 上默认 GBK），
            # 客户端如果不容错，读线程会被这行呛死、之后所有调用都超时。
            sys.stdout.buffer.write(b"\xd5\xe2\xca\xc7 GBK \xc8\xd5\xd6\xbe\n")
            sys.stdout.buffer.flush()
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = message["params"]["name"]
            arguments = message["params"].get("arguments") or {}

            if name == "echo":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"content": [{"type": "text", "text": f"echo: {arguments.get('text', '')}"}]},
                    }
                )
            elif name == "boom":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"content": [{"type": "text", "text": "炸了"}], "isError": True},
                    }
                )
            elif name == "die":
                sys.exit(3)
            elif name == "hang":
                pass  # 永不回应
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "没有这个工具"},
                    }
                )


if __name__ == "__main__":
    main()
