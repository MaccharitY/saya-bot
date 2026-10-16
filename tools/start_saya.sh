#!/usr/bin/env bash
# 一键启动 saya_bot（在 WSL 里跑）。
#
# 做五件事：
#   0. **看看是不是已经有一个在跑**（要重启就先停掉它）
#   1. 从 .env 读 QQ 号
#   2. 跑一次自检（配置 / 附属 / 模型通道）—— **不过就不往下走**
#   3. 确保 NapCat 在跑，不在就拉起来
#   4. 启动机器人（前台，Ctrl+C 停止）
#
# ⚠️ 顺序是刻意的：**自检在碰 QQ 之前**。配置有问题时不该去动 QQ 侧。
#
# 用法：
#   bash tools/start_saya.sh            # 确保它在跑（已在跑就告诉你，不动它）
#   bash tools/start_saya.sh --restart  # **重启**：停掉旧的，用现在这份代码起
#   bash tools/start_saya.sh --check    # 只自检（不碰 NapCat、不启动、不杀进程）
set -uo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../project_main" && pwd)"
NAPCAT_DIR="${NAPCAT_DIR:-$HOME/Napcat}"
ENV_FILE="$PROJECT/.env"
PYTHON="${SAYA_PYTHON:-$HOME/saya-venv/bin/python}"

bold() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
info() { printf '  %s\n' "$*"; }
# 把多行 PID 拼成一行（`paste` 比 `tr` 好在不会留个尾空格）
pids_str() { paste -sd' ' -; }

CHECK_ONLY=0
RESTART=0
case "${1:-}" in
    "")         ;;
    --check)    CHECK_ONLY=1 ;;
    --restart)  RESTART=1 ;;
    *)          fail "不认识的参数：$1（可用：--check / --restart）"; exit 2 ;;
esac

# ---- 0. 已经在跑吗 ----
#
# ⚠️ 这一段是补上的。在此之前"已经在跑"这件事**根本没人管**：脚本直接跑到自检，
# 而自检会发现端口被占、判定失败、拒绝启动 —— 于是这个"一键启动"按钮
# **没法重启一个正在跑的机器人**，还甩给用户一句"自检没过，先把它修好"。
# 实测就是这么卡住的。
#
# 认哪个进程是它**用端口认**，因为那才是真正的冲突来源；命令行匹配只作兜底
# （进程名是相对的 `run.py`，不带路径，光靠它认不准）。
PORT="$(grep -E '^SAYA_ONEBOT_LISTEN_PORT=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')"
PORT="${PORT:-8080}"

find_running() {
    local pids
    pids="$(ss -tlnpH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
    if [ -n "$pids" ]; then
        echo "$pids"
        return
    fi
    pgrep -f "$PYTHON run\.py" 2>/dev/null | sort -u || true
}

EXISTING="$(find_running)"
if [ -n "$EXISTING" ]; then
    if [ "$CHECK_ONLY" = "1" ]; then
        info "已有一个实例在跑（PID $(echo "$EXISTING" | pids_str)）"
    elif [ "$RESTART" = "1" ]; then
        bold "停掉旧实例"
        info "PID：$(echo "$EXISTING" | pids_str)"
        # shellcheck disable=SC2086
        kill $EXISTING 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            sleep 0.5
            [ -z "$(find_running)" ] && break
        done
        STILL="$(find_running)"
        if [ -n "$STILL" ]; then
            info "它没理会，强杀：$(echo "$STILL" | pids_str)"
            # shellcheck disable=SC2086
            kill -9 $STILL 2>/dev/null || true
            sleep 1
        fi
        if [ -n "$(find_running)" ]; then
            fail "停不掉（PID $(find_running | pids_str)）。手动处理：kill -9 <PID>"
            exit 1
        fi
        info "已停。"
    else
        echo
        echo "已经有一个在跑了（PID $(echo "$EXISTING" | pids_str)），不用再起一个。"
        echo "  要换成现在这份代码：  bash tools/start_saya.sh --restart"
        echo "  想让它停下来：        去它那个窗口按 Ctrl+C"
        exit 0
    fi
fi

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
    echo "  运行中（QQ $QQ）"
elif [ -x "$NAPCAT_DIR/opt/QQ/qq" ]; then
    echo "  未运行，启动中…"
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
bold "启动 saya_bot（Ctrl+C 停止）"
cd "$PROJECT" || exit 1
exec "$PYTHON" run.py
