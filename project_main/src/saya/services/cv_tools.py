"""附属（专用 CV 模型）的调用入口。

**单独一层的理由**：让 `vision.py` 不关心"工具从哪来"。
现在它们由 MCP server 提供，将来换成 HTTP 服务或进程内函数，只改这里
（这是 ADR-005 定的接口约束：三条路线共用同一套调用方式）。

⚠️ 与路线 A 的区别：这里调用的是**确定性模型**（YOLO / OCR），
不是多模态大模型 —— 也正是报告里要对比的那件事。
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
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


def split_command(command: str, *, posix: bool | None = None) -> list[str]:
    """把 `.env` 里的一行命令拆成 argv。

    ⚠️ **两种模式各有各的坑，所以两个都得处理**（实测见下）：

        posix=True   C:\\Users\\me\\...  →  C:Usersme...       反斜杠被当转义符吃掉
        posix=False  "C:\\Program Files"  →  '"C:\\Program Files"'  引号被原样保留

    `.env` 是人在 Windows 侧编辑的，而机器人跑在 WSL —— **两边都会遇到**。
    所以：按平台选模式，再把 token 两头的引号剥掉。
    """
    if posix is None:
        posix = os.name != "nt"
    try:
        argv = shlex.split(command, posix=posix)
    except ValueError as exc:
        raise CvToolsError(
            f"附属的命令行拆不开（多半是引号没配对）：{exc}。现在的值是：{command!r}"
        ) from exc
    # posix 模式已经自己处理过引号了；非 posix 模式留下的引号要剥掉
    return argv if posix else [token.strip('"').strip("'") for token in argv]


def cv_subprocess_env(
    *,
    model_dir: str = "",
    ocr_key: str = "",
    ocr_secret: str = "",
    ocr_endpoint: str = "",
) -> dict[str, str]:
    """附属子进程需要、而**普通环境变量送不到**的那些键，显式传下去。

    ⚠️ 为什么需要这个口子：附属是通过 `subprocess` 起的，它的环境来自
    `os.environ.copy()` —— 而 `.env` 只被读成一个 dict，**从来没写进
    `os.environ`**。于是 `SAYA_CV_MODEL_DIR` 在 `.env` 里是个**死键**：
    填了没有任何反应、也不报错（权重照样下到 `~/.cache/saya-cv`），
    而 `run.py --check` 还会专门提示"未赋值键：SAYA_CV_MODEL_DIR"，
    等于把人往这个坑里引。

    新增"只有子进程才用得上"的配置键时，**要加到这里**，
    否则它就是个摆设。
    """
    env: dict[str, str] = {}
    if model_dir:
        env["SAYA_CV_MODEL_DIR"] = model_dir
    # ⚠️ 百度 OCR 的三个键**只被附属子进程读**（`cv_mcp/cloud_ocr.py`）。
    # 不在这里传，`.env` 里填了也等于没填 —— 云 OCR 会静默退回本地。
    if ocr_key:
        env["SAYA_BAIDU_OCR_KEY"] = ocr_key
    if ocr_secret:
        env["SAYA_BAIDU_OCR_SECRET"] = ocr_secret
    if ocr_endpoint:
        env["SAYA_BAIDU_OCR_ENDPOINT"] = ocr_endpoint
    return env


def start_cv_tools(
    command: str, *, timeout: float = 180.0, extra_env: Mapping[str, str] | None = None
) -> CvTools:
    """按配置里的命令行启动附属并握手。

    command 形如 `"/home/me/venv/bin/python /path/to/cv_mcp/server.py"` ——
    用一个字符串而不是列表，是为了能直接写在 `.env` 里。

    `extra_env` 是**从 Config 显式传下来**给附属子进程的环境变量。
    普通环境变量在这里读不到 `.env` 里的值（`.env` 从没被写进 `os.environ`），
    所以要传的键必须走这个口子，否则在 `.env` 里填了也是白填。
    """
    if not command.strip():
        raise CvToolsError("没有配置附属的命令（SAYA_CV_MCP_COMMAND），无法使用路线 delegated / hybrid")

    client = McpStdioClient(split_command(command), timeout=timeout, extra_env=extra_env)
    client.start()
    return CvTools(client)
