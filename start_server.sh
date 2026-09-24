#!/bin/bash
# 啟動 Flask 應用（gunicorn 生產模式）
# 用法：./start_server.sh [端口]
#
# 停止：pkill -f "gunicorn.*wsgi:app"

set -e

cd "$(dirname "$0")"

PORT="${1:-5010}"
export GUNICORN_BIND="0.0.0.0:${PORT}"

# 優先用 PATH 上的 gunicorn；找不到就 fallback 到 user site
GUNICORN_BIN="$(command -v gunicorn || true)"
if [ -z "$GUNICORN_BIN" ]; then
    # macOS Homebrew Python 的 --user 安裝路徑
    for p in \
        "$HOME/.local/bin/gunicorn"; do
        if [ -x "$p" ]; then
            GUNICORN_BIN="$p"
            break
        fi
    done
fi

if [ -z "$GUNICORN_BIN" ]; then
    echo "找不到 gunicorn。請先安裝：pip3 install --user gunicorn"
    exit 1
fi

echo "啟動 gunicorn：${GUNICORN_BIN}"
echo "地址：${GUNICORN_BIND}"
exec "$GUNICORN_BIN" -c gunicorn_config.py wsgi:app
