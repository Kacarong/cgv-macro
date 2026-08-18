# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 스펙: cgv_gui.py 를 onedir exe 로 빌드하고 Chromium 을 함께 번들.
import os
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('playwright')

# build_exe.bat 가 PLAYWRIGHT_BROWSERS_PATH=./ms-playwright 로 크로미움을 설치한다.
# 그 폴더가 있으면 통째로 번들에 포함(오프라인/자체완결 실행).
ms = os.path.join(os.getcwd(), 'ms-playwright')
if os.path.isdir(ms):
    datas.append((ms, 'ms-playwright'))

hiddenimports += ['cgv_macro', 'cgv_macro.cgv', 'cgv_macro.monitor',
                  'cgv_macro.notifier', 'cgv_macro.state', 'cgv_macro.config']

a = Analysis(
    ['cgv_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CGV취소표감시기',
    debug=False,
    strip=False,
    upx=False,
    console=False,           # GUI 앱: 콘솔 창 숨김
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='CGV취소표감시기',
)
