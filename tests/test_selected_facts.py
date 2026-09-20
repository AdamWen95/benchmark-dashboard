"""Selected M2C packs use synthetic fixtures and no live/private I/O."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from benchmark_dashboard import briefing, metrics, selected_facts
from benchmark_dashboard.fact_pack import compute_fact_hash
from benchmark_dashboard.selected_facts import build_selected_fact_pack


INDEX = 'evaluations.artificial_analysis_intelligence_index'
PRICE = 'pricing.price_1m_input_tokens'
UNKNOWN = 'evaluations.livecodebench'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@pytest.fixture
def selected_state():
    records = [{'id': f'SYNTHETIC_PRIVATE_ID_{i}', 'name': f'SYNTHETIC_PRIVATE_NAME_{i}',
                'slug': 'SYNTHETIC_PRIVATE_SLUG', 'model_creator': {'id': f'creator-{i}', 'name': '测试厂商'},
                'evaluations': {'artificial_analysis_intelligence_index': 20 + i, 'livecodebench': 0.5},
                'pricing': {'price_1m_input_tokens': i, 'price_1m_output_tokens': 5 - i},
                'median_output_tokens_per_second': 10 + i,
                'median_time_to_first_token_seconds': 0.5 + i} for i in range(5)]
    snapshot = {'id': 7, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
                'collected_at': '2026-09-20T00:00:00+00:00', 'metadata': {},
                'prompt_options': {'prompt_length': 'medium'}, 'coverage': {}}
    return {'records': records, 'snapshot': snapshot, 'metric_paths': list(metrics.DISPLAY_METRICS),
            'latest_attempt': {'id': 1, 'status': 'success'},
            'comparison': {'status': 'baseline', 'before': None, 'after': None,
                           'message': 'SYNTHETIC_PRIVATE_HISTORY_TEXT'}}


def ids(state, n=2):
    return [row['id'] for row in state['records'][:n]]


def metric(pack, path):
    return next(fact for fact in pack['facts'] if fact.get('metric_path') == path)


@pytest.mark.parametrize('count', [2, 4])
def test_selected_pack_is_compact_and_compatible_without_identity_leak(selected_state, count):
    pack = build_selected_fact_pack(selected_state, ids(selected_state, count))
    assert set(pack) == {'schema_version', 'source', 'snapshot', 'metric_mapping_version',
                         'rule_version', 'scope', 'facts', 'fact_hash'}
    assert pack['schema_version'] == 'm2b-facts-v1'
    assert pack['scope']['selected_count'] == count
    assert pack['scope']['aliases'] == [f'R{i + 1}' for i in range(count)]
    assert pack['scope']['total_records'] == 5
    assert pack['scope']['omitted_records'] == 5 - count
    assert len(canonical(pack).encode('utf-8')) < 60000
    assert len(pack['facts']) <= 64
    assert compute_fact_hash(pack) == pack['fact_hash']
    assert 'SYNTHETIC_PRIVATE' not in canonical(pack)
    assert briefing._validate_pack(pack, briefing.AnalysisSettings(input_mode='real')) == pack


def test_selected_and_full_coverage_differ_and_zero_is_preserved(selected_state):
    selected_state['records'][1]['pricing'][PRICE.split('.')[-1]] = None
    pack = build_selected_fact_pack(selected_state, ids(selected_state))
    row = metric(pack, PRICE)
    assert row['coverage']['selected'] == {'valid': 1, 'total': 2}
    assert row['coverage']['full'] == {'valid': 4, 'total': 5}
    assert [item['raw_value'] for item in row['values']] == [0, None]
    assert row['values'][1]['display_value'] == '暂无'
    assert row['differences'] == []


def test_program_differences_use_first_selected_baseline_and_keep_zero_relative_unknown(selected_state):
    pack = build_selected_fact_pack(selected_state, ids(selected_state, 4))
    row = metric(pack, PRICE)
    assert row['directional_observation_allowed'] is True
    assert len(row['differences']) == 3
    difference = row['differences'][0]
    assert difference['from'] == 'R1' and difference['to'] == 'R2'
    assert difference['absolute'] == '1' and difference['relative_percent'] is None
    assert difference['relation'] == 'higher'
    assert difference['delta_unit'] == '美元/百万 Token'


def test_partial_and_unknown_fields_are_raw_only_without_comparative_direction(selected_state):
    pack = build_selected_fact_pack(selected_state, ids(selected_state))
    for path in (INDEX, UNKNOWN):
        row = metric(pack, path)
        assert row['direction'] == 'unknown' and not row['directional_observation_allowed']
        assert row['differences'] == []
        assert '从高到低' not in row['text']


def test_unit_conflict_never_converts_or_compares(selected_state, monkeypatch):
    selected_state['snapshot']['metadata']['metric_units'] = {PRICE: '%'}
    monkeypatch.setitem(metrics.METRICS, PRICE, replace(metrics.metric_for(PRICE), multiplier=100))
    selected_state['records'][0]['pricing']['price_1m_input_tokens'] = 0.3
    pack = build_selected_fact_pack(selected_state, ids(selected_state))
    row = metric(pack, PRICE)
    assert row['assessment']['unit_conflict'] is True
    assert row['unit'] == '源站原值（单位声明待核对）'
    assert row['values'][0]['display_value'] == '0.3'
    assert row['differences'] == [] and row['direction'] == 'unknown'


def test_configuration_is_used_locally_but_not_transmitted(selected_state):
    selected_state['records'][0]['inference_config'] = {'secret_note': 'SYNTHETIC_CONFIG_HIGH'}
    selected_state['records'][1]['inference_config'] = {'secret_note': 'SYNTHETIC_CONFIG_LOW'}
    pack = build_selected_fact_pack(selected_state, ids(selected_state))
    assert not metric(pack, PRICE)['directional_observation_allowed']
    assert 'SYNTHETIC_CONFIG' not in canonical(pack)
    assert metric(pack, PRICE)['assessment']['status'] == 'changed'


def test_determinism_tracks_selection_order_snapshot_values_and_mapping(selected_state, monkeypatch):
    ordered = ids(selected_state)
    original = build_selected_fact_pack(selected_state, ordered)
    reordered = deepcopy(selected_state)
    reordered['records'].reverse()
    reordered['metric_paths'].reverse()
    assert original == build_selected_fact_pack(reordered, ordered)
    assert original['scope']['selection_hash'] != build_selected_fact_pack(selected_state, ordered[::-1])['scope']['selection_hash']
    selected_state['records'][0]['pricing']['price_1m_input_tokens'] = 42
    assert original['fact_hash'] != build_selected_fact_pack(selected_state, ordered)['fact_hash']
    selected_state['snapshot']['content_hash'] = 'b' * 64
    changed = build_selected_fact_pack(selected_state, ordered)
    assert changed['snapshot']['content_hash'] == 'b' * 64 and changed['fact_hash'] != original['fact_hash']
    monkeypatch.setattr(metrics, 'METRIC_MAPPING_VERSION', 'synthetic-new-mapping')
    assert changed['fact_hash'] != build_selected_fact_pack(selected_state, ordered)['fact_hash']


def test_rules_reuse_selected_metric_facts_and_controlled_aliases(selected_state):
    pack = build_selected_fact_pack(selected_state, ids(selected_state, 4))
    fact_ids = {fact['id'] for fact in pack['facts']}
    rules = [fact for fact in pack['facts'] if fact['kind'] == 'rule']
    assert rules
    for rule in rules:
        assert set(rule['evidence_fact_ids']) <= fact_ids
        assert '记录1' not in rule['text']
        assert 'SYNTHETIC_PRIVATE' not in rule['text']


def test_baseline_failed_status_is_shared_honestly(selected_state):
    selected_state['latest_attempt'] = {'id': 2, 'status': 'failed', 'message': 'SYNTHETIC_PRIVATE_ERROR'}
    pack = build_selected_fact_pack(selected_state, ids(selected_state))
    change = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    assert change['status'] == 'baseline' and change['latest_attempt_failed'] is True
    assert change['text'].startswith('最近一次采集失败') and '初始基线' in change['text']
    assert '无变化' not in change['text'] and 'SYNTHETIC_PRIVATE_ERROR' not in canonical(pack)


def test_history_counts_are_limited_to_selected_ids_and_selected_metric_fields(selected_state):
    before = deepcopy(selected_state)
    before['snapshot']['id'] = 6
    before['snapshot']['content_hash'] = 'b' * 64
    selected_state['records'][4]['pricing']['price_1m_input_tokens'] = 999
    selected_state['records'][0]['evaluations']['gpqa'] = 123
    selected_state['comparison'] = {'status': 'ready',
        'before': {'snapshot': before['snapshot'], 'records': before['records'],
                   'run': {'id': 1, 'status': 'success'}},
        'after': {'snapshot': selected_state['snapshot'], 'records': selected_state['records'],
                  'run': {'id': 2, 'status': 'success'}}}
    pack = build_selected_fact_pack(selected_state, ids(selected_state))
    change = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    assert change['status'] == 'ready'
    assert sum(change['counts'].values()) == 0
    assert change['comparison_scope'] == 'current_selected_stable_ids_only'
    selected_state['records'][0]['pricing']['price_1m_input_tokens'] = 7
    changed = build_selected_fact_pack(selected_state, ids(selected_state))
    change = next(fact for fact in changed['facts'] if fact['kind'] == 'changes')
    assert change['counts']['value'] == 1
    assert 'SYNTHETIC_PRIVATE' not in canonical(changed)


def test_bounds_and_invalid_selection_fail_before_external_work(selected_state, monkeypatch):
    with pytest.raises(ValueError):
        build_selected_fact_pack(selected_state, ids(selected_state, 1))
    monkeypatch.setattr(selected_facts, 'MAX_PACK_BYTES', 100)
    with pytest.raises(ValueError, match='^所选模型事实包无法生成，请核对选择或继续查看本地规则。$'):
        build_selected_fact_pack(selected_state, ids(selected_state))


def test_no_file_database_or_network_access_and_input_not_mutated(selected_state, monkeypatch):
    import builtins
    import socket
    import sqlite3
    original = deepcopy(selected_state)

    def forbidden(*a, **kw):
        pytest.fail('Selected facts must not perform I/O')

    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    assert build_selected_fact_pack(selected_state, ids(selected_state))['facts']
    assert selected_state == original
