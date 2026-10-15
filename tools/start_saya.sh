#!/usr/bin/env bash
# 一键启动 saya_bot（在 WSL 里跑）。
#
# 做四件事：
#   1. 从 .env 读 QQ 号
#   2. 跑一次自检（配置 / 附属 / 模型通道）—— **不过就不往下走**
#   3. 确保 NapCat 在跑，不在就拉起来
#   4. 启动机器人（前台，Ctrl+C 停）
#
# ⚠️ 顺序是刻意的：**自检在碰 QQ 之前**。配置有问题时不该去动 QQ 侧。
#
# 用法：
#   bash tools/start_saya.sh            # 正常启动
#   bash tools/start_saya.sh --check    # 只自检（不碰 NapCat、不启动）
set -uo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../project_main" && pwd)"
NAPCAT_DIR="${NAPCAT_DIR:-$HOME/Napcat}"
ENV_FILE="$PROJECT/.env"
PYTHON="${SAYA_PYTHON:-$HOME/saya-venv/bin/python}"

bold() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# ---- 1. QQ 号 ----
QQ="$(grep -E '^SAYA_SELF_ID=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')"
if [ -z "$QQ" ]; then
    fail "还没设 QQ 号。请在 $ENV_FILE 里把 SAYA_SELF_ID= 填上你的 QQ 号。"
    exit 1
fi

# ---- 2. 自检（在碰 QQ 之前）----
bold "自检"
if ! "$PYTHON" "$PROJECT/run.py" --check; then
    fail "自检没过，先把它修好再启动。"
    exit 1
fi

if [ "$CHECK_ONLY" = "1" ]; then
    echo
    echo "只自检，不启动。"
    exit 0
fi

# ---- 3. NapCat ----
bold "NapCat（QQ 侧）"
if pgrep -f "opt/QQ/qq" >/dev/null 2>&1; then
    echo "  已经在跑（QQ $QQ）"
elif [ -x "$NAPCAT_DIR/opt/QQ/qq" ]; then
    echo "  没在跑，拉起来…"
    mkdir -p "$NAPCAT_DIR/log"
    (cd "$NAPCAT_DIR" && setsid nohup xvfb-run -a "$NAPCAT_DIR/opt/QQ/qq" --no-sandbox -q "$QQ" \
        >"$NAPCAT_DIR/log/manual-start.log" 2>&1 &)
    echo "  已启动（日志：$NAPCAT_DIR/log/manual-start.log）"
    echo "  ⚠️ 如果这是重启 WSL 后的第一次，可能需要在 WebUI 再扫一次码"
else
    fail "找不到 NapCat（$NAPCAT_DIR/opt/QQ/qq）。先装 NapCat，见 docs/04。"
    exit 1
fi

# ---- 4. 启动 ----
bold "启动 saya_bot（Ctrl+C 停）"
cd "$PROJECT" || exit 1
exec "$PYTHON" run.py
