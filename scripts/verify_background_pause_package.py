"""Check the actual packaged Python code and frontend against current sources."""
import hashlib
import argparse
import json
from pathlib import Path
from types import CodeType
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('activity', 'controller', 'window_group', 'engine', 'current_chat', 'settings',
    'contact_database', 'contact_preferences', 'media', 'vision', 'visual_wechat', 'native_voice_control', 'native_accessibility',
    'conversation_identity', 'reply_batches', 'api_protocols')


def same_code(a, b):
    if not isinstance(a, CodeType) or not isinstance(b, CodeType):
        return a == b
    fields = ('co_code', 'co_names', 'co_varnames', 'co_freevars', 'co_cellvars',
        'co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_flags')
    return all(getattr(a, k) == getattr(b, k) for k in fields) and len(a.co_consts) == len(b.co_consts) and all(
        same_code(x, y) for x, y in zip(a.co_consts, b.co_consts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', default='release/background-pause/心动微信.exe')
    parser.add_argument('--report', default='data/background-pause-package-2026-10-14.json')
    args = parser.parse_args()
    exe = ROOT / args.exe
    archive = CArchiveReader(str(exe))
    python = archive.open_embedded_archive('PYZ.pyz')
    checks = {}
    for name in MODULES:
        bundled = python.extract('galchat.' + name)
        expected = compile((ROOT / 'src/galchat' / (name + '.py')).read_text('utf-8'), bundled.co_filename, 'exec')
        checks[name] = same_code(bundled, expected)
    frontend = ROOT / 'prototype-heart-wechat/dist/client'
    resource_checks = {}
    for path in frontend.rglob('*'):
        if path.is_file():
            relative = path.relative_to(ROOT).as_posix()
            key = next((k for k in archive.toc if k.replace('\\', '/') == relative), None)
            resource_checks[path.relative_to(frontend).as_posix()] = bool(key and archive.extract(key) == path.read_bytes())
    report = {'passed': all(checks.values()) and all(resource_checks.values()),
        'exe_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
        'bytes': exe.stat().st_size, 'python_matches_current_source': checks,
        'frontend_files_checked': len(resource_checks),
        'frontend_all_match': all(resource_checks.values()),
        'native_instance_restarted': False, 'wechat_operated': False, 'model_called': False}
    (ROOT / args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
