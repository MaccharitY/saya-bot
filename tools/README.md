# tools/ —— 小工具集

放**与机器人本体无关、但项目里会反复用到**的独立脚本。

## 约定

1. **每个工具一个文件，零第三方依赖**（只用 Python 标准库）。
   理由：本机没装任何第三方包，零依赖意味着 clone 下来就能跑。
2. **都能当命令行用**，并且**函数可被 import 复用**。
3. 新增工具时**在下面加一行说明**——写清楚"为什么需要它"和"怎么用"。

## 清单

### `start_saya.bat` —— 一键启动（Windows 双击）

你平时就用这个。它会转去调 WSL 里的 `start_saya.sh`。

### `start_saya.sh` —— 一键启动（WSL 侧）

```bash
bash tools/start_saya.sh          # 起 NapCat → 自检 → 起机器人
bash tools/start_saya.sh --check  # 只自检
```

顺序是刻意的：**先确保 QQ 侧活着、再自检、最后才启动** ——
省得开了半天才发现配置有问题。自检不过就**不会启动**。

QQ 号从 `project_main/.env` 的 `SAYA_SELF_ID` 读，不写死在脚本里。

### `fetch_arcaea_corpus.py` —— 把剧情原件切成可检索的语料库

**为什么需要**：机器人查 Arcaea 设定时，得能在几十万字剧情里**按词找到那一段**。
原始素材是几个大文本（项目所有者提供的 `stories/`），得切成"一章一文件"才好检索。

```bash
python tools/fetch_arcaea_corpus.py                       # 从 stories/ 切到 persona/corpus/
python tools/fetch_arcaea_corpus.py --outdir <目录>        # 换个输出位置
python tools/fetch_arcaea_corpus.py --only 18             # 只处理某些章节
python tools/fetch_arcaea_corpus.py --force               # 覆盖已有文件
```

产出 `persona/corpus/`：`NN-ch<标记>.txt` ×179 ＋ `index.json`（给程序查）＋ `INDEX.md`（给人看）。
脚本会顺手做三件事：跳过 B 站文章开头重复的「目录」标记群、剥掉页脚杂质
（`cvXXXXXXX` / 分享 / 标签串）、给关键章节写摘要。

⚠️ **`stories/` 是只读的** —— 那是所有者提供的原件，脚本只读不改。
⚠️ 名字里的 "fetch" 是历史遗留：它**现在不联网**，只做本地切分
（原先要抓的 `wiki.arcaea.cn` 已因反爬放弃，见 `docs/01-architecture.md` ADR-012）。

### `notes.py` —— 查看与删除长期记忆

**为什么需要**：长期记忆（主人/管理员让机器人记住的事）是唯一一份"自己攒下来、
又不会被自动清理"的数据。没有查看和删除的口子，它就变成只能进不能出的黑箱。

```bash
python tools/notes.py list          # 全部生效中的
python tools/notes.py list --all    # 连已停用的也看
python tools/notes.py search 猫      # 按关键词找
python tools/notes.py forget 3      # 停用第 3 条（只失效，不删行）
python tools/notes.py --db <路径> …  # 默认读 project_main/data/notebook.db
```

`forget` 只把记录标成失效、不再注入提示词 —— 留行是为了出问题时能回头看。

### `fetch.py` —— 抓网页转文本

**为什么需要**：本机的 WebFetch 工具坏了——它要联网校验"域名是否可安全抓取"，那一步失败，
导致任何网页都读不了。**但网络本身是通的**（`curl` 与 `urllib` 直连均返回 200），
所以用这个脚本绕过 WebFetch。

```bash
python tools/fetch.py https://arxiv.org/abs/2310.11441
python tools/fetch.py https://example.com --max-chars 5000
python tools/fetch.py https://example.com --links        # 列链接，用于导航
python tools/fetch.py https://example.com --raw --out page.html
```

能力：跟随重定向、UA 伪装、编码自动探测（含 `gb18030` 兜底，中文站不声明编码也能读）、
HTML 转纯文本、链接提取。遇到 PDF 会明确报错而不是输出乱码。

可 import：`from tools.fetch import fetch, html_to_text, extract_links`

### 抓取经验（2026-10-15 实测）

**能抓**：arXiv、CVF Open Access（`openaccess.thecvf.com`）、**OpenAlex API**、`modelcontextprotocol.io`、
GitHub、DeepSeek 官方文档（`api-docs.deepseek.com`）、
**B 站图文/专栏（`bilibili.com/opus/<id>`）** —— 见下

> 💡 **B 站图文是服务端渲染的，正文直接在 HTML 里，`fetch.py` 能完整抓到**（实测 2026-10-16：
> 单篇 5～8 万字，中英对照带注释，一字不少）。
> 抓下来的文本会带页面外壳 —— `目录`、`分享至`、`微信扫一扫分享`、`投诉或建议`、
> `给UP主投上硬币`、末尾的标签行与 `cvXXXXXXX`。用之前要剥掉。
> ⚠️ 只适合**少量、按需**地读几篇公开文章；不要拿去批量爬。
> 若遇到验证码或登录墙就停下 —— 那就是不该继续的信号。

**抓不到**（反爬或本机问题，**不代表目标条目有问题**）：IEEE Xplore（返回空响应）、
Semantic Scholar 网页、Semantic Scholar API（持续 429）、DBLP（本机 SSL 证书链问题）

> ⚠️ **`wiki.arcaea.cn` 是主动放弃的，不是抓不到**：它挂在 **Anubis 反爬**后面
> （PoW 挑战页 "Making sure you're not a bot!"）。绕过它属于对抗性手段，
> 项目红线不允许。详见 `docs/01-architecture.md` 的 ADR-012。
> 它同时也是"随机 SSL 断连"的真凶 —— 连上的那几次拿到的也是挑战页，不是内容。

> 💡 **查论文的会议归属与作者**：**arXiv 页本身常常不写会议名**（Comments 栏可能只写 "Technical Report"）。
> 用 OpenAlex 更靠得住：
>
> ```bash
> python tools/fetch.py "https://api.openalex.org/works/doi:10.1109/bigdata66926.2025.11402578" --max-chars 0
> ```
>
> ⚠️ **OpenAlex 返回的是 JSON，必须加 `--max-chars 0`**，否则 JSON 被截断后无法解析。
> 也可按标题搜索：`https://api.openalex.org/works?search=<标题>`
>
> 输出里 `title` / `publication_year` / `primary_location.source.display_name`（发表处）/ `authorships`（作者）
> 就是写参考文献要的字段。
