"""百度 OCR（通用文字识别）。

为什么要有这一层：本地的 PP-OCRv3 是 2022 年的模型，拿它代表"专用 OCR 路线"
对委派路线不公平 —— 会让人以为「委派不行」，其实只是「这个 OCR 不行」。

⚠️ **只用标准库**：百度这套是"换 token + POST base64"，两个 HTTP 请求就完事，
不需要 SDK、不需要签名。所以这一层**不引入任何依赖**。

⚠️ **检测那一半上不了云**：百度的通用物体识别只回标签
（`{"keyword": "新垣结衣", "score": 0.97}`），没有框也没有实例数，
数不了数。检测留在本地。

凭据只从环境变量读，**不落任何文件**（同 ADR-035）。
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENV_KEY = "SAYA_BAIDU_OCR_KEY"
ENV_SECRET = "SAYA_BAIDU_OCR_SECRET"

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
# `accurate_basic` 是高精度版；价格档不同，靠环境变量切换
ENV_ENDPOINT = "SAYA_BAIDU_OCR_ENDPOINT"
DEFAULT_ENDPOINT = "general_basic"
TIMEOUT = 30.0
# 百度要求 base64 后不超过 4 MB 左右；超了先拒，别等它报错
MAX_BYTES = 3 * 1024 * 1024

_token_cache: dict[str, tuple[str, float]] = {}


def available() -> bool:
    """凭据在不在。**没配就安静退回本地**，不该让整个附属起不来。"""
    return bool(os.environ.get(ENV_KEY) and os.environ.get(ENV_SECRET))


def _access_token() -> str:
    key = os.environ[ENV_KEY]
    secret = os.environ[ENV_SECRET]
    cached = _token_cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]

    query = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": key, "client_secret": secret}
    )
    with urllib.request.urlopen(f"{TOKEN_URL}?{query}", timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = str(payload.get("access_token") or "")
    if not token:
        raise RuntimeError(f"取 token 失败：{payload.get('error_description') or payload}")
    expires = float(payload.get("expires_in") or 2592000)
    _token_cache[key] = (token, time.time() + expires - 60)
    return token


def recognize(image_path: str | Path) -> list[str]:
    """识别一张图，按行返回。"""
    data = Path(image_path).read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError(f"图片 {len(data) // 1024} KB，超过百度 OCR 的上限")

    endpoint = os.environ.get(ENV_ENDPOINT, DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    body = urllib.parse.urlencode({"image": base64.b64encode(data).decode()}).encode()
    request = urllib.request.Request(
        f"https://aip.baidubce.com/rest/2.0/ocr/v1/{endpoint}?access_token={_access_token()}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if "error_code" in payload:
        raise RuntimeError(f"{payload.get('error_code')}: {payload.get('error_msg')}")
    words = payload.get("words_result") or []
    return [str(item.get("words", "")).strip() for item in words if isinstance(item, dict)]


def read_text(image_path: str | Path) -> str:
    """识别一张图里的文字。返回格式与本地 `read_text` 保持一致。"""
    started = time.monotonic()
    lines = [line for line in recognize(image_path) if line]
    elapsed = int((time.monotonic() - started) * 1000)

    if not lines:
        return f"没有识别到任何文字（百度 OCR，耗时 {elapsed} ms）"
    header = f"识别到 {len(lines)} 行文字（百度 OCR，耗时 {elapsed} ms）："
    return "\n".join([header, *lines])
