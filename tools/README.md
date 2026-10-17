# tools/ —— 小工具集

放**与机器人本体无关、但项目里会反复用到**的独立脚本。

## 约定

1. **每个工具一个文件，零第三方依赖**（只用 Python 标准库）。
   理由：本机没装任何第三方包，零依赖意味着 clone 下来就能跑。
2. **都能当命令行用**，并且**函数可被 import 复用**。
3. 新增工具时**在下面加一行说明**——写清楚"为什么需要它"和"怎么用"。

## 清单

> ⚠️ **先看这张表再往下翻。** 下面每条都有详细说明，但「这台机器上我到底能用什么」
> 应该一眼看完 —— 2026-10-17 就出过一次：**手上有 `fetch.py`，却对外说"打不开网页"**。
> （详见 `docs/01-architecture.md` 的 ADR-037。）

| 脚本 | 干什么 |
|---|---|
| `saya_bot_controller.bat`（**仓库根目录**） | 启停机器人 + 改设置，双击即用 |
| `start_saya.bat` / `start_saya.sh` | 只管把机器人拉起来（不经控制台） |
| `panel.py` | 常驻控制台 —— 启停机器人 + 改设置 + QQ 侧（NapCat）启停 |
| `config.py` | 离线改设置（机器人**没在跑**时用） |
| **`fetch.py`** | **抓网页转文本。本机 `WebFetch` 工具不通，要读网页正文就用它** |
| `notes.py` | 查 / 删长期记忆 |
| `privacy_scan.py` | 发布前隐私自检（扫可能被提交的文件 + 全历史） |
| `fetch_arcaea_corpus.py` | 切剧情语料。⚠️ **现在不联网**，名字是历史遗留 |

### `start_saya.bat` —— 一键启动 / **重启**（Windows 双击，在 `tools/` 里）

它转去调 WSL 里的 `start_saya.sh`，并传 `--restart`（双击这个按钮的意思就是
"给我一个跑着的机器人"）。

> 👉 **要启停 + 改设置，用仓库根目录的 `saya_bot_controller.bat`**（见下面那条）。
> 这个是"只管把它拉起来"的。

⚠️ **两个 `.bat` 通用的硬约束**（`project_main/tests/test_launchers.py` 钉着）：

1. **一个非 ASCII 字符都不能有。** cmd.exe 解析含中文的 UTF-8 `.bat` 时会
   **从行中间断开**，把后半句当命令去执行（实测报过
   `'…双击就没用了。）' is not recognized as an internal or external command`）。
   中文提示一律由 WSL 那边输出。行尾必须是 **CRLF**。
2. **不许有 `for /f`。** ⚠️ 这条是 2026-10-16 实测撞出来的：反引号形式的
   `for /f ... in (\`wsl ...\`)` **会吞掉控制台的 stdin** ——
   机器人和命令台都起来了、横幅也打了，**但敲进去的命令一条都没反应**。
   原来这里是拿 `for /f` 调 `wslpath` 转路径的，现在不用了：
   **WSL 会继承 cmd 的当前目录**，一句 `cd /d "%~dp0.."` 就够了。

### `saya_bot_controller.bat` —— **常驻控制台**（仓库根目录，Windows 双击）

**为什么放在根目录**：它是**双击就用**的入口，不该让人先 `cd`
再想起命令叫什么。放在根目录一眼就能看见。

双击它 = 在 WSL 里打开 `tools/panel.py`，**就在这个窗口里**启停机器人、改设置：

```
start / end / restart / status / quit   +   全部设置命令
```

`end` 和 `restart` 要打 `y` 确认。机器人**在跑**时设置命令直接送进它的 stdin
（当场生效）；**没在跑**就写 `.env`。

细节见下面 `panel.py` 那条。`.bat` 本身的写法约束见上面。

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

**第三步（QQ 侧）调的是 `python -m saya.napcat ensure`**，判据与面板同一份
（见下面 `panel.py` 的「QQ 侧」）。`--check` 只做**只读**探测，不启动、不停止。

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

### `config.py` —— **离线**改机器人的设置

⚠️ **改完要重启才生效。** 不想重启就在**机器人自己那个跑着日志的终端**里敲同样的命令
（那是 `core/console.py` 的运行时命令台，改完立刻生效）。

```bash
python tools/config.py show                       # 看现在是什么样
python tools/config.py group 123456789 mention    # 设某个群的策略
python tools/config.py group 123456789 -          # 移除某个群
python tools/config.py default silent             # 没列出的群默认怎么办
python tools/config.py owner 10001                # 主人（只能一个）
python tools/config.py admin add 20001            # 管理员（最多 3 个）
python tools/config.py friend add 30001           # 白名单（最多 15 个）
python tools/config.py keyword set saya,咲弥       # keyword 模式用的词
python tools/config.py prob 0.15                  # random 模式的概率
python tools/config.py rate 3                     # 每群每分钟最多回几条
python tools/config.py ocr cloud                  # 云 OCR：auto / cloud / local
python tools/config.py yolo x                     # 检测权重：n / s / m / l / x
python tools/config.py device cuda:0              # 检测跑在哪：auto / cpu / cuda:N
```

**两个前端，一份后端**（前后端分离，ADR-022）：

| 层 | 在哪 | 职责 |
|---|---|---|
| 命令语义与校验 | `saya/manage.py` | **唯一一份**，返回结构化结果、不打印 |
| 文件编辑 | `saya/envfile.py` | 改 `.env` 的那一行 |
| 前端 | 这个脚本 / `core/console.py` / 将来的 web | 只负责读命令、显示结果 |

**为什么文件编辑要单独一层**：`.env` 里**装着 API key**，谁都不该自己拼字符串去写它。
那一层的三条规矩：只动目标行（注释、其余行、**行尾符**全保留）、
写前先备份、写盘时检查**有没有被 git 忽略**（`--env 别的名字` 能绕开
`.gitignore` 的 `.env` / `.env.*`，这条不是假想，见 ADR-022）。

### `panel.py` —— **常驻控制台**：启停机器人 + 改设置

**为什么需要它**：以前每动一下都要另开一个终端（启动一个、改设置一个、看日志又一个），
而改设置那个还是**离线**的（改完得重启才生效）。前后端分离之后（ADR-022）
命令语义已经和前端无关了，所以可以有一个**开着就不关**的控制台把这些收进来。

⚠️ **在 WSL 里跑**（它要调 `tools/start_saya.sh`，那是个 bash 脚本）。
平时不用手敲，双击根目录的 `saya_bot_controller.bat` 就行。

```bash
python3 tools/panel.py
```

```
  start            启动机器人（日志就在这一屏）
  end              停掉它            ← 要确认
  restart          重启（用现在这份代码）← 要确认
  status           机器人与 QQ 侧一起报
  quit             退出控制台（**机器人一起停**）← 要确认
  napcat [start|stop|restart]   QQ 侧（NapCat）
  show / group / admin / friend / keyword / prob / rate / help
```

#### QQ 侧（NapCat）：判据是端口，不是进程

**为什么加这一块**：2026-10-17 上午，机器人进程在、8080 在听、控制台报
「运行中」—— 但群里一条都没回。真因是 **QQ 账号掉线**，
NapCat 的 OneBot 适配器（`:3000`）没起来，事件根本到不了机器人。
旧判活是 `pgrep -f "opt/QQ/qq"`：**进程在 = 运行中**，看不出这个。

现在判据只有一条：**`3000` 端口上有人在听**。状态分四种：

| 看到什么 | 意思 |
|---|---|
| `就绪` | QQ 通着，机器人能收发 |
| `启动中` | 进程在、端口还没开、且起来不到 90 秒 |
| `未就绪` | 进程在、端口没开、已超过 90 秒 —— **账号可能未登录** |
| `未运行` | 没有 QQ 进程 |

**⚠️ NapCat 不跟着控制台一起退出。** 机器人是"前端走了就跟着走"
（ADR-026），NapCat 反过来：它揣着 QQ 登录态，重启**不保证**自动登录。
所以 `quit` / 关窗口只带走机器人。

登录这一步本模块不碰 —— 要扫码就开
`http://127.0.0.1:6099/webui`。细节与判断理由见
[`../docs/01-architecture.md`](../docs/01-architecture.md) **ADR-041**。

判据与启停的实现在 `project_main/src/saya/napcat.py`，
面板和 `start_saya.sh` **共用这一份**，不再各写一个 `pgrep`。

启动时会**自动报到一次**（等于替你敲了一遍 `status`），
所以一进来看见的就是当前状态，不用先敲一下。

#### 清缓存：`cache` / `cache clear`

```
cache         查看将清理的内容
cache clear   真的清            ← 要确认
```

**清**：聊天记录（消息、话题）与收到的图片（索引 + `data/images/` 下的文件）。
**不清**：长期记忆（`notebook.db`）、设置（`.env`）、成员花名册。
理由见 [`../docs/01-architecture.md`](../docs/01-architecture.md) **ADR-044**。

⚠️ 机器人**跑着也能清** —— 聊天记录只在磁盘上，不转给进程；
配置才是"进程里的一份对象"，所以那个要转进去，这个不用。

⚠️ `DATA_DIR` 是相对路径时，按 **`.env` 所在目录**解析。
面板是从仓库根目录跑的，直接拿 `./data` 会指到别处。

#### ⚠️ 这个窗口**管着机器人的命**（ADR-026）

| 情形 | 允许吗 |
|---|---|
| 前端在跑、机器人停着 | ✅（`end` 之后就是这样） |
| **机器人跑着、前端没了** | ❌ 那就是**无头后端** —— 端口被占、没人看日志、下次 `start` 还起不来 |

所以：

- **前端退出一定带走机器人**（`quit` / 关窗口 / 断管道 / Ctrl+C 全算）
- 启动时检查归属：
  - 有**另一个控制台** → 提示一句、**自己退出**（两个一起敲会打架）
  - 有**无头机器人**（上次留下的孤儿）→ **问一句**，确认后接管（关掉它、起自己的）

> **⚠️ 但这只是"前端看着机器人"一个方向。** 窗口被**直接叉掉**（或 `kill -9`）时
> 上面那个 `finally` **根本跑不到** —— 所以还有**反方向**的一道：
> 面板把自己的 PID 传给机器人（`SAYA_OWNER_PID`），**机器人自己盯着它**，
> 发现没了就退出（`saya/watchdog.py`）。**双向都查，才算真的管住。**
> （无头启动时没有那个变量 —— 那时机器人本来就该独立活着。）

⚠️ **这一问有两处坑，都是实测撞出来的**（都已钉测试）：

1. **别把同一件事说两遍。** 原来先打一遍 `status`（它已经把无头实例描述了一遍），
   紧接着提问又说一遍 —— 两段叠在一起，所有者直接说
   "我没看懂这是什么交互逻辑"。现在**那个问题本身就是报到**，不再另打 `status`。
2. **提问要认它自己教的命令。** 提示语写着"敲 restart 可以接管它"，
   可 `restart` 这一答不在 `y/yes/是` 里，于是**被当成"否"静默吃掉** ——
   用户只看到"那就不动它"，完全不知道自己的命令去哪了。
   所以接管那一问**多认 `restart` / `start`**。
   （其余 `[y/N]` 一律只认 `y/yes/是`，不自作聪明。）

> **为什么 web 不一样**：终端前端是**监视器 + 控制器**，伴随整个生命周期；
> web 可能被关掉而机器人继续跑 —— 所以将来有 web 时，
> **仍然会有一个终端前端在旁边跑着**管进程。

**改设置的两种走法**（自动选）：

- 机器人**在跑** → 命令直接送进它的 stdin，**当场生效**（走 `core/console.py`）
- 机器人**没在跑** → 直接写 `.env`，下次启动生效

**为什么 `end` 要发 SIGINT 而不是 SIGTERM**：机器人接住了 `KeyboardInterrupt`，
会走 `finally` 里的 `pipeline.stop()`（关接入、落记忆）。SIGTERM 会跳过清理。

⚠️ **这个脚本不能叫 `saya.py`** —— 会和 `src/saya/` 这个包同名，
`from saya import ...` 会导到包而不是它。实测撞过一次。

⚠️ **`_pid_alive` 用的是 `os.kill(pid, 0)`** —— 它在 POSIX 上才是"检查存在"，
**Windows 上会真的杀进程**。面板只在 WSL 里跑，但测试跑在 Windows 上，
所以有 `os.name == "nt"` 护栏，测试里也一律打桩。

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
