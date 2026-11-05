#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
PROJECT_DIR="$PWD"
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_DIR/.runtime/python"
export UV_PYTHON_BIN_DIR="$PROJECT_DIR/.runtime/bin"
export UV_PYTHON_INSTALL_BIN=0
export UV_LINK_MODE=copy
export TMPDIR="$PROJECT_DIR/data/tmp"
mkdir -p "$TMPDIR" "$PROJECT_DIR/.runtime/bin"
if [ ! -x "$PROJECT_DIR/.runtime/bin/uv" ]; then
  echo "正在下载项目专用安装工具，不修改全局环境…"
  curl -fLsS --retry 2 https://astral.sh/uv/install.sh -o "$PROJECT_DIR/.uv-install.sh"
  UV_UNMANAGED_INSTALL="$PROJECT_DIR/.runtime/bin" sh "$PROJECT_DIR/.uv-install.sh"
fi
if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  "$PROJECT_DIR/.runtime/bin/uv" python install 3.12 --no-bin
  "$PROJECT_DIR/.runtime/bin/uv" venv --python 3.12 --managed-python "$PROJECT_DIR/.venv"
fi
DEPENDENCIES="$PROJECT_DIR/requirements.txt"
if [ -s "$PROJECT_DIR/requirements.lock.txt" ]; then
  DEPENDENCIES="$PROJECT_DIR/requirements.lock.txt"
fi
"$PROJECT_DIR/.runtime/bin/uv" pip install --python "$PROJECT_DIR/.venv/bin/python" -r "$DEPENDENCIES"
echo "安装完成。双击「启动拾课.command」即可使用。"
