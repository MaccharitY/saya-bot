# 10 · 选题调研笔记（锚点论文与量化证据）

> 状态：**调研 v1（2026-10-15）**，由子代理检索产出，**尚未逐条打开原文核对**。
> 📚 **已扩充**：完整的约 45 条文献库、核实结果与"仍不确定"清单见 **[11-literature.md](11-literature.md)**。
> 本文件保留的是**选题判定**（第 4 节）与**实验设计**（第 5 节），不重复文献条目。
> ⚠️ **引用纪律**：本文件中所有具体数字都来自搜索引擎摘要，**写进报告前必须打开原文核对**。
> 已明确标注为"仅二手转述"的条目，**不要引用**。

---

## 1. 锚点论文候选

| # | 论文 | 出处 | 一句话 | 为什么适合当锚点 |
|---|---|---|---|---|
| 1 | **Set-of-Mark Prompting Unleashes Extraordinary Visual Grounding in GPT-4V** | arXiv 2310.11441，Microsoft，2023 | 先用分割模型（SEEM）在图上叠带编号的 mark，再让 GPT-4V 对 mark 指代/定位，视觉定位大幅提升 | **两条路线的交叉点**：专用 CV 出候选 + MLLM 做语义推理。可作为"混合路线最强"的核心论据 |
| 2 | **VisionLLM / VisionLLM v2** | arXiv 2305.11175（NeurIPS 2023）／ v2 arXiv 2406.08394（NeurIPS 2024） | 把 LLM 当开放式解码器，统一处理检测/分割等视觉中心任务 | "MLLM 直接当检测器"路线的代表作；v2 提供 2024 更新 |
| 3 | **COUNTS: Benchmarking Object Detectors and MLLMs under Distribution Shifts** | CVPR 2025 | 在分布偏移下让专用检测器与 MLLM 头对头对比 | 直接提供**两路线对比的评测框架与指标**，results 设计可照搬 |

**备选**：
- **LMM-Det**（arXiv 2507.18300，ICCV 2025）：指出 LMM 检测落后专用器，**瓶颈在 recall**
- **MM-OVOD**（arXiv 2306.05493，ICML 2023）：开放词表检测

## 2. MCP 侧材料

- **官方定位**：MCP 是标准化「LLM 应用 ↔ 外部工具/数据」连接的开放协议（modelcontextprotocol.io）。
- **tool use 相关规范页**：`https://modelcontextprotocol.io/specification/2025-11-25/server/tools`（含 `tools/list`、`tools/call`）
- **明确结论：MCP 不是学术论文。** 它是工程规范/开源协议（Anthropic 2024-11 发布），无同行评审。
- **规范引用格式**（报告里就这么写）：
  > Model Context Protocol, Specification 2025-11-25, §§ Tools, `https://modelcontextprotocol.io/specification/2025-11-25/server/tools`（2026-10-15 访问）。
  > **切勿为 MCP 编造 arXiv 编号。**
- 「LLM 调专用 CV 模型」的实践：Ophiuchus（ICML 2026，tool-augmented 医学分割）；Roboflow 工程博客 *YOLO vs. VLMs: When to Use Each*；社区已有 `mcp-yolo` 等 MCP server。

## 3. 可引用的量化证据（**全部待核对**）

| # | 结论 | 来源 | 可信度 |
|---|---|---|---|
| 1 | LMM 检测整体落后专用检测器，主因 **recall 低** | LMM-Det, ICCV 2025（CVF Open Access） | 较高，仍需核原文 |
| 2 | 头对头定量基准（MLLM vs 专用检测器） | *Do MLLMs Compete with Specialized Object Detection Models? A Quantitative Benchmark*, IEEE 2025 | 待核 |
| 3 | VLM 计数能力差，连数到 4 都吃力 | *Counting to Four is still a Chore for VLMs*, CVPRW 2026, arXiv 2604.10039；另有 PLOS ONE 2025《Visual enumeration remains challenging for multimodal generative AI》 | 待核 |
| 4 | 成本/延迟：何时该用监督训练 | *When Does Supervised Training Pay Off?*，arXiv 2510.11302 | 待核 |
| 5 | 传统检测器推理效率优于 VLM；TypeFly 因速度选 YOLO 而非 VLM | Springer doi 10.1007/s00170-026-17818-y；TypeFly, arXiv 2312.14950 | 待核 |

⛔ **不要引用**：流传的"GPT-4o Vision 检测精度仅 0.02"——**仅见中文二手转述**，数字明显失真（精度不可能是 0.02），引用会被当场问倒。

## 4. ⚠️ 对选题定位的重大影响（**务必读**）

**Set-of-Mark 严格说是"混合路线"，不是纯"MLLM 直接检测"。** 这印证了 [../docs/01-architecture.md](../docs/01-architecture.md) ADR-007 的担忧：

- 题目里"MCP"（协议）与"多模态模型"（模型）**不是同一层级**，直接对比是**范畴错误**。
- 而且现实中**最强的方案往往是混合的**（SoM = 专用 CV 出候选 + MLLM 推理）。

**建议的报告主线**（比"谁更好"更站得住）：

> 把视觉任务**全部交给 MLLM 本体**、**全部外包给专用 CV 模型 + LLM 编排**，以及**两者混合**，三种做法的取舍——在 QQ 群聊这类真实场景下，什么任务该走哪条路？

这样：选题不再犯范畴错误；SoM / VisionLLM / LMM-Det / COUNTS 全部能自然纳入；而且**你自己的实测数据正好能回答"该走哪条"**，是报告最有价值的部分。

## 5. 最小对照实验设计（可直接用于 M3）

- **规模**：同批 **≥100 张图**
- **难度分层**（每层约 20 张）：
  1. 常见类（猫、车、食物）
  2. 长尾/细粒度（具体角色、特定型号）
  3. 小目标
  4. 多目标计数 ← 论文已知 VLM 弱项，预期差异最大
  5. 含文字（OCR）
- **两条 pipeline**：
  - (A) `direct_mllm`：DeepSeek 多模态直接看图作答
  - (B) `mcp_tools`：MLLM 先路由 → 调 YOLO / GroundingDINO / OCR → 结构化结果回灌给 MLLM 汇总
- **控制**：固定 prompt 与温度；**配对设计**（同图同问）；报 mean ± std，做配对显著性检验
- **指标**：分类 top-1 ／ 检测 mAP@0.5 ／ 计数 MAE ｜ 延迟 P50 & P95 ｜ 单次成本 ｜ 幻觉率与拒答率 ｜ 可解释性（能否给 bbox/置信度）｜ 实现复杂度

> 这个规模足以撑起 1000 词的 results 一节。

## 6. 待人工核实清单

- [ ] 第 3 节**全部数字**：均未打开原文，尤其需要核实出处真实存在
- [ ] MCP spec 版本：确认存在 `2025-11-25`；疑似还有 `2026-07-28` 路径，**是否正式发布待核**
- [ ] COUNTS 的 arXiv 编号未确认（仅见 CVF Open Access / IEEE 11093413）
- [ ] "开放词表/长尾优势"的量化证据**较弱**（MQADet、Sci Rep 2026、OVEarth-Bench 只给评测集）→ **勿硬引，建议自测**
- [ ] 交作业前对照 `requirement/img1.jpg` 核对课程要求原文

## 7. 主要来源

- [Set-of-Mark (arXiv 2310.11441)](https://huggingface.co/papers/2310.11441)
- [VisionLLM (arXiv 2305.11175)](https://huggingface.co/papers/2305.11175)
- [VisionLLM v2 (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/file/81a60d18e010b27b36cd465c6604b915-Paper-Conference.pdf)
- [MM-OVOD (arXiv 2306.05493)](https://axi.lims.ac.uk/paper/2306.05493)
- [COUNTS, CVPR 2025](https://www.openaccess.thecvf.com/content/CVPR2025/html/Li_COUNTS_Benchmarking_Object_Detectors_and_Multimodal_Large_Language_Models_under_CVPR_2025_paper.html)
- [LMM-Det, ICCV 2025](https://www.openaccess.thecvf.com/content/ICCV2025/html/Li_LMM-Det_Make_Large_Multimodal_Models_Excel_in_Object_Detection_ICCV_2025_paper.html)
- [MCP tools spec 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Roboflow: YOLO vs VLMs](https://roboflow.com/blog/yolo-vs-vlms-when-to-use-each)
- [Hidden Economics (arXiv 2510.11302)](https://ar5iv.labs.arxiv.org/html/2510.11302)
