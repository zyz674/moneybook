#!/usr/bin/env bash
# 记账本启动脚本（macOS / Linux / Termux 通用）
set -e
cd "$(dirname "$0")"
export PYTHONUTF8=1
export PYTHONPATH="$(pwd)/src"
exec python3 -m moneybook "$@"
