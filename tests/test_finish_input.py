"""Next-input compaction and prompt checks; synthetic memory fixtures only."""
from copy import deepcopy
import json

import pytest
import requests

from benchmark_dashboard import briefing, fact_pack, insights, metrics, selected_facts
from benchmark_dashboard.briefing import AnalysisSettings, validate_result
from benchmark_dashboard.modex_client import build_request_body, request_body_size


@pytest.fixture
def state():
    return {'records': [
        {'id': f'private-synthetic-{index}', 'name': f'私人合成名称{index}',
         'model_creator': {'name': '合成厂商'},
         'evaluations': {'artificial_analysis_coding_index': 20 + index,
                         'artificial_analysis_intelligence_index': 10 + index,
                         'livecodebench': None if index == 2 else 0.4 + index / 10},
         'pricing': {'price_1m_input_tokens': index, 'price_1m_output_tokens': 4 - index},
         'median_output_tokens_per_second': 10 + index,
         'median_time_to_first_token_seconds': 0.2 + index,
         'median_time_to_first_answer_token': 1 + index} for index in range(4)],
        'snapshot': {'source': 'artificial_analysis', 'id': 2, 'content_hash': 'c' * 64,
                     'collected_at': '2026-09-20T00:00:00+00:00', 'metadata': {},
                     'prompt_options': {'prompt_length': 'medium'}},
        'metric_paths': list(metrics.DISPLAY_METRICS), 'latest_attempt': {'id': 1, 'status': 'success'},
        'comparison': {'status': 'baseline', 'before': None, 'after': None}}


def chosen(state, count=2):
    return [row['id'] for row in state['records'][:count]]


def example(pack, path):
    return next(fact for fact in pack['facts'] if fact.get('metric_path') == path)


@pytest.mark.parametrize('count', [2, 4])
def test_compaction_preserves_numeric_evidence_units_missing_and_limits(state, monkeypatch, count):
    compact = selected_facts._compact
    monkeypatch.setattr(selected_facts, '_compact', lambda pack: pack)
    uncompressed = selected_facts.build_selected_fact_pack(state, chosen(state, count))
    monkeypatch.setattr(selected_facts, '_compact', compact)
    compressed = selected_facts.build_selected_fact_pack(state, chosen(state, count))
    assert compressed['scope']['pack_version'] == 'm2c-selected-v2'
    for original in uncompressed['facts']:
        if original['kind'] != 'metric_example':
            continue
        current = example(compressed, original['metric_path'])
        for field in ('values', 'unit', 'raw_unit', 'coverage', 'differences', 'direction', 'directional_observation_allowed'):
            assert current[field] == original[field]
        assert current['mapping']['verification_status'] == original['mapping']['verification_status']
        assert current['mapping']['definition'] == original['mapping']['definition']
        assert compressed['scope']['notes'][current['mapping']['comparability_ref']] == original['mapping']['comparability_limit']
        assert compressed['scope']['notes'][current['assessment']['reason_ref']] == original['assessment']['reason']
    assert len(fact_pack._canonical(compressed).encode('utf-8')) < len(fact_pack._canonical(uncompressed).encode('utf-8'))
    settings = AnalysisSettings(model='gpt-5.6-sol')
    assert request_body_size(compressed, settings) < request_body_size(uncompressed, settings)
    assert '不等于可靠实测覆盖率' in compressed['scope']['numeric_coverage_definition']
    assert 'null 是缺失' in compressed['scope']['value_semantics']
    assert '私人合成名称' not in fact_pack._canonical(compressed)


def test_short_references_resolve_locally_and_keep_complete_hash_binding(state):
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    references = selected_facts.fact_id_map(pack)
    assert list(references) == [f'F{index + 1}' for index in range(len(pack['facts']))]
    assert len(set(references.values())) == len(pack['facts'])
    assert all(len(full.rsplit('-', 1)[1]) == 64 for full in references.values())
    assert not any(full in fact_pack._canonical(pack) for full in references.values())
    for fact in pack['facts']:
        assert set(fact.get('evidence_fact_ids', [])) <= references.keys()
        if 'coverage_fact_id' in fact:
            assert fact['coverage_fact_id'] in references
    assert fact_pack.compute_fact_hash(pack) == pack['fact_hash']
    state['records'][0]['pricing']['price_1m_input_tokens'] = 2
    changed = selected_facts.build_selected_fact_pack(state, chosen(state))
    assert selected_facts.fact_id_map(changed)['F1'] != references['F1']
    changed['fact_hash'] = 'forged'
    with pytest.raises(ValueError):
        selected_facts.fact_id_map(changed)


def test_historical_long_reference_map_is_identity_and_never_rewritten(state):
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    pack['scope']['pack_version'] = 'm2c-selected-v1'
    for index, fact in enumerate(pack['facts']):
        fact['id'] = 'historical-full-fact-' + str(index)
    pack['fact_hash'] = fact_pack.compute_fact_hash(pack)
    original = deepcopy(pack)
    assert selected_facts.fact_id_map(pack) == {fact['id']: fact['id'] for fact in pack['facts']}
    assert pack == original


def test_next_prompt_requires_programming_raw_values_and_performance_zero_limits(state):
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    prompt = briefing.build_prompt(pack, AnalysisSettings(model='gpt-5.6-sol'))
    assert briefing.PROMPT_VERSION == 'm2c-finish-prompt-v1'
    for expected in ('代码指数', 'LiveCodeBench', 'partial/unknown', '不能省略整个编程维度',
                     '确实缺失则明确暂无', '速度/延迟0测量含义待确认', '评测零分、零价格不改成缺失',
                     '同次采集不等于同次测试', '初始基线', '只引用事实中实际存在的短ID'):
        assert expected in prompt
    assert '私人合成名称' not in prompt and 'private-synthetic' not in prompt


def test_request_body_contains_one_canonical_pack_and_exact_byte_count(state):
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    settings = AnalysisSettings(model='gpt-5.6-sol')
    prompt = briefing.build_prompt(pack, settings)
    body = build_request_body(pack, prompt)
    assert body['messages'][0]['content'] == prompt
    assert json.loads(body['messages'][1]['content']) == pack
    assert set(body) == {'model', 'messages', 'stream'} and body['stream'] is False
    prepared = requests.Request('POST', 'https://offline.invalid', json=body).prepare()
    assert request_body_size(pack, settings) == len(prepared.body)


def test_result_validator_accepts_short_ids_and_rejects_unknown_references(state):
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    by_kind = {fact['kind']: fact for fact in pack['facts']}
    result = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
              'fact_hash': pack['fact_hash'], 'model': 'gpt-5.6-sol',
              'sections': [{'key': key, 'claims': [{'text': by_kind[kind]['text'], 'fact_ids': [by_kind[kind]['id']]}]}
                           for key, kind in [('current', 'coverage'), ('changes', 'changes'), ('limitations', 'limitations')]]}
    settings = AnalysisSettings(model='gpt-5.6-sol')
    assert validate_result(json.dumps(result, ensure_ascii=False), pack, settings) == result
    result['sections'][0]['claims'][0]['fact_ids'] = ['F9999']
    with pytest.raises(ValueError):
        validate_result(json.dumps(result, ensure_ascii=False), pack, settings)


def test_performance_zero_retains_raw_value_and_quality_without_differences(state):
    state['records'][0]['median_output_tokens_per_second'] = 0
    state['records'][1]['median_time_to_first_token_seconds'] = 0
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    for path in ('median_output_tokens_per_second', 'median_time_to_first_token_seconds'):
        fact = example(pack, path)
        assert any(row['raw_value'] == 0 for row in fact['values'])
        assert not fact['directional_observation_allowed'] and fact['differences'] == []
        assert fact['assessment']['quality_status'] == 'unconfirmed_zero'
        assert fact['assessment']['performance_zero_count'] == 1
        assert '源站记录为 0' in fact['assessment']['quality_reason']
    price = example(pack, 'pricing.price_1m_input_tokens')
    assert price['values'][0]['raw_value'] == 0 and price['differences']


def test_conflicting_units_preserve_mapping_and_effective_unknown_units_separately(state):
    path = 'pricing.price_1m_input_tokens'
    state['snapshot']['metadata']['metric_units'] = {path: '%'}
    pack = selected_facts.build_selected_fact_pack(state, chosen(state))
    fact = example(pack, path)
    assert fact['raw_unit'] == '单位声明待核对'
    assert fact['mapping']['raw_unit'] == metrics.metric_for(path).raw_unit
    assert fact['unit'] == '源站原值（单位声明待核对）'
    assert fact['mapping']['display_unit'] == metrics.metric_for(path).unit
    assert fact['assessment']['unit_conflict'] and not fact['directional_observation_allowed']
