"""Scoped attempt and artifact tests use synthetic local state and fake clients."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Barrier

import pytest

from benchmark_dashboard import briefing, m2c_run, metrics
from benchmark_dashboard.briefing import AnalysisClientError, AnalysisResponse, AnalysisSettings
from benchmark_dashboard.m2c_run import (
    SCOPE_ID, get_default_saved_ids, read_current_artifact, render_markdown, run_once, scoped_settings,
)


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
KEY = 'synthetic-m2c-key-only-offline'


@pytest.fixture
def state():
    records = [{'id': f'synthetic-{index}', 'name': f'合成模型 {index}',
                'model_creator': {'id': f'creator-{index % 2}', 'name': f'合成厂商 {index % 2}'},
                'pricing': {'price_1m_input_tokens': index, 'price_1m_output_tokens': 5 - index},
                'evaluations': {'artificial_analysis_intelligence_index': 20 + index,
                                'livecodebench': None},
                'median_output_tokens_per_second': 10 + index} for index in range(5)]
    return {'records': records,
            'snapshot': {'id': 7, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
                         'collected_at': '2026-09-20T00:00:00+00:00', 'metadata': {},
                         'prompt_options': {'prompt_length': 'medium'}},
            'metric_paths': list(metrics.DISPLAY_METRICS), 'latest_attempt': {'status': 'success', 'id': 1},
            'comparison': {'status': 'baseline', 'before': None, 'after': None}}


@pytest.fixture
def settings():
    return AnalysisSettings(enabled=True, data_use_confirmed=False, base_url=m2c_run.BASE_URL,
                            model=m2c_run.MODEL, api_key=KEY, input_mode='real', timeout_seconds=120,
                            purpose_authorized=True, transmission_authorized=True,
                            real_integration_authorized=True, protocol_verified=True)


def ids(state, count=2):
    return [row['id'] for row in state['records'][:count]]


class Client:
    def __init__(self, *, error=None, invalid=False, with_html=False):
        self.calls = 0
        self.error = error
        self.invalid = invalid
        self.with_html = with_html
        self.last_request_body_bytes = 12345

    def generate(self, pack, *, prompt, timeout_seconds):
        self.calls += 1
        if self.error:
            raise self.error
        if self.invalid:
            return AnalysisResponse('{invalid-json', m2c_run.MODEL, m2c_run.MODEL, http_status=200)
        by_kind = {fact['kind']: fact for fact in pack['facts']}
        sections = [{'key': key, 'claims': [{'text': by_kind[kind]['text'], 'fact_ids': [by_kind[kind]['id']]}]}
                    for key, kind in [('current', 'coverage'), ('changes', 'changes'), ('limitations', 'limitations')]]
        if self.with_html:
            sections[0]['claims'][0]['text'] += ' 合成安全展示：<script>alert(1)</script> [链接](https://invalid.test)。'
        content = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
                   'fact_hash': pack['fact_hash'], 'model': m2c_run.MODEL, 'sections': sections}
        return AnalysisResponse(json.dumps(content, ensure_ascii=False), m2c_run.MODEL, m2c_run.MODEL,
                                usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                                http_status=200, elapsed_seconds=1.2)


def run(state, settings, client, root, selected=None, **kwargs):
    return run_once(state, selected or ids(state), settings, client, root=root,
                    confirmed_scope=True, retention_approved=True, request_body_bytes=12000, now=NOW, **kwargs)


@pytest.mark.parametrize('count', [2, 4])
def test_complete_selected_run_persists_full_bound_artifact_and_report(state, settings, tmp_path, count):
    selected = ids(state, count)
    client = Client()
    result = run(state, settings, client, tmp_path, selected)
    assert result['status'] == 'generated', result
    assert client.calls == 1 and result['persisted'] and result['request_reserved']
    artifact = result['artifact']
    assert artifact['selected_ids'] == selected
    assert artifact['fact_pack']['scope']['selected_count'] == count
    assert artifact['request_body_bytes'] == 12345 and artifact['request_body_bytes_source'] == 'transport'
    assert artifact['authorization_scope']['scope_id'] == SCOPE_ID
    assert artifact['generation']['human_review_status'] == 'pending'
    assert result['data_collected_at'] == state['snapshot']['collected_at']
    assert result['http_status'] == 200 and result['usage']['total_tokens'] == 15
    assert Path(result['artifact_path']).exists() and Path(result['attempt_path']).exists()
    attempt = json.loads(Path(result['attempt_path']).read_text(encoding='utf-8'))
    assert attempt['status'] == 'generated'
    assert get_default_saved_ids(tmp_path) == selected
    read = read_current_artifact(state, selected, root=tmp_path, now=NOW)
    assert read['status'] == 'generated' and read['result'] == result['result']
    report = render_markdown(artifact)
    for section in artifact['result']['sections']:
        assert section['claims'][0]['text'] in report
        assert section['claims'][0]['fact_ids'][0] in report
    assert all(row['name'] in report for row in artifact['local_model_mapping'])
    assert KEY not in report + Path(result['artifact_path']).read_text(encoding='utf-8')
    assert not settings.data_use_confirmed  # no permanent grant


@pytest.mark.parametrize('change', [
    {'enabled': False}, {'real_integration_authorized': False}, {'purpose_authorized': False},
    {'transmission_authorized': False}, {'input_mode': 'synthetic'}, {'api_key': ''},
    {'api_key': 'invalid\nheader'}, {'model': 'other'}, {'base_url': 'https://other.invalid'},
    {'protocol_verified': False},
])
def test_missing_independent_gates_prevent_any_attempt_or_client_call(state, settings, tmp_path, change):
    client = Client()
    result = run(state, replace(settings, **change), client, tmp_path)
    assert result['status'] == 'failed' and client.calls == 0
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize(('confirmed', 'retention'), [(False, True), (True, False), (False, False)])
def test_scope_and_local_save_grants_are_both_explicit(state, settings, tmp_path, confirmed, retention):
    client = Client()
    result = run_once(state, ids(state), settings, client, root=tmp_path,
                      confirmed_scope=confirmed, retention_approved=retention, now=NOW)
    assert result['status'] == 'failed' and client.calls == 0 and not (tmp_path / 'data').exists()


def test_only_current_immutable_settings_copy_receives_data_use_override(settings):
    scoped = scoped_settings(settings, confirmed_scope=True, retention_approved=True)
    assert scoped.data_use_confirmed and not settings.data_use_confirmed
    assert replace(scoped, data_use_confirmed=False) == settings


@pytest.mark.parametrize('selected', [[], ['synthetic-0'], ['synthetic-0', 'synthetic-0'],
                                     ['synthetic-0', 'missing'], [f'synthetic-{i}' for i in range(5)]])
def test_invalid_selection_is_stopped_before_files_and_client(state, settings, tmp_path, selected):
    client = Client()
    result = run_once(state, selected, settings, client, root=tmp_path, confirmed_scope=True,
                      retention_approved=True, request_body_bytes=10000, now=NOW)
    assert result['status'] == 'failed' and client.calls == 0 and not (tmp_path / 'data').exists()


@pytest.mark.parametrize('name', [{'invalid': 'name'}, 'x' * (512 * 1024), KEY], ids=['wrong-type', 'oversized', 'sensitive'])
def test_invalid_local_mapping_is_stopped_before_consuming_request(state, settings, tmp_path, name):
    state['records'][0]['name'] = name
    client = Client()
    result = run(state, settings, client, tmp_path)
    assert result['status'] == 'failed' and client.calls == 0
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('target', ['directory', 'reports'])
def test_unwritable_results_or_reports_preflight_uses_zero_calls(state, settings, tmp_path, monkeypatch, target):
    original = m2c_run._probe
    def fail(directory):
        if directory.name == ('analysis_results' if target == 'directory' else 'reports'):
            raise PermissionError('private ' + KEY)
        original(directory)
    monkeypatch.setattr(m2c_run, '_probe', fail)
    client = Client()
    result = run(state, settings, client, tmp_path)
    assert result['status'] == 'failed' and client.calls == 0
    assert not (tmp_path / 'data' / 'analysis_results' / (SCOPE_ID + '.attempt.json')).exists()
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize('error', [AnalysisClientError('timeout'), AnalysisClientError('auth_error'), RuntimeError(KEY)])
def test_failure_consumes_durable_attempt_and_does_not_retry_across_new_clients(state, settings, tmp_path, error):
    first_client = Client(error=error)
    failed = run(state, settings, first_client, tmp_path)
    assert failed['status'] == 'failed' and first_client.calls == 1 and failed['request_reserved']
    assert not Path(failed['artifact_path']).exists()
    second_client = Client()
    second = run(state, settings, second_client, tmp_path)
    assert second['status'] == 'already_attempted' and second_client.calls == 0
    attempt = Path(failed['attempt_path']).read_text(encoding='utf-8')
    assert 'failed' in attempt and KEY not in attempt


def test_invalid_result_never_saved_as_artifact_but_attempt_remains(state, settings, tmp_path):
    result = run(state, settings, Client(invalid=True), tmp_path)
    assert result['status'] == 'failed' and Path(result['attempt_path']).exists()
    assert not Path(result['artifact_path']).exists()
    assert get_default_saved_ids(tmp_path) == []


def test_repeated_success_reads_saved_output_with_zero_additional_generation(state, settings, tmp_path):
    first = run(state, settings, Client(), tmp_path)
    second_client = Client()
    second = run(state, settings, second_client, tmp_path)
    assert second['status'] == 'generated' and second['recovered'] and second_client.calls == 0
    assert second['result'] == first['result']


def test_exclusive_creation_blocks_concurrent_process_equivalent_attempts(state, settings, tmp_path, monkeypatch):
    barrier = Barrier(2)
    original = m2c_run._reserve
    def synchronized(path, attempt):
        barrier.wait(timeout=5)
        return original(path, attempt)
    monkeypatch.setattr(m2c_run, '_reserve', synchronized)
    clients = [Client(), Client()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda client: run(state, settings, client, tmp_path), clients))
    assert sum(client.calls for client in clients) == 1
    assert sorted(result['status'] for result in results) == ['already_attempted', 'generated']


@pytest.mark.parametrize('change', ['selection', 'order', 'snapshot', 'fact', 'name', 'version'])
def test_current_reader_rejects_changed_selection_snapshot_facts_mapping_or_version(state, settings, tmp_path, monkeypatch, change):
    run(state, settings, Client(), tmp_path)
    selected = ids(state)
    if change == 'selection': selected = ids(state, 4)
    elif change == 'order': selected.reverse()
    elif change == 'snapshot': state['snapshot']['content_hash'] = 'b' * 64
    elif change == 'fact': state['records'][0]['pricing']['price_1m_input_tokens'] = 99
    elif change == 'name': state['records'][0]['name'] = 'changed local name'
    elif change == 'version': monkeypatch.setattr(briefing, 'PROMPT_VERSION', 'changed-prompt')
    read = read_current_artifact(state, selected, root=tmp_path, now=NOW)
    assert read['status'] in ('mismatch', 'failed') and read['result'] is None
    assert read['fact_pack'] is None


def test_expired_artifact_is_history_not_current_and_never_refreshes(state, settings, tmp_path):
    run(state, settings, Client(), tmp_path)
    read = read_current_artifact(state, ids(state), root=tmp_path, now=NOW + timedelta(days=1))
    assert read['status'] == 'stale' and read['result'] is None and read['result_stale']
    new_client = Client()
    result = run_once(state, ids(state), settings, new_client, root=tmp_path, confirmed_scope=True,
                      retention_approved=True, request_body_bytes=12000, now=NOW + timedelta(days=1))
    assert result['status'] == 'already_attempted' and new_client.calls == 0


def test_recovery_output_repairs_primary_without_new_request(state, settings, tmp_path, monkeypatch):
    original = m2c_run._atomic_write
    def fail_primary(path, content):
        if path.name == SCOPE_ID + '.json':
            raise PermissionError('simulated one-file save failure')
        return original(path, content)
    monkeypatch.setattr(m2c_run, '_atomic_write', fail_primary)
    first = run(state, settings, Client(), tmp_path)
    assert first['status'] == 'generated' and first['recovered']
    assert Path(first['artifact_path']).name.endswith('.recovery.json')
    monkeypatch.setattr(m2c_run, '_atomic_write', original)
    second_client = Client()
    repaired = run(state, settings, second_client, tmp_path)
    assert repaired['status'] == 'generated' and second_client.calls == 0
    assert Path(repaired['artifact_path']).name == SCOPE_ID + '.json'
    assert repaired['result'] == first['result']


def test_invalid_or_unknown_artifact_fields_do_not_grant_default_selection(state, settings, tmp_path):
    result = run(state, settings, Client(), tmp_path)
    path = Path(result['artifact_path'])
    artifact = json.loads(path.read_text(encoding='utf-8'))
    artifact['unexpected'] = 'private'
    path.write_text(json.dumps(artifact), encoding='utf-8')
    assert get_default_saved_ids(tmp_path) == []
    assert read_current_artifact(state, ids(state), root=tmp_path, now=NOW)['status'] == 'failed'


def test_artifact_reader_does_not_need_settings_grants_client_or_writes(state, settings, tmp_path, monkeypatch):
    first = run(state, settings, Client(), tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('read-only artifact path must not authorize, generate or write')
    monkeypatch.setattr(m2c_run, 'scoped_settings', forbidden)
    monkeypatch.setattr(m2c_run, '_atomic_write', forbidden)
    monkeypatch.setattr(briefing, 'generate_briefing', forbidden)
    read = read_current_artifact(state, ids(state), root=tmp_path, now=NOW)
    assert read['status'] == 'generated' and read['result'] == first['result']
    assert get_default_saved_ids(tmp_path) == ids(state)


def test_report_preserves_plain_text_without_executing_html_or_links(state, settings, tmp_path):
    state['records'][0]['name'] = '<img src=x onerror=alert(1)> [合成](https://invalid.test)'
    result = run(state, settings, Client(with_html=True), tmp_path,
                 selection_method='technical_acceptance_fallback')
    report = render_markdown(result['artifact'])
    assert '<script>' not in report and '<img' not in report
    assert '&lt;script&gt;' in report and '\\[链接\\]' in report
    assert '技术验收样例' in report and '不是最佳模型' in report
    assert result['artifact']['result']['sections'][0]['claims'][0]['text'].find('<script>') >= 0
