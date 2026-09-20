"""M2C CLI tests use temporary roots, synthetic states and injected transports."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from benchmark_dashboard import analysis_config, briefing, modex_client, store
from scripts import generate_briefing as command


ARGS = ['--m2c', '--real-once', '--allow-one-request', '--enable-once',
        '--confirm-purpose', '--confirm-transmission', '--confirm-m2c-scope', '--save-local']


@pytest.fixture(autouse=True)
def prevent_network_private_configuration(monkeypatch):
    monkeypatch.setattr(requests.Session, 'request', Mock(side_effect=AssertionError('No live transport')))
    original = Path.open
    actual = Path(__file__).resolve().parents[1] / '.env'
    def guard(path, *a, **kw):
        if path.resolve() == actual.resolve():
            raise AssertionError('No private configuration in tests')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', guard)


@pytest.fixture
def synthetic_state():
    records = [{'id': f'offline-{i}', 'name': f'离线模型{i}', 'slug': f'offline-{i}',
        'model_creator': {'name': f'离线厂商{i%2}'},
        'pricing': {'price_1m_input_tokens': i + 1, 'price_1m_output_tokens': i + 2},
        'median_output_tokens_per_second': 20 + i,
        'median_time_to_first_token_seconds': 0.5 + i,
        'evaluations': {'artificial_analysis_coding_index': 30 + i}}
        for i in range(4)]
    paths = ['pricing.price_1m_input_tokens', 'pricing.price_1m_output_tokens',
             'median_output_tokens_per_second', 'median_time_to_first_token_seconds',
             'evaluations.artificial_analysis_coding_index']
    snapshot = {'id': 1, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
        'collected_at': '2026-01-01T00:00:00+00:00', 'metadata': {},
        'coverage': dict.fromkeys(paths, 4), 'prompt_options': {}}
    run = {'id': 1, 'source': 'artificial_analysis', 'snapshot_id': 1, 'status': 'success',
           'started_at': '2026-01-01T00:00:00+00:00', 'finished_at': '2026-01-01T00:00:01+00:00'}
    return {'records': records, 'snapshot': snapshot, 'latest_attempt': run, 'last_success': run,
        'metric_paths': paths, 'snapshot_count': 1,
        'comparison': {'status': 'baseline', 'before': None,
                       'after': {'snapshot': snapshot, 'run': run, 'records': records}}}


class Fake:
    def __init__(self, settings):
        self.settings = settings
        self.calls = 0

    def generate(self, pack, *, prompt, timeout_seconds):
        self.calls += 1
        assert self.settings.data_use_confirmed is True
        by_kind = lambda kind: next(f for f in pack['facts'] if f['kind'] == kind)
        sections = [{'key': key, 'claims': [{'text': by_kind(kind)['text'], 'fact_ids': [by_kind(kind)['id']]}]}
            for key, kind in [('current', 'metric_example'), ('changes', 'changes'), ('limitations', 'limitations')]]
        content = json.dumps({'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
            'fact_hash': pack['fact_hash'], 'model': 'gpt-5.6-sol', 'sections': sections}, ensure_ascii=False)
        return briefing.AnalysisResponse(content, 'gpt-5.6-sol', 'gpt-5.6-sol',
            {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}, 200, 0.5)

    def close(self):
        pass


@pytest.mark.parametrize('missing', ['--allow-one-request', '--enable-once', '--confirm-purpose',
    '--confirm-transmission', '--confirm-m2c-scope', '--save-local', '--real-once'])
def test_missing_scoped_flags_stop_before_config_and_database(monkeypatch, missing):
    forbidden = Mock(side_effect=AssertionError('Must stop before private I/O'))
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    monkeypatch.setattr(store, 'read_dashboard', forbidden)
    assert command.main([arg for arg in ARGS if arg != missing]) == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize('ids', [['offline-0', 'offline-1'], ['offline-0', 'offline-1', 'offline-2', 'offline-3']])
def test_scoped_false_old_config_single_call_persists_full_report_and_repeat_blocked(monkeypatch, tmp_path, synthetic_state, ids):
    monkeypatch.setattr(command, 'ROOT', tmp_path)
    old = briefing.AnalysisSettings(enabled=True, data_use_confirmed=False,
        base_url='https://hk.modex-ai.cloud/v1', model='gpt-5.6-sol', api_key='SYNTHETIC_M2C_KEY',
        purpose_authorized=True, transmission_authorized=True,
        real_integration_authorized=True, protocol_verified=True, timeout_seconds=120)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', lambda *a, **kw: old)
    reader = Mock(return_value=synthetic_state)
    monkeypatch.setattr(store, 'read_dashboard', reader)
    fakes = []
    original_client = modex_client.ModexClient
    class RecordingFake(Fake):
        def __init__(self, settings):
            super().__init__(settings)
            self._settings = settings
            self._used = self._closed = False
            fakes.append(self)

        def _preflight(self, pack, prompt, timeout):
            return original_client(self._settings)._preflight(pack, prompt, timeout)
    monkeypatch.setattr(modex_client, 'ModexClient', RecordingFake)
    assert command.main(ARGS + ['--models', *ids]) == 0
    assert old.data_use_confirmed is False
    assert reader.call_count == 1 and sum(f.calls for f in fakes) == 1
    report = (tmp_path / 'reports/M2C_first_real_briefing.md').read_text(encoding='utf-8')
    for text in ('初始基线', 'gpt-5.6-sol', '离线模型0', ids[-1]):
        assert text in report
    assert 'SYNTHETIC_M2C_KEY' not in report
    assert len(list((tmp_path / 'data/analysis_results').glob('*.attempt.json'))) == 1
    command.main(ARGS + ['--models', *ids])
    assert sum(f.calls for f in fakes) == 1
    assert not (tmp_path / 'data/dashboard.sqlite3').exists()


def test_m2c_flags_cannot_enable_legacy_path(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('No config access'))
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    assert command.main(['--real-once', '--confirm-m2c-scope', '--save-local']) == 2
    forbidden.assert_not_called()


def test_actual_serialized_request_size_and_selected_prompt(synthetic_state):
    from benchmark_dashboard.selected_facts import build_selected_fact_pack
    pack = build_selected_fact_pack(synthetic_state, ['offline-0', 'offline-1'])
    settings = briefing.AnalysisSettings(model='gpt-5.6-sol')
    prompt = briefing.build_prompt(pack, settings)
    assert '原值和单位' in prompt and '程序differences' in prompt
    assert '不能省略整个编程维度' in prompt and '速度/延迟0测量含义待确认' in prompt
    body = modex_client.build_request_body(pack, prompt)
    prepared = requests.Request('POST', modex_client.MODEX_ENDPOINT, json=body).prepare()
    assert modex_client.request_body_size(pack, settings) == len(prepared.body)
    assert set(body) == {'model', 'messages', 'stream'} and body['stream'] is False
