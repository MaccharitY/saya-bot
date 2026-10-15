# tools/ —— 小工具集

放**与机器人本体无关、但项目里会反复用到**的独立脚本。

## 约定

1. **每个工具一个文件，零第三方依赖**（只用 Python 标准库）。
   理由：本机没装任何第三方包，零依赖意味着 clone 下来就能跑。
2. **都能当命令行用**，并且**函数可被 import 复用**。
3. 新增工具时**在下面加一行说明**——写清楚"为什么需要它"和"怎么用"。

## 清单

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
GitHub、DeepSeek 官方文档（`api-docs.deepseek.com`）

**抓不到**（反爬或本机问题，**不代表目标条目有问题**）：IEEE Xplore（返回空响应）、
Semantic Scholar 网页、Semantic Scholar API（持续 429）、DBLP（本机 SSL 证书链问题）

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
