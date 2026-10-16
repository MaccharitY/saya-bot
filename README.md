# saya_bot

一个在 QQ 群聊中运行的对话机器人。它以**个人账号**身份接入（走 NapCat / OneBot v11，不依赖官方机器人开放平台），
对话能力由 **DeepSeek** 提供。机器人本体**零第三方依赖**，只用 Python 标准库。

## 概述

| 能力 | 说明 |
|---|---|
| **触发判定** | 每个群单独配置策略：`silent` / `mention` / `keyword` / `random` / `always`。非 `silent` 的群会记录全部消息（供记忆与统计），但是否开口由策略决定 |
| **对话** | 以固定人格回应，带上下文与记忆；支持中文、日文、英文，跟随对方使用的语言 |
| **多模态** | 识别群里的图片。三条可选路线：主模型直接看图 / 外包给专用 CV 模型出文字 / CV 画编号框后交主模型推理 |
| **记忆** | SQLite 本地存储，分三层：会话消息、话题、成员清单。带容量上限与自动裁剪，注入时只取相关片段 |
| **关系记忆** | 主人与管理员说过的、需要长期记住的事，单独存一份不参与裁剪 |
| **防注入** | 结构性隔离为主：群友文本一律包进边界标记后才进提示词；私聊白名单关掉整块攻击面；另加输入检测与输出过滤 |
| **埋点** | 每条消息的处理过程写入 JSONL：触发原因、token、延迟、成本、注入命中情况 |

## 使用

### 环境要求

- **Python 3.11+**。机器人本体不需要安装任何第三方包。
- 可选 **NapCat**：接入真实 QQ 时才需要。
- 可选**第二套解释器**：只有视觉的路线 B / C 需要（CV 进程有第三方依赖，与本体隔离）。

### 配置

```bash
cp .env.example .env
```

`.env.example` 里每一项都有注释说明。常用项：

| 键 | 作用 |
|---|---|
| `SAYA_LLM_API_KEY` | 模型密钥。**只放在 `.env`，永不提交** |
| `SAYA_ADAPTER` | `mock`（本地假事件，开发用）或 `onebot11_http`（真实 QQ） |
| `SAYA_OWNER_ID` / `SAYA_ADMIN_IDS` / `SAYA_FRIEND_ALLOWLIST` | 允许私聊机器人的人。**不配则任何人的私聊都被忽略** |
| `SAYA_GROUP_POLICIES` | 形如 `群号:策略`，逗号分隔；未列出的群走 `SAYA_GROUP_DEFAULT_POLICY` |
| `SAYA_VISION_ROUTE` | `direct` / `delegated` / `hybrid` |

### 运行

```bash
cd project_main
python run.py --check     # 先自检：配置、模型连通性、端口占用
python run.py             # 正式启动
```

`--check` 不连 QQ、不发消息，会**一次性列出所有问题**而不是遇到第一个就退出。

接真实 QQ 时，一键启动脚本会按「确认 QQ 侧存活 → 自检 → 启动」的顺序来：

```bash
tools/start_saya.bat      # Windows 双击
bash tools/start_saya.sh  # WSL
```

### 测试

```bash
cd project_main && python -m unittest discover -s tests -v
```

全部测试只用标准库，clone 下来即可运行，不需要建虚拟环境。

## 实现

### 分层

```
adapter（接入） → core（内核） → services（能力） → obs（埋点）
```

依赖单向流动，**换 QQ 后端只改 adapter**。适配器接口是同步的：接入层收事件，
内核逐条处理，任一环节出错只丢当前这条消息，主循环继续。

| 目录 | 职责 |
|---|---|
| `project_main/src/saya/adapter/` | OneBot v11 事件解析、HTTP 接入、Mock 回放 |
| `project_main/src/saya/core/` | 触发判定、提示词组装、防注入护栏、记忆、流水线 |
| `project_main/src/saya/services/` | 模型通道、视觉、DNS 缓存、MCP 客户端 |
| `project_main/src/saya/obs/` | JSONL 埋点 |
| `project_main/cv_mcp/` | 专用 CV 模型的 MCP server（独立进程，依赖隔离在这里） |
| `tools/` | 与机器人本体无关的独立脚本（启动、抓网页等） |
| `docs/` | 需求基线、架构与决策记录、路线图 |

### 一条消息的处理过程

```
adapter 收到事件
  → 触发判定（不该观察就到此为止）
  → 写记忆
  → 组装上下文（人格提示 + 记忆片段 + 本条消息，图片按配置的路线处理）
  → 护栏（群友内容包裹成不可信区域）
  → 生成
  → 输出过滤
  → 发送
  → 埋点
```

### 设计约束

- **零第三方依赖**是刻意的：clone 下来就能跑，测试不需要环境准备。CV 的依赖被隔离在独立进程里。
- **一切用户产生的文本都是不可信的**，包括群名片、记忆内容和历史消息。它们进提示词前必须经过
  `guard.wrap_untrusted` / `wrap_history`，否则就是一条存储型注入通道。唯一可信的是代码生成的结构性标签。
- **密钥不落盘**：只从环境变量或本地 `.env` 读取，代码、文档、提交历史中都不出现。
- **不做任何绕过 QQ 风控或规避封号的手段**。个人账号接入非官方协议本身有封号风险，知情接受，但不做对抗。

### 决策记录

架构决策写在 [docs/01-architecture.md](docs/01-architecture.md) 的 ADR 一节，包含每项选择的理由与被取代的关系。
文档索引见 [CLAUDE.md](CLAUDE.md)。
