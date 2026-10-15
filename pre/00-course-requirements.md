# 00 · 课程要求（从投屏截图提取）

> 来源：`requirement/img1.jpg`、`requirement/img2.jpg`（课程投屏照片，**不可再生，勿删**）
> 提取日期：2026-10-15 ｜ 提取方式：多模态读图
> ⚠️ 以下为**读图所得**，个别字词可能有误认，交作业前请与原件核对一遍。

---

## 1. 原文要求（Course Project Information，img1）

> Choose a topic that you are interested in, such as object detection or face recognition, read a relevant paper, write a report, and give a talk.

**Report（~1000 words）**
- **Due date：Thursday (Week 9), 29-Oct-2026**
- **Each student** should complete the report **independently**.
- The report should consist of **background, methodology, results and conclusion**.
- Sent TA（E-mail: **（TA 邮箱见本地原件）**）your report **along with the presentation slides**.

## 2. 课程安排（Course Schedule，img2）

- **Weeks 4 & 6**：Introduction to AI ／ Introduction to Computer Vision ／ Introduction to Pattern Recognition
- **Weeks 7-9**：**Talks**（each group includes **2-5 students**）
- **29 Oct 2026**：Submit reports

## 3. 关键推论

| 推论 | 依据 | 影响 |
|---|---|---|
| 必须**读一篇相关论文** | img1 原文 "read a relevant paper" | 报告需要**锚点论文**，纯工程自述不合格 |
| 报告**每人独立**，talk 才分组 | "Each student … independently" vs "each group includes 2-5 students" | 不能靠组员的数据交报告；但小组可以共用实验平台 |
| 结构**固定四节** | background / methodology / results / conclusion | 别自由发挥结构 |
| slides 要**一起交** | "along with the presentation slides" | slides 也是交付物，不是讲讲就算 |
| **本周可能就要讲** | 今天 2026-10-15；若 10-29 是 Week 9 周四，则 Week 1 ≈ 8/31，**Week 7 = 10/12–10/18** | **talk 可能就在这几天，优先确认日期** |

## 4. 我们的选题（已定稿，2026-10-15）

> **在模型应用中做图像识别：把视觉任务交给多模态大模型本体 / 外包给专用 CV 模型 + LLM 编排 / 两者混合——三条路线在真实场景下的取舍**

**已决定走三条路线**（含混合），而不是简单比"谁更好"；**报告语言：中文**（四节标题保留英文原样）。理由与证据见 [10-research-notes.md](10-research-notes.md) 第 4 节。

**特色**：本项目自己就是实验平台（QQ 群聊机器人），report 的 **results** 用自己的实测数据，而不是只引用别人的论文数字。

## 5. ⚠️ 选题的一个概念风险（必须在定稿前想清楚）

题目里"MCP"（Model Context Protocol）是**协议规范**，"多模态模型"是**模型**——**两者不是同一层级的东西，直接对比会犯范畴错误**，答辩时容易被问倒。

**建议的表述**（对照的是"做法"而不是"名词"）：
- 路线 A：**把视觉任务交给多模态模型本体**（模型直接看图输出结论）
- 路线 B：**把视觉任务外包给专用 CV 模型，由 LLM 做编排**（工具调用 / MCP 只是实现协议之一）

这样两条路线在同一层级上，比较才成立。

**已于 2026-10-15 定案**：采用**三条路线**（A 纯多模态模型 / B 外包专用 CV + 编排 / C 混合），并按"**什么任务该走哪条路**"组织报告主线。**仍需跟老师或 TA 当面确认一句措辞**（答辩时的第一个问题很可能就是这个）。

## 6. 交付物清单

| 交付物 | 要求 | 位置 |
|---|---|---|
| 报告 | ~1000 词，四节结构，**独立完成** | `pre/report/` |
| 演示稿 | slides，与报告一起交 | `pre/slides/` |
| 实验数据 | 两条路线的对照数据（自产） | `pre/experiments/` |
| 提交 | 邮件发 TA `（TA 邮箱见本地原件）` | —— |

## 7. 倒排时间线

| 日期 | 该做什么 |
|---|---|
| 10-15（今天） | 地基落盘；**确认 talk 日期与小组分工** |
| 10-16 ~ 10-18 | 内核最小闭环（M1） |
| 10-18 ~ 10-20 | 双路线看图最小可用（M2） |
| 10-20 ~ 10-22 | 跑对照实验、出数据与图（M3） |
| 10-22 ~ 10-26 | 报告初稿 + slides（M4） |
| 10-27 ~ 10-28 | 定稿、核对原件要求、发邮件 |
| **10-29** | **截止** |

## 8. 缺口 / 已定项

- [x] **选题措辞** —— 已定：**三条路线**（A 纯 MLLM / B 外包专用 CV + 编排 / C 混合）
- [x] **报告语言** —— 已定：**中文**（四节标题保留英文原样）
- [x] **小组分工** —— 已定：不考虑分工，按"全组都做完"对待
- [x] **锚点论文** —— 文献库已成：[11-literature.md](11-literature.md)（**约 45 条**，含混合路线与工具编排方向），并已提炼出报告核心论点
- [ ] **逐条核对原文** —— ⚠️ 文献库**全部条目都只看了搜索摘要**，引用前必须自行打开原文（本机 WebFetch 被拦截）
- [ ] **talk 日期**：**未确认**（今天已是 Week 7，可能很近）← 唯一剩下的时间风险
