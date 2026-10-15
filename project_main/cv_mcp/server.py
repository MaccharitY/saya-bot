"""专用 CV 模型的 MCP server —— 路线 B / C 里的"附属"。

**这是一个独立进程**，有自己的依赖（torch / ultralytics / OCR），
所以机器人主体仍然保持零依赖；两边只通过 MCP 协议说话。

启动（用装了依赖的那个 venv）：

    ~/saya-venv/bin/python project_main/cv_mcp/server.py

它暴露三个工具给模型：
    detect_objects  目标检测（YOLO）—— 能数数、能定位
    read_text       文字识别（OCR）—— 读图里的字
    image_info      图片基本信息 —— 用来验证链路通不通

设计取舍：
- **模型懒加载**：第一次调用才加载，这样"启动 server"本身很快，
  也让冷启动耗时能被单独测出来（报告里要区分"首次"和"稳态"延迟）
- **输出给模型看**：格式紧凑（token 很贵），默认只给类别计数，
  要坐标时才给坐标（路线 C 需要坐标，路线 B 通常不需要）
- **出错不抛异常**：返回一条能读懂的说明，让模型自己决定怎么办 ——
  这比让整条链断掉好，也是我们在路线 B 里想测的"健壮性"
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# ⚠️ MCP Python SDK 在 2.x 里把 `FastMCP` 改名成了 `MCPServer`
# （`mcp.server.fastmcp` → `mcp.server.mcpserver`），v1 的写法在 2.x 上会直接
# 抛 ModuleNotFoundError。所以这里两个版本都认 —— 这类"依赖改名"是真实世界里
# 很常见的地雷，不值得让整个附属因此起不来。
#
# 顺带一提：我们**手写的那个客户端不受影响** —— 它说的是协议本身，
# 不是某个 SDK 的类名。这也是自己实现协议的一个附带好处。
try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

mcp = _Server("saya-cv")

# 权重放专门的缓存目录，**别落在仓库里**。
# （Ultralytics 默认把模型下到当前工作目录，实测因此往仓库扔了个 5.6MB 的 .pt。）
_MODEL_DIR = Path(os.environ.get("SAYA_CV_MODEL_DIR", Path.home() / ".cache" / "saya-cv"))
_YOLO_WEIGHTS = str(_MODEL_DIR / "yolo11n.pt")  # nano：先跑通，够用；要精度可换 s/m

_yolo = None
_ocr = None


def _get_yolo():
    global _yolo
    if _yolo is None:
        from ultralytics import YOLO

        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        _yolo = YOLO(_YOLO_WEIGHTS)
    return _yolo


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _check(image_path: str) -> Path | None:
    path = Path(image_path)
    if not path.is_file():
        return None
    return path


@mcp.tool()
def image_info(image_path: str) -> str:
    """返回图片的基本信息（宽高、格式、文件大小）。

    最轻的工具，用来快速确认"附属"这一侧是活的。
    """
    path = _check(image_path)
    if path is None:
        return f"错误：文件不存在 {image_path}"

    from PIL import Image

    started = time.monotonic()
    with Image.open(path) as img:
        width, height = img.size
        fmt = img.format or "未知"
    elapsed = int((time.monotonic() - started) * 1000)

    size_kb = path.stat().st_size // 1024
    return f"图片：{width}×{height} 像素，格式 {fmt}，{size_kb} KB（耗时 {elapsed} ms）"


@mcp.tool()
def detect_objects(image_path: str, detail: str = "count") -> str:
    """检测图片里的目标（YOLO）。

    Args:
        image_path: 本地图片路径
        detail: "count" 只给类别与数量（省 token）；"boxes" 额外给出坐标框

    Returns:
        检测结果的文字描述。检测不到目标时会明确说明。
    """
    path = _check(image_path)
    if path is None:
        return f"错误：文件不存在 {image_path}"

    started = time.monotonic()
    try:
        results = _get_yolo().predict(str(path), verbose=False)
    except Exception as exc:  # noqa: BLE001 - 出错要让模型看到原因，而不是断链
        return f"检测失败：{type(exc).__name__}: {exc}"
    elapsed = int((time.monotonic() - started) * 1000)

    boxes = results[0].boxes if results else None
    if boxes is None or len(boxes) == 0:
        return f"没有检测到任何目标（耗时 {elapsed} ms）"

    names = results[0].names
    rows: list[tuple[str, float, tuple]] = []
    for box in boxes:
        label = names[int(box.cls.item())]
        confidence = float(box.conf.item())
        coords = tuple(round(v) for v in box.xyxy[0].tolist())
        rows.append((label, confidence, coords))

    counts: dict[str, int] = {}
    for label, _, _ in rows:
        counts[label] = counts.get(label, 0) + 1

    lines = [f"检测到 {len(rows)} 个目标（{len(counts)} 类），耗时 {elapsed} ms："]
    for label, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {label} ×{count}")

    if detail == "boxes":
        lines.append("坐标框（x1,y1,x2,y2）与置信度：")
        for label, confidence, coords in rows:
            lines.append(f"  {label} {confidence:.2f} ({coords[0]},{coords[1]})-({coords[2]},{coords[3]})")

    return "\n".join(lines)


@mcp.tool()
def read_text(image_path: str) -> str:
    """识别图片里的文字（OCR，中文友好）。

    Args:
        image_path: 本地图片路径

    Returns:
        识别到的文本，按行。没识别到文字时会明确说明。
    """
    path = _check(image_path)
    if path is None:
        return f"错误：文件不存在 {image_path}"

    started = time.monotonic()
    try:
        result, _ = _get_ocr()(str(path))
    except Exception as exc:  # noqa: BLE001
        return f"OCR 失败：{type(exc).__name__}: {exc}"
    elapsed = int((time.monotonic() - started) * 1000)

    if not result:
        return f"没有识别到任何文字（耗时 {elapsed} ms）"

    lines = [str(item[1]) for item in result if item and len(item) > 1]
    header = f"识别到 {len(lines)} 行文字（耗时 {elapsed} ms）："
    return "\n".join([header, *lines])


@mcp.tool()
def annotate_objects(image_path: str, output_path: str) -> str:
    """检测目标、**在图上画出编号方框**并另存为新图片，返回编号说明。

    这是路线 C（混合）的"结构化产物"：附属负责**定位与编号**，
    主模型负责**语义推理**。做法与 Set-of-Mark（arXiv 2310.11441）一致 ——
    先用现成模型出候选，再把带标记的图交给多模态模型。

    Args:
        image_path: 输入图片路径
        output_path: 标注后图片保存到哪里（建议 .png）

    Returns:
        编号说明（第几号框是什么），供主模型对照图片使用。
    """
    path = _check(image_path)
    if path is None:
        return f"错误：文件不存在 {image_path}"

    from PIL import Image, ImageDraw

    started = time.monotonic()
    try:
        results = _get_yolo().predict(str(path), verbose=False)
    except Exception as exc:  # noqa: BLE001
        return f"检测失败：{type(exc).__name__}: {exc}"

    boxes = results[0].boxes if results else None
    if boxes is None or len(boxes) == 0:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.open(path).save(out)
        return f"没有检测到目标，已原样另存到 {out}"

    names = results[0].names
    with Image.open(path) as raw:
        canvas = raw.convert("RGB")
    draw = ImageDraw.Draw(canvas)

    lines: list[str] = []
    for index, box in enumerate(boxes, start=1):
        label = names[int(box.cls.item())]
        x1, y1, x2, y2 = (round(v) for v in box.xyxy[0].tolist())
        # 画框 + 左上角编号，编号用白底黑字保证可读
        draw.rectangle((x1, y1, x2, y2), outline=(255, 0, 0), width=3)
        tag = str(index)
        draw.rectangle((x1, y1 - 22, x1 + 20 + 10 * len(tag), y1), fill=(255, 255, 255))
        draw.text((x1 + 5, y1 - 20), tag, fill=(0, 0, 0))
        lines.append(f"  {index}: {label}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    elapsed = int((time.monotonic() - started) * 1000)

    return "\n".join(
        [f"已在图上标出 {len(lines)} 个目标并保存到 {out}（耗时 {elapsed} ms）：", *lines]
    )


if __name__ == "__main__":
    mcp.run()
