"""Transactional local history; reading the dashboard never loads credentials."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

from .metrics import value_at
from .validation import canonical_json, content_hash, validate_payload

if TYPE_CHECKING:
    from .acquisition import Acquisition


SOURCE = 'artificial_analysis'
ENDPOINT = 'https://artificialanalysis.ai/api/v2/data/llms/models'

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    prompt_options_json TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    unknown_fields_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (source, content_hash)
);
CREATE TABLE IF NOT EXISTS models (
    source TEXT NOT NULL,
    model_id TEXT NOT NULL,
    PRIMARY KEY (source, model_id)
);
CREATE TABLE IF NOT EXISTS model_records (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    source TEXT NOT NULL,
    model_id TEXT NOT NULL,
    name TEXT NOT NULL,
    creator_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, source, model_id),
    FOREIGN KEY (source, model_id) REFERENCES models(source, model_id)
);
CREATE TABLE IF NOT EXISTS model_metrics (
    snapshot_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    model_id TEXT NOT NULL,
    metric_path TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, source, model_id, metric_path),
    FOREIGN KEY (snapshot_id, source, model_id)
        REFERENCES model_records(snapshot_id, source, model_id)
);
CREATE TABLE IF NOT EXISTS acquisition_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    snapshot_id INTEGER REFERENCES snapshots(id),
    http_status INTEGER,
    attempts INTEGER NOT NULL,
    error_code TEXT,
    message TEXT,
    CHECK ((status = 'success' AND snapshot_id IS NOT NULL)
        OR (status = 'failed' AND snapshot_id IS NULL))
);
CREATE INDEX IF NOT EXISTS acquisition_runs_source ON acquisition_runs(source, id);
"""


def _connect(db_path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    target = path.as_uri() + '?mode=ro' if readonly else str(path)
    connection = sqlite3.connect(target, uri=readonly, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    if readonly:
        connection.execute('PRAGMA query_only = ON')
    return connection


def initialize(db_path: str | Path) -> None:
    """Create the schema only; this never seeds synthetic or placeholder data."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(db_path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def record_success(db_path: str | Path, result: Acquisition) -> dict:
    """Validate again inside the transaction, then atomically save one run.

    A return to an older identical payload reuses its snapshot but makes it the
    latest successful run. Snapshot timestamps describe the first capture of
    that content; run timestamps describe every successful check.
    """
    initialize(db_path)
    connection = _connect(db_path)
    try:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            # Acquisition is a public dataclass; do not trust its cached digest,
            # coverage, or records when constructed by another caller.
            valid = validate_payload(result.valid.payload)
            existing = connection.execute(
                'SELECT id FROM snapshots WHERE source = ? AND content_hash = ?',
                (SOURCE, valid.content_hash),
            ).fetchone()
            new_snapshot = existing is None
            if existing:
                snapshot_id = existing['id']
            else:
                cursor = connection.execute(
                    '''INSERT INTO snapshots
                       (source, content_hash, collected_at, endpoint, record_count,
                        prompt_options_json, coverage_json, unknown_fields_json,
                        warnings_json, payload_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (SOURCE, valid.content_hash, result.collected_at, ENDPOINT,
                     len(valid.records), canonical_json(valid.payload.get('prompt_options')),
                     canonical_json(valid.coverage), canonical_json(valid.unknown_fields),
                     canonical_json(valid.warnings), canonical_json(valid.payload)),
                )
                snapshot_id = cursor.lastrowid
                for record in valid.records:
                    identity = (SOURCE, record['id'])
                    connection.execute(
                        'INSERT OR IGNORE INTO models(source, model_id) VALUES (?, ?)', identity)
                    connection.execute(
                        '''INSERT INTO model_records
                           (snapshot_id, source, model_id, name, creator_json, raw_json)
                           VALUES (?, ?, ?, ?, ?, ?)''',
                        (snapshot_id, *identity, record['name'],
                         canonical_json(record.get('model_creator')), canonical_json(record)),
                    )
                    # JSON stores the source numeric value without converting
                    # zeros/missing values or coercing integer precision to REAL.
                    connection.executemany(
                        '''INSERT INTO model_metrics
                           (snapshot_id, source, model_id, metric_path, value_json)
                           VALUES (?, ?, ?, ?, ?)''',
                        [(snapshot_id, *identity, path, canonical_json(value_at(record, path)))
                         for path in valid.coverage],
                    )
            cursor = connection.execute(
                '''INSERT INTO acquisition_runs
                   (source, started_at, finished_at, status, snapshot_id, http_status, attempts)
                   VALUES (?, ?, ?, 'success', ?, ?, ?)''',
                (SOURCE, result.started_at, result.collected_at, snapshot_id,
                 result.http_status, result.attempts),
            )
            return {'run_id': cursor.lastrowid, 'snapshot_id': snapshot_id,
                    'new_snapshot': new_snapshot}
    finally:
        connection.close()


def record_failure(db_path: str | Path, *, started_at: str, finished_at: str,
                   error_code: str, message: str, http_status: int | None = None,
                   attempts: int = 0) -> int:
    """Save an attempt only. Callers must supply fixed, redacted public errors."""
    initialize(db_path)
    connection = _connect(db_path)
    try:
        with connection:
            cursor = connection.execute(
                '''INSERT INTO acquisition_runs
                   (source, started_at, finished_at, status, snapshot_id,
                    http_status, attempts, error_code, message)
                   VALUES (?, ?, ?, 'failed', NULL, ?, ?, ?, ?)''',
                (SOURCE, started_at, finished_at, http_status, attempts, error_code, message),
            )
            return cursor.lastrowid
    finally:
        connection.close()


class _HistoryUnavailable(ValueError):
    """Stored associations are insufficient; never guess the missing history."""


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _HistoryUnavailable('历史运行时间缺少时区。')
    return parsed


def _read_point(connection: sqlite3.Connection, run: dict) -> dict:
    row = connection.execute('SELECT * FROM snapshots WHERE id = ?',
                             (run['snapshot_id'],)).fetchone()
    if row is None:
        raise _HistoryUnavailable('成功运行未关联有效快照。')
    snapshot = dict(row)
    if snapshot['source'] != run['source'] or run['source'] != SOURCE:
        raise _HistoryUnavailable('运行与快照的来源不一致。')
    payload = json.loads(snapshot.pop('payload_json'))
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise _HistoryUnavailable('快照内容无法还原。')
    for key in ('prompt_options', 'coverage', 'unknown_fields', 'warnings'):
        snapshot[key] = json.loads(snapshot.pop(key + '_json'))
    snapshot['metadata'] = {key: value for key, value in payload.items() if key != 'data'}
    if not isinstance(snapshot['coverage'], dict):
        raise _HistoryUnavailable('快照指标索引无法还原。')
    if snapshot['prompt_options'] != payload.get('prompt_options'):
        raise _HistoryUnavailable('快照配置与原始内容不一致。')
    rows = connection.execute(
        'SELECT source, model_id, raw_json FROM model_records WHERE snapshot_id = ? ORDER BY model_id',
        (snapshot['id'],),
    ).fetchall()
    records = []
    for row in rows:
        record = json.loads(row['raw_json'])
        if (not isinstance(record, dict) or row['source'] != snapshot['source']
                or record.get('id') != row['model_id']):
            raise _HistoryUnavailable('模型记录与快照关联不一致。')
        records.append(record)
    if (len(records) != snapshot['record_count'] or not records
            or len({record['id'] for record in records}) != len(records)
            or canonical_json(records) != canonical_json(sorted(payload['data'], key=lambda record: record['id']))
            or content_hash(payload) != snapshot['content_hash']):
        raise _HistoryUnavailable('快照与模型记录无法可靠对应。')
    started, finished = _timestamp(run['started_at']), _timestamp(run['finished_at'])
    if finished < started or _timestamp(snapshot['collected_at']) > finished:
        raise _HistoryUnavailable('运行与快照的时间关联无法可靠还原。')
    return {'run': run, 'snapshot': snapshot, 'records': records}


def _comparison(status: str, *, before: dict | None = None, after: dict | None = None,
                message: str | None = None) -> dict:
    messages = {'empty': '暂无成功采集数据，缺少比较依据',
                'baseline': '已建立初始基线，暂无历史可比较',
                'ready': '比较最近两次成功采集对应的源站记录',
                'unavailable': '缺少比较依据：无法可靠还原成功采集时间点及其关联数据'}
    return {'status': status, 'before': before, 'after': after,
            'message': message or messages[status]}


def read_dashboard(db_path: str | Path) -> dict:
    """Read a consistent view through SQLite's read-only URI; never create a DB."""
    state = {'records': [], 'snapshot': None, 'latest_attempt': None,
             'last_success': None, 'snapshot_count': 0, 'metric_paths': [],
             'comparison': _comparison('empty')}
    if not Path(db_path).is_file():
        return state
    connection = _connect(db_path, readonly=True)
    try:
        connection.execute('BEGIN')
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'acquisition_runs' not in tables:
            return state
        for key, condition in [('latest_attempt', ''), ('last_success', " AND status = 'success'")]:
            row = connection.execute(
                'SELECT * FROM acquisition_runs WHERE source = ?' + condition + ' ORDER BY id DESC LIMIT 1',
                (SOURCE,),
            ).fetchone()
            state[key] = dict(row) if row else None
        if not {'snapshots', 'model_records'}.issubset(tables):
            state['comparison'] = _comparison('unavailable')
            return state
        state['snapshot_count'] = connection.execute(
            'SELECT COUNT(*) FROM snapshots WHERE source = ?', (SOURCE,)).fetchone()[0]
        if state['last_success'] is None:
            return state
        # Select runs, not distinct snapshots: two identical successful checks
        # are still the latest two time points, even if older content differed.
        runs = [dict(row) for row in connection.execute(
            "SELECT * FROM acquisition_runs WHERE source = ? AND status = 'success' ORDER BY id DESC LIMIT 2",
            (SOURCE,),
        )]
        after = _read_point(connection, runs[0])
        state['snapshot'] = after['snapshot']
        state['metric_paths'] = sorted(after['snapshot']['coverage'])
        state['records'] = after['records']
        state['comparison'] = _comparison('baseline', after=after)
        if len(runs) == 2:
            try:
                before = _read_point(connection, runs[1])
                if _timestamp(after['run']['finished_at']) < _timestamp(before['run']['finished_at']):
                    raise _HistoryUnavailable('成功运行时间与记录顺序不一致。')
                state['comparison'] = _comparison('ready', before=before, after=after)
            except (ValueError, TypeError, KeyError, sqlite3.Error):
                state['comparison'] = _comparison('unavailable', after=after)
        return state
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        state['comparison'] = _comparison('unavailable')
        return state
    finally:
        connection.close()
