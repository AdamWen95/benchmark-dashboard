"""Verify a byte-preserving test deployment without keys, writes, or network."""
from hashlib import sha256
from contextlib import closing
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(root: Path) -> dict:
    manifest = json.loads((root / 'deployment-manifest.json').read_text(encoding='utf-8'))
    if manifest['schema_version'] != 'benchmark-test-deployment-v1':
        raise ValueError('Unsupported manifest')
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in item['path']:
            raise ValueError('Invalid manifest path')
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Invalid deployment path')
        if path.stat().st_size != item['bytes'] or sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Deployment checksum mismatch')
    if (root / '.env').exists() or (root / 'data/daily_control/active.lock').exists():
        raise ValueError('Test bundle must have no real configuration or active job')
    if json.loads((root / 'daily_config.json').read_text(encoding='utf-8'))['enabled'] is not False:
        raise ValueError('Daily update must remain disabled')
    with closing(sqlite3.connect((root / 'data/dashboard.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database integrity failure')
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('snapshots', 'acquisition_runs', 'models', 'model_records', 'model_metrics')}
    if counts != manifest['database_counts']:
        raise ValueError('Database history count mismatch')
    return {'files_verified': len(manifest['files']), 'database_integrity': 'ok',
            'database_counts': counts, 'daily_enabled': False, 'configuration_included': False}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(ROOT), ensure_ascii=False))
    except Exception:
        print('Deployment verification failed. Stop; do not start or modify historical evidence.')
        raise SystemExit(1)
