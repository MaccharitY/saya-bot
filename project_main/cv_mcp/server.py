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
# ⚠️ 权重可换：`SAYA_YOLO_WEIGHTS=yolo11x.pt`。
# 换之前先读报告里的讨论 —— 我们撞到的限制是**词表**（80 类），不是精度，
# 换更大的模型不会让它认识"棋子"或"数字标签"。
_YOLO_WEIGHTS = str(_MODEL_DIR / os.environ.get("SAYA_YOLO_WEIGHTS", "yolo11n.pt"))
# 开放词表模型（YOLOE-26 一类）。只有调用方传了 `classes` 才会加载，
# 所以闭合词表的实验与线上行为完全不受影响。
_WORLD_WEIGHTS = str(_MODEL_DIR / os.environ.get("SAYA_YOLO_WORLD", "yoloe-26x-seg.pt"))

_yolo = None
_world = None
_world_classes = ""
_ocr = None
# 检测侧的降级说明（换权重失败 / GPU 不可用）。和云 OCR 那条一样：**退回要留痕**。
_yolo_note = ""


def _get_yolo():
    global _yolo, _yolo_note
    if _yolo is None:
        from ultralytics import YOLO

        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _yolo = YOLO(_YOLO_WEIGHTS)
        except Exception as exc:  # noqa: BLE001
            # 配置的权重加载不了（没下完 / 文件坏了）→ 退回最小可用的一档。
            # ⚠️ 必须留痕：11x 和 11n 的精度差一截，静默换掉会让结果无从解释。
            fallback = str(_MODEL_DIR / "yolo11n.pt")
            _yolo = YOLO(fallback)
            _yolo_note = (
                f"（检测器 {Path(_YOLO_WEIGHTS).name} 加载失败，"
                f"已退回 {Path(fallback).name}：{type(exc).__name__}）\n"
            )
    return _yolo


def _predict(path):
    """跑一次检测。**设备出错退回 CPU**，同样留痕。

    ⚠️ GPU 出错的原因很多（显存、驱动、CUDA 版本），但结果都一样：
    这一题不该因为一次设备故障被记成"检测不到目标"。
    """
    global _yolo_note
    model = _get_yolo()
    device = _device()
    try:
        return model.predict(str(path), verbose=False, device=device)
    except Exception:  # noqa: BLE001
        if device == "cpu":
            raise
        _yolo_note += "（GPU 推理失败，检测已退回 CPU）\n"
        return model.predict(str(path), verbose=False, device="cpu")


def _get_world(classes: str):
    """加载开放词表模型，并把词表设成调用方给的那一组。

    词表没变就不重复 `set_classes` —— 它会重建文本嵌入，比推理本身还贵。
    """
    global _world, _world_classes
    if _world is None:
        from ultralytics import YOLOE  # noqa: PLC0415 - 只有开放词表实验才用

        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        _world = YOLOE(_WORLD_WEIGHTS)
    if classes != _world_classes:
        names = [item.strip() for item in classes.split(",") if item.strip()]
        _world.set_classes(names)
        _world_classes = classes
    return _world


def _predict_world(path, classes: str):
    global _yolo_note
    model = _get_world(classes)
    device = _device()
    try:
        return model.predict(str(path), verbose=False, device=device)
    except Exception:  # noqa: BLE001
        if device == "cpu":
            raise
        _yolo_note += "（GPU 推理失败，检测已退回 CPU）\n"
        return model.predict(str(path), verbose=False, device="cpu")


def _device() -> str:
    """YOLO 跑在哪儿。

    ⚠️ **Ultralytics 默认是 CPU** —— 即使 CUDA 可用也不自动上去
    （2026-10-17 实测：`YOLO(...).device` 是 `cpu`，GPU 全程没动静）。
    所以这里显式选：`SAYA_CV_DEVICE` 优先，不写就自动挑 GPU。
    """
    raw = os.environ.get("SAYA_CV_DEVICE", "").strip()
    if raw:
        return raw
    try:
        import torch  # noqa: PLC0415 - 只有需要时才 import，省启动时间

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001 - 装不上 torch 也不该让附属起不来
        return "cpu"


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
def detect_objects(image_path: str, detail: str = "count", classes: str = "") -> str:
    """检测图片里的目标（YOLO / YOLOE）。

    Args:
        image_path: 本地图片路径
        detail: "count" 只给类别与数量（省 token）；"boxes" 额外给出坐标框
        classes: 可选，逗号分隔的开放词表类别（如 "rabbit,bird"）。
                 传了就用开放词表模型；不传则用闭合词表模型。

    Returns:
        检测结果的文字描述。检测不到目标时会明确说明。
    """
    path = _check(image_path)
    if path is None:
        return f"错误：文件不存在 {image_path}"

    started = time.monotonic()
    try:
        if classes.strip():
            results = _predict_world(path, classes)
        else:
            results = _predict(path)
    except Exception as exc:  # noqa: BLE001 - 出错要让模型看到原因，而不是断链
        return f"检测失败：{type(exc).__name__}: {exc}"
    elapsed = int((time.monotonic() - started) * 1000)

    boxes = results[0].boxes if results else None
    if boxes is None or len(boxes) == 0:
        return _yolo_note + f"没有检测到任何目标（耗时 {elapsed} ms）"

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

    return _yolo_note + "\n".join(lines)


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

    # ⚠️ **云 OCR 是可选的**（`SAYA_CV_OCR_BACKEND`）。
    # 本地的 PP-OCRv3 是 2022 年的模型；拿它代表"专用 OCR 路线"不公平，
    # 所以实验里可以把这一半换成阿里云（达摩院）—— 检测那一半仍留本地，
    # 因为阿里云的物体检测只收公网 URL，要多配一层 OSS。
    backend = os.environ.get("SAYA_CV_OCR_BACKEND", "auto").strip().lower()
    try:
        import cloud_ocr  # noqa: PLC0415 - 同目录；依赖在模块内部按需 import
    except ImportError:
        cloud_ocr = None

    # ⚠️⚠️ **云失败一定退回本地，而且要在结果里留痕。**
    # 直接返回一句错误，等于整条委派路线因为一次网络抖动全废；
    # 而静默降级又会让"这一轮到底用的谁"无从判断。所以退回 + 前缀说明。
    note = ""
    if backend != "local":
        if cloud_ocr is None or not cloud_ocr.available():
            if backend == "cloud":
                note = "（云 OCR 未配置，已退回本地：缺 SAYA_BAIDU_OCR_KEY / SECRET）\n"
        else:
            try:
                return cloud_ocr.read_text(path)
            except Exception as exc:  # noqa: BLE001
                note = f"（云 OCR 不可用，已退回本地：{type(exc).__name__}）\n"

    started = time.monotonic()
    try:
        result, _ = _get_ocr()(str(path))
    except Exception as exc:  # noqa: BLE001
        return f"{note}OCR 失败：{type(exc).__name__}: {exc}"
    elapsed = int((time.monotonic() - started) * 1000)

    if not result:
        return f"{note}没有识别到任何文字（耗时 {elapsed} ms）"

    lines = [str(item[1]) for item in result if item and len(item) > 1]
    header = f"识别到 {len(lines)} 行文字（耗时 {elapsed} ms）："
    return note + "\n".join([header, *lines])


@mcp.tool()
def annotate_objects(image_path: str, output_path: str, classes: str = "") -> str:
    """检测目标、**在图上画出编号方框**并另存为新图片，返回编号说明。

    这是路线 C（混合）的"结构化产物"：附属负责**定位与编号**，
    主模型负责**语义推理**。做法与 Set-of-Mark（arXiv 2310.11441）一致 ——
    先用现成模型出候选，再把带标记的图交给多模态模型。

    Args:
        image_path: 输入图片路径
        output_path: 标注后图片保存到哪里（建议 .png）
        classes: 可选，逗号分隔的开放词表类别；不传则用闭合词表模型

    Returns:
        编号说明（第几号框是什么），供主模型对照图片使用。
    """
    path = _check(image_path)
    if path is None:
        return f"错误：文件不存在 {image_path}"

    from PIL import Image, ImageDraw

    started = time.monotonic()
    try:
        if classes.strip():
            results = _predict_world(path, classes)
        else:
            results = _predict(path)
    except Exception as exc:  # noqa: BLE001
        return f"检测失败：{type(exc).__name__}: {exc}"

    boxes = results[0].boxes if results else None
    if boxes is None or len(boxes) == 0:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.open(path).save(out)
        return _yolo_note + f"没有检测到目标，已原样另存到 {out}"

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

    return _yolo_note + "\n".join(
        [f"已在图上标出 {len(lines)} 个目标并保存到 {out}（耗时 {elapsed} ms）：", *lines]
    )


if __name__ == "__main__":
    mcp.run()
