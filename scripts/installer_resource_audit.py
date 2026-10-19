"""Audit the installer payload without opening WeChat or reading user settings."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--payload', type=Path, default=ROOT / 'release/installer-payload/心动微信.exe')
parser.add_argument('--output', type=Path, default=ROOT / 'release/installer/resource-audit.json')
arguments = parser.parse_args()
exe = arguments.payload
toc = ast.literal_eval((ROOT / 'build/pyinstaller-installer/heart-wechat/Analysis-00.toc').read_text('utf-8'))
bundled = {row[0].replace('\\', '/') for section in toc if isinstance(section, list)
           for row in section if isinstance(row, tuple) and len(row) == 3 and row[2] == 'DATA'}
allowed = {
    'data/dependency-review/wechatauto_replica-1.2.5.1-py3-none-any.whl',
    'data/dependency-review/replica-1.2.5.1/wechatauto/db.py',
    'data/dependency-review/replica-1.2.5.1/wechatauto/media.py',
}
assert {p for p in bundled if p.startswith('data/')} == allowed
for p in bundled:
    assert Path(p).name not in {'settings.json', 'memory.sqlite3', 'contact-preferences.json', '_acceptance-session.json'}
archive = CArchiveReader(str(exe))
archive_names = {p.replace('\\', '/') for p in archive.toc}
frontend_files = list((ROOT / 'prototype-heart-wechat/dist/client').rglob('*'))
for path in frontend_files:
    if path.is_file():
        relative = path.relative_to(ROOT).as_posix()
        archive_path = next(p for p in archive.toc if p.replace('\\', '/') == relative)
        assert archive.extract(archive_path) == path.read_bytes(), relative
assert 'assets/heart-wechat.ico' in archive_names
with pefile.PE(str(exe)) as pe:
    assert pe.FILE_HEADER.Machine == 0x8664
    assert any(entry.id == 14 for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries)
report = {'passed': True, 'payload_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
          'data_files': sorted(allowed), 'frontend_file_count': sum(p.is_file() for p in frontend_files),
          'frontend_matches_latest_build': True, 'architecture': 'x64', 'icon_embedded': True}
destination = arguments.output
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps(report, ensure_ascii=False))
