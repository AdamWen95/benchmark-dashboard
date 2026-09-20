"""Daily facts use synthetic in-memory states; no configuration or live I/O."""
from copy import deepcopy
import json

import pytest

from benchmark_dashboard import briefing, daily_facts, metrics
from benchmark_dashboard.changes import EVENT_TYPES
from benchmark_dashboard.fact_pack import compute_fact_hash


PRICE = 'pricing.price_1m_input_tokens'
SPEED = 'median_output_tokens_per_second'
CODING = 'evaluations.artificial_analysis_coding_index'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@pytest.fixture
def state():
    records = [{'id': f'SYNTHETIC_PRIVATE_ID_{i}', 'name': f'SYNTHETIC_PRIVATE_NAME_{i}',
                'model_creator': {'id': f'SYNTHETIC_PRIVATE_CREATOR_{i % 2}', 'name': f'合成厂商{i % 2}'},
                'evaluations': {'artificial_analysis_coding_index': i * 3, 'livecodebench': .2 + i / 10},
                'pricing': {'price_1m_input_tokens': i}, SPEED: 0 if i < 2 else 10 + i}
               for i in range(5)]
    snap = {'id': 4, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
            'collected_at': '2026-09-20T00:00:00+00:00', 'metadata': {},
            'coverage': dict.fromkeys(metrics.DISPLAY_METRICS, 1),
            'prompt_options': {'prompt_length': 'medium'}}
    run = {'id': 4, 'status': 'success', 'started_at': '2026-09-20T00:00:00+00:00',
           'finished_at': '2026-09-20T00:00:01+00:00'}
    point = {'snapshot': deepcopy(snap), 'records': deepcopy(records), 'run': deepcopy(run)}
    return {'records': records, 'snapshot': snap, 'metric_paths': list(metrics.DISPLAY_METRICS),
            'latest_attempt': run, 'last_success': deepcopy(run),
            'comparison': {'status': 'baseline', 'before': None, 'after': point}}


def choose(state, count=2):
    return [record['id'] for record in state['records'][:count]]


def with_history(state):
    before = deepcopy(state['comparison']['after'])
    before['run']['id'] -= 1
    before['run']['finished_at'] = '2026-09-19T00:00:01+00:00'
    before['snapshot']['id'] -= 1
    before['snapshot']['content_hash'] = 'b' * 64
    before['snapshot']['collected_at'] = '2026-09-19T00:00:00+00:00'
    state['comparison'] = {'status': 'ready', 'before': before,
                           'after': deepcopy(state['comparison']['after'])}
    return state


def test_overview_all_records_creators_coverage_zero_missing_and_real_times(state):
    state['records'][4]['pricing']['price_1m_input_tokens'] = None
    overview = daily_facts.build_daily_overview(state)
    assert overview['total_records'] == 5
    assert sum(row['count'] for row in overview['creator_distribution']) == 5
    assert len(overview['coverage']) == len(metrics.DISPLAY_METRICS) == 23
    speed = next(row for row in overview['coverage'] if row['path'] == SPEED)
    assert speed['valid_count'] == 5 and speed['performance_zero_count'] == 2
    assert speed['quality_notice'] == metrics.PERFORMANCE_ZERO_NOTICE
    price = next(row for row in overview['coverage'] if row['path'] == PRICE)
    assert price['valid_count'] == 4 and price['missing_count'] == 1 and price['performance_zero_count'] == 0
    assert overview['snapshot']['collected_at'] == state['snapshot']['collected_at']
    assert overview['latest_success']['finished_at'] == state['last_success']['finished_at']
    assert overview['changes']['status'] == 'baseline'


@pytest.mark.parametrize('count', [2, 4])
def test_daily_pack_compatible_full_vs_examples_and_does_not_leak_records(state, count):
    state['records'][4]['injected_text'] = 'SYNTHETIC_PRIVATE_SECRET'
    pack = daily_facts.build_daily_fact_pack(state, choose(state, count))
    assert pack['scope']['pack_version'] == 'm3-daily-v1'
    assert pack['scope']['kind'] == 'daily'
    assert pack['scope']['all_records']['total_records'] == 5
    assert pack['scope']['selected_examples']['retained_count'] == count
    assert pack['scope']['selected_count'] == count
    assert compute_fact_hash(pack) == pack['fact_hash']
    assert briefing._validate_pack(pack, briefing.AnalysisSettings(input_mode='real')) == pack
    assert 'SYNTHETIC_PRIVATE' not in canonical(pack)
    assert len(canonical(pack).encode('utf-8')) <= 128 * 1024 and len(pack['facts']) <= 64
    coding = next(fact for fact in pack['facts'] if fact.get('metric_path') == CODING)
    assert coding['values'][0]['raw_value'] == 0
    assert coding['mapping']['verification_status'] == 'partial'
    speed = next(fact for fact in pack['facts'] if fact.get('metric_path') == SPEED)
    assert speed['assessment']['quality_status'] == 'unconfirmed_zero'
    assert speed['differences'] == []
    changes = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    assert changes['comparison_scope'] == 'all_records'
    assert '初始基线' in changes['text']


def test_nonselected_change_is_full_scope_with_all_local_details_and_no_identity_upload(state):
    state = with_history(state)
    state['records'][4]['pricing']['price_1m_input_tokens'] = 17
    state['comparison']['after']['records'] = deepcopy(state['records'])
    overview = daily_facts.build_daily_overview(state)
    assert overview['changes']['counts']['value'] == 1
    assert len(overview['changes']['events']) == 1
    assert overview['changes']['events'][0]['model_id'] == state['records'][4]['id']
    pack = daily_facts.build_daily_fact_pack(state, choose(state))
    changes = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    assert changes['counts']['value'] == 1
    assert changes['detail_scope'] == 'all_records_aggregated_by_type_and_metric'
    assert changes['details'][0]['count'] == 1
    assert 'SYNTHETIC_PRIVATE' not in canonical(pack)


def test_same_snapshot_and_failed_attempt_are_not_fabricated_as_baseline_or_new_success(state):
    before = deepcopy(state['comparison']['after'])
    before['run']['id'] = 3
    state['comparison'] = {'status': 'ready', 'before': before, 'after': deepcopy(before)}
    state['comparison']['after']['run']['id'] = 4
    state['latest_attempt'] = {'id': 5, 'status': 'failed', 'finished_at': '2026-09-20T01:00:00+00:00',
                               'error': 'SYNTHETIC_PRIVATE_SECRET'}
    pack = daily_facts.build_daily_fact_pack(state)
    change = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    assert change['status'] == 'ready' and change['same_snapshot']
    assert change['latest_attempt_failed'] and change['text'].startswith('最近一次采集失败')
    assert '无数据变化' in change['text']
    assert change['counts'] == dict.fromkeys(EVENT_TYPES, 0)


def test_all_seven_change_types_and_performance_zero_keep_complete_local_evidence(state):
    state = with_history(state)
    before = state['comparison']['before']['records']
    state['records'] = state['records'][1:]
    new = deepcopy(state['records'][0])
    new.update(id='SYNTHETIC_PRIVATE_NEW', name='本次新收录')
    state['records'].append(new)
    state['records'][0]['name'] = '同ID更名'
    before[2]['pricing']['price_1m_input_tokens'] = None
    state['records'][2]['pricing']['price_1m_input_tokens'] = None
    state['records'][3]['pricing']['price_1m_input_tokens'] = 17
    before[4][SPEED] = 0
    state['comparison']['after']['records'] = deepcopy(state['records'])
    changes = daily_facts.build_daily_overview(state)['changes']
    assert set(changes['counts']) == set(EVENT_TYPES)
    assert all(changes['counts'][kind] > 0 for kind in EVENT_TYPES)
    assert len(changes['events']) == sum(changes['counts'].values())
    zero = next(event for event in changes['events'] if event['path'] == SPEED and event['type'] == 'value')
    assert zero['before'] == 0 and zero['after'] == 14
    assert zero['absolute'] is None and zero['relative_percent'] is None
    pack = daily_facts.build_daily_fact_pack(state)
    fact = next(row for row in pack['facts'] if row['kind'] == 'changes')
    assert fact['counts'] == changes['counts']
    assert sum(row['count'] for row in fact['details']) + fact['omitted_event_count'] == len(changes['events'])


def test_determinism_and_semantic_cache_ignore_collection_clock_but_track_unselected_values(state):
    ids = choose(state)
    original = daily_facts.build_daily_fact_pack(state, ids)
    changed = deepcopy(state)
    changed['records'].reverse()
    changed['metric_paths'].reverse()
    assert original == daily_facts.build_daily_fact_pack(changed, ids)
    changed = deepcopy(state)
    changed['snapshot']['id'] = 9
    changed['snapshot']['content_hash'] = 'd' * 64
    changed['snapshot']['collected_at'] = '2026-09-21T00:00:00+00:00'
    changed['latest_attempt']['id'] = 9
    changed['latest_attempt']['finished_at'] = '2026-09-21T00:00:01+00:00'
    changed['last_success'] = deepcopy(changed['latest_attempt'])
    changed['comparison']['after'] = {'snapshot': deepcopy(changed['snapshot']),
                                     'records': deepcopy(changed['records']),
                                     'run': deepcopy(changed['last_success'])}
    later = daily_facts.build_daily_fact_pack(changed, ids)
    assert original['fact_hash'] != later['fact_hash']
    assert daily_facts.semantic_daily_payload(original) == daily_facts.semantic_daily_payload(later)
    changed['records'][4]['pricing']['price_1m_input_tokens'] = 123
    numeric = daily_facts.build_daily_fact_pack(changed, ids)
    assert daily_facts.semantic_daily_payload(later) != daily_facts.semantic_daily_payload(numeric)
    changed['records'][4]['evaluation_date'] = '2026-01-01'
    evaluated = daily_facts.build_daily_fact_pack(changed, ids)
    assert daily_facts.semantic_daily_payload(numeric) != daily_facts.semantic_daily_payload(evaluated)


def test_byte_limit_reduces_tail_examples_deterministically_and_discloses(state, monkeypatch):
    two = daily_facts.build_daily_fact_pack(state, choose(state))
    four = daily_facts.build_daily_fact_pack(state, choose(state, 4))
    assert len(canonical(two).encode('utf-8')) < len(canonical(four).encode('utf-8'))
    budget = len(canonical(two).encode('utf-8')) + 700
    monkeypatch.setattr(daily_facts, 'MAX_PACK_BYTES', budget)
    result = daily_facts.build_daily_fact_pack(state, choose(state, 4))
    scope = result['scope']['selected_examples']
    assert scope['requested_count'] == 4 and scope['retained_count'] < 4
    assert scope['omitted_from_selection'] > 0
    assert result['scope']['reduction']['applied']
    assert len(canonical(result).encode('utf-8')) <= budget
    monkeypatch.setattr(daily_facts, 'MAX_PACK_BYTES', 100)
    with pytest.raises(ValueError, match='^日更事实包无法生成，请继续查看本地全量规则概况。$'):
        daily_facts.build_daily_fact_pack(state, choose(state, 4))


def test_ready_zero_change_cache_ignores_snapshot_storage_reuse_only(state):
    state = with_history(state)
    distinct = daily_facts.build_daily_fact_pack(state)
    state['comparison']['before']['snapshot'] = deepcopy(state['snapshot'])
    reused = daily_facts.build_daily_fact_pack(state)
    assert distinct['fact_hash'] != reused['fact_hash']
    assert daily_facts.semantic_daily_payload(distinct) == daily_facts.semantic_daily_payload(reused)
    state['latest_attempt']['status'] = 'failed'
    failed = daily_facts.build_daily_fact_pack(state)
    assert daily_facts.semantic_daily_payload(reused) != daily_facts.semantic_daily_payload(failed)


def test_repeated_current_content_does_not_reuse_an_older_nonempty_change_interval(state):
    state = with_history(state)
    state['comparison']['before']['records'][4]['pricing']['price_1m_input_tokens'] = 2
    nonempty = daily_facts.build_daily_fact_pack(state)
    before = deepcopy(state['comparison']['after'])
    after = deepcopy(before)
    after['run']['id'] += 1
    after['run']['finished_at'] = '2026-09-20T01:00:00+00:00'
    state['comparison'] = {'status': 'ready', 'before': before, 'after': after}
    state['latest_attempt'] = deepcopy(after['run'])
    state['last_success'] = deepcopy(after['run'])
    unchanged = daily_facts.build_daily_fact_pack(state)
    assert unchanged['snapshot'] == nonempty['snapshot']
    assert daily_facts.semantic_daily_payload(unchanged) != daily_facts.semantic_daily_payload(nonempty)
    assert next(row for row in nonempty['facts'] if row['kind'] == 'changes')['counts']['value'] == 1
    assert not any(next(row for row in unchanged['facts'] if row['kind'] == 'changes')['counts'].values())


def test_empty_overview_and_fixed_failure_without_io(state, monkeypatch):
    import socket
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError('No I/O is permitted')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    overview = daily_facts.build_daily_overview({'records': [], 'snapshot': None})
    assert overview['total_records'] == 0 and overview['changes']['status'] == 'empty'
    assert len(overview['coverage']) == 23
    assert daily_facts.build_daily_fact_pack(state)['facts']
    with pytest.raises(ValueError) as error:
        daily_facts.build_daily_fact_pack({'records': [], 'snapshot': None})
    assert str(error.value) == daily_facts.SAFE_ERROR


def test_invalid_selection_is_rejected_and_metadata_text_not_in_pack(state):
    with pytest.raises(ValueError, match='^日更事实包无法生成，请继续查看本地全量规则概况。$'):
        daily_facts.build_daily_fact_pack(state, [state['records'][0]['id']] * 2)
    state['snapshot']['metadata']['evaluation_config'] = {'instruction': 'SYNTHETIC_PRIVATE_SECRET'}
    state['snapshot']['metadata']['metric_units'] = {SPEED: 'SYNTHETIC_PRIVATE_UNIT'}
    assert 'SYNTHETIC_PRIVATE' not in canonical(daily_facts.build_daily_fact_pack(state))


def test_default_selection_mapping_and_single_record_aggregate_fallback(state):
    from benchmark_dashboard.selection import choose_acceptance_ids

    assert daily_facts.daily_example_ids(state) == choose_acceptance_ids(state)
    state['records'] = state['records'][:1]
    state['comparison']['after']['records'] = deepcopy(state['records'])
    pack = daily_facts.build_daily_fact_pack(state)
    assert pack['scope']['all_records']['total_records'] == 1
    assert pack['scope']['selected_count'] == 0
    assert daily_facts.daily_example_ids(state) == []
    assert '不足两条' in next(row['text'] for row in pack['facts'] if row['kind'] == 'limitations')
    assert briefing._validate_pack(pack, briefing.AnalysisSettings(input_mode='real')) == pack
