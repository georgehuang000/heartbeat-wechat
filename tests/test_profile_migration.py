import importlib.util
import json
from pathlib import Path
import sqlite3

spec = importlib.util.spec_from_file_location('profile_migration', Path(__file__).parents[1] / 'scripts/migrate_desktop_data.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_migrate_committed_wal_and_preserve_existing_settings(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'settings.json').write_text('{"protected_key":"original-protected"}')
    (target / 'settings.json').write_text('{"protected_key":"already-configured"}')
    (source / 'contact-preferences.json').write_text('{"fictional-contact":{"role":"young-female"}}')
    reader = sqlite3.connect(source / 'memory.sqlite3')
    reader.execute('PRAGMA journal_mode=WAL')
    reader.execute('CREATE TABLE memories (value TEXT)')
    reader.execute("INSERT INTO memories VALUES ('fictional committed row')")
    reader.commit()
    result = migration.migrate(source, target)
    reader.close()
    assert json.loads((target / 'settings.json').read_text())['protected_key'] == 'already-configured'
    with sqlite3.connect(target / 'memory.sqlite3') as conn:
        assert conn.execute('SELECT value FROM memories').fetchone()[0] == 'fictional committed row'
    assert 'settings.json' in result['skipped_existing']
    assert (source / 'memory.sqlite3').is_file()


def test_migration_does_not_copy_db_keys_decrypted_cache_or_logs(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    for name in ('unused-keys.json', 'desktop-stdout.log', 'contact.db'):
        (source / name).write_text('fictional private data')
    migration.migrate(source, target)
    assert list(target.iterdir()) == []
