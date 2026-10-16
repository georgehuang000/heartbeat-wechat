# Build only explicit resources: never include the user's data directory.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
review = root / 'data/dependency-review'
onefile = os.environ.get('GALCHAT_BUNDLE_MODE') == 'onefile'
datas = [
    (str(root / 'prototype-heart-wechat/dist/client'), 'prototype-heart-wechat/dist/client'),
    (str(root / 'assets/heart-wechat.ico'), 'assets'),
    (str(root / 'scripts/probe_wechat_db.py'), 'scripts'),
    (str(review / 'wechatauto_replica-1.2.5.1-py3-none-any.whl'), 'data/dependency-review'),
    (str(review / 'replica-1.2.5.1/wechatauto/db.py'), 'data/dependency-review/replica-1.2.5.1/wechatauto'),
    (str(review / 'replica-1.2.5.1/wechatauto/media.py'), 'data/dependency-review/replica-1.2.5.1/wechatauto'),
]
datas += collect_data_files('uiautomation')
binaries = collect_dynamic_libs('uiautomation')
a = Analysis([str(root / 'main.py')],
    pathex=[str(root / 'src'), str(review / 'probe-deps')],
    binaries=binaries, datas=datas,
    hiddenimports=collect_submodules('uvicorn') + ['zstandard', 'cryptography.hazmat.primitives.ciphers',
        'webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr_loader',
        'comtypes.gen.UIAutomationClient'],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter', 'gi', 'cefpython3', 'pytest'],
    noarchive=False)
pyz = PYZ(a.pure)
options = dict(name='心动微信', debug=False, strip=False, upx=False, console=False,
    icon=str(root / 'assets/heart-wechat.ico'), version=str(root / 'packaging/version-info.txt'))
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **options)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **options)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='心动微信')
