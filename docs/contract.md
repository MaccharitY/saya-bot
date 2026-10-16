> ⚠️ 本文件里的群号与 QQ 号**已替换为占位符** —— 原来是真实值，
> 而这是公开仓库。（这份对照由所有者手写，整理时做了脱敏。）

原来：

─────────────────────────────────────────────
 saya 控制台。敲 help 看命令。
 start / end / restart / status + 全部设置命令
 end / restart / quit 要打 y 确认
 [!] 这个窗口管着机器人的命：它一关，机器人就停。
─────────────────────────────────────────────
  没在跑。敲 start 起它。
start
  启动中（PID 17578）。日志会出现在下面。

== 自检
== saya_bot 自检 ==
  接入后端 : onebot11_http
  模型     : deepseek-flash @ https://api.deepseek.com
  看图路线 : direct（可选：direct / delegated / hybrid）
  温度     : 对话 0.8 / 看图 0.2
  思考模式 : 开 —— reasoning_effort=high（[!] 此时 temperature 不生效，延迟与输出费用都会涨）
  默认群策略: mention（每群可单独覆盖）
    · 123456789 → keyword
  可私聊它的人: 1 个（主人/管理/白名单），其余私聊一律不理
  埋点     : ./logs/trace.jsonl（启用=True）
  计价     : 输入 2.0 / 命中 0.04 / 输出 8.0 元每百万 token
  数据目录 : ./data
  人格     : /mnt/d/Claude/DeepSeek_api/saya_bot/project_main/persona/saya.md（1565 字）
  长期记忆 : 开
  知识源   : 开（萌娘百科；Arcaea 本地剧情语料 有）

  提示：你的 .env 里没有这些键（会走默认值）：SAYA_CV_MODEL_DIR
        要改它们的话，照 .env.example 补上。

→ 试着占用事件端口…
21:07:15 INFO    saya.adapter.onebot11_http | 事件服务器已就绪：http://127.0.0.1:8080/onebot
√ 端口可用：http://127.0.0.1:8080/onebot
  （把上面这个地址填进 NapCat 的「HTTP 上报」）

  API key  : 已配置（长度 35）
→ 试着跟模型说一句话…
√ 模型回了：'在的'（36+20 tokens，1279 ms）

一切正常，可以启动：python run.py

== NapCat（QQ 侧）
  已经在跑（QQ 10001）

== 启动 saya_bot（Ctrl+C 停）
21:07:17 INFO    saya | 记忆已启用：data/memory.db（{'members': 10, 'messages': 150, 'topics': 19, 'images': 17}）
21:07:17 INFO    saya | 长期记忆已启用：data/notebook.db（0 条）
21:07:17 INFO    saya | 人格已加载：/mnt/d/Claude/DeepSeek_api/saya_bot/project_main/persona/saya.md（1565 字）
21:07:17 INFO    saya | 知识源已接入：arcaea_lookup、moegirl_lookup（本地剧情语料：有）

─────────────────────────────────────────────
 命令台就绪。敲 help 看有哪些命令。
 改完立刻生效，同时写回 .env（下次启动还是它）。
 例：show            admin add 10086
     group 123 silent    rate 3
 另：status 看它跑了多久；start / restart 在命令行台里
     （就是双击 saya_bot_controller.bat 出来的那个窗口）
─────────────────────────────────────────────
21:07:17 INFO    saya.pipeline | saya_bot 启动：adapter=onebot11_http 看图路线=direct 模型=deepseek-flash 默认群策略=mention
21:07:17 INFO    saya.adapter.onebot11_http | 事件服务器已就绪：http://127.0.0.1:8080/onebot



更改后：

─────────────────────────────────────────────
 saya 控制台--输入 help 查看命令。
 start / end / restart / quit / status
 [!] bot会在此终端退出时同时终止
─────────────────────────────────────────────
  未运行。输入 start 启动。
start
  启动中（PID 17578）--开始输出日志。

== 自检
== saya_bot 自检 ==
  接入后端 : onebot11_http
  模型     : deepseek-flash @ https://api.deepseek.com
  视图模式 : direct（direct / delegated / hybrid）
  温度     : 对话 0.8 / 识图图 0.2
  思考模式 : 开 —— reasoning_effort=high（[!] 此时 temperature 不生效，延迟与输出费用将提高）
  默认群策略: mention（每群可单独覆盖）
    · 123456789 → keyword
  可私聊用户: 1 （主人/管理/白名单），其余私聊无效
  埋点     : ./logs/trace.jsonl（启用=True）
  计价     : 输入 2.0 / 命中 0.04 / 输出 8.0 元每百万 token
  数据目录 : ./data
  人格     : /mnt/d/Claude/DeepSeek_api/saya_bot/project_main/persona/saya.md（1565 字）
  长期记忆 : 开
  知识源   : 开（萌娘百科；Arcaea 本地剧情语料 有）

  提示： .env 内未赋值键（默认值）：SAYA_CV_MODEL_DIR
        可依据 .env.example 更改。

→ 尝试占用事件端口…
21:07:15 INFO    saya.adapter.onebot11_http | 事件服务器已就绪：http://127.0.0.1:8080/onebot
√ 端口可用：http://127.0.0.1:8080/onebot
  （可填入 NapCat「HTTP 上报」）

  API key  : 已配置
→ 输入测试
√ 输出测试：'在的'（36+20 tokens，1279 ms）

一切正常，可以启动：python run.py

== NapCat（QQ 侧）
  运行中（QQ 10001）

== 启动 saya_bot（Ctrl+C 停止）
21:07:17 INFO    saya | 记忆已启用：data/memory.db（{'members': 10, 'messages': 150, 'topics': 19, 'images': 17}）
21:07:17 INFO    saya | 长期记忆已启用：data/notebook.db（0 条）
21:07:17 INFO    saya | 人格已加载：/mnt/d/Claude/DeepSeek_api/saya_bot/project_main/persona/saya.md（1565 字）
21:07:17 INFO    saya | 知识源已接入：arcaea_lookup、moegirl_lookup（本地剧情语料：有）

─────────────────────────────────────────────
 命令台就绪--输入 help 查看命令。
更改后立刻及长期生效(写入.env)
 start / end / restart / quit / status
 [!] bot会在此终端退出时同时终止
─────────────────────────────────────────────
21:07:17 INFO    saya.pipeline | saya_bot 启动：adapter=onebot11_http 识图模式=direct 模型=deepseek-flash 默认群策略=mention
21:07:17 INFO    saya.adapter.onebot11_http | 事件服务器已就绪：http://127.0.0.1:8080/onebot