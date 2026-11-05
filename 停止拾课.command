#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  .venv/bin/python stop.py
else
  echo "应用尚未安装，无需停止。"
fi
