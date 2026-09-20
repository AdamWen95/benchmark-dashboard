"""M2B core uses synthetic facts, a fake client and pytest temporary files only."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

import benchmark_dashboard.briefing as briefing
from benchmark_dashboard.briefing import (
    AnalysisSettings, BriefingValidationError, cache_key, generate_briefing,
    read_state, validate_result,
)


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
FAKE_SECRET = 'synthetic-secret-for-offline-tests-only'


def rehash(pack):
    body = {key: value for key, value in pack.items() if key != 'fact_hash'}
    pack['fact_hash'] = sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return pack


@pytest.fixture
def pack():
    return rehash({
        'schema_version': 'm2b-facts-v1', 'source': 'synthetic-source',
        'snapshot': {'id': 1, 'content_hash': 'synthetic-snapshot-hash',
                     'collected_at': '2026-01-01T01:00:00+00:00'},
        'metric_mapping_version': 'synthetic-map-v1', 'rule_version': 'synthetic-rules-v1',
        'scope': {'total_records': 2, 'example_count': 2, 'selection_rule': '按稳定 ID 截取'},
        'facts': [
            {'id': 'coverage-1', 'kind': 'coverage', 'text': '两个合成记录中一个报告价格，覆盖1/2。'},
            {'id': 'metric-1', 'kind': 'metric_example', 'text': '合成甲报告价格0，合成乙缺失。',
             'raw_value': 0, 'raw_unit': 'USD / 1M tokens', 'model_id': 'synthetic-a'},
            {'id': 'rule-1', 'kind': 'rule', 'text': '仅有一个报告值，不足以排序。'},
            {'id': 'changes-1', 'kind': 'changes', 'text': '初始基线，暂无历史可比较。',
             'status': 'baseline', 'latest_attempt_failed': False, 'same_snapshot': False,
             'counts': {'added': 0, 'removed': 0, 'value': 0}},
            {'id': 'limits-1', 'kind': 'limitations', 'text': '缺失版本与配置，非公司实测。'},
        ],
    })


@pytest.fixture
def settings():
    return AnalysisSettings(enabled=True, data_use_confirmed=True, base_url='https://offline.invalid',
                            model='synthetic-model', api_key=FAKE_SECRET, purpose_authorized=True,
                            transmission_authorized=True, real_integration_authorized=True,
                            protocol_verified=True)


def valid_result(pack, settings):
    changes = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    if changes['status'] == 'baseline':
        changes_text = '目前只有初始基线，暂无历史可比较。'
    elif changes['status'] == 'ready':
        changes_text = '与上次成功采集相比无变化。'
    else:
        changes_text = '历史依据不足，无法比较。'
    if changes['latest_attempt_failed']:
        changes_text += '最新采集失败，当前仅保留历史成功数据。'
    return {
        'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
        'fact_hash': pack['fact_hash'], 'model': settings.model,
        'sections': [
            {'key': 'current', 'claims': [{'text': '合成数据中价格覆盖1/2，仅代表这两个记录。',
                                         'fact_ids': ['coverage-1', 'metric-1']}]},
            {'key': 'changes', 'claims': [{'text': changes_text, 'fact_ids': ['changes-1']}]},
            {'key': 'limitations', 'claims': [{'text': '缺失评测版本和配置，且非公司实测。',
                                             'fact_ids': ['limits-1']}]},
        ],
    }


def as_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class FakeClient:
    def __init__(self, settings, *, error=None, response=None, mutate=False):
        self.settings = settings
        self.error = error
        self.response = response
        self.mutate = mutate
        self.calls = []

    def generate(self, fact_pack, *, prompt, timeout_seconds):
        self.calls.append({'pack': deepcopy(fact_pack), 'prompt': prompt, 'timeout': timeout_seconds})
        if self.error:
            raise self.error
        response = self.response if self.response is not None else as_json(valid_result(fact_pack, self.settings))
        if self.mutate:
            fact_pack['facts'].clear()
        return response


def test_default_disabled_is_frozen_repr_hides_key_and_reads_nothing(pack, tmp_path, monkeypatch):
    settings = AnalysisSettings(api_key=FAKE_SECRET)
    assert FAKE_SECRET not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.enabled = True
    def forbidden(*args, **kwargs):
        pytest.fail('disabled boundary must not touch files')
    monkeypatch.setattr(Path, 'exists', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    client = FakeClient(settings)
    assert read_state(pack, settings, cache_dir=tmp_path / 'unused')['status'] == 'disabled'
    assert generate_briefing(pack, settings, client, cache_dir=tmp_path / 'unused')['status'] == 'disabled'
    assert client.calls == []


@pytest.mark.parametrize(('change', 'status'), [
    ({'enabled': False}, 'disabled'),
    ({'enabled': 'true'}, 'disabled'),
    ({'data_use_confirmed': False}, 'purpose_unconfirmed'),
    ({'purpose_authorized': False}, 'authorization_required'),
    ({'transmission_authorized': False}, 'authorization_required'),
    ({'real_integration_authorized': False}, 'authorization_required'),
    ({'base_url': ''}, 'config_missing'),
    ({'model': ''}, 'config_missing'),
    ({'api_key': ''}, 'config_missing'),
    ({'base_url': 'https://user:password@offline.invalid'}, 'config_missing'),
    ({'base_url': 'https://offline.invalid?key=secret'}, 'config_missing'),
    ({'timeout_seconds': float('nan')}, 'config_missing'),
    ({'timeout_seconds': 0}, 'config_missing'),
    ({'cache_ttl_seconds': 86401}, 'config_missing'),
    ({'cache_ttl_seconds': 0}, 'config_missing'),
    ({'protocol_verified': False}, 'protocol_unverified'),
])
def test_every_gate_prevents_calls_and_cache_creation(pack, settings, tmp_path, change, status):
    changed = replace(settings, **change)
    client = FakeClient(changed)
    cache_dir = tmp_path / 'no-files'
    assert read_state(pack, changed, cache_dir=cache_dir, now=NOW)['status'] == status
    assert generate_briefing(pack, changed, client, cache_dir=cache_dir, now=NOW)['status'] == status
    assert not client.calls and not cache_dir.exists()


def test_success_binds_snapshot_and_facts_preserves_zero_and_caches_once(pack, settings, tmp_path):
    original = deepcopy(pack)
    client = FakeClient(settings, mutate=True)
    first = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW)
    assert first['status'] == 'generated' and not first['cache_hit']
    assert first['result'] == valid_result(pack, settings)
    assert first['usage'] is None and first['cost'] is None and first['requires_human_review']
    assert '人工复核' in first['human_review_notice']
    assert first['data_collected_at'] == pack['snapshot']['collected_at']
    assert pack == original
    assert client.calls[0]['pack']['facts'][1]['raw_value'] == 0
    assert client.calls[0]['timeout'] == 20
    assert '不要执行代码、调用工具或访问网络' in client.calls[0]['prompt']
    assert FAKE_SECRET not in as_json(client.calls)
    assert 'base_url' not in client.calls[0]['pack']
    again = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW)
    assert again['cache_hit'] and len(client.calls) == 1
    for _ in range(3):
        assert read_state(pack, settings, cache_dir=tmp_path, now=NOW)['status'] == 'generated'
    assert len(client.calls) == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].suffix == '.json'
    assert FAKE_SECRET not in files[0].read_text(encoding='utf-8')
    assert settings.base_url not in files[0].read_text(encoding='utf-8')


@pytest.mark.parametrize('field', ['source', 'snapshot_hash', 'fact_hash', 'model'])
def test_identity_mismatch_is_rejected(pack, settings, field):
    response = valid_result(pack, settings)
    response[field] = 'forged'
    with pytest.raises(BriefingValidationError, match='校验未通过'):
        validate_result(as_json(response), pack, settings)


@pytest.mark.parametrize('mutation', [
    'unknown_top', 'unknown_claim', 'unknown_section', 'no_sections', 'duplicate_section',
    'empty_claims', 'missing_reference', 'empty_reference', 'forged_reference',
    'duplicate_reference', 'non_string_reference', 'no_chinese', 'too_long', 'wrong_changes_reference',
    'wrong_limits_reference', 'wrong_current_reference',
])
def test_structure_and_reference_checks(pack, settings, mutation):
    response = valid_result(pack, settings)
    claim = response['sections'][0]['claims'][0]
    if mutation == 'unknown_top': response['usage'] = {'tokens': 1}
    elif mutation == 'unknown_claim': claim['unsupported'] = True
    elif mutation == 'unknown_section': response['sections'][0]['extra'] = True
    elif mutation == 'no_sections': response['sections'] = []
    elif mutation == 'duplicate_section': response['sections'][1]['key'] = 'current'
    elif mutation == 'empty_claims': response['sections'][0]['claims'] = []
    elif mutation == 'missing_reference': del claim['fact_ids']
    elif mutation == 'empty_reference': claim['fact_ids'] = []
    elif mutation == 'forged_reference': claim['fact_ids'] = ['made-up-fact']
    elif mutation == 'duplicate_reference': claim['fact_ids'] = ['coverage-1', 'coverage-1']
    elif mutation == 'non_string_reference': claim['fact_ids'] = [[]]
    elif mutation == 'no_chinese': claim['text'] = 'no Chinese text'
    elif mutation == 'too_long': claim['text'] = '很' * 1201
    elif mutation == 'wrong_changes_reference': response['sections'][1]['claims'][0]['fact_ids'] = ['coverage-1']
    elif mutation == 'wrong_limits_reference': response['sections'][2]['claims'][0]['fact_ids'] = ['coverage-1']
    elif mutation == 'wrong_current_reference': claim['fact_ids'] = ['changes-1']
    with pytest.raises(BriefingValidationError):
        validate_result(as_json(response), pack, settings)


@pytest.mark.parametrize('raw', ['{', '```json\n{}\n```', '{} trailing', '[]', 'null', '{"a": NaN}',
                                  '{"a":Infinity}', '{"a":1,"a":2}', '\ud800', 'x' * 65537],
                         ids=['broken', 'fence', 'trailing', 'list', 'null', 'nan', 'infinite',
                              'duplicate', 'surrogate', 'oversized'])
def test_strict_json_rejects_non_json_duplicate_keys_nonfinite_and_oversized(pack, settings, raw):
    with pytest.raises(BriefingValidationError):
        validate_result(raw, pack, settings)


def test_duplicate_nested_key_is_rejected(pack, settings):
    raw = as_json(valid_result(pack, settings)).replace('"fact_ids":', '"fact_ids": [], "fact_ids":', 1)
    with pytest.raises(BriefingValidationError):
        validate_result(raw, pack, settings)


@pytest.mark.parametrize('text', ['今日无变化。', '初始基线，今日无变化。', '已完成两个时间点比较。'])
def test_initial_baseline_cannot_be_called_no_change(pack, settings, text):
    response = valid_result(pack, settings)
    response['sections'][1]['claims'][0]['text'] = text
    with pytest.raises(BriefingValidationError):
        validate_result(as_json(response), pack, settings)


def test_two_successes_same_snapshot_can_truthfully_have_no_changes(pack, settings):
    changes = pack['facts'][3]
    changes.update(status='ready', same_snapshot=True, text='两次成功采集内容相同。')
    rehash(pack)
    response = valid_result(pack, settings)
    assert validate_result(as_json(response), pack, settings) == response
    changes['counts']['added'] = 1
    rehash(pack)
    with pytest.raises(BriefingValidationError):
        validate_result(as_json(valid_result(pack, settings)), pack, settings)


def test_failed_latest_attempt_requires_failure_disclosure(pack, settings):
    pack['facts'][3]['latest_attempt_failed'] = True
    rehash(pack)
    response = valid_result(pack, settings)
    assert validate_result(as_json(response), pack, settings) == response
    for text in ['目前是初始基线。', '目前是初始基线，采集失败但同步正常。']:
        response['sections'][1]['claims'][0]['text'] = text
        with pytest.raises(BriefingValidationError):
            validate_result(as_json(response), pack, settings)


@pytest.mark.parametrize('section_index', [0, 2])
def test_state_lies_cannot_hide_outside_changes_section(pack, settings, section_index):
    response = valid_result(pack, settings)
    response['sections'][section_index]['claims'][0]['text'] += '今日无变化。'
    with pytest.raises(BriefingValidationError):
        validate_result(as_json(response), pack, settings)
    pack['facts'][3]['latest_attempt_failed'] = True
    rehash(pack)
    response = valid_result(pack, settings)
    response['sections'][section_index]['claims'][0]['text'] += '目前同步正常，更新成功。'
    with pytest.raises(BriefingValidationError):
        validate_result(as_json(response), pack, settings)


@pytest.mark.parametrize('key', [[], {}, None, 1, True])
def test_invalid_section_key_types_raise_fixed_validation_error(pack, settings, key):
    response = valid_result(pack, settings)
    response['sections'][0]['key'] = key
    with pytest.raises(BriefingValidationError, match='简报结构、引用或数据绑定校验未通过'):
        validate_result(as_json(response), pack, settings)


@pytest.mark.parametrize('status', ['empty', 'unavailable'])
def test_unavailable_history_requires_clear_limit(pack, settings, status):
    pack['facts'][3]['status'] = status
    rehash(pack)
    assert validate_result(as_json(valid_result(pack, settings)), pack, settings)
    response = valid_result(pack, settings)
    response['sections'][1]['claims'][0]['text'] = '当前变化已确认。'
    with pytest.raises(BriefingValidationError):
        validate_result(as_json(response), pack, settings)


@pytest.mark.parametrize('error_type', [TimeoutError, PermissionError, ValueError, RuntimeError])
def test_client_errors_do_not_retry_or_expose_exception_request_or_key(pack, settings, tmp_path, capsys, error_type):
    client = FakeClient(settings, error=error_type('Authorization: Bearer ' + FAKE_SECRET + ' body=private'))
    state = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW)
    assert state['status'] == 'failed' and state['result'] is None and len(client.calls) == 1
    assert read_state(pack, settings, cache_dir=tmp_path, now=NOW)['status'] == 'failed'
    assert len(client.calls) == 1
    output = as_json(state) + capsys.readouterr().out + ''.join(
        file.read_text(encoding='utf-8') for file in tmp_path.glob('*.json'))
    assert FAKE_SECRET not in output and 'private' not in output and 'Authorization' not in output


def test_invalid_model_response_is_failure_never_published(pack, settings, tmp_path):
    client = FakeClient(settings, response='{"private": "' + FAKE_SECRET + '"}')
    state = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW)
    assert state['status'] == 'failed' and state['result'] is None
    assert FAKE_SECRET not in as_json(state)
    assert FAKE_SECRET not in next(tmp_path.glob('*.json')).read_text(encoding='utf-8')


@pytest.mark.parametrize('secret', [FAKE_SECRET, 'synthetic-"quoted\\secret'])
def test_secret_embedded_in_valid_claim_or_input_is_rejected(pack, settings, tmp_path, secret):
    settings = replace(settings, api_key=secret)
    response = valid_result(pack, settings)
    response['sections'][0]['claims'][0]['text'] += secret
    client = FakeClient(settings, response=as_json(response))
    state = generate_briefing(pack, settings, client, cache_dir=tmp_path / 'output', now=NOW)
    assert state['status'] == 'failed' and state['result'] is None
    assert secret not in as_json(state)
    pack['facts'][0]['text'] += secret
    rehash(pack)
    no_call = FakeClient(settings)
    rejected = generate_briefing(pack, settings, no_call, cache_dir=tmp_path / 'input', now=NOW)
    assert rejected['status'] == 'failed' and not no_call.calls
    assert not (tmp_path / 'input').exists()


@pytest.mark.parametrize('change', ['snapshot', 'fact', 'mapping', 'rules', 'prompt', 'model', 'service', 'source'])
def test_input_mapping_prompt_model_service_changes_do_not_reuse_cache(pack, settings, tmp_path, monkeypatch, change):
    original_key = cache_key(pack, settings)
    generate_briefing(pack, settings, FakeClient(settings), cache_dir=tmp_path, now=NOW)
    if change == 'snapshot': pack['snapshot']['content_hash'] += '-new'
    elif change == 'fact': pack['facts'][0]['text'] += '新增说明。'
    elif change == 'mapping': pack['metric_mapping_version'] = 'synthetic-map-v2'
    elif change == 'rules': pack['rule_version'] = 'synthetic-rules-v2'
    elif change == 'source': pack['source'] = 'different-synthetic-source'
    elif change == 'prompt': monkeypatch.setattr(briefing, 'PROMPT_VERSION', 'test-prompt-v2')
    elif change == 'model': settings = replace(settings, model='synthetic-model-2')
    elif change == 'service': settings = replace(settings, base_url='https://other-offline.invalid')
    rehash(pack)
    assert cache_key(pack, settings) != original_key
    state = read_state(pack, settings, cache_dir=tmp_path, now=NOW)
    assert state['status'] == 'not_generated' and state['result'] is None
    assert len(list(tmp_path.glob('*.json'))) == 1


def test_expiration_and_shorter_retention_are_stale_without_request(pack, settings, tmp_path):
    client = FakeClient(settings)
    original = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW)
    later = NOW + timedelta(seconds=86400)
    expired = read_state(pack, settings, cache_dir=tmp_path, now=later)
    assert expired['status'] == 'stale' and expired['result_stale']
    assert expired['result'] == original['result'] and expired['generated_at'] == original['generated_at']
    shortened = replace(settings, cache_ttl_seconds=5)
    assert read_state(pack, shortened, cache_dir=tmp_path, now=NOW + timedelta(seconds=5))['status'] == 'stale'
    assert len(client.calls) == 1


def test_failed_refresh_preserves_old_result_and_original_generation_time(pack, settings, tmp_path):
    initial = generate_briefing(pack, settings, FakeClient(settings), cache_dir=tmp_path, now=NOW)
    failed = generate_briefing(pack, settings, FakeClient(settings, error=TimeoutError(FAKE_SECRET)),
                               cache_dir=tmp_path, now=NOW + timedelta(seconds=1), force_refresh=True)
    assert failed['status'] == 'failed' and failed['result'] == initial['result']
    assert failed['generated_at'] == initial['generated_at'] and not failed['result_stale']
    read = read_state(pack, settings, cache_dir=tmp_path, now=NOW + timedelta(days=2))
    assert read['status'] == 'failed' and read['result_stale'] and read['result'] == initial['result']


def test_cache_atomic_write_failure_preserves_previous_file(pack, settings, tmp_path, monkeypatch):
    initial = generate_briefing(pack, settings, FakeClient(settings), cache_dir=tmp_path, now=NOW)
    path = next(tmp_path.glob('*.json'))
    before = path.read_bytes()
    def fail_replace(*args, **kwargs):
        raise OSError('private ' + FAKE_SECRET)
    monkeypatch.setattr(briefing.os, 'replace', fail_replace)
    failed = generate_briefing(pack, settings, FakeClient(settings), cache_dir=tmp_path,
                               now=NOW + timedelta(seconds=1), force_refresh=True)
    assert failed['status'] == 'failed' and failed['result'] == initial['result']
    assert path.read_bytes() == before and len(list(tmp_path.iterdir())) == 1
    assert FAKE_SECRET not in as_json(failed)


@pytest.mark.parametrize('damage', ['invalid_json', 'forged_fact', 'unknown_field', 'oversized', 'future', 'extended_ttl', 'key', 'secret'])
def test_cache_revalidated_on_every_read(pack, settings, tmp_path, damage):
    generate_briefing(pack, settings, FakeClient(settings), cache_dir=tmp_path, now=NOW)
    path = next(tmp_path.glob('*.json'))
    envelope = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'invalid_json': path.write_text('{', encoding='utf-8')
    elif damage == 'oversized': path.write_text('x' * (briefing.MAX_CACHE_BYTES + 1), encoding='utf-8')
    else:
        if damage == 'forged_fact': envelope['result']['sections'][0]['claims'][0]['fact_ids'] = ['forged']
        elif damage == 'unknown_field': envelope['unexpected'] = True
        elif damage == 'future': envelope['last_attempt_at'] = (NOW + timedelta(days=1)).isoformat()
        elif damage == 'extended_ttl': envelope['expires_at'] = (NOW + timedelta(days=2)).isoformat()
        elif damage == 'key': envelope['cache_key'] = 'wrong-key'
        elif damage == 'secret': envelope['result']['sections'][0]['claims'][0]['text'] += FAKE_SECRET
        path.write_text(as_json(envelope), encoding='utf-8')
    before = path.read_bytes()
    state = read_state(pack, settings, cache_dir=tmp_path, now=NOW)
    assert state['status'] == 'failed' and state['result'] is None
    assert FAKE_SECRET not in as_json(state) and path.read_bytes() == before


def test_no_cache_and_no_facts_do_not_create_result_directory(pack, settings, tmp_path):
    absent = tmp_path / 'not-created'
    assert read_state(pack, settings, cache_dir=absent, now=NOW)['status'] == 'not_generated'
    assert read_state(None, settings, cache_dir=absent, now=NOW)['status'] == 'failed'
    assert not absent.exists()


def test_force_refresh_requires_explicit_boolean(pack, settings, tmp_path):
    client = FakeClient(settings)
    state = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW, force_refresh='false')
    assert state['status'] == 'failed' and not client.calls


def test_fact_pack_builder_interoperates_with_core_using_only_synthetic_state(settings, tmp_path):
    from benchmark_dashboard.fact_pack import build_fact_pack
    path = 'pricing.price_1m_input_tokens'
    snapshot = {'id': 11, 'source': 'artificial_analysis', 'content_hash': 'b' * 64,
                'collected_at': '2026-01-01T01:00:00+00:00', 'metadata': {}}
    records = [{'id': 'synthetic-a', 'pricing': {'price_1m_input_tokens': 0}},
               {'id': 'synthetic-b', 'pricing': {'price_1m_input_tokens': None}}]
    state = {'snapshot': snapshot, 'records': records, 'metric_paths': [path],
             'comparison': {'status': 'baseline', 'before': None, 'after': None}, 'latest_attempt': None}
    pack = build_fact_pack(state)
    by_kind = {fact['kind']: fact for fact in pack['facts']}
    response = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
                'fact_hash': pack['fact_hash'], 'model': settings.model,
                'sections': [{'key': key, 'claims': [{'text': by_kind[kind]['text'],
                              'fact_ids': [by_kind[kind]['id']]}]}
                             for key, kind in [('current', 'coverage'), ('changes', 'changes'),
                                               ('limitations', 'limitations')]]}
    client = FakeClient(settings, response=as_json(response))
    generated = generate_briefing(pack, settings, client, cache_dir=tmp_path, now=NOW)
    assert generated['status'] == 'generated' and generated['result'] == response
    assert read_state(pack, settings, cache_dir=tmp_path, now=NOW)['status'] == 'generated'
    assert len(client.calls) == 1


@pytest.mark.parametrize('damage', ['hash', 'nan', 'duplicate_id', 'missing_changes', 'missing_limits', 'too_many', 'oversized'])
def test_invalid_fact_pack_is_rejected_before_client_call(pack, settings, tmp_path, damage):
    if damage == 'hash': pack['fact_hash'] = 'forged'
    elif damage == 'nan': pack['facts'][1]['raw_value'] = float('nan')
    elif damage == 'duplicate_id': pack['facts'][1]['id'] = pack['facts'][0]['id']; rehash(pack)
    elif damage == 'missing_changes': pack['facts'] = [fact for fact in pack['facts'] if fact['kind'] != 'changes']; rehash(pack)
    elif damage == 'missing_limits': pack['facts'] = [fact for fact in pack['facts'] if fact['kind'] != 'limitations']; rehash(pack)
    elif damage == 'too_many':
        pack['facts'].extend({'id': f'extra-{index}', 'kind': 'coverage', 'text': '合成事实。'} for index in range(65))
        rehash(pack)
    elif damage == 'oversized': pack['scope']['too_much'] = 'x' * briefing.MAX_FACT_BYTES; rehash(pack)
    client = FakeClient(settings)
    cache_dir = tmp_path / 'not-created'
    state = generate_briefing(pack, settings, client, cache_dir=cache_dir, now=NOW)
    assert state['status'] == 'failed' and not client.calls and not cache_dir.exists()
