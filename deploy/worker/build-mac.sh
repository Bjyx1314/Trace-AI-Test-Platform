#!/usr/bin/env bash
# 把 App 真机执行机 worker 打包成单个 macOS 原生二进制 tp-worker（内含 Python + 依赖 + adb）
# 用法（在有 Python 3.10+ 的 Mac 上）：bash deploy/worker/build-mac.sh
# 产物：dist/tp-worker（拷到平台服务器 worker_exe_path_mac 指向的位置即可供「连接我的真机」下载）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO"

echo "== 打包 tp-worker (macOS) =="

echo "[1/4] 安装 worker 依赖 + PyInstaller..."
python3 -m pip install -q -r deploy/worker/requirements-worker.txt
python3 -m pip install -q pyinstaller

echo "[2/4] 准备内置 adb (Google platform-tools, mac)..."
PT_DIR="$SCRIPT_DIR/platform-tools"
if [ ! -x "$PT_DIR/adb" ]; then
  ZIP="$(mktemp -t platform-tools).zip"
  curl -fsSL "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip" -o "$ZIP"
  unzip -oq "$ZIP" -d "$SCRIPT_DIR"
  rm -f "$ZIP"
  chmod +x "$PT_DIR/adb"
fi

echo "[3/4] PyInstaller 打包(几分钟)..."
# 与 Windows 版对齐：不整包打 app(只跟随真实 import)，exclude 掉 worker 用不到的重依赖进一步瘦身。
# --add-data 用冒号分隔(mac/linux)；--add-binary 带上 adb 保留可执行位。
python3 -m PyInstaller --noconfirm --onefile --name tp-worker \
  --paths backend \
  --add-binary "deploy/worker/platform-tools/adb:platform-tools" \
  --collect-all uiautomator2 \
  --collect-all adbutils \
  --hidden-import uiautomator2 \
  --hidden-import PIL \
  --hidden-import pydantic_settings \
  --exclude-module fastapi --exclude-module starlette --exclude-module uvicorn \
  --exclude-module sqlalchemy --exclude-module asyncpg --exclude-module alembic \
  --exclude-module anthropic --exclude-module openai --exclude-module greenlet \
  --exclude-module rq --exclude-module redis --exclude-module playwright \
  deploy/worker/worker.py

echo "[4/4] 完成 → dist/tp-worker"
ls -lh dist/tp-worker | awk '{print $NF, $5}'
echo "提示：分发给测试员前建议对二进制做代码签名/公证，避免 Gatekeeper 拦截(否则首次运行需在「系统设置→隐私与安全性」放行)。"
