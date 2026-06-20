@echo off
chcp 65001 >nul
echo ============================================
echo   自动视频脚本工作台 - Windows 打包
echo ============================================
echo.
echo [1/2] 安装依赖（pywebview / pythonnet / pyinstaller）...
python -m pip install --upgrade pip
pip install pywebview pythonnet pyinstaller
if errorlevel 1 (
  echo 依赖安装失败，请确认已安装 Python 3.10+ 并勾选 Add to PATH。
  pause
  exit /b 1
)
echo.
echo [2/2] 开始打包...
pyinstaller --noconfirm --clean videostudio.spec
if errorlevel 1 (
  echo 打包失败，请把上面的报错发给开发者。
  pause
  exit /b 1
)
echo.
echo ============================================
echo   打包完成！可执行文件： dist\AutoVideoStudio.exe
echo.
echo   重要：视频制作需要 ffmpeg。请把 ffmpeg.exe 和 ffprobe.exe
echo   放到 dist\ 目录（与 exe 同级），或确保它们已在系统 PATH 中。
echo ============================================
pause
