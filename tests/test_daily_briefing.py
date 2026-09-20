"""M3 artifacts and cache exercise only synthetic facts and fake clients."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from benchmark_dashboard import briefing, daily_briefing, daily_facts, metrics
from benchmark_dashboard.briefing import AnalysisClientError, AnalysisResponse, AnalysisSettings
from benchmark_dashboard.daily_briefing import (
    generate_daily_briefing, read_daily_briefing, read_daily_history, render_daily_markdown, semantic_key,
)
from benchmark_dashboard.fact_pack import compute_fact_hash
from benchmark_dashboard.modex_client import build_request_body
from benchmark_dashboard.selection import local_model_mapping


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
KEY = 'synthetic-daily-key-offline-only'


@pytest.fixture
def state():
    records = [{'id': f'synthetic-{index}', 'name': f'合成记录{index}',
                'model_creator': {'id': f'creator-{index % 2}', 'name': '合成厂商'},
                'evaluations': {'artificial_analysis_coding_index': 10 + index,
                                'livecodebench': None if index == 1 else 0.4},
                'pricing': {'price_1m_input_tokens': index},
                'median_output_tokens_per_second': 0 if index < 2 else 15} for index in range(5)]
    snapshot = {'id': 4, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
                'collected_at': '2026-09-20T00:00:00+00:00', 'metadata': {},
                'prompt_options': {'prompt_length': 'medium'}}
    run = {'id': 4, 'status': 'success', 'started_at': '2026-09-20T00:00:00+00:00',
           'finished_at': '2026-09-20T00:00:01+00:00'}
    point = {'run': deepcopy(run), 'snapshot': deepcopy(snapshot), 'records': deepcopy(records)}
    return {'records': records, 'snapshot': snapshot, 'metric_paths': list(metrics.DISPLAY_METRICS),
            'latest_attempt': run, 'last_success': deepcopy(run),
            'comparison': {'status': 'baseline', 'before': None, 'after': point}}


@pytest.fixture
def settings():
    return AnalysisSettings(enabled=True, data_use_confirmed=True, base_url=daily_briefing.BASE_URL,
                            model=daily_briefing.MODEL, api_key=KEY, purpose_authorized=True,
                            transmission_authorized=True, real_integration_authorized=True,
                            protocol_verified=True, input_mode='real', timeout_seconds=120)


def ids(state, count=2):
    return [row['id'] for row in state['records'][:count]]


class FakeClient:
    def __init__(self, error=None, invalid=False):
        self.calls = 0
        self.error = error
        self.invalid = invalid

    def generate(self, pack, *, prompt, timeout_seconds):
        self.calls += 1
        if self.error:
            raise self.error
        assert 'all_records' in prompt and 'selected_examples' in prompt
        by_kind = {fact['kind']: fact for fact in pack['facts']}
        value = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
                 'fact_hash': pack['fact_hash'], 'model': daily_briefing.MODEL,
                 'sections': [{'key': key, 'claims': [{'text': by_kind[kind]['text'], 'fact_ids': [by_kind[kind]['id']]}]}
                              for key, kind in [('current', 'coverage'), ('changes', 'changes'), ('limitations', 'limitations')]]}
        content = '{invalid' if self.invalid else json.dumps(value, ensure_ascii=False)
        return AnalysisResponse(content, daily_briefing.MODEL, daily_briefing.MODEL,
                                http_status=200, elapsed_seconds=0.5, usage={'total_tokens': 15})


def generate(state, settings, root, client=None, *, selected=None, now=NOW):
    selected = ids(state) if selected is None else selected
    pack = daily_facts.build_daily_fact_pack(state, selected)
    mapping = local_model_mapping(state, selected)
    return generate_daily_briefing(pack, settings, client or FakeClient(), root=root,
                                   run_id='daily-test', scope_id='isolated-test-scope', retention_approved=True,
                                   local_model_mapping=mapping, now=now)


def later_collection(state):
    changed = deepcopy(state)
    changed['snapshot'].update(id=14, content_hash='b' * 64, collected_at='2026-09-20T01:00:00+00:00')
    for key in ('latest_attempt', 'last_success'):
        changed[key].update(id=14, started_at='2026-09-20T01:00:00+00:00', finished_at='2026-09-20T01:00:09+00:00')
    changed['comparison']['after'] = {'run': deepcopy(changed['latest_attempt']),
                                    'snapshot': deepcopy(changed['snapshot']), 'records': deepcopy(changed['records'])}
    return changed


@pytest.mark.parametrize('count', [2, 4])
def test_success_has_daily_scope_original_hashes_full_body_and_mapping(state, settings, tmp_path, count):
    client = FakeClient()
    result = generate(state, settings, tmp_path, client, selected=ids(state, count))
    assert result['status'] == 'generated', result
    assert result['kind'] == 'daily' and result['persisted'] and client.calls == 1
    artifact = result['artifact']
    assert artifact['fact_pack']['scope']['all_records']['total_records'] == 5
    assert artifact['fact_pack']['scope']['selected_examples']['retained_count'] == count
    assert len(result['local_model_mapping']) == count
    assert artifact['result']['fact_hash'] == artifact['fact_pack']['fact_hash']
    assert artifact['generation']['human_review_status'] == 'pending'
    assert result['usage'] == {'total_tokens': 15} and result['cost'] is None
    assert result['versions']['prompt_version'] == briefing.DAILY_PROMPT_VERSION
    markdown = render_daily_markdown(artifact)
    for section in artifact['result']['sections']:
        assert section['claims'][0]['text'] in markdown
    assert '全量变化' in markdown and '用户人工内容复核待完成' in markdown
    assert KEY not in markdown + Path(result['artifact_path']).read_text(encoding='utf-8')
    assert not (tmp_path / 'data/analysis_results').exists()


def test_same_input_and_changed_collection_bookkeeping_reuse_without_new_time_or_call(state, settings, tmp_path):
    initial = generate(state, settings, tmp_path)
    path = Path(initial['artifact_path'])
    saved_bytes = path.read_bytes()
    newer = later_collection(state)
    current_pack = daily_facts.build_daily_fact_pack(newer, ids(newer))
    assert current_pack['fact_hash'] != initial['artifact']['fact_pack']['fact_hash']
    assert semantic_key(current_pack) == initial['semantic_hash']
    read = read_daily_briefing(current_pack, root=tmp_path, now=NOW + timedelta(hours=1))
    assert read['status'] == 'generated' and read['semantic_reuse'] and read['cache_hit']
    assert read['generated_at'] == initial['generated_at'] and read['expires_at'] == initial['expires_at']
    assert read['fact_pack'] == initial['fact_pack'] and read['result'] == initial['result']
    assert read['current_data_collected_at'] == newer['snapshot']['collected_at']
    assert read['data_collected_at'] == state['snapshot']['collected_at']
    client = FakeClient()
    reused = generate(newer, settings, tmp_path, client, now=NOW + timedelta(hours=1))
    assert reused['status'] == 'generated' and client.calls == 0 and path.read_bytes() == saved_bytes


@pytest.mark.parametrize('change', ['selection', 'nonselected_value', 'evaluation_date', 'mapping_version', 'rule_version', 'prompt_version'])
def test_scope_meaningful_data_and_versions_do_not_reuse_stale_semantics(state, settings, tmp_path, monkeypatch, change):
    original = generate(state, settings, tmp_path)
    selected = ids(state)
    if change == 'selection': selected = ids(state, 4)
    elif change == 'nonselected_value': state['records'][4]['pricing']['price_1m_input_tokens'] = 99
    elif change == 'evaluation_date': state['records'][4]['evaluation_date'] = '2026-01-01'
    elif change == 'mapping_version': monkeypatch.setattr(metrics, 'METRIC_MAPPING_VERSION', 'test-mapping-version')
    elif change == 'rule_version':
        from benchmark_dashboard import insights
        monkeypatch.setattr(insights, 'RULE_VERSION', 'test-rule-version')
    elif change == 'prompt_version': monkeypatch.setattr(briefing, 'DAILY_PROMPT_VERSION', 'test-daily-prompt')
    pack = daily_facts.build_daily_fact_pack(state, selected)
    assert semantic_key(pack) != original['semantic_hash']
    read = read_daily_briefing(pack, root=tmp_path, now=NOW)
    assert read['status'] == 'not_generated' and read['result'] is None


def test_current_expiry_and_history_keep_original_body_and_timestamps(state, settings, tmp_path):
    original = generate(state, settings, tmp_path)
    pack = daily_facts.build_daily_fact_pack(state, ids(state))
    later = NOW + timedelta(days=1)
    current = read_daily_briefing(pack, root=tmp_path, now=later)
    assert current['status'] == 'stale' and current['result'] is None and current['result_stale']
    history = read_daily_history(root=tmp_path, now=later)
    assert len(history) == 1 and history[0]['status'] == 'historical' and history[0]['result_stale']
    assert history[0]['result'] == original['result']
    assert history[0]['generated_at'] == original['generated_at'] and history[0]['expires_at'] == original['expires_at']


def test_failure_is_separate_from_preserved_success_and_new_rule_data(state, settings, tmp_path):
    original = generate(state, settings, tmp_path)
    path = Path(original['artifact_path'])
    before = path.read_bytes()
    client = FakeClient(AnalysisClientError('timeout', elapsed_seconds=120))
    failed = generate(state, settings, tmp_path, client, now=NOW + timedelta(days=1))
    assert failed['status'] == 'failed' and client.calls == 1 and path.read_bytes() == before
    pack = daily_facts.build_daily_fact_pack(state, ids(state))
    read = read_daily_briefing(pack, root=tmp_path, now=NOW + timedelta(days=1, seconds=1))
    assert read['status'] == 'failed' and read['result'] is None and read['error_code'] == 'timeout'
    assert read_daily_history(root=tmp_path, now=NOW + timedelta(days=1))[0]['result'] == original['result']


def test_new_authorized_generation_preserves_previous_history(state, settings, tmp_path):
    original = generate(state, settings, tmp_path)
    refreshed = generate(state, settings, tmp_path, now=NOW + timedelta(days=1))
    assert refreshed['status'] == 'generated' and refreshed['generated_at'] != original['generated_at']
    history = read_daily_history(root=tmp_path, now=NOW + timedelta(days=1, seconds=1))
    assert len(history) == 2
    assert {row['generated_at'] for row in history} == {original['generated_at'], refreshed['generated_at']}


@pytest.mark.parametrize('change', [{'enabled': False}, {'data_use_confirmed': False}, {'api_key': ''}, {'real_integration_authorized': False}])
def test_disabled_unconfirmed_missing_key_and_missing_grant_make_zero_calls(state, settings, tmp_path, change):
    client = FakeClient()
    result = generate(state, replace(settings, **change), tmp_path, client)
    assert result['status'] != 'generated' and client.calls == 0 and not (tmp_path / 'data').exists()


def test_retention_and_mapping_preflight_are_required_before_client(state, settings, tmp_path):
    pack = daily_facts.build_daily_fact_pack(state, ids(state))
    client = FakeClient()
    assert generate_daily_briefing(pack, settings, client, root=tmp_path, run_id='test', scope_id='scope',
                                   retention_approved=False)['status'] == 'authorization_required'
    assert generate_daily_briefing(pack, settings, client, root=tmp_path, run_id='test', scope_id='scope',
                                   retention_approved=True)['status'] == 'failed'
    assert client.calls == 0 and not (tmp_path / 'data').exists()


def test_selected_m2c_pack_cannot_be_saved_or_read_as_daily(state, settings, tmp_path):
    from benchmark_dashboard.selected_facts import build_selected_fact_pack
    pack = build_selected_fact_pack(state, ids(state))
    client = FakeClient()
    result = generate_daily_briefing(pack, settings, client, root=tmp_path, run_id='test', scope_id='scope',
                                     retention_approved=True, local_model_mapping=local_model_mapping(state, ids(state)))
    assert result['status'] == 'failed' and client.calls == 0
    assert read_daily_briefing(pack, root=tmp_path)['status'] == 'failed'


def test_invalid_response_is_not_persisted_as_success(state, settings, tmp_path):
    result = generate(state, settings, tmp_path, FakeClient(invalid=True))
    assert result['status'] == 'failed' and result['result'] is None
    assert read_daily_history(root=tmp_path, now=NOW) == []
    assert all(path.name.endswith('.failure.json') for path in (tmp_path / 'data/daily_briefings').glob('*.json'))


def test_corrupt_artifact_fails_closed_and_unknown_source_text_not_printed(state, settings, tmp_path):
    result = generate(state, settings, tmp_path)
    path = Path(result['artifact_path'])
    artifact = json.loads(path.read_text(encoding='utf-8'))
    artifact['result']['sections'][0]['claims'][0]['text'] += KEY
    path.write_text(json.dumps(artifact), encoding='utf-8')
    read = read_daily_briefing(daily_facts.build_daily_fact_pack(state, ids(state)), root=tmp_path, now=NOW)
    assert read['status'] == 'failed' and read['result'] is None and KEY not in json.dumps(read)


def test_readers_never_load_settings_generate_or_write(state, settings, tmp_path, monkeypatch):
    original = generate(state, settings, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('read-only daily result must not authorize, generate or write')
    monkeypatch.setattr(briefing, 'generate_briefing', forbidden)
    monkeypatch.setattr(briefing, 'check_settings', forbidden)
    monkeypatch.setattr(daily_briefing, '_write', forbidden)
    pack = daily_facts.build_daily_fact_pack(state, ids(state))
    assert read_daily_briefing(pack, root=tmp_path, now=NOW)['result'] == original['result']
    assert len(read_daily_history(root=tmp_path, now=NOW)) == 1


def test_failed_current_slot_write_retains_validated_recovery_without_retry(state, settings, tmp_path, monkeypatch):
    original = daily_briefing._write
    def fail_current(path, content):
        if path.parent.name == 'daily_briefings':
            raise PermissionError(KEY)
        return original(path, content)
    monkeypatch.setattr(daily_briefing, '_write', fail_current)
    client = FakeClient()
    result = generate(state, settings, tmp_path, client)
    assert result['status'] == 'failed' and client.calls == 1
    assert result['artifact'] and Path(result['recovery_path']).exists()
    assert KEY not in json.dumps(result)
    assert len(read_daily_history(root=tmp_path, now=NOW)) == 1


def test_daily_prompt_and_minimal_body_keep_old_m2c_version_independent(state):
    pack = daily_facts.build_daily_fact_pack(state, ids(state))
    prompt = briefing.build_prompt(pack, AnalysisSettings(model=daily_briefing.MODEL))
    for text in ('all_records', 'selected_examples', '代码指数', 'LiveCodeBench', '七类', '初始基线',
                 '速度/延迟0', '有限数值覆盖', '不得推算Modex费用'):
        assert text in prompt
    assert briefing.PROMPT_VERSION == 'm2c-finish-prompt-v1'
    assert briefing.DAILY_PROMPT_VERSION == 'm3-daily-prompt-v1'
    body = build_request_body(pack, prompt)
    assert set(body) == {'model', 'messages', 'stream'} and body['messages'][0]['content'] == prompt
    assert json.loads(body['messages'][1]['content']) == pack
