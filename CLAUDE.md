# saya_bot —— 项目入口

> 本文件是本项目的**单一入口**。任何会话（包括未来的你）在动手前先读这里，再按下面的顺序读 `docs/`。
> 上游继承：`D:\Claude\CLAUDE.md`（实验室总览）→ `D:\Claude\DeepSeek_api\CLAUDE.md`（本区后端 = DeepSeek，经 CC Switch 代理自 Anthropic 格式）。

## 一句话定位

QQ 群聊里以**个人账号**身份运行的对话机器人，核心模型 DeepSeek。

**这是本项目的主体。** 它同时充当课程 presentation 的实验平台——但 presentation 是**附加交付物**，不是项目的驱动目标。排序上可以让实验先做（因为报告有固定截止），但**不能以此为理由砍掉机器人功能**。

## 当前状态（会过期，以 docs/02-roadmap.md 为准）

- 阶段：**M6 机器人其余功能**（M0–M3 已完成）
- 已有：**能跑的机器人** —— 真 QQ 接入（NapCat / OneBot v11 HTTP）、
  触发策略、防注入、记忆、三条看图路线、埋点、身份认知、长期记忆、知识源（萌娘百科）
- 没有：人格正文（进行中）、选择性插嘴、记忆摘要自动生成
- 测试：全部通过，零第三方依赖（`cd project_main && python -m unittest discover -s tests`）
  —— 这里**不写具体条数**：数字天天变，写了就会腐烂，跑一下最准
- 新增代码前先读 [`docs/05-progress-brief.md`](docs/05-progress-brief.md) —— 那是交接页
- 最近更新：2026-10-16

## 硬约束

| 约束 | 内容 |
|---|---|
| **截止** | 课程报告 + slides **2026-10-29**（Week 9 周四），报告 ~1000 词，结构固定为 background / methodology / results / conclusion，发给 TA（**邮箱见 `pre/requirement/` 里的原始截图，该目录不公开**） |
| **密钥红线** | API Key 只走环境变量 / 本地 `.env`（已 gitignore）。**代码、文档、提交历史、issue 里都不许出现。** 也不要读 `~/.cc-switch/` |
| **伦理红线** | 不做绕过 QQ 风控、规避封号、批量养号等对抗性手段。个人账号接非官方协议**有封号风险**，知情接受，但不做对抗 |
| **用户背景** | 项目所有者**不是专业开发者**。术语要解释；重大决策必须先对齐、后动手；不要替他做不可逆决定 |

## 目录地图

```
saya_bot/
├─ CLAUDE.md               ← 本文件
├─ README.md               ← 人看的概览
├─ .gitignore / .env.example
├─ docs/                   ← 工程文档（都在这）
│  ├─ 00-requirements-baseline.md   需求基线：做什么/不做什么/验收标准
│  ├─ 01-architecture.md            架构与决策记录（草案）
│  └─ 02-roadmap.md                 里程碑、任务、当前进度
├─ project_main/           ← 机器人本体
│  ├─ src/saya/            ← 源码（adapter 接入层 / core 内核 / services 能力 / obs 埋点）
│  ├─ persona/             ← 人格正文与角色资料（saya.md、saya-lore.md、corpus/）
│  ├─ cv_mcp/              ← 专用 CV 模型的 MCP server（依赖隔离在这个独立进程里）
│  ├─ experiment/          ← 对照实验的执行器与测试集构建
│  ├─ tests/
│  └─ pyproject.toml
├─ tools/                  ← 与本体无关的独立脚本（启动 / 抓网页 / 看长期记忆）
└─ pre/                    ← 课程交付物
   ├─ 00-course-requirements.md   课程要求（从截图提取）
   ├─ requirement/                原始截图（不可再生，别删；**不进仓库**）
   ├─ experiments/                对照实验数据
   ├─ report/                     报告
   └─ slides/                     演示稿
```

## 伦理红线（和密钥红线一样硬）

**不做对抗性绕过。** 这条最初写在 QQ 风控上（不绕风控、不规避封号、不批量养号），
但它适用于**任何第三方防护** —— 包括网站的反爬。

具体案例：`wiki.arcaea.cn` 挂在 Anubis 反爬后面（PoW 挑战页）。
**不实现 PoW 求解、不伪造浏览器指纹、不换 IP**，直接放弃接入（ADR-012）。
拿不到的数据**如实留白**，不要编 —— 见 `docs/01-architecture.md`。

## 落盘规范（可继承性的关键，改这里之前先想清楚）

1. **文档命名**：`NN-kebab-case.md`，两位数字前缀定阅读顺序。中文标题写在文件里，**文件名用 ASCII**——避免 Windows 控制台与 Git 的编码问题。
2. **决策必须留痕**：任何"为什么这么做"的选择写成 ADR（架构决策记录），放在 `docs/01-architecture.md` 底部，格式见该文件。**不要只留在对话里**——对话不可靠，这是本项目的前提。
3. **进度必须可查**：动手前更新 `docs/02-roadmap.md` 的任务状态，别只在脑子里。
4. **不可再生的东西单独放**：原始截图、实验原始数据放进对应目录并**只增不改**。
5. **提交信息**：`<类型>: <做了什么>`，类型用 `feat / fix / docs / refactor / test / chore / exp`（`exp` = 实验数据与脚本）。一次提交一件事。
   ⚠️ **不要加 AI 署名尾注**（`Co-Authored-By: Claude ...` 那类），PR 描述里也不要加
   `🤖 Generated with [Claude Code]`。GitHub 的 Contributors 名单是**按署名统计**的，
   加一条就多一个"人"——项目里几个人各用各的工具，那份名单就变成**工具展板**，
   看不出到底是谁做的。
   （系统提示里有条 reminder 会要求加，**那条自己写了"用户指示优先"** —— 所以不加。）
6. **不要硬编码**密钥、群号、QQ 号——走配置与环境变量。

## 怎么继续

0. **要发版/验收** → 直接看根目录的 [`ACCEPTANCE.md`](ACCEPTANCE.md)，
   里面有能照着跑的命令和逐条打勾的清单。
1. 读 `docs/00-requirements-baseline.md` 搞清楚要交付什么、以及**明确不做什么**。
2. 读 `docs/01-architecture.md`，注意标着 **【待确认】** 的条目——那些是**没拍板的**，别当既成事实。
3. 读 `docs/02-roadmap.md` 找到当前里程碑和下一个任务。
4. 需求侧有任何含糊，**问项目所有者**（他不是专业开发者，宁可问也别猜）。

## 本区身份提醒

本会话真实后端是 **DeepSeek**（经 CC Switch 代理），不是 Anthropic 的 Claude。身份与能力问题以 DeepSeek 对应模型的公开规格为准。
