# 04 · NapCat 部署（WSL）

> 记录于 2026-10-15。**这里是踩过的坑与正确路径**，不要照网上的旧教程。
> 权威文档：[Shell 安装与兼容说明](https://napneko.github.io/guide/boot/Shell)

---

## 目标拓扑

```
WSL 里的 Ubuntu 26.04                Windows
┌────────────────────┐              ┌──────────────────────┐
│ Linux QQ           │              │  saya_bot            │
│ NapCat（协议端）    │ ◀── HTTP ──▶ │  （我们的代码）        │
│ OneBot v11         │              │  事件服务器 :8080     │
└────────────────────┘              └──────────────────────┘
```

分工：**NapCat 负责跟 QQ 说话，saya_bot 只讲 OneBot 协议。** 换后端不改内核（ADR-001）。

## 环境事实（2026-10-15 实测）

| 项 | 值 |
|---|---|
| WSL | 3.0.3.0，内核 6.18.54.1-microsoft-standard-WSL2 |
| 发行版 | Ubuntu **26.04.1 LTS**（远高于 NapCat 要求的 20.04+）|
| 用户 | `maccharity`（家目录 `/home/maccharity`）|
| 架构 / 磁盘 / 内存 | x86_64 / 955G 可用 / 7.6G |
| 出网 | WSL 内可直连 GitHub（200）|
| Node | **v24.22.0**，装在 `~/.local/node`（已加进 `~/.bashrc`）|

## ⚠️ 坑一：WSL 首次初始化会让命令"假死"

`wsl -d Ubuntu -e <命令>` 在 **WSL 的 OOBE（建用户名那一步）没完成时会一直挂住**，
看上去像"进不去 WSL"，其实是在等那个交互提示。

**以后遇到 WSL 命令无响应，先确认发行版不是停在 OOBE。**

✅ **好消息**：建好用户之后，AI 可以**直接进入 WSL 执行命令**：
`wsl -d Ubuntu -e bash -lc '...'` —— 不需要配 SSH。

## ⚠️ 坑二：Linux 包名与文档不符

- ❌ **不要**自己下 `NapCat.Shell.zip`：它是 **Windows** 包（解压出来是 `.bat` / `.exe` / `.dll`）。
  我实际下了 112MB 才发现，已删除。
- ❌ 文档镜像站（mintlify.wiki）里写的 `NapCat-linux-x64.zip` **在仓库里并不存在**，那份镜像已过期。
- ✅ 当前 release 的资源只有：`NapCat.Framework.zip`、`NapCat.Shell.zip`、
  `NapCat.Shell.Windows.Node.zip`、`NapCat.Shell.Windows.OneKey.zip` —— **没有 Linux 专用包**。

**Linux 走官方安装器，不走手动解压。**

## ✅ 正确安装路径：官方一键脚本

来自 `NapNeko/NapCat-Installer`：

```bash
curl -fL --connect-timeout 20 --max-time 1800 \
  -o napcat.sh https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh &&
bash napcat.sh --docker n --cli y --github-proxy 0
```

- 安装到 `~/Napcat`（当前用户，不动系统目录）
- ⚠️ **需要 sudo**：要装 QQ 的 deb、**Xvfb**（QQ NT 是图形程序，无头环境靠虚拟显示）等系统依赖
- ⚠️ 因此这条命令**必须由本人在自己的 WSL 终端里跑** —— AI 无法代答 sudo 密码提示
- 装完后用 `napcat` 命令管理服务
- 更新时：同一用户执行同样命令并加 `--force`，配置与插件会保留

**常用参数**（`bash napcat.sh --help` 看全部）：

| 参数 | 含义 |
|---|---|
| `--docker n` | 不用 Docker（我们直接在 WSL 里跑）|
| `--github-proxy 0` | 直连 GitHub，不用加速站 |
| `--cli y` | 安装 CLI 管理工具 |
| `--tui` | 交互式安装 |
| `--force` | 强制更新 |
| `--skip-deps` | 跳过系统依赖（需自己先装好）|

> 若系统仓库没有 Xvfb，官方建议改用 Docker —— 本机 Ubuntu 26.04 有，所以不用。

## 网络：WSL ↔ Windows

两边要互相说话，方向不对称：

- **Windows → WSL2**：`localhost` 转发**默认就能用**（WSL2 会自动转发）。
  → 我们调 NapCat 的 HTTP API（默认 `127.0.0.1:3000`）走这条，通常不用额外配置。
- **WSL2 → Windows**：**需要额外处理**。NapCat 要把事件 POST 给我们在 Windows 上的事件服务器。
  - **方案 A（推荐）**：在 Windows 的 `C:\Users\<你>\.wslconfig` 写
    ```
    [wsl2]
    networkingMode=mirrored
    ```
    然后 `wsl --shutdown` 重进。之后两边共享 `localhost`，省掉一整类问题。
  - **方案 B**：saya_bot 的 `SAYA_ONEBOT_LISTEN_HOST` 改成 `0.0.0.0`，
    NapCat 里填 Windows 主机的 IP。缺点是要处理防火墙，且 IP 会变。

## 待办

- [ ] 用户执行官方安装脚本（需要 sudo 密码）
- [ ] 启动并用 WebUI 扫码登录 QQ
- [ ] 确认网络方案（A 还是 B）
- [ ] 在 NapCat 里配置两处：
  - **HTTP 服务端**（我们调它发消息）→ 对应 `SAYA_ONEBOT_API_BASE`
  - **HTTP 上报**（它把事件推给我们）→ 填 `http://<地址>:8080/onebot`
- [ ] `SAYA_ADAPTER=onebot11_http`
- [ ] 在群里 @ 机器人，验证端到端
