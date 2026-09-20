"""Daily orchestration only uses isolated SQLite fixtures and fake transports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from benchmark_dashboard import daily_run
from benchmark_dashboard.acquisition import Acquisition
from benchmark_dashboard.briefing import AnalysisClientError, AnalysisResponse, AnalysisSettings
from benchmark_dashboard.client import SourceError
from benchmark_dashboard.store import read_dashboard, record_success
from benchmark_dashboard.validation import validate_payload

NOW = datetime(2026, 9, 20, 6, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path, payload, monkeypatch):
    source = deepcopy(payload)
    for i in range(1, 4):
        record = deepcopy(payload['data'][0])
        record.update(id=f'synthetic-daily-{i}', name=f'合成日更模型{i}')
        source['data'].append(record)
    valid = validate_payload(source)
    record_success(tmp_path / 'data/dashboard.sqlite3',
                   Acquisition(valid, '2026-09-19T06:00:00+00:00', '2026-09-19T06:01:00+00:00', 200, 1, tmp_path / 'fake.json'))
    (tmp_path / 'daily_config.json').write_text(json.dumps({'enabled': False, 'timezone': 'Asia/Shanghai',
                                                           'output_directory': 'data/daily_runs'}))
    monkeypatch.setattr(daily_run, 'utc_now', lambda: NOW.isoformat())
    return tmp_path, source


class FakeClient:
    def __init__(self, failure=False):
        self.calls = 0
        self.failure = failure

    def generate(self, pack, **kwargs):
        self.calls += 1
        if self.failure:
            raise AnalysisClientError('timeout')
        kinds = {f['kind']: f for f in pack['facts']}
        response = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
                    'fact_hash': pack['fact_hash'], 'model': 'gpt-5.6-sol',
                    'sections': [{'key': section, 'claims': [{'text': kinds[kind]['text'],
                                                               'fact_ids': [kinds[kind]['id']]}]}
                                 for section, kind in [('current', 'coverage'), ('changes', 'changes'), ('limitations', 'limitations')]]}
        return AnalysisResponse(json.dumps(response, ensure_ascii=False), 'gpt-5.6-sol', 'gpt-5.6-sol',
                                usage={'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30},
                                http_status=200, elapsed_seconds=0.1)


def run(root, source, *, client=None, acquire_error=None, **kwargs):
    calls = []
    def acquisition(project, **options):
        calls.append(options['max_attempts'])
        assert options['max_attempts'] == 1
        assert options['api_key'] == 'synthetic-source-key'
        if acquire_error:
            raise acquire_error
        return Acquisition(validate_payload(source), options['started_at'], NOW.isoformat(), 200, 1, root / 'fake.json')
    settings = AnalysisSettings(enabled=True, data_use_confirmed=False, base_url='https://hk.modex-ai.cloud/v1',
                                model='gpt-5.6-sol', api_key='synthetic-analysis-key',
                                purpose_authorized=True, transmission_authorized=True,
                                real_integration_authorized=True, protocol_verified=True)
    params = dict(run_date='2026-09-20', scope_id='synthetic-grant', enable_once=True,
                  allow_analysis=client is not None, confirm_limited_use=client is not None, now=NOW,
                  acquire_fn=acquisition, client_factory=lambda settings: client,
                  source_key_loader=lambda root: 'synthetic-source-key',
                  analysis_settings_loader=lambda root, **kwargs: settings)
    params.update(kwargs)
    result = daily_run.execute_daily(root, **params)
    return result, calls


def test_success_no_change_is_full_scope_and_keeps_history(setup):
    root, source = setup
    client = FakeClient()
    result, calls = run(root, source, client=client)
    assert calls == [1] and client.calls == 1
    assert result['collection'] == {'status': 'success', 'attempts': 1, 'http_status': 200,
                                    'run_id': 2, 'snapshot_id': 1, 'new_snapshot': False}
    assert result['overview']['total_records'] == 4
    assert sum(result['overview']['changes']['counts'].values()) == 0
    assert '无数据变化' in result['overview']['changes']['message']
    assert result['analysis']['status'] == 'generated'
    assert daily_run.read_latest_run(root)['run_id'] == 'daily-2026-09-20'
    backup = root / result['backup_path']
    assert read_dashboard(backup)['last_success']['id'] == 1
    assert read_dashboard(root / 'data/dashboard.sqlite3')['last_success']['id'] == 2


@pytest.mark.parametrize('cache_state', ['missing', 'expired', 'failed'])
def test_scheduled_unchanged_data_skips_ai_without_valid_cache(setup, monkeypatch, cache_state):
    from benchmark_dashboard import daily_briefing

    root, source = setup
    if cache_state != 'missing':
        original_generate = daily_briefing.generate_daily_briefing
        if cache_state == 'expired':
            def generate_expired(*args, **kwargs):
                return original_generate(*args, **kwargs,
                    now=datetime.now(timezone.utc) - timedelta(days=2))
            monkeypatch.setattr(daily_briefing, 'generate_daily_briefing', generate_expired)
        previous, _ = run(root, source, client=FakeClient(failure=cache_state == 'failed'),
                          run_date='2026-09-19', now=NOW - timedelta(days=1), scope_id='earlier-grant')
        assert previous['analysis']['attempts'] == 1
        monkeypatch.setattr(daily_briefing, 'generate_daily_briefing', original_generate)
    protected = {path: path.read_bytes() for path in (root / 'data/daily_briefings').rglob('*.json')}
    def forbidden(*args, **kwargs):
        pytest.fail('unchanged scheduled data loaded analysis config or created a client')
    result, calls = run(root, source, client=FakeClient(), analysis_only_on_change=True,
                        analysis_settings_loader=forbidden, client_factory=forbidden)
    assert calls == [1] and result['collection']['status'] == 'success'
    assert result['analysis']['status'] == 'skipped_no_changes'
    assert result['analysis']['attempts'] == 0 and result['analysis']['cached'] is False
    guard_path = root / daily_run.CONTROL / 'daily-2026-09-20.attempt.json'
    guard = json.loads(guard_path.read_text())
    assert guard['collection_attempts'] == 1 and guard['analysis_attempts'] == 0
    assert guard['analysis_status'] == 'skipped_no_changes'
    assert protected == {path: path.read_bytes() for path in protected}
    with pytest.raises(daily_run.DailyError):
        run(root, source, client=FakeClient(), analysis_only_on_change=True, scope_id='retry-grant')
    assert json.loads(guard_path.read_text()) == guard


def test_scheduled_unchanged_data_reuses_valid_cache_before_skip(setup):
    root, source = setup
    first, _ = run(root, source, client=FakeClient(), run_date='2026-09-19',
                   now=NOW - timedelta(days=1), scope_id='earlier-grant')
    artifact_path = Path(first['analysis']['artifact_path'])
    original = artifact_path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('cached scheduled data loaded analysis config or created a client')
    result, calls = run(root, source, client=FakeClient(), analysis_only_on_change=True,
                        analysis_settings_loader=forbidden, client_factory=forbidden)
    assert calls == [1] and result['analysis']['status'] == 'generated'
    assert result['analysis']['attempts'] == 0 and result['analysis']['cached'] is True
    assert result['analysis']['generated_at'] == first['analysis']['generated_at']
    assert artifact_path.read_bytes() == original


def test_scheduled_changed_data_reuses_matching_semantic_cache(setup):
    root, source = setup
    changed = deepcopy(source)
    changed['data'][-1]['pricing']['price_1m_input_tokens'] = 9
    first, _ = run(root, changed, client=FakeClient(), run_date='2026-09-19',
                   now=NOW - timedelta(days=1), scope_id='earlier-grant')
    record_success(root / 'data/dashboard.sqlite3',
        Acquisition(validate_payload(source), NOW.isoformat(), NOW.isoformat(),
                    200, 1, root / 'fake.json'))
    def forbidden(*args, **kwargs):
        pytest.fail('matching semantic cache triggered analysis config or client')
    result, calls = run(root, changed, client=FakeClient(), analysis_only_on_change=True,
                        analysis_settings_loader=forbidden, client_factory=forbidden)
    assert calls == [1] and result['overview']['changes']['counts']['value'] == 1
    assert result['analysis']['status'] == 'generated'
    assert result['analysis']['attempts'] == 0 and result['analysis']['cached'] is True
    assert result['analysis']['generated_at'] == first['analysis']['generated_at']


@pytest.mark.parametrize('failure', [False, True])
def test_scheduled_changed_data_makes_at_most_one_ai_request(setup, failure):
    root, source = setup
    source['data'][-1]['pricing']['price_1m_input_tokens'] = 9
    client = FakeClient(failure=failure)
    result, calls = run(root, source, client=client, analysis_only_on_change=True)
    assert calls == [1] and client.calls == 1
    assert result['collection']['status'] == 'success'
    assert result['analysis']['attempts'] == 1
    assert (result['analysis']['status'] == 'generated') is not failure
    with pytest.raises(daily_run.DailyError):
        run(root, source, client=client, analysis_only_on_change=True, scope_id='retry-grant')
    assert client.calls == 1


def test_scheduled_return_to_old_snapshot_is_a_change(setup):
    root, source = setup
    changed = deepcopy(source)
    changed['data'][-1]['pricing']['price_1m_input_tokens'] = 9
    record_success(root / 'data/dashboard.sqlite3',
        Acquisition(validate_payload(changed), '2026-09-19T07:00:00+00:00',
                    '2026-09-19T07:01:00+00:00', 200, 1, root / 'fake.json'))
    client = FakeClient()
    result, calls = run(root, source, client=client, analysis_only_on_change=True)
    assert calls == [1] and client.calls == 1
    assert result['collection']['new_snapshot'] is False
    assert result['overview']['changes']['counts']['value'] == 1
    assert result['analysis']['status'] == 'generated'


def test_scheduled_ai_config_failure_preserves_successful_collection_and_day_guard(setup):
    from benchmark_dashboard.analysis_config import AnalysisConfigError

    root, source = setup
    source['data'][-1]['pricing']['price_1m_input_tokens'] = 9
    def missing(*args, **kwargs):
        raise AnalysisConfigError('missing_config')
    client = FakeClient()
    result, calls = run(root, source, client=client, analysis_only_on_change=True,
                        analysis_settings_loader=missing)
    assert calls == [1] and client.calls == 0
    assert result['collection']['status'] == 'success'
    assert result['analysis']['status'] == 'failed' and result['analysis']['attempts'] == 0
    with pytest.raises(daily_run.DailyError):
        run(root, source, client=client, analysis_only_on_change=True, scope_id='retry-grant')


def test_collection_failure_stops_ai_and_preserves_latest_valid(setup):
    root, source = setup
    client = FakeClient()
    result, calls = run(root, source, client=client,
                        acquire_error=SourceError('timeout', '固定测试错误', attempts=1))
    assert calls == [1] and client.calls == 0
    assert result['analysis']['status'] == 'skipped_collection_failed'
    state = read_dashboard(root / 'data/dashboard.sqlite3')
    assert state['last_success']['id'] == 1 and state['latest_attempt']['status'] == 'failed'
    assert state['snapshot_count'] == 1 and len(state['records']) == 4


def test_ai_failure_keeps_successful_changed_full_data(setup):
    root, source = setup
    source['data'][-1]['pricing']['price_1m_input_tokens'] = 9
    result, calls = run(root, source, client=FakeClient(failure=True))
    assert calls == [1]
    assert result['collection']['status'] == 'success' and result['collection']['new_snapshot']
    assert result['analysis']['status'] != 'generated'
    assert result['analysis']['attempts'] == 1
    assert result['overview']['changes']['counts']['value'] == 1
    assert read_dashboard(root / 'data/dashboard.sqlite3')['snapshot_count'] == 2


def test_repeated_day_and_changed_output_cannot_restore_allowance(setup):
    root, source = setup
    result, _ = run(root, source)
    old_guard = (root / daily_run.CONTROL / 'daily-2026-09-20.attempt.json').read_bytes()
    (root / 'daily_config.json').write_text(json.dumps({'enabled': False, 'timezone': 'Asia/Shanghai',
                                                       'output_directory': 'data/another-output'}))
    with pytest.raises(daily_run.DailyError):
        run(root, source, scope_id='different-grant')
    assert (root / daily_run.CONTROL / 'daily-2026-09-20.attempt.json').read_bytes() == old_guard
    assert read_dashboard(root / 'data/dashboard.sqlite3')['last_success']['id'] == 2


def test_pending_global_lock_is_not_auto_cleared(setup):
    root, source = setup
    active = root / daily_run.CONTROL / 'active.lock'
    active.parent.mkdir(parents=True)
    active.write_text('interrupted-run')
    with pytest.raises(daily_run.DailyError):
        run(root, source)
    assert active.read_text() == 'interrupted-run'


@pytest.mark.parametrize('options', [{'enable_once': False}, {'run_date': '2026-09-21'},
                                    {'allow_analysis': True, 'confirm_limited_use': False},
                                    {'analysis_only_on_change': 'true'}])
def test_authorization_prechecks_before_key_loading(setup, options):
    root, source = setup
    def forbidden(root):
        pytest.fail('precheck loaded a key')
    with pytest.raises(daily_run.DailyError):
        run(root, source, source_key_loader=forbidden, **options)
    assert read_dashboard(root / 'data/dashboard.sqlite3')['last_success']['id'] == 1


def test_preview_does_not_touch_keys_config_network_db_or_allowance(setup, monkeypatch):
    root, source = setup
    def forbidden(*args, **kwargs):
        pytest.fail('offline preview reached a side effect')
    for name in ('load_api_key', 'load_analysis_settings', 'load_daily_config', '_write', 'acquire'):
        monkeypatch.setattr(daily_run, name, forbidden)
    import requests
    monkeypatch.setattr(requests, 'Session', forbidden)
    before = (root / 'data/dashboard.sqlite3').read_bytes()
    result = daily_run.preview_daily(root)
    assert result['network_requests'] == 0 and result['overview']['total_records'] == 4
    assert (root / 'data/dashboard.sqlite3').read_bytes() == before
    assert not (root / daily_run.CONTROL).exists()


def test_latest_pointer_tampering_is_rejected(setup):
    root, source = setup
    result, _ = run(root, source)
    path = Path(daily_run.read_latest_run(root)['result_path'])
    path.write_text('{}')
    assert daily_run.read_latest_run(root) is None


def test_backup_sees_committed_wal_content(setup):
    root, source = setup
    db = root / 'data/dashboard.sqlite3'
    with sqlite3.connect(db) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE synthetic_wal (value TEXT)')
        connection.execute("INSERT INTO synthetic_wal VALUES ('committed')")
        connection.commit()
        target = root / 'backup.sqlite3'
        daily_run.backup_database(db, target)
        with sqlite3.connect(target) as backup:
            assert backup.execute('SELECT value FROM synthetic_wal').fetchone()[0] == 'committed'
