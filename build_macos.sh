#!/usr/bin/env bash
# 自动视频脚本工作台 - macOS / Linux 打包
set -e
echo "[1/2] 安装依赖（pywebview / pyinstaller）..."
python3 -m pip install --upgrade pip
python3 -m pip install pywebview pyinstaller

echo "[2/2] 开始打包..."
python3 -m PyInstaller --noconfirm --clean videostudio.spec

echo "完成！产物在 dist/ 目录。"
echo "提示：视频制作需要 ffmpeg（macOS: brew install ffmpeg），"
echo "可将 ffmpeg/ffprobe 放到可执行文件同级目录，或确保在 PATH 中。"
