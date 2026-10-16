"""Explicit local migration; never bundle these files or overwrite a profile."""
import json
import os
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def migrate(source: Path, target: Path):
    if source.resolve() == target.resolve():
        raise ValueError('Migration source and destination must differ')
    target.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for name in ('settings.json', 'contact-preferences.json'):
        original, destination = source / name, target / name
        if not original.is_file():
            continue
        if destination.exists():
            skipped.append(name)
            continue
        # Validate JSON before copying; protected API strings remain DPAPI.
        json.loads(original.read_text('utf-8'))
        shutil.copy2(original, destination)
        copied.append(name)
    original, destination = source / 'memory.sqlite3', target / 'memory.sqlite3'
    if original.is_file() and not destination.exists():
        reader = sqlite3.connect(original.resolve().as_uri() + '?mode=ro', uri=True)
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
            reader.close()
        copied.append('memory.sqlite3')
    elif destination.exists():
        skipped.append('memory.sqlite3')
    annotations = source / 'attachment-text'
    if annotations.is_dir():
        for original in annotations.glob('*.dpapi'):
            destination = target / 'attachment-text' / original.name
            if original.is_file() and original.stat().st_size <= 64000 and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, destination)
                copied.append('attachment-text/' + original.name)
    return {'copied': copied, 'skipped_existing': skipped, 'private_files_bundled': False,
            'source_preserved': True, 'destination': str(target)}


if __name__ == '__main__':
    target = Path(os.environ['LOCALAPPDATA']) / 'HeartWeChat'
    result = migrate(ROOT / 'data', target)
    (ROOT / 'data/packaging-local-migration-2026-10-14.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**result, 'copied': len(result['copied'])}, ensure_ascii=False))
