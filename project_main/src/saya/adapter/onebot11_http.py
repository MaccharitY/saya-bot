"""OneBot v11 接入 —— **HTTP 模式**（标准库实现，保持零依赖）。

两个方向都走 HTTP，所以**不需要 WebSocket 库**：

    NapCat ──POST 事件──▶ 我们的 HTTP 服务器（默认 /onebot）
    NapCat ◀──POST 调用── 我们的 HTTP 客户端（send_group_msg 等）

⚠️ 在 NapCat 里需要两处配置：
  1. 打开它的 **HTTP 服务器**（我们调它发消息）—— 记下端口，填进 `SAYA_ONEBOT_API_BASE`
  2. 配置 **HTTP 上报** 指向 `http://<本机>:<我们的端口>/onebot`

⚠️ **网络监听地址**：默认只听 `127.0.0.1`。若 NapCat 跑在 WSL/虚拟机里，
   必须改成 `0.0.0.0` 并在 WSL 侧用宿主 IP 访问，否则连不上。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from typing import Any

from saya.adapter.base import Adapter, AdapterError
from saya.adapter.onebot11 import parse_event
from saya.domain import IncomingMessage, OutgoingMessage

logger = logging.getLogger("saya.adapter.onebot11_http")

DEFAULT_EVENT_PATH = "/onebot"
_QUEUE_POLL_SECONDS = 0.5
_MAX_BODY_BYTES = 8 * 1024 * 1024  # 群消息事件不会太大；留个上限防内存被灌爆

Poster = Callable[[str, dict[str, Any], float], dict[str, Any]]


class _EventServer(ThreadingHTTPServer):
    daemon_threads = True

    # ⚠️ Windows 上 SO_REUSEADDR 的语义与 Unix **不同**：它允许第二个进程也绑定同一端口。
    # 于是"端口已被占用"不会报错，变成两个服务抢连接、行为诡异 —— 用户手滑启动两次
    # 机器人时尤其危险。所以 Windows 上关掉它，让冲突**当场暴露**。
    # Unix 上仍然开启，避免重启时被 TIME_WAIT 卡住。
    allow_reuse_address = not sys.platform.startswith("win")


class OneBot11HttpAdapter(Adapter):
    def __init__(
        self,
        *,
        api_base: str,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8080,
        event_path: str = DEFAULT_EVENT_PATH,
        access_token: str = "",
        self_id: int | None = None,
        timeout: float = 15.0,
        poster: Poster | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._event_path = event_path
        self._access_token = access_token
        self._self_id = self_id
        self._timeout = timeout
        self._poster = poster or _urllib_post

        self._queue: Queue[IncomingMessage] = Queue()
        self._server: ThreadingHTTPServer | None = None
        self._stopping = False

    # ---- 生命周期 ----

    def start(self) -> tuple[str, int]:
        """启动事件服务器，返回实际绑定的 (host, port)。重复调用无副作用。"""
        if self._server is None:
            try:
                self._server = _EventServer(
                    (self._listen_host, self._listen_port), _make_handler(self)
                )
            except OSError as exc:
                raise AdapterError(
                    f"监听 {self._listen_host}:{self._listen_port} 失败：{exc}。"
                    f"端口多半被占用了（NapCat 或别的程序，也可能你已经开了一个 saya_bot）。"
                ) from exc
            threading.Thread(
                target=self._server.serve_forever, name="saya-onebot-http", daemon=True
            ).start()

        host, port = self._server.server_address[:2]
        logger.info("事件服务器已就绪：http://%s:%s%s", host, port, self._event_path)
        return str(host), int(port)

    def stop(self) -> None:
        self._stopping = True

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # ---- Adapter 接口 ----

    def events(self) -> Iterator[IncomingMessage]:
        self.start()
        while True:
            try:
                yield self._queue.get(timeout=_QUEUE_POLL_SECONDS)
            except Empty:
                # 先把手头的消息发完，再退出 —— 否则收到 stop() 时队列里剩下的会被丢掉
                if self._stopping:
                    return
                continue

    def send(self, message: OutgoingMessage) -> None:
        if message.source == "group":
            self._call(
                "send_group_msg", {"group_id": message.target_id, "message": message.text}
            )
        else:
            self._call(
                "send_private_msg", {"user_id": message.target_id, "message": message.text}
            )

    # ---- 内部 ----

    def _call(self, action: str, params: dict[str, Any]) -> None:
        url = f"{self._api_base}/{action}"
        try:
            response = self._poster(url, params, self._timeout)
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转成 AdapterError，便于上层分类
            raise AdapterError(f"调用 {action} 失败：{exc}") from exc

        retcode = response.get("retcode")
        if retcode not in (0, None):
            raise AdapterError(
                f"{action} 被后端拒绝：retcode={retcode} {response.get('message', '')}"
            )

    def _deliver_event(self, body: bytes, *, headers: Any = None) -> None:
        try:
            raw = json.loads(body)
        except json.JSONDecodeError:
            hint = ""
            if headers is not None:
                hint = (
                    f"（Content-Length={headers.get('Content-Length')!r}, "
                    f"Transfer-Encoding={headers.get('Transfer-Encoding')!r}）"
                )
            logger.warning("收到不是 JSON 的请求体，丢弃（%d 字节）%s", len(body), hint)
            return
        if not isinstance(raw, dict):
            logger.warning("事件 JSON 顶层不是对象，丢弃")
            return

        message = parse_event(raw, self_id=self._self_id)
        if message is None:
            return  # 心跳等非消息事件，正常丢弃
        self._queue.put(message)


def _urllib_post(url: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AdapterError(f"HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise AdapterError(f"连不上后端 {url}：{exc.reason}") from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        raise AdapterError(f"后端返回的不是 JSON：{body[:200]}") from None
    return result if isinstance(result, dict) else {}


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """读请求体，**同时支持 Content-Length 与 chunked**。

    ⚠️ 这个坑我们真踩过：NapCat 的上报用 `Transfer-Encoding: chunked`（Node 的
    http 客户端流式发送时就是这样），而 `BaseHTTPRequestHandler` **不会**自动解码
    chunked —— 只按 Content-Length 读会拿到空 body，日志里只有一句"收到 0 字节"，
    消息全部静默丢失，看起来像"机器人没反应"。
    """
    encoding = (handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in encoding:
        return _read_chunked(handler)

    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return b""
    return handler.rfile.read(min(length, _MAX_BODY_BYTES))


def _read_chunked(handler: BaseHTTPRequestHandler) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        size_line = handler.rfile.readline(128)
        if not size_line:
            break
        # 块长度后面可能有 ";扩展"，切掉
        try:
            size = int(size_line.split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if size <= 0:
            handler.rfile.readline()  # 结尾那个 CRLF
            break

        chunks.append(handler.rfile.read(size))
        handler.rfile.read(2)  # 每块末尾的 CRLF
        total += size
        if total > _MAX_BODY_BYTES:
            logger.warning("上报体超过 %d 字节上限，截断", _MAX_BODY_BYTES)
            break
    return b"".join(chunks)


def _make_handler(adapter: OneBot11HttpAdapter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "saya-bot"

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定的名字
            # ⚠️ **先读请求体，再决定回什么** —— 哪怕是要拒绝它。
            # 不读就关连接的话，客户端发来的 body 还堆在接收缓冲区里，
            # Windows 会因此发 RST 而不是 FIN，客户端那边报
            # `ConnectionAbortedError [WinError 10053]`。
            # 这是 HTTP 服务端的基本卫生；偶发正是因为它取决于 TCP 时序。
            body = _read_body(self)

            if self.path.split("?")[0].rstrip("/") != adapter._event_path.rstrip("/"):
                self._reply(404, b'{"error":"not here"}')
                return
            if not self._authorised():
                self._reply(401, b'{"error":"unauthorised"}')
                return

            adapter._deliver_event(body, headers=self.headers)
            self._reply(204)

        def do_GET(self) -> None:  # noqa: N802
            # 便于人工确认服务活着：浏览器打开就能看到
            self._reply(200, "saya_bot onebot adapter ok".encode("utf-8"))

        def _authorised(self) -> bool:
            expected = adapter._access_token
            if not expected:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {expected}"

        def _reply(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            """静音 BaseHTTPRequestHandler 默认的逐请求日志 —— 太吵，
            我们自己在 _deliver_event 里只记有价值的。"""

    return Handler
