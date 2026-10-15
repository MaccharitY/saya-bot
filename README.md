# saya_bot

在 QQ 群聊里以**个人账号身份**（非官方机器人开放平台）运行的对话机器人，以 **DeepSeek** 为核心模型。
同时是课程 presentation 的实验平台：用它实测「多模态大模型直接看图」与「MCP / 工具调用专用 CV 模型」两条路线的优劣。

## 现在到哪一步了

**M0 地基已完成，没有可用机器人。** 接入层骨架 + 13 个单元测试通过。下一步见 [docs/02-roadmap.md](docs/02-roadmap.md)。

## 快速开始

目前**零第三方依赖**（只用 Python 标准库），所以直接就能跑测试：

```
cd project_main && python -m unittest discover -s tests -v
```

配置从 `.env.example` 复制成 `.env` 再填；**`.env` 已被 gitignore，密钥永远不进仓库**。

## 接手先读（按顺序）

1. [CLAUDE.md](CLAUDE.md) —— 项目入口与落盘规范
2. [docs/00-requirements-baseline.md](docs/00-requirements-baseline.md) —— 做什么、不做什么、验收标准
3. [docs/01-architecture.md](docs/01-architecture.md) —— 分层、模块、关键决策（**草案，待评审**）
4. [docs/02-roadmap.md](docs/02-roadmap.md) —— 里程碑与当前任务
5. [pre/00-course-requirements.md](pre/00-course-requirements.md) —— 课程硬性要求（截止 2026-10-29）

## 目录

| 目录 | 作用 |
|---|---|
| `project_main/` | 机器人本体（代码、测试、配置模板） |
| `pre/` | 课程 presentation 的交付物：报告、slides、实验数据 |
| `docs/` | 需求基线、架构决策、路线图 |
| `pre/requirement/` | 原始课程要求截图（不可再生，保留） |

## 两条硬约束（别忘）

- **密钥永不落盘。** 一切 API Key 只走环境变量 / 本地 `.env`（已 gitignore）。代码、文档、提交历史里都不许出现。
- **不做任何绕过 QQ 风控或规避封号的手段。** 个人账号接入非官方协议本身就有封号风险，接受这个风险，但不做对抗。

## 状态

- 仓库：本地 git（`main`），远端 GitHub 待建
- 语言：Python 3.11
- 后端方案：Mock adapter 先行，真后端（NapCat / OneBot v11）后续接入
