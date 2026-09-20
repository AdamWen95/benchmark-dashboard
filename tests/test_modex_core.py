"""Modex core extension checks: fixed synthetic facts and fake clients only."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from benchmark_dashboard import briefing
from benchmark_dashboard.briefing import (
    AnalysisClientError, AnalysisResponse, AnalysisSettings, check_settings,
    generate_briefing, read_state, read_synthetic_state,
)
from benchmark_dashboard.synthetic_briefing import build_synthetic_pack


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
FAKE_SECRET = 'offline-modex-key-never-a-real-credential'


@pytest.fixture
def settings():
    return AnalysisSettings(enabled=True, base_url='https://hk.modex-ai.cloud/v1', model='gpt-5.6-sol',
                            api_key=FAKE_SECRET, protocol_verified=True, real_integration_authorized=True,
                            input_mode='synthetic', timeout_seconds=120)


def body(pack, model):
    by_kind = {fact['kind']: fact for fact in pack['facts']}
    value = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
             'fact_hash': pack['fact_hash'], 'model': model,
             'sections': [{'key': key, 'claims': [{'text': by_kind[kind]['text'],
                                                  'fact_ids': [by_kind[kind]['id']]}]}
                          for key, kind in [('current', 'coverage'), ('changes', 'changes'),
                                            ('limitations', 'limitations')]]}
    return json.dumps(value, ensure_ascii=False)


class Client:
    def __init__(self, settings, *, error=None, metadata=None):
        self.settings = settings
        self.error = error
        self.metadata = metadata or {}
        self.calls = 0

    def generate(self, pack, *, prompt, timeout_seconds):
        self.calls += 1
        assert timeout_seconds == self.settings.timeout_seconds
        assert '一条简短中文' in prompt
        if self.error:
            raise self.error
        fields = {'content': body(pack, self.settings.model), 'request_model': self.settings.model,
                  'response_model': self.settings.model, 'usage': None, 'http_status': 200,
                  'elapsed_seconds': 1.25, **self.metadata}
        return AnalysisResponse(**fields)


def rehash(pack):
    plain = {key: value for key, value in pack.items() if key != 'fact_hash'}
    pack['fact_hash'] = sha256(json.dumps(plain, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return pack


def test_synthetic_skips_real_data_grants_but_does_not_mutate_them(settings):
    assert not settings.data_use_confirmed and not settings.purpose_authorized and not settings.transmission_authorized
    assert check_settings(settings) is None
    client = Client(settings)
    state = generate_briefing(build_synthetic_pack(), settings, client, now=NOW)
    assert state['status'] == 'generated' and client.calls == 1
    assert state['input_mode'] == 'synthetic' and not state['persisted'] and not state['cache_hit']
    assert not settings.data_use_confirmed and not settings.purpose_authorized and not settings.transmission_authorized


@pytest.mark.parametrize(('change', 'status'), [
    ({'enabled': False}, 'disabled'),
    ({'real_integration_authorized': False}, 'authorization_required'),
    ({'api_key': ''}, 'config_missing'),
    ({'protocol_verified': False}, 'protocol_unverified'),
    ({'input_mode': 'unknown'}, 'config_missing'),
    ({'input_mode': 'real'}, 'purpose_unconfirmed'),
])
def test_synthetic_still_requires_explicit_enable_single_call_grant_and_config(settings, change, status):
    changed = replace(settings, **change)
    client = Client(changed)
    assert generate_briefing(build_synthetic_pack(), changed, client, now=NOW)['status'] == status
    assert not client.calls


@pytest.mark.parametrize('alteration', ['source', 'text', 'extra_fact', 'scope', 'snapshot'])
def test_synthetic_mode_requires_the_entire_fixed_pack_not_a_spoofed_source(settings, alteration):
    pack = build_synthetic_pack()
    if alteration == 'source': pack['source'] = 'artificial_analysis'
    elif alteration == 'text': pack['facts'][0]['text'] += '混入真实数据。'
    elif alteration == 'extra_fact': pack['facts'].append({'id': 'extra', 'kind': 'coverage', 'text': '额外数据。'})
    elif alteration == 'scope': pack['scope']['extra'] = 'private'
    elif alteration == 'snapshot': pack['snapshot']['content_hash'] = 'c' * 64
    rehash(pack)
    client = Client(settings)
    assert generate_briefing(pack, settings, client, now=NOW)['status'] == 'failed'
    assert client.calls == 0


def test_real_mode_rejects_fixed_synthetic_source(settings):
    real = replace(settings, input_mode='real', data_use_confirmed=True,
                   purpose_authorized=True, transmission_authorized=True)
    client = Client(real)
    assert generate_briefing(build_synthetic_pack(), real, client, now=NOW)['status'] == 'failed'
    assert client.calls == 0


def test_memory_only_generation_never_touches_cache_files(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('memory-only generation must not read or write cache files')
    monkeypatch.setattr(briefing, '_load_cache', forbidden)
    monkeypatch.setattr(briefing, '_save', forbidden)
    result = generate_briefing(build_synthetic_pack(), settings, Client(settings), now=NOW)
    assert result['status'] == 'generated' and not result['persisted']
    assert result['usage'] is None and result['cost'] is None
    assert result['request_model'] == result['response_model'] == settings.model
    assert result['http_status'] == 200 and result['elapsed_seconds'] == 1.25
    assert '未写入缓存' in result['message']
    assert read_state(build_synthetic_pack(), settings, now=NOW)['status'] == 'not_generated'


@pytest.mark.parametrize('nested', [False, True])
def test_synthetic_refuses_real_results_directory_before_call(settings, tmp_path, nested):
    directory = tmp_path / 'data' / 'analysis_results'
    if nested:
        directory /= 'nested'
    client = Client(settings)
    result = generate_briefing(build_synthetic_pack(), settings, client, cache_dir=directory, now=NOW)
    assert result['status'] == 'failed' and client.calls == 0 and not directory.exists()


def test_metadata_and_usage_round_trip_through_isolated_cache(settings, tmp_path):
    usage = {'prompt_tokens': 10, 'completion_tokens': 0, 'total_tokens': 10, 'ignored_details': {'raw': 'discard'}}
    initial = generate_briefing(build_synthetic_pack(), settings, Client(settings, metadata={'usage': usage}),
                                cache_dir=tmp_path, now=NOW)
    expected = {'prompt_tokens': 10, 'completion_tokens': 0, 'total_tokens': 10}
    assert initial['usage'] == expected and initial['result_metadata']['usage'] == expected
    assert initial['persisted']
    cached = read_state(build_synthetic_pack(), settings, cache_dir=tmp_path, now=NOW)
    assert cached['status'] == 'generated' and cached['usage'] == expected and cached['cache_hit']
    saved = next(tmp_path.glob('*.json')).read_text(encoding='utf-8')
    assert FAKE_SECRET not in saved and 'ignored_details' not in saved
    assert initial['cost'] is None


def test_partial_usage_preserves_unknown_fields_without_guessing_total(settings):
    result = generate_briefing(build_synthetic_pack(), settings,
                               Client(settings, metadata={'usage': {'prompt_tokens': 7}}), now=NOW)
    assert result['status'] == 'generated' and result['usage'] == {'prompt_tokens': 7}
    assert result['cost'] is None


@pytest.mark.parametrize('metadata', [
    {'usage': {'prompt_tokens': True}}, {'usage': {'completion_tokens': -1}},
    {'usage': {'total_tokens': '10'}}, {'usage': []}, {'http_status': True},
    {'http_status': 700}, {'elapsed_seconds': float('nan')}, {'elapsed_seconds': -1},
    {'elapsed_seconds': 10 ** 1000},
    {'response_model': '<html>private</html>'},
])
def test_malformed_response_metadata_is_rejected_without_publish(settings, metadata):
    client = Client(settings, metadata=metadata)
    state = generate_briefing(build_synthetic_pack(), settings, client, now=NOW)
    assert state['status'] == 'failed' and state['error_code'] == 'invalid_response'
    assert state['result'] is None and client.calls == 1 and not state['persisted']


def test_response_model_mismatch_stops_publication_but_records_both_names(settings):
    client = Client(settings, metadata={'response_model': 'different-channel-model'})
    state = generate_briefing(build_synthetic_pack(), settings, client, now=NOW)
    assert state['status'] == 'failed' and state['error_code'] == 'model_mismatch'
    assert state['result'] is None and state['request_model'] == settings.model
    assert state['response_model'] == 'different-channel-model' and state['http_status'] == 200
    assert client.calls == 1


@pytest.mark.parametrize('code', sorted(briefing.ERROR_MESSAGES))
def test_safe_error_categories_never_retry(settings, code):
    error = AnalysisClientError(code, http_status=429, elapsed_seconds=2.5, response_model=settings.model)
    client = Client(settings, error=error)
    state = generate_briefing(build_synthetic_pack(), settings, client, now=NOW)
    assert state['status'] == 'failed' and state['error_code'] == code and state['result'] is None
    assert state['http_status'] == 429 and state['elapsed_seconds'] == 2.5
    assert client.calls == 1


def test_error_constructor_and_core_never_echo_unknown_error_text_or_key(settings, tmp_path, capsys):
    error = AnalysisClientError('raw body: ' + FAKE_SECRET, http_status='401',
                                elapsed_seconds=True, response_model=FAKE_SECRET)
    assert error.code == 'client_error' and FAKE_SECRET not in str(error) + repr(error)
    assert error.http_status is None and error.elapsed_seconds is None
    state = generate_briefing(build_synthetic_pack(), settings, Client(settings, error=error),
                              cache_dir=tmp_path, now=NOW)
    assert state['response_model'] is None
    output = json.dumps(state) + capsys.readouterr().out + next(tmp_path.glob('*.json')).read_text(encoding='utf-8')
    assert FAKE_SECRET not in output


def test_mutated_client_error_still_cannot_publish_arbitrary_error_code(settings, tmp_path):
    error = AnalysisClientError('auth_error')
    error.code = FAKE_SECRET
    result = generate_briefing(build_synthetic_pack(), settings, Client(settings, error=error),
                               cache_dir=tmp_path, now=NOW)
    assert result['status'] == 'failed' and result['error_code'] == 'client_error'
    assert FAKE_SECRET not in json.dumps(result)
    assert FAKE_SECRET not in next(tmp_path.glob('*.json')).read_text(encoding='utf-8')


def test_failed_refresh_preserves_success_metadata_separately(settings, tmp_path):
    initial = generate_briefing(build_synthetic_pack(), settings,
                                Client(settings, metadata={'usage': {'total_tokens': 8}}),
                                cache_dir=tmp_path, now=NOW)
    error = AnalysisClientError('auth_error', http_status=401, elapsed_seconds=0.5)
    failed = generate_briefing(build_synthetic_pack(), settings, Client(settings, error=error),
                               cache_dir=tmp_path, now=NOW + timedelta(seconds=1), force_refresh=True)
    assert failed['status'] == 'failed' and failed['http_status'] == 401 and failed['usage'] is None
    assert failed['result_metadata']['http_status'] == 200 and failed['result_metadata']['usage'] == {'total_tokens': 8}
    assert failed['result'] == initial['result'] and failed['generated_at'] == initial['generated_at']
    assert read_synthetic_state(cache_dir=tmp_path, now=NOW + timedelta(seconds=2))['status'] == 'failed'


def test_synthetic_view_is_read_only_without_keys_or_fake_authorization(settings, tmp_path, monkeypatch):
    initial = generate_briefing(build_synthetic_pack(), settings, Client(settings), cache_dir=tmp_path, now=NOW)
    def forbidden(*args, **kwargs):
        pytest.fail('synthetic viewer cannot authorize or generate')
    monkeypatch.setattr(briefing, '_gate', forbidden)
    monkeypatch.setattr(briefing, 'generate_briefing', forbidden)
    monkeypatch.setattr(briefing, '_save', forbidden)
    read = read_synthetic_state(cache_dir=tmp_path, now=NOW)
    assert read['status'] == 'generated' and read['result'] == initial['result']
    assert read['input_mode'] == 'synthetic' and read['persisted']


def test_missing_or_corrupt_synthetic_cache_never_creates_files(settings, tmp_path):
    missing = tmp_path / 'missing'
    assert read_synthetic_state(cache_dir=missing, now=NOW)['status'] == 'not_generated'
    assert not missing.exists()
    generate_briefing(build_synthetic_pack(), settings, Client(settings), cache_dir=tmp_path, now=NOW)
    path = next(tmp_path.glob('*.json'))
    envelope = json.loads(path.read_text(encoding='utf-8'))
    envelope['response_model'] = 'forged-channel'
    path.write_text(json.dumps(envelope), encoding='utf-8')
    before = path.read_bytes()
    assert read_synthetic_state(cache_dir=tmp_path, now=NOW)['status'] == 'failed'
    assert path.read_bytes() == before
