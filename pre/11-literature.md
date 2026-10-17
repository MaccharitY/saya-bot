# 11 · 文献库（三路线取舍）

> 状态：**v1（2026-10-15）**，由自动化检索产出，约 40 篇/条目。
> ⚠️ **引用纪律（务必先读）**
> 1. 条目来源：首轮**只能看搜索摘要**（当时以为网络被拦，实为 **WebFetch 工具本身坏了**，见下）。第二轮已用直连工具**联网核实了 19 条**，其余仍未核对。
> 2. 可信度分三档：
>    - **【✅已联网核实】** —— 已用 `tools/fetch.py` 直连官方页面核对过元数据（标题 / 作者 / 会议 / 编号）。
>    - **【出处已定位】** —— 搜索返回了官方页面，标题与编号一致，**元数据可信，但正文数字未核对**。
>    - **【仅搜索摘要】** —— 只看到二手来源（博客、聚合站），**谨慎**。
> 3. **写进报告前，每一条被引用的都必须自己打开原文核对。**
> 4. **核对手段**：`python tools/fetch.py <url>`（本机 WebFetch 坏了，但**网络是通的**，见 `../tools/README.md`）。**查会议归属用 OpenAlex API** —— 这是核实过程中最有用的发现。
> 5. 路线标签：**A** = 多模态大模型本体直接看图；**B** = 专用 CV 模型 + LLM 编排；**C** = 混合（检测器出候选 + MLLM 语义推理）。

---

## 0. 核心论点（本库支撑的报告主线）

证据方向高度一致，可以撑起这样一句结论：

> **精确、可量化、高吞吐、成本敏感的任务 → 路线 B 压倒性占优；完全没见过的类别 + 无标注的冷启动、纯语义理解 → 路线 A 有优势但强度中等；既需要专精定位又需要语义推理 → 路线 C 是最优折中。**
>
> 而 **2025–2026 的主旋律是 B 与 C 的工程化**（MCP、工具编排、SoM 升级），**"让 MLLM 直接看图就得出精确结论"（纯 A）在可量化任务上被反复证伪**。

**这正好支撑我们的实验设计**：不比"谁更好"，而比"什么任务该走哪条路"。自己的 QQ 群聊实测数据就是这句话的现场证据。

---

## 1. 路线 B 主干：让 LLM 调度视觉模型

### 1.1 范式起源（2023）
| 论文 | 出处 | 一句话 |
|---|---|---|
| **Visual ChatGPT** | arXiv 2303.04671，微软，2023 | 把大量视觉基础模型（检测/分割/深度/编辑）封装成 ChatGPT 可调工具，由 LLM 决定何时调哪个。路线 B 的原始原型之一。**【出处已定位】** |
| **HuggingGPT**（JARVIS） | NeurIPS 2023，arXiv 2303.17580 | "LLM 做控制器、HuggingFace 模型做专家"的四阶段流水线：任务规划→模型选择→执行→汇总。经典的 LLM-as-orchestrator 范式。**【✅ 已打开原文核对：2026-10-18，arXiv 摘要页；摘要确认四阶段与 controller 定位。⚠️ arXiv 页未标会议，NeurIPS 2023 需另行核实】** |
| **MM-REACT** | arXiv 2303.11381，微软，2023 | 提示 + 工具（caption/OCR/detector）让纯文本 ChatGPT 获得多模态推理能力；强调小规模视觉工具即可补足 LLM 视觉短板。**【出处已定位】** |
| **ViperGPT** | ICCV 2023，arXiv 2303.08128，哥大 | 用 LLM 把自然语言问题**编译成 Python 程序**，程序内部调用视觉 API 并组合运算。"视觉任务 = 代码合成 + 工具调用"的代表。**【出处已定位】** |
| **IdealGPT** | EMNLP 2023 Findings，arXiv 2305.14985 | 把复杂视觉问答拆成子问题，由"推理 LLM + 描述/回答模型"迭代（分解→回答→复核）。接近 C 的拆分思路。**B/C**【出处已定位】 |

### 1.2 2025–2026 的系统化/学习化（新近正式发表）
| 论文 | 出处 | 一句话 |
|---|---|---|
| **VipAct** | AAAI 2026，arXiv 2410.16400，Adobe 等 | 主力 VLM 在**需要像素级细节的细粒度视觉感知**上仍然吃力；用 orchestrator + 多专家 agent + 视觉专家模型补全感知，并做消融。**【✅ 已打开原文核对：2026-10-18；Comments 明确 AAAI 2026。⚠️ 原描述"视觉提示/标注区域"不在摘要里，已按摘要改正 —— 若报告要引用该细节，须再读正文】** |
| **VTOS** | EMNLP 2026，arXiv 2606.20728 | 面向开放词表检测器/分割模型/后处理算子的**可学习编排**框架。**【出处已定位】** |
| **AdaReasoner** | ICLR 2026，arXiv 2601.18631 | 为迭代式视觉推理做**动态工具编排**，自适应决定调哪些视觉工具、调几次。**【出处已定位】** |
| **CodeDance** | CVPR 2026 | 把工具调用与代码执行**内化进 MLLM**，做可执行视觉推理。**【出处已定位】** |
| **Thinking with Programming Vision** | CVPR 2026，arXiv 2512.03746 | 把"用图像思考"统一为"用程序调用视觉操作"的视角，可当 B/C 的理论框架。**【出处已定位】** |
| **DART** | EACL 2026 | 用多 agent 分歧决定招募哪些工具。**【出处已定位】** |

**小结**：整条线都指向路线 B —— **凡是要精确、量化、多步操作的任务，让专用模型干，LLM 只做编排。**

---

## 2. MCP × 视觉 / 多模态

| 条目 | 出处 | 说明 |
|---|---|---|
| **M³-Bench**（读作 M-cubed，**不是 M3**） | arXiv 2511.17729，2025，Yang Zhou 等 9 人（含 Dimitris N. Metaxas） | 摘要原文确有 "the first benchmark for evaluating multimodal tool use under the Model Context Protocol"。MCP + 视觉目前能找到的**最正式的可引用学术来源**。**【✅已联网核实】** ⚠️ "first" 是**论文的自我声明**，无法独立验证——引用时别把"首个"当既成事实 |
| **MCP 规范 2025-11-25** | 官方 changelog / PR #1887 | **确认为正式版**。**【出处已定位】** |
| **MCP 规范 2026-07-28** | 官方 release tag | **确实已发布**，是"无状态核心"大改版。**【出处已定位】** |
| 社区实现：`mcp-yolo` / `yolo-mcp-server` / `ultralytics_mcp_server` | GitHub | 已有多个把 YOLO/Ultralytics 封装成 MCP 工具的**成熟公开实现**，证明"专用 CV 模型当工具、LLM 经 MCP 调用"工程上可行。**【仅搜索摘要】** |
| OSWorld-MCP / "MCP-Based AI Agent for PC Control" | scite / IEEE 11722066 | 与视觉任务关系较弱，边缘来源。**【仅搜索摘要】** |

**⚠️ 一条值得注意的工程实践佐证**：社区有项目（如 `deepseek-vision-pro`）做的就是"**给纯文字 DeepSeek 装上识图眼睛：本地代理 + 智谱 GLM-4V 免费视觉模型**"。这就是**路线 B 在真实中文生态里的落地形态**，可作为"该路线已被工程界采用"的旁证。
引用时按**软件/工程来源**处理（仓库 + commit/访问日期），**不是论文**。

**⚠️ MCP 的引用规范（报告里必须这样做）**

1. MCP **不是学术文献**，不能当论文引用，**切勿编造会议名或 arXiv 号**。
2. 上位引用**官方规范**：`Model Context Protocol, Specification 2026-07-28, §§ Tools, https://modelcontextprotocol.io/specification/2026-07-28/changelog （访问日期）`。
3. 社区仓库按**软件/工具**引用，**必须带 commit hash 或访问日期**（内容会变）。
4. 社区实现只能作为"存在性佐证"写在正文，不放进论文参考文献列表。

---

## 3. 路线 C 谱系：检测/分割出候选 + MLLM 推理

| 论文 | 出处 | 一句话 |
|---|---|---|
| ⭐ **Set-of-Mark Prompting Unleashes Extraordinary Visual Grounding in GPT-4V** | arXiv 2310.11441（v2, 2023-11-06），Microsoft | **路线 C 的奠基作**。用**现成的交互式分割模型（SEEM / SAM）**把图切成不同粒度的区域，叠上标号（字母数字 / 掩码 / 框），再让 GPT-4V 对编号做指代与定位。**核心结果：GPT-4V + SoM 在零样本设定下，超过了 RefCOCOg 上全量微调的 SOTA 指代理解与分割模型。** 作者：Jianwei Yang, Hao Zhang, Feng Li, Xueyan Zou, Chunyuan Li, Jianfeng Gao。**【✅ 已打开原文核对：2026-10-15，用 tools/fetch.py 抓 arXiv 摘要页】** |
| **SoM-LLaVA**（List Items One by One） | COLM 2024，arXiv 2404.16375 | 用 Set-of-Mark 作**数据源与训练范式**，把 SoM 从"推理期技巧"升级为"训练期能力"。SoM 谱系直接后继。**【出处已定位】** |
| **Ferret / Ferret-v2** | ICLR 2024，arXiv 2310.07704 / 2404.07973，Apple | 混合区域输入（点/框/任意形状），任意粒度 grounding。**【出处已定位】** |
| **Shikra** | arXiv 2306.15195，2023 | "指代对话"：坐标以自然数直接输入输出，无需额外词表。**【出处已定位】** |
| **CogVLM**（含 Grounding 版） | NeurIPS 2024，arXiv 2311.03079，清华/智谱 | 每层加"视觉专家"做深度融合，Grounding 版刷新 SOTA。**"把定位训进模型"与"外接检测器"之间的折中**——讨论 A vs C 的关键对照。**A/C**【出处已定位】 |
| **Kosmos-2** | ICLR 2024，arXiv 2306.14824，微软 | 用"链接框"对齐文本 span 与图像区域，**模型原生输出坐标框**。A 的"原生 grounding"代表。**A/C**【出处已定位】 |
| **LLaVA-Grounding** | ECCV 2024，arXiv 2312.02949，微软 | 在 LLaVA 上接入 grounding，支持 RefCOCO 等。C 路线常用基线。**【出处已定位】** |
| **Grounded SAM** | arXiv 2401.14159，IDEA | Grounding DINO + SAM 组合 = **路线 C 的基础设施**。**【出处已定位】** |
| **Graph-of-Mark** | **AAAI 2026**，arXiv 2603.06663 | SoM 升级：不只编号，还**构建区域间关系图**注入提示，改善空间关系推理；摘要报告视觉问答与定位的基线准确率最多提升 **11 个百分点**。**【✅ 已打开原文核对：2026-10-18；Comments 给出正式版 AAAI 2026, Vol. 40, No. 36, pp. 30726-30734】** |
| **Seg-Agent** | arXiv 2605.12953，2026 | 免训练，把语言引导分割做成测试时多模态推理（agent 化管线）。**【仅搜索摘要】** |
| **SpatialRGPT** | NeurIPS 2024，arXiv 2406.01584 | 引入区域级深度/空间信息做显式 grounding —— 说明纯 RGB+语言不足以做空间推理。**【出处已定位】** |

**小结（技术谱系很清晰）**：
- Ferret / Shikra / Kosmos-2 / CogVLM → 走向"**把 grounding 训进模型**"（偏 A）
- Grounded-SAM / SoM-LLaVA / Graph-of-Mark / Seg-Agent → 走向"**外接候选 + 提示**"（纯 C）
- **Graph-of-Mark (AAAI 2026) 是 C 路线最新的强证据。**

---

## 4. MLLM 视觉能力的已知短板（六类，全部有量化证据）

这些证据**一致指向**：精细定位 / 量化 / 枚举 / 识别字符 / 空间关系这些"可被专用模型确定性解决"的任务，MLLM 本体不可靠。

### 4.1 计数
- **CountQA**（arXiv 2508.06585，2025）：MLLM 在真实场景计数普遍很差，归因于固定低分辨率编码。**【出处已定位】**
- **Visual Enumeration Remains Challenging for Multimodal Generative AI**（**PLoS ONE 2025**，DOI 10.1371/journal.pone.0331566）：系统证明现代多模态系统**仍不能可靠枚举数量**。**【出处已定位】**
- **Counting to Four is still a Chore for VLMs**（**CVPRW 2026**，arXiv 2604.10039）：连"数到四"都做不好。**已核实真实存在。**
  **【✅ 已打开原文核对：2026-10-18】** 摘要的结论比"纯感知失败"更细：计数相关证据在最强的模态投影层之后、**在语言层被显著削弱**，模型转而依赖文本先验 —— 失败同时来自**视觉感知上限**与**语言阶段对视觉证据利用不足**。这直接支持我们补做"开思考预算"的对照（见 `14-experiment-iteration-log.md`）。

### 4.2 小目标 / 细尺度
- **The Last Visible Pixel**（arXiv 2606.07861，2026）：探测 VLM 在像素级细尺度感知上的失败。**【出处已定位】**

### 4.3 细粒度识别
- **Eyes Wide Shut?（MMVP 基准）**（**CVPR 2024**，arXiv 2401.06209，含 Yann LeCun / Saining Xie）：提出"CLIP-blind pairs"——多模态 LLM 在视觉上**系统性失败**于人类觉得简单、但 CLIP 编码器也分不清的成对图像。
  ⭐ **这是"路线 A 的能力上限受制于视觉编码器"的核心实证**，强烈支持 B/C。**【✅ 已打开原文核对：2026-10-18；CLIP-blind pairs、九类视觉模式、GPT-4V 出错并编造解释、MoF 补救均已在摘要确认。⚠️ arXiv 页未标 CVPR，会议归属需另核】**

### 4.4 空间关系
- **MLLM-CompBench**（NeurIPS 2024 D&B）：量化 MLLM 在比较/空间关系上的不足。**【出处已定位】**
- 另见 SpatialRGPT（空间推理需外部结构化输入）。

### 4.5 OCR
- **CC-OCR**（**ICCV 2025**）：大规模多场景 OCR 基准，显示 LMM 在复杂版面/小字上仍**显著落后专用 OCR**。**【出处已定位】**
- **MME-VideoOCR**（NeurIPS 2025，arXiv 2505.21333）。**【出处已定位】**
- **Seeing is Believing? Mitigating OCR Hallucinations in MLLMs**（NeurIPS 2025）：OCR 幻觉。**【出处已定位】**

### 4.6 幻觉
- **POPE**（EMNLP 2023，arXiv 2305.10355）：轮询式对象探测，揭示 VLM 频繁"看到不存在的物体"。**【出处已定位】**
- **HallusionBench**（CVPR 2024，arXiv 2310.14566）：语言幻觉 + 视觉错觉诊断套件，**GPT-4V 亦受挫**。**【出处已定位】**
- **MIRAGE**（arXiv 2505.24238）：多模态推理链中的幻觉。**【出处已定位】**

---

## 5. 成本 / 延迟 / 吞吐

| 来源 | 出处 | 说明 |
|---|---|---|
| ⭐ **When Does Supervised Training Pay Off? The Hidden Economics of Object Detection in the Era of VLMs** | arXiv 2510.11302，2025 | **最直接回答"付费 API vs 本地专用模型摊销"的论文**。给出成本表（规模/标注/训练+基建/总前置成本）。搜索摘要出现"YOLO 与 Gemini 相差 22.7 个百分点、与 GPT-4 相差 19.9 个百分点"。**【出处已定位】／数字【仅搜索摘要】** |
| **Comparative analysis of cloud-based AI object detection services and YOLO11** | **Journal of Cloud Computing (Springer) 2025**，DOI 10.1186/s13677-025-00835-9 | 同行评审期刊，系统对比云检测 API 与本地 YOLO11 的性能/成本/易用性（含 RTX 3060 Ti 实测）。**路线 B 成本论证的高质量可引用来源。**【出处已定位】 |
| **VL-RouterBench** | CVPR 2026，arXiv 2512.23562 | 为"按难度路由到不同 VLM"建基准，凸显按任务分配算力省成本的必要性。**【出处已定位】** |
| **A Picture is Worth a Thousand Tokens** | ICML 2026 | 视觉 token 压缩省钱省延迟，间接说明 MLLM 视觉请求 token 成本高昂。**【出处已定位】** |
| **Cost-Efficient Multimodal LLM Inference via Cross-Tier GPU Heterogeneity** | arXiv 2603.12707，2026 | 跨级 GPU 异构降低 MLLM 推理成本。**【出处已定位】** |
| **Roboflow: CapEx vs OpEx in CV Deployment** | 工程博客 | "自建摊销 vs 云服务"的 TCO 框架，按博客引用。**【仅搜索摘要】** |

**小结**：路线 B 在"高吞吐、固定任务"下本地专用模型摊销更划算；MLLM API 只适合低吞吐、长尾、需语义理解的场景。⚠️ 与本项目 `docs/03` 第 6 节的口径提醒一致：**两者不可直接相加**。

---

## 6. 开放词表 / 长尾：MLLM 的优势证据（结论：有，但"有条件"）

| 来源 | 出处 | 说明 |
|---|---|---|
| ⭐ **Do Multimodal Large Language Models Compete with Specialized Object Detection Models? A Quantitative Benchmark** | **2025 IEEE International Conference on Big Data (BigData)**，DOI `10.1109/bigdata66926.2025.11402578` | 直接对比两者的定量基准。**✅书目已核实**：作者 **Andrew Dunton, Priya Govindarajulu, Mohammad Masum**（均 San Jose State University）——文献库原先漏了第三作者。⚠️ **IEEE 与 Semantic Scholar 反爬抓不到，摘要读不了 → "核心结论"仍未核验**，写进报告前必须自己拿到摘要 |
| **RAR: Retrieving And Ranking Augmented MLLMs** | arXiv 2403.13805，2024 | 在 **LVIS（大量长尾类别）** 上用检索增强放大 MLLM 识别能力 → MLLM 的语义知识对罕见类别有独特价值，**但需外部检索辅助**。**【出处已定位】** |
| **Unlocking Object Detection in Vision-Language Models** | IEEE Access 2026，DOI 10.1109/ACCESS.2026.3675517 | VLM 可**免任务特定训练**做类别提示式检测，体现开放词表灵活性。**【出处已定位】** |
| **WeDetect** | CVPR 2026 | 开放词表检测专用模型的新进展。**【出处已定位】** |
| **OVEarth-Bench** | arXiv 2607.27278 | 开放词表遥感基准。**【出处已定位】** |
| ⭐ **COUNTS**（反证） | **CVPR 2025，arXiv 2504.10158** | 同一套计数任务下，**专用检测器在分布漂移下鲁棒性明显更好**。**【出处已定位】** |

**净结论（推断）**：上一轮说的"这类证据较弱"基本成立。
**"完全没见过的类别名 + 无标注"场景，MLLM 有冷启动优势（A）；一旦能定义任务并采少量数据，专用模型（含开放词表检测器）反超（B）。**

---

## 7. 核实结果（逐条回填）

| 待核项 | 结论 |
|---|---|
| "Do MLLMs Compete…" | ✅ 真实存在。**第二轮已查清**：会议 = **2025 IEEE International Conference on Big Data (BigData)**，DOI `10.1109/bigdata66926.2025.11402578`，作者**三人**：Andrew Dunton, Priya Govindarajulu, **Mohammad Masum**（文献库原先漏了第三位） |
| COUNTS 的 arXiv 编号 | ✅ **= 2504.10158**（CVPR 2025） |
| "Counting to Four is still a Chore for VLMs" | ✅ **完全真实**：**CVPRW 2026**（MMFM5 workshop），arXiv 2604.10039，作者 Lê Đình Anh Duy |
| 流传的 "GPT-4o Vision 检测精度仅 0.02" | ⚠️ **疑似失真转述，不要引用**。溯源到 AIMultiple 商业博客及其中文转载；页面因 WebFetch 被拦截无法打开。**旁证**：arXiv 2510.11302 给的是"**YOLO 与 GPT-4 相差 19.9 个百分点**"，与 0.02 **完全不同量级**。该数字很可能是某个 precision 取值或被中文转载误读 |
| MCP 规范版本 2025-11-25 | ✅ 正式版（官方 changelog + 合并 PR #1887） |
| MCP 规范版本 2026-07-28 | ✅ **确实已发布**（官方 release tag 存在），是"无状态核心"大改版 |

### 7.1 第二轮：18 条联网逐条核实（2026-10-15）

**结论：18 条全部真实存在；17 条与文献库一致；1 条有实质出入（已修正）。**

手段：`tools/fetch.py` 直连 + **OpenAlex API** 查会议录归属，与 IEEE DOI 交叉验证。

**有出入 / 需要记住的**：

| 项 | 修正 |
|---|---|
| **IEEE 11402578** | 会议与年份查明：**2025 IEEE International Conference on Big Data (BigData)**，DOI `10.1109/bigdata66926.2025.11402578`。作者**三人**，文献库漏了第三作者 **Mohammad Masum**。⚠️ IEEE 与 Semantic Scholar 反爬（返回空响应），**摘要读不到 → "核心结论"仍未核验** |
| **M3-Bench** | 正确写法 **M³-Bench**（M-cubed）。"首个 MCP 多模态工具基准"是**论文自我声明**，无法独立验证 |
| **COUNTS / POPE** | 正式题名要用全称：**M**ultimodal **L**arge **L**anguage **M**odels（非 "MLLMs"）、**L**arge **V**ision-**L**anguage **M**odels（非 "LVLMs"）。缩写不算错，但严格引用要写全 |
| **Counting to Four** | CVF 正式页：**CVPRW 2026 MMFM5 workshop, pp. 7377-7385**；署名顺序 **Duy Le Dinh Anh, Patrick Irawan, Tuan Van Vo**（arXiv 页作 "Patrick **Amadeus** Irawan"，引用时需统一） |
| **CC-OCR** | 编号补上：**arXiv 2412.02210**，DOI `10.1109/iccv51701.2025.02019`。⚠️ **已有 V2**（arXiv 2605.03903, 2026）—— 引用时别混淆 |

**方法学提示（重要）**：

- **多条 arXiv 页本身不标会议归属**（Comments 只写 "Technical Report" 之类）——会议信息必须另行核实才能写进报告。
- 👉 **报告里的参考文献建议直接引会议正式页**（CVF Open Access / 会议录 DOI），而不是 arXiv 页。
- **可抓**：arXiv、CVF Open Access、**OpenAlex API**、modelcontextprotocol.io、GitHub
- **抓不到（反爬，不代表条目有问题）**：IEEE Xplore、Semantic Scholar 网页；Semantic Scholar API 持续 429；DBLP 因本机 SSL 证书链问题失败

### 7.2 第三轮：每路线「最有用 + 较新」各一篇，逐篇开原文（2026-10-18）

**规则（所有者定）**：每条路线一篇最有用、一篇较新；较新须 2025 年及以后、最好 2026。
2023 年的文章除非确属奠基作，否则不选。两篇若重合则只留一篇。

| 路线 | 角色 | 论文 | 已核到什么 | 不能写什么 |
|---|---|---|---|---|
| **A** | 最有用 | **MMVP / Eyes Wide Shut?**（arXiv 2401.06209） | CLIP-blind pairs、九类视觉模式、GPT-4V 出错且编造解释、MoF 补救 | arXiv 页未标 CVPR 2024，会议归属需另核 |
| **A** | 较新 | **Counting to Four is still a Chore for VLMs**（CVPRW 2026，arXiv 2604.10039） | 计数证据在语言层被削弱、模型转向文本先验；失败兼有感知与语言阶段两因 | 不要把结论简化成"纯感知问题" |
| **B** | 最有用 | **HuggingGPT**（arXiv 2303.17580） | 四阶段流水线、LLM-as-controller | arXiv 页未标 NeurIPS 2023；摘要无量化结果 |
| **B** | 较新 | **VipAct**（AAAI 2026，arXiv 2410.16400） | orchestrator + 多专家 agent + 视觉专家；Comments 确认 AAAI 2026 | "视觉提示/标注区域"这一细节不在摘要，引用前须读正文 |
| **C** | 最有用 | **Set-of-Mark**（arXiv 2310.11441） | 分割出候选 + 编号 + GPT-4V 指代；零样本超过 RefCOCOg 全量微调 SOTA（2026-10-15 已核） | —— |
| **C** | 较新 | **Graph-of-Mark**（AAAI 2026，arXiv 2603.06663） | 场景图叠加、最多 +11 pp；正式版页码 30726-30734 | 只测了 3 个开源 MLM、4 个数据集，别写成通用结论 |

**关于 PerceptionBench 榜单**：模型名单已过期（不含 2026 下半年的新模型）。
**只引用它的构造方法与结构性结论**（16 个前沿模型无一达到 60%），
**不引用具体排名**，也不把它当"当前 SOTA"。

---

## 8. 证据归属汇总（报告可直接取用）

- **支持路线 A**：Kosmos-2、CogVLM（原生 grounding）、Unlocking Object Detection in VLMs（免训练类别提示）、RAR（长尾语义优势）。
  → A 在**开放词表冷启动、语义理解、零样本迁移**上有优势，**但强度中等、常需辅助**。
- **支持路线 B（最强、最多）**：Visual ChatGPT / HuggingGPT / ViperGPT / MM-REACT（范式）；VipAct / VTOS / AdaReasoner / CodeDance（2025–2026 系统化）；M3-Bench（MCP 正式基准）；**全部六类短板论文**；COUNTS；IEEE 11402578；Hidden Economics + Springer YOLO11（成本）。
  → **精确、量化、高吞吐、成本敏感任务，B 压倒性占优。**
- **支持路线 C**：SoM、SoM-LLaVA、Ferret、Shikra、LLaVA-Grounding、Grounded-SAM、**Graph-of-Mark (AAAI 2026)**、Seg-Agent、SpatialRGPT。
  → **C 是"既要专精定位、又要语义推理"场景的最优折中。**

---

### 8.1 ⭐ 一手观察：**本项目自己的运行环境就是路线 B 的工业级实例**

> ⚠️ **这是第一手观察（作者亲历），不是文献证据**。放进报告时应当作"讨论 / 案例"，不能当文献论据用。

写这份报告时，作者本机正在用一个"**模型当大脑 + 外部工具 + 模型做编排**"的架构跑一个真实的编程助手会话：
模型 = **DeepSeek-V4.1-Flash**（即本项目主模型 v41f），工具 = 读写文件 / 执行命令 / 检索，
编排完全由模型侧发起（function calling）。换句话说——**路线 B 不是纸上方案，它此刻正在跑，跑的就是我们要测的那个模型。**

**从中观察到的、文献里读不到的失败模式**：

1. **工具一失效，agent 必须绕行或停摆。** 实测中一个网页抓取工具（WebFetch）因它自身的安全校验服务不可达而全线失效，而网络本身是通的。agent 的应对是：**绕开该工具，用 shell 直接发 HTTP 请求**。→ 说明工具化架构的鲁棒性瓶颈往往**不在模型，而在工具链**。
2. **"能调工具"和"API 暴露了工具参数"是两件事。** 同一个模型可能在 A 接口上被成功用于工具调用，却在 B 接口上需要另行确认参数支持——**评估时要分清"模型能力"与"API 表面"**。
3. **官方在文档里直接列出 Agent Integrations（Claude Code / Codex / OpenCode / DeepSeek Harness）** —— 这是"官方认定该模型可做工具调用大脑"的**直接背书**，比基准分数更有说服力。

**对报告的用处**：作为"路线 B 已在工业界实际部署"的**存在性证据**，并支撑一个论点——**评估一条路线不能只看单次问答的准确率，还要看工具链的失效概率与绕行成本**。这也反过来支持我们在指标里加"失败率"（见 `12-experiment-design.md` 第 6 节）。

---

## 9. 仍不确定 / 未找到（诚实清单）

1. **IEEE 11402578 的确切会议名与年份** —— 未找到（试了多个关键词与 DOI 检索）
2. **"GPT-4o 检测精度 0.02"的确切含义与原始数值** —— 无法确认（WebFetch 被拦截）。**建议不引用**
3. **Hidden Economics 的完整成本表数值** —— 只看到摘要级片段（22.7 pp / 19.9 pp），未逐行核对
4. **M3-Bench 的具体评测排名** —— 只确认"首个 MCP 下多模态工具使用基准"，未拿到数字
5. **开源 MCP CV server 的版本/commit** —— 引用时需自行补 commit hash 与访问日期
6. **是否有权威综述** —— 本次未找到"vision tool orchestration survey"，只有零散系统论文。若需要可再扩大检索
7. **SoM-LLaVA 作者全名** —— 未确认（只有 GitHub 账号 `zzxslp`）
8. ⚠️ 本库约 45 条，**已联网核实 19 条**（set-of-Mark + §7.1 的 18 条）；**其余仍未核对**，引用前必须自行核实。
   📌 **核对手段**：本机 WebFetch 工具坏了，但**网络是通的**——`python tools/fetch.py <url>`；**查会议归属用 OpenAlex API**（本次核实最有用的发现）。

---

## 10. 参考文献列表（可直接进报告，**引用前逐条核对**）

**路线 B · 工具编排**
1. Visual ChatGPT — arXiv:2303.04671 — https://huggingface.co/papers/2303.04671
2. HuggingGPT — NeurIPS 2023, arXiv:2303.17580 — https://ar5iv.labs.arxiv.org/html/2303.17580
3. MM-REACT — arXiv:2303.11381 — https://huggingface.co/papers/2303.11381
4. ViperGPT — ICCV 2023, arXiv:2303.08128 — https://ar5iv.labs.arxiv.org/html/2303.08128
5. IdealGPT — EMNLP 2023 Findings, arXiv:2305.14985
6. VipAct — AAAI 2026, arXiv:2410.16400 — https://ojs.aaai.org/index.php/AAAI/article/view/40976
7. VTOS — EMNLP 2026, arXiv:2606.20728 — https://huggingface.co/papers/2606.20728
8. AdaReasoner — ICLR 2026, arXiv:2601.18631
9. CodeDance — CVPR 2026
10. Thinking with Programming Vision — CVPR 2026, arXiv:2512.03746
11. DART — EACL 2026 — https://aclanthology.org/2026.eacl-long.253/

**MCP**
12. M3-Bench — arXiv:2511.17729 — https://huggingface.co/papers/2511.17729
13. Model Context Protocol, Specification 2025-11-25
14. Model Context Protocol, Specification 2026-07-28

**路线 C · 混合**
15. SoM-LLaVA — COLM 2024, arXiv:2404.16375
16. Ferret — ICLR 2024, arXiv:2310.07704 ／ Ferret-v2 arXiv:2404.07973
17. Shikra — arXiv:2306.15195
18. CogVLM — NeurIPS 2024, arXiv:2311.03079
19. Kosmos-2 — ICLR 2024, arXiv:2306.14824
20. LLaVA-Grounding — ECCV 2024, arXiv:2312.02949
21. Grounded SAM — arXiv:2401.14159
22. Graph-of-Mark — AAAI 2026, arXiv:2603.06663
23. Seg-Agent — arXiv:2605.12953
24. SpatialRGPT — NeurIPS 2024, arXiv:2406.01584

**MLLM 短板**
25. CountQA — arXiv:2508.06585
26. Visual Enumeration (PLoS ONE 2025) — DOI 10.1371/journal.pone.0331566
27. Counting to Four is still a Chore for VLMs — CVPRW 2026, arXiv:2604.10039
28. The Last Visible Pixel — arXiv:2606.07861
29. MMVP / Eyes Wide Shut? — CVPR 2024, arXiv:2401.06209
30. MLLM-CompBench — NeurIPS 2024 D&B
31. CC-OCR — ICCV 2025
32. MME-VideoOCR — NeurIPS 2025, arXiv:2505.21333
33. POPE — EMNLP 2023, arXiv:2305.10355
34. HallusionBench — CVPR 2024, arXiv:2310.14566
35. MIRAGE — arXiv:2505.24238

**成本 / 调度**
36. Hidden Economics — arXiv:2510.11302
37. Cloud AI services vs YOLO11 — J. Cloud Computing 2025, DOI 10.1186/s13677-025-00835-9
38. VL-RouterBench — CVPR 2026, arXiv:2512.23562
39. A Picture is Worth a Thousand Tokens — ICML 2026

**开放词表 / 长尾**
40. Do MLLMs Compete… — IEEE Xplore 11402578
41. RAR — arXiv:2403.13805
42. Unlocking Object Detection in VLMs — IEEE Access 2026, DOI 10.1109/ACCESS.2026.3675517
43. WeDetect — CVPR 2026
44. OVEarth-Bench — arXiv:2607.27278
45. COUNTS — CVPR 2025, arXiv:2504.10158
