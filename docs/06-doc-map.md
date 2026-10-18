# 06 · 文档地图（哪份文档管什么）

> 2026-10-18。项目文档是**按时间追加**的，越写越像补丁。这一页给每份文档定职责，
> 并说明**哪个数字以哪里为准**。新增文档必须在这里登记，否则不算数。

---

## 1. 权威关系（先看这张）

| 想知道 | 以哪里为准 | 不是哪里 |
|---|---|---|
| **实验结论与最终数字** | `pre/01-overview.md` | 对话、slides 草稿 |
| **原始数据** | `pre/experiments/analysis-*.md`、`results-*.jsonl` | 报告正文里的转述 |
| **预注册假设与分析计划** | `pre/13-experimental-protocol.md` | 事后写的报告 |
| **每条决策的理由** | `docs/01-architecture.md` 的 ADR | 过程流水里的口语 |
| **当前进度 / 下一步** | `docs/02-roadmap.md`、`docs/05-progress-brief.md` §0 | 旧章节 |
| **每轮为什么改** | `pre/14-experiment-iteration-log.md` | 内部报告 |
| **文献是否核对过** | `pre/11-literature.md` §7 | 文献库正文的"已定位" |
| **课程要求** | `pre/00-course-requirements.md`（原件在 `pre/requirement/`） | 任何转述 |

⚠️ **数字冲突时**：analysis 文件 > 总览 > 内部报告 > 提交稿。
提交稿是给人读的压缩版，不承担"精确复现"的职责。

## 2. 工程文档（`docs/`）

| 文档 | 职责 | 状态 |
|---|---|---|
| `../CLAUDE.md` | 项目入口、硬约束、目录地图 | 现行 |
| `00-requirements-baseline.md` | FR/NFR、做什么/不做什么 | 现行 |
| `01-architecture.md` | 架构与 **ADR-001~049** | 现行；决策唯一权威 |
| `02-roadmap.md` | 里程碑与任务状态 | 现行；进度唯一权威 |
| `03-model-and-toolchain.md` | 模型、计价、工具链 | 现行 |
| `04-napcat-deployment.md` | QQ 侧部署 | 现行 |
| `05-progress-brief.md` | 交接页；**§0 是状态**，其余是历史记录 | §0 现行，其后章节为历史 |
| `06-doc-map.md` | 本页 | 现行 |
| `style.md` / `contract.md` | 界面文风规则与对照 | 现行 |
| `../ACCEPTANCE.md` | 验收清单 | 现行 |
| `releases/` | 旧版发布说明 | 历史 |

## 3. 研究与交付物（`pre/`）

| 文档 | 职责 | 状态 |
|---|---|---|
| `00-course-requirements.md` | 课程要求提取 | 现行 |
| **`01-overview.md`** | **实验总览：问题、矩阵、结果、结论、索引** | **现行；实验唯一入口** |
| `10-research-notes.md` | 选题调研 v1 | **历史**；数字以 `11` 为准 |
| `11-literature.md` | 文献库与核对状态 | 现行；引用前看 §7 |
| `12-experiment-design.md` | 三条路线的操作定义、附属选型、指标口径 | 现行；**§5 测试集部分是历史**（coco128） |
| `13-experimental-protocol.md` | 预注册假设、效度威胁、分析计划 | 现行；事后追加见文末 |
| `14-experiment-iteration-log.md` | 逐轮过程流水（为什么改、结果） | 历史；不改写 |
| `15-chat-workload.md` | 真实工况的设计 | 已完成；结果在 `experiments/chat-real/analysis.md` |
| `16-mechanism-and-value.md` | 机制解释与产品建议 | 现行 |
| `report/report-draft.md` | 提交版报告（~2230 字） | 草稿；待老师口径后裁剪 |
| `report/internal-report.md` | 长版内部报告（写 slides 用） | 草稿；与总览重复处以总览为准 |
| `experiments/` | 原始数据、判官结果、分析 | **唯一数据源** |
| `slides/` | 演示稿 | 未开始 |

## 4. 按角色的阅读路径

**项目所有者 / 答辩前**：
`01-overview` → `16-mechanism-and-value` → `report-draft` → `report/internal-report`。

**未来接手会话**：
`CLAUDE.md` → `docs/05` §0 → `docs/02` → `01-overview` → `14`（需要时）。

**助教 / 外部读者**：
`report-draft` → `01-overview` → `11-literature` §7。

## 5. 维护规则

1. **新文档先登记**：没有出现在本页的文档，不算权威来源。
2. **过期不删，标注状态**：历史文档保留，但要在本页标"历史"。
3. **数字只在一个地方更新**：实验数字先改 analysis 文件与 `01-overview`，
   再同步报告；不要只在报告里改。
4. **ADR 只增不改**：结论变化时新增 ADR，不覆盖旧决策。
