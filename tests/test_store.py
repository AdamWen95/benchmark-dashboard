"""Storage acceptance with synthetic payloads and isolated temporary databases."""
from copy import deepcopy
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from benchmark_dashboard import store
from benchmark_dashboard.validation import DataValidationError, validate_payload


def acquisition(payload, *, day=19):
    # Avoid importing the network/config acquisition module into storage tests.
    return SimpleNamespace(valid=validate_payload(payload),
                           started_at=f'2026-09-{day:02d}T08:00:00+00:00',
                           collected_at=f'2026-09-{day:02d}T08:00:01+00:00',
                           http_status=200, attempts=1,
                           snapshot_path=Path('unused-synthetic-snapshot.json'))


def counts(db):
    with sqlite3.connect(db) as connection:
        return {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('snapshots', 'models', 'model_records', 'model_metrics', 'acquisition_runs')}


def test_missing_database_read_does_not_create_anything(tmp_path):
    db = tmp_path / 'missing' / 'dashboard.sqlite3'
    state = store.read_dashboard(db)
    assert state == {'records': [], 'snapshot': None, 'latest_attempt': None,
                     'last_success': None, 'snapshot_count': 0, 'metric_paths': [],
                     'comparison': {'status': 'empty', 'before': None, 'after': None,
                                    'message': '暂无成功采集数据，缺少比较依据'}}
    assert not db.parent.exists()


def test_initialized_database_is_empty_and_read_only(tmp_path, monkeypatch):
    db = tmp_path / 'dashboard with spaces.sqlite3'
    store.initialize(db)
    original = sqlite3.connect
    calls = []

    def connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        return original(database, *args, **kwargs)

    monkeypatch.setattr(store.sqlite3, 'connect', connect)
    before = db.read_bytes()
    assert store.read_dashboard(db)['records'] == []
    assert db.read_bytes() == before
    assert len(calls) == 1
    assert calls[0][0].endswith('?mode=ro')
    assert calls[0][1]['uri'] is True


def test_success_preserves_raw_values_unknown_fields_and_prompt_options(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    payload['response_metadata'] = {'synthetic_version': 'v1'}
    payload['data'][0]['unknown'] = {'exact': ['original', 0, None]}
    payload['data'][0]['evaluations']['unknown_score'] = 0.125
    payload['data'][0]['evaluations']['unknown_metadata'] = {'context': 'synthetic'}
    result = acquisition(payload)
    recorded = store.record_success(db, result)
    state = store.read_dashboard(db)
    assert recorded['new_snapshot'] is True
    assert state['records'] == payload['data']
    assert state['snapshot']['prompt_options'] == payload['prompt_options']
    assert state['snapshot']['content_hash'] == result.valid.content_hash
    assert state['snapshot']['record_count'] == 1
    assert state['snapshot']['coverage']['evaluations.artificial_analysis_intelligence_index'] == 1
    assert 'response.response_metadata' in state['snapshot']['unknown_fields']
    assert 'evaluations.unknown_score' in state['metric_paths']
    assert 'evaluations.unknown_metadata' not in state['metric_paths']
    assert state['snapshot']['warnings']
    assert state['last_success']['status'] == 'success'
    with sqlite3.connect(db) as connection:
        values = dict(connection.execute('SELECT metric_path, value_json FROM model_metrics'))
        assert values['evaluations.artificial_analysis_intelligence_index'] == '0'
        assert values['evaluations.artificial_analysis_math_index'] == 'null'
        assert values['evaluations.unknown_score'] == '0.125'
        assert 'response_metadata' in connection.execute('SELECT payload_json FROM snapshots').fetchone()[0]


def test_duplicate_content_and_reordered_records_add_runs_only(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    second = deepcopy(payload['data'][0])
    second['id'] = 'synthetic-model-2'
    payload['data'].append(second)
    first = store.record_success(db, acquisition(payload))
    initial_counts = counts(db)
    payload['data'].reverse()
    duplicate = store.record_success(db, acquisition(payload, day=20))
    assert duplicate['snapshot_id'] == first['snapshot_id']
    assert duplicate['new_snapshot'] is False
    assert duplicate['run_id'] != first['run_id']
    assert counts(db) == dict(initial_counts, acquisition_runs=2)
    state = store.read_dashboard(db)
    assert state['last_success']['finished_at'].startswith('2026-09-20')
    assert state['snapshot']['collected_at'].startswith('2026-09-19')


def test_changed_name_same_id_and_same_name_different_ids_are_distinct(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    store.record_success(db, acquisition(payload))
    original_id = payload['data'][0]['id']
    payload['data'][0]['name'] = '已更名的合成模型'
    store.record_success(db, acquisition(payload, day=20))
    assert counts(db)['models'] == 1
    second = deepcopy(payload['data'][0])
    second['id'] = 'synthetic-model-2'
    payload['data'].append(second)
    store.record_success(db, acquisition(payload, day=21))
    state = store.read_dashboard(db)
    assert {row['id'] for row in state['records']} == {original_id, second['id']}
    assert len({row['name'] for row in state['records']}) == 1
    assert counts(db)['models'] == 2
    assert state['snapshot_count'] == 3


def test_return_to_older_content_uses_latest_successful_run(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    original = deepcopy(payload)
    first = store.record_success(db, acquisition(original))
    payload['data'][0]['name'] = '暂时变更'
    store.record_success(db, acquisition(payload, day=20))
    again = store.record_success(db, acquisition(original, day=21))
    state = store.read_dashboard(db)
    assert again['new_snapshot'] is False
    assert state['snapshot']['id'] == first['snapshot_id']
    assert state['records'] == original['data']
    assert state['snapshot_count'] == 2


def test_prompt_configuration_and_unknown_metadata_are_part_of_content_version(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    first = store.record_success(db, acquisition(payload))
    payload['prompt_options']['parallel_queries'] = 2
    second = store.record_success(db, acquisition(payload, day=20))
    payload['source_revision'] = {'synthetic': 'revision-2'}
    third = store.record_success(db, acquisition(payload, day=21))
    assert len({item['snapshot_id'] for item in (first, second, third)}) == 3
    state = store.read_dashboard(db)
    assert state['snapshot']['prompt_options']['parallel_queries'] == 2
    assert state['snapshot_count'] == 3
    assert state['records'] == payload['data']


def test_failed_attempt_keeps_last_valid_data(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    success = store.record_success(db, acquisition(payload))
    initial_counts = counts(db)
    failed_id = store.record_failure(db, started_at='2026-09-20T08:00:00+00:00',
        finished_at='2026-09-20T08:00:01+00:00', error_code='unauthorized',
        message='鉴权失败，请检查本机配置。', http_status=401, attempts=1)
    state = store.read_dashboard(db)
    assert state['latest_attempt']['id'] == failed_id
    assert state['latest_attempt']['status'] == 'failed'
    assert state['latest_attempt']['snapshot_id'] is None
    assert state['last_success']['id'] == success['run_id']
    assert state['records'] == payload['data']
    assert counts(db) == dict(initial_counts, acquisition_runs=2)


def test_failure_before_first_success_has_no_models(tmp_path):
    db = tmp_path / 'dashboard.sqlite3'
    store.record_failure(db, started_at='start', finished_at='end',
                         error_code='timeout', message='连接超时。', attempts=3)
    state = store.read_dashboard(db)
    assert state['records'] == []
    assert state['snapshot'] is None
    assert state['last_success'] is None
    assert state['latest_attempt']['error_code'] == 'timeout'


def test_revalidates_public_acquisition_and_ignores_cached_metadata(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    result = acquisition(payload)
    expected_hash = result.valid.content_hash
    result.valid.content_hash = 'untrusted cached hash'
    result.valid.coverage = {}
    result.valid.records = []
    store.record_success(db, result)
    state = store.read_dashboard(db)
    assert state['snapshot']['content_hash'] == expected_hash
    assert state['records'] == payload['data']
    assert state['snapshot']['coverage']


def test_invalid_payload_cannot_pollute_saved_history(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    store.record_success(db, acquisition(payload))
    before = counts(db)
    bad = acquisition(deepcopy(payload))
    bad.valid.payload['data'][0]['evaluations']['gpqa'] = 'invalid'
    with pytest.raises(DataValidationError):
        store.record_success(db, bad)
    assert counts(db) == before
    assert store.read_dashboard(db)['records'] == payload['data']


def test_failure_mid_transaction_rolls_back_snapshot_records_and_identity(tmp_path, payload):
    db = tmp_path / 'dashboard.sqlite3'
    store.record_success(db, acquisition(payload))
    before = counts(db)
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON model_metrics
                              BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END""")
    changed = deepcopy(payload)
    changed['data'][0]['id'] = 'synthetic-new-id'
    with pytest.raises(sqlite3.IntegrityError, match='synthetic disk failure'):
        store.record_success(db, acquisition(changed, day=20))
    assert counts(db) == before
    assert store.read_dashboard(db)['records'] == payload['data']
