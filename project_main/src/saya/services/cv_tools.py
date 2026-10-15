"""附属（专用 CV 模型）的调用入口。

**单独一层的理由**：让 `vision.py` 不关心"工具从哪来"。
现在它们由 MCP server 提供，将来换成 HTTP 服务或进程内函数，只改这里
（这是 ADR-005 定的接口约束：三条路线共用同一套调用方式）。

⚠️ 与路线 A 的区别：这里调用的是**确定性模型**（YOLO / OCR），
不是多模态大模型 —— 也正是报告里要对比的那件事。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from saya.services.mcp_client import McpError, McpStdioClient, McpTool

__all__ = ["CvTools", "CvToolsError", "start_cv_tools"]


class CvToolsError(RuntimeError):
    """附属调用失败。与"附属说没检测到"要区分开 —— 前者是坏了，后者是结论。"""


class CvTools:
    def __init__(self, client: McpStdioClient) -> None:
        self._client = client

    @property
    def server_info(self) -> dict:
        return self._client.server_info

    def list_tools(self) -> list[McpTool]:
        return self._client.list_tools()

    def _call(self, tool: str, **arguments: str) -> str:
        try:
            return self._client.call_tool(tool, dict(arguments))
        except McpError as exc:
            raise CvToolsError(str(exc)) from exc

    def detect(self, image_path: str | Path, *, detail: str = "count") -> str:
        """目标检测。detail="count" 只给类别计数（省 token）；"boxes" 带坐标。"""
        return self._call("detect_objects", image_path=str(image_path), detail=detail)

    def ocr(self, image_path: str | Path) -> str:
        """识别图里的文字。"""
        return self._call("read_text", image_path=str(image_path))

    def annotate(self, image_path: str | Path, output_path: str | Path) -> str:
        """画出编号框并把新图另存到 output_path。"""
        return self._call(
            "annotate_objects", image_path=str(image_path), output_path=str(output_path)
        )

    def close(self) -> None:
        self._client.close()


def start_cv_tools(command: str, *, timeout: float = 180.0) -> CvTools:
    """按配置里的命令行启动附属并握手。

    command 形如 `"/home/me/venv/bin/python /path/to/cv_mcp/server.py"` ——
    用一个字符串而不是列表，是为了能直接写在 `.env` 里。
    """
    if not command.strip():
        raise CvToolsError("没有配置附属的命令（SAYA_CV_MCP_COMMAND），无法使用路线 delegated / hybrid")

    client = McpStdioClient(shlex.split(command), timeout=timeout)
    client.start()
    return CvTools(client)
