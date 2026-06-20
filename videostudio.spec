# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：把 app.py 及 web/ 界面打成单文件可执行程序。
# 用法：pyinstaller --noconfirm --clean videostudio.spec
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web'), ('config.example.json', '.'),
         ('assets/icon.png', 'assets'), ('assets/icon.ico', 'assets')]
binaries = []
hiddenimports = []

# 收集 pywebview 及其原生后端（Windows 用 WebView2 / macOS 用 WebKit）
for pkg in ('webview', 'clr_loader', 'pythonnet'):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AutoVideoStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # 关闭 UPX，避免杀软误报
    runtime_tmpdir=None,
    console=False,        # 无控制台窗口；排查闪退时可临时改为 True 重新打包
    icon='assets/icon.ico',
)
