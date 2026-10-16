# tools/ —— 小工具集

放**与机器人本体无关、但项目里会反复用到**的独立脚本。

## 约定

1. **每个工具一个文件，零第三方依赖**（只用 Python 标准库）。
   理由：本机没装任何第三方包，零依赖意味着 clone 下来就能跑。
2. **都能当命令行用**，并且**函数可被 import 复用**。
3. 新增工具时**在下面加一行说明**——写清楚"为什么需要它"和"怎么用"。

## 清单

### `start_saya.bat` —— 一键启动 / **重启**（Windows 双击）

你平时就用这个。它会转去调 WSL 里的 `start_saya.sh`，并传 `--restart`。

⚠️ **这个文件里一个非 ASCII 字符都不能有。** cmd.exe 解析含中文的 UTF-8 `.bat` 时
会**从行中间断开**，把后半句当命令去执行（实测报过
`'…双击就没用了。）' is not recognized as an internal or external command`）。
中文提示一律由 WSL 那边的脚本输出 —— 那里是 UTF-8 的正常环境。
行尾也必须是 CRLF。

### `start_saya.sh` —— 启动 / 重启（WSL 侧）

```bash
bash tools/start_saya.sh            # 确保它在跑（已在跑就提示一句，不动它）
bash tools/start_saya.sh --restart  # **重启**：停掉旧的，用现在这份代码起
bash tools/start_saya.sh --check    # 只自检（不碰 NapCat、不启动、不杀进程）
```

顺序是刻意的：**先看有没有在跑 → 再自检 → 最后才碰 QQ**。

⚠️ **「已经在跑」以前是没人管的**：脚本直接跑到自检，而自检发现端口被占、
判定失败、拒绝启动 —— 于是这个"一键启动"按钮**没法重启一个正在跑的机器人**，
还甩给用户一句"自检没过，先把它修好"。现在分三种情况处理：
无参数时提示并退出 0；`--restart` 时停掉旧的（先 `kill`，不理就 `kill -9`）；
`--check` 时只报告。

**认哪个进程是它，用端口认**（`ss -tlnpH "sport = :<端口>"`），
命令行匹配只作兜底 —— 进程名是相对的 `run.py`，光靠它认不准。

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

### `privacy_scan.py` —— 发布前的隐私自检

**为什么需要，而且为什么必须是个脚本**：检查「有没有泄露 X」的命令，本身就写着 X ——
把它写进文档或验收清单，**那份文档自己就成了泄露源**。这不是假设，是真踩过的坑：
验收清单的第一版把真群号与 TA 邮箱（用来当检查关键词）写进了公开仓库，
最后靠重写 git 历史才擦掉。

```bash
python tools/privacy_scan.py             # 当前文件 + 全历史（发布前跑这个）
python tools/privacy_scan.py --current   # 只扫当前文件（快）
python tools/privacy_scan.py --env <路径>  # 指定 .env
```

- **要查的值一律从 `.env` 读**，不写在任何会被提交的地方
- 只扫**可能被提交的文件**（`git ls-files -c -o --exclude-standard`）——
  否则 `.env`、埋点日志这些被 gitignore 的东西会制造满屏假警报，假警报多了真警报就没人看了
- **同时扫全历史**：改当前文件是不够的，git 会把你写过的每个字都带上

退出码 0 = 干净，1 = 有命中。

> ⚠️ 跑完如果报命中，**别只改当前文件** —— 见 `docs/05-progress-brief.md` 第 7 节
> 里那套「改内容 + 改提交信息 + 删备份引用 + 过期 reflog + gc」的完整流程。
> 少做一步，备份引用（`refs/original`、`refs/stash`）都还会把旧数据留在本地。

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

### `config.py` —— 改机器人的设置（群策略 / 主人 / 管理员 / 白名单）

**为什么需要**：这些设置全在 `project_main/.env` 里，而那个文件**既有密钥、
又有大段说明注释**。手工改很容易改坏 —— 多一个空格、少一个逗号，或者顺手把注释删了。
改坏的后果分两种，**两种都很难一眼看出来**：值写错了，机器人**照旧跑但用的还是旧设置**
（你以为改了）；文件写坏了，API key 一起没了。

```bash
python tools/config.py show                        # 看现在是什么样
python tools/config.py group 123456789 mention     # 设某个群的策略
python tools/config.py group 123456789 --del       # 移除某个群
python tools/config.py default silent              # 没列出的群默认怎么办
python tools/config.py owner 10001                 # 主人（只能一个）
python tools/config.py admin add 20001             # 管理员（最多 3 个）
python tools/config.py admin del 20001
python tools/config.py friend add 30001            # 白名单（最多 15 个）
python tools/config.py keyword set saya,咲弥        # keyword 模式用的词
python tools/config.py prob 0.15                   # random 模式的概率
python tools/config.py rate 3                      # 每群每分钟最多回几条
python tools/config.py path                        # 只打印 .env 的路径
```

⚠️ **改完必须重启才生效** —— 配置是**启动时读一次**的（`bash tools/start_saya.sh --restart`）。

六种群策略：`silent`（不记不回）/ `mention`（记录但不被 @ 不回，**默认**）/
`keyword`（命中词才回）/ `random`（按概率回）/ `always`（全回，会很吵）/
`selective`（**尚未实现，会降级成 mention**）。

**三个刻意的设计**：

1. **只做外科手术式替换** —— 改哪一行就只动那一行，行尾注释、其余行、
   **行尾符（LF/CRLF）全部原样保留**。写回时用 `newline=""`：
   默认的通用换行会把 LF 翻成 CRLF，于是"只改一个值"却让整个文件 diff 一片红（实测撞过）。
2. **写之前先备份**成 `.env.bak`（被 gitignore 覆盖），并在写盘时检查
   **目标文件有没有被 git 忽略** —— 它装着 API key，`--env 别的名字` 就可能
   绕开 `.gitignore` 的 `.env` / `.env.*` 规则。这不是假想：写这个工具的当天，
   拿真 `.env` 复制出来做测试，副本名不匹配忽略规则，**差点把 key 提上去**。
3. **键名一律写全**（`SAYA_OWNER_ID`），不搞"自动补 `SAYA_` 前缀"那套贴心约定 ——
   只在一侧生效的隐式约定最容易坑人。

配置校验（QQ 号、策略名、数量上限、概率范围）在写盘**之前**做，不合法就报错不动文件。
`replace_key()` 可被 import 复用。

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
