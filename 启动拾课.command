#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export TMPDIR="$PWD/data/tmp"
mkdir -p "$TMPDIR"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import fastapi, faster_whisper, uvicorn, mlx_audio, mlx_whisper, Vision' >/dev/null 2>&1; then
  if ! bash ./setup.sh; then
    echo "安装失败，请检查网络。按回车关闭后，可再次双击重试。"
    read -r
    exit 1
  fi
fi
exec .venv/bin/python run.py
