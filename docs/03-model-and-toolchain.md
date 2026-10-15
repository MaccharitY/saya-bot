# 03 · 模型与工具链选型

> 回答的问题：**三条路线各自用什么模型、什么工具落地？**
> 探明日期：2026-10-15 ｜ 状态：**建议方案，待评审**
> ⚠️ 国内模型的版本号迭代很快，下表写的是**能力档位**，实际取用时以厂商最新文档为准。

---

## 0. 三句话结论

1. **所有多模态大模型（路线 A / C 用）：一律用 OpenAI 兼容 SDK 调** —— 一个 `openai` 包 + 改 `base_url`，就能切换全部国内模型。
2. **所有专用 CV 模型（路线 B / C 用）：本地跑开源模型** —— 本机有 12GB 显存，不花钱、延迟可控，而且"专用模型"本就该是本地/自持的。
3. **编排层两种实现都做**：先 `function calling`（裸、简单），再 `MCP`（标准化）—— 报告里正好对比"多一层 MCP 到底多给了什么"。

---

## 1. 统一底座：OpenAI 兼容 SDK

**这一条就是"在什么工具上实现"的答案。**

国内厂商（DeepSeek、通义千问、智谱、Kimi、豆包、阶跃、MiniMax…）**几乎都提供 OpenAI 兼容端点**。所以：

```python
from openai import OpenAI

client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
resp = client.chat.completions.create(model=cfg.model, messages=msgs)
```

切换模型 = 改两个配置项，**不用为每个厂商学一套 SDK**。这层封装归 `services/llm.py`。

⚠️ 唯一要小心的地方：**多模态输入格式厂商之间有差异**（图片走 base64 还是 URL、是否支持多图）。所以 `services/llm.py` 里要有一层薄适配，把内部统一的 `Image` 对象翻译成各家格式。这是本项目唯一需要为"多厂商"付出的额外代码。

## 2. 路线 A · 多模态模型直接看图

| 模型档位 | 厂商 | 说明 |
|---|---|---|
| `deepseek-v4.1-flash` | DeepSeek | **本工作区既定后端**，原生多模态，可直接识图 |
| Qwen-VL 系列 | 阿里 | 国内第一梯队视觉能力 |
| GLM-4V 系列 | 智谱 | |
| Kimi 视觉 | 月之暗面 | |
| 豆包 vision | 字节 | |
| Step-1V 系列 | 阶跃 | |

**建议的实验设计**：
- **主实验固定用一个模型**（建议 DeepSeek，因为本工作区已配好、且是项目既定核心）
- **另用 Qwen-VL 跑一轮稳健性对照** —— 否则结论可能被质疑"只对某个模型成立"

## 3. 路线 B · 专用 CV 模型 + LLM 编排

### 3.1 专用模型（本地开源）

| 任务 | 模型 | Python 包 | 显存 |
|---|---|---|---|
| 检测（闭集） | YOLO11 | `ultralytics` | 小 |
| 检测（**开放词表**） | GroundingDINO | `transformers` 或官方实现 | 中 |
| 分割 | SAM 2 | `segment-anything-2` | 中大 |
| OCR（中文友好） | PaddleOCR 或 RapidOCR | `paddleocr` / `rapidocr-onnxruntime` | 小 |
| 图文相似度/分类 | CLIP | `open_clip_torch` | 小 |

> 12GB 显存足够跑上述**全部**（不是同时跑）。若显存吃紧，OCR 有轻量 ONNX 版本。

### 3.2 编排层 —— 两种实现，**都要做**

**方式 1 · function calling（裸调用）**
用 OpenAI 兼容的 `tools` 参数。LLM 输出"我要调 `detect(image, classes)`"，本地执行后把结果回灌。简单、直接、无额外抽象。

**方式 2 · MCP（标准化）**
`pip install mcp`，用 FastMCP 把上面的 CV 模型包成 MCP server（暴露 `detect` / `ocr` / `segment` 等 tool），LLM 侧用 MCP client 连接。

**为什么两种都做**：报告里就能对比"裸 function calling" vs "标准化 MCP"，**用实测回答"MCP 到底带来了什么额外价值"**——而不是空谈协议设计。这正是选题里"MCP 那一半"的落点。

## 4. 路线 C · 混合（Set-of-Mark 一类）

= **路线 B 的 CV 部分出候选** + **画框/编号**（PIL 或 OpenCV 绘制）+ **路线 A 的模型做语义判断**

落地：`services/vision.py` 内部多一个 `annotate()` 步骤，把 CV 的输出渲染成带编号的图，再交给 MLLM。这正是 SoM 论文的做法，报告里可直接对照。

## 5. 三条路线共用一个接口

```python
def describe(
    image: Image,
    question: str,
    route: Literal["direct_mllm", "mcp_tools", "hybrid"],
) -> Result
```

`Result` 必须带 `meta`：`route / model / tokens / latency_ms / cost / artifacts(bbox, ocr_text, ...)`

三条路线返回**同样的 meta 结构**，才可能公平对照（见 ADR-005）。

## 6. ⚠️ 成本口径（报告的方法学要点，答辩最容易被问）

- 路线 A、C 的**模型部分**：按 API token 计费 → 有明确账单
- 路线 B、C 的**CV 部分**：本地算力 → **没有 API 账单**，只有硬件折旧 + 电费

**两者不能直接相加比较。** 报告里必须**分列两种口径**：

- **边际 API 成本**（每千次调用多少钱）
- **本地算力摊销**（按 GPU 时价折算）

并说明：本地模型有**前期投入换长期低边际成本**的性质，图片量大时优势才显现。**忽略这一点的对比是无效的。**

## 7. 本机环境现状（2026-10-15 实测）

| 项 | 状态 |
|---|---|
| GPU | **NVIDIA RTX 5070 Ti Laptop, 12GB 显存** ✓ 本地 CV 可行 |
| Python | 3.11.9 ✓ |
| Node | 24.21 ✓ |
| git | 2.55 ✓ |
| **QQ 客户端** | **已装**：`D:\Program Files\Tencent\QQ\QQ.exe`；数据目录 `%APPDATA%\Tencent\QQNT` ✓ |
| 已装相关包 | 仅 `pillow` —— 其余需装 |
| docker / gh CLI | **无**（NapCat 用 Windows 版，不需要 docker） |
| 磁盘 | C: 剩余 ~137 GB ✓ |

## 8. 待办

- [ ] 装依赖：`openai` / `httpx` / `pillow` / `pydantic` / `ultralytics` / `paddleocr` / `mcp`
- [ ] 各厂商 API key 的获取与配额确认（所有者负责）
- [ ] 实测 GPU 上 YOLO / PaddleOCR 的吞吐，验证"本地路线"是否真的更快
- [ ] 确认 NapCat 的当前推荐安装方式（版本迭代快，安装前现查）
