"""Distribution smoke check: no WeChat reads, draft writes or model requests."""
import importlib
import json
from pathlib import Path
import sys


def check_runtime(root: Path, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    report = {'frozen': bool(getattr(sys, 'frozen', False)), 'checks': {},
              'wechat_operated': False, 'model_called': False}
    for name in ('webview', 'clr', 'win32gui', 'uiautomation', 'cryptography', 'zstandard', 'pysilk'):
        try:
            importlib.import_module(name)
            report['checks'][name] = True
        except Exception as error:
            report['checks'][name] = {'error_type': type(error).__name__, 'error': str(error)}
    # The production loader verifies both the pinned archive and exact source;
    # do not construct the reader (that would query the actual user's WeChat).
    path = root / 'scripts/probe_wechat_db.py'
    try:
        spec = importlib.util.spec_from_file_location('distribution_db_check', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        upstream, _ = module.load_reader()
        importlib.import_module('wechatauto.media')
        report['checks']['reviewed_reader'] = hasattr(upstream, 'WeChatDB')
    except Exception as error:
        report['checks']['reviewed_reader'] = {'error_type': type(error).__name__, 'error': str(error)}
    frontend = root / 'prototype-heart-wechat/dist/client'
    report['checks']['frontend'] = (frontend / 'index.html').is_file()
    report['checks']['icon'] = (root / 'assets/heart-wechat.ico').is_file()
    report['passed'] = all(value is True for value in report['checks'].values())
    (directory / 'runtime-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['passed']:
        raise SystemExit(2)
