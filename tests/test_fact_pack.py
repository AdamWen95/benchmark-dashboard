"""M2B minimal fact packs: synthetic states and isolated temporary DBs only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark_dashboard import fact_pack, insights, metrics, store
from benchmark_dashboard.fact_pack import build_fact_pack, compute_fact_hash
from benchmark_dashboard.validation import validate_payload


INDEX = 'evaluations.artificial_analysis_intelligence_index'
PRICE = 'pricing.price_1m_input_tokens'
LCB = 'evaluations.livecodebench'
TB = 'evaluations.terminalbench_hard'
TB2 = 'evaluations.terminalbench_v2_1'
ANSWER = 'median_time_to_first_answer_token'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@pytest.fixture
def synthetic_state():
    records = []
    for index in range(3):
        records.append({'id': f'synthetic-{index}', 'name': 'SYNTHETIC_PRIVATE_NAME',
                        'slug': 'SYNTHETIC_PRIVATE_SLUG',
                        'evaluations': {'artificial_analysis_intelligence_index': 20 + index,
                                        'artificial_analysis_coding_index': None,
                                        'artificial_analysis_math_index': 0,
                                        'livecodebench': 0.2, 'terminalbench_hard': 0.4,
                                        'terminalbench_v2_1': None, 'gpqa': 0.3},
                        'pricing': {'price_1m_input_tokens': index,
                                    'price_1m_output_tokens': 2},
                        'median_output_tokens_per_second': 20,
                        'median_time_to_first_token_seconds': 0.25,
                        'median_time_to_first_answer_token': 1.25})
    snapshot = {'id': 7, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
                'collected_at': '2026-09-19T00:00:00+00:00',
                'metadata': {}, 'prompt_options': {'prompt_length': 'medium'}}
    paths = [f'evaluations.{key}' for key in records[0]['evaluations']]
    paths += [f'pricing.{key}' for key in records[0]['pricing']]
    paths += ['median_output_tokens_per_second', 'median_time_to_first_token_seconds', ANSWER]
    snapshot['coverage'] = dict.fromkeys(paths, len(records))
    run = {'id': 1, 'source': 'artificial_analysis', 'status': 'success', 'snapshot_id': 7,
           'started_at': '2026-09-19T00:00:00+00:00', 'finished_at': '2026-09-19T00:00:01+00:00'}
    point = {'run': deepcopy(run), 'snapshot': deepcopy(snapshot), 'records': deepcopy(records)}
    return {'records': records, 'snapshot': snapshot, 'latest_attempt': run, 'last_success': run,
            'metric_paths': paths, 'snapshot_count': 1,
            'comparison': {'status': 'baseline', 'before': None, 'after': point,
                           'message': 'SYNTHETIC_UNTRUSTED_COMPARISON_TEXT'}}


def facts(pack, kind):
    return [fact for fact in pack['facts'] if fact['kind'] == kind]


def example(pack, path, model_id='synthetic-0'):
    return next(fact for fact in facts(pack, 'metric_example')
                if fact['metric_path'] == path and fact['model_id'] == model_id)


def test_minimal_pack_is_deterministic_under_record_dict_and_path_order(synthetic_state):
    state = synthetic_state
    before = deepcopy(state)
    first = build_fact_pack(state)
    reordered = json.loads(canonical(state))
    reordered['records'].reverse()
    reordered['metric_paths'].reverse()
    assert first == build_fact_pack(reordered)
    assert before == state
    assert first['schema_version'] == 'm2b-facts-v1'
    assert first['metric_mapping_version'] == metrics.METRIC_MAPPING_VERSION
    assert first['source'] == 'artificial_analysis'
    assert first['snapshot'] == {key: state['snapshot'][key] for key in ('id', 'content_hash', 'collected_at')}
    assert first['rule_version'] == insights.RULE_VERSION
    assert compute_fact_hash(first) == first['fact_hash']
    content = {key: value for key, value in first.items() if key != 'fact_hash'}
    assert first['fact_hash'] == sha256(canonical(content).encode('utf-8')).hexdigest()
    assert len(first['facts']) <= 64
    assert len(canonical(first).encode('utf-8')) <= 128 * 1024


def test_example_selection_is_explicit_small_and_not_a_ranking(synthetic_state):
    pack = build_fact_pack(synthetic_state)
    scope = pack['scope']
    assert scope['selection_rule'] == 'stable_id_lexicographic_first_2'
    assert scope['total_records'] == 3
    assert scope['example_count'] == 2
    assert scope['omitted_records'] == 1
    assert scope['example_ids'] == [{'source': 'artificial_analysis', 'model_id': f'synthetic-{i}'}
                                    for i in (0, 1)]
    assert len(facts(pack, 'metric_example')) == 22
    assert '全市场排名' in facts(pack, 'limitations')[0]['text']
    assert all(row['model_id'] in ('synthetic-0', 'synthetic-1') for row in facts(pack, 'metric_example'))


def test_coverage_is_full_snapshot_and_zero_is_valid(synthetic_state):
    state = synthetic_state
    state['records'][1]['pricing']['price_1m_input_tokens'] = None
    state['records'][2]['pricing']['price_1m_input_tokens'] = float('nan')
    pack = build_fact_pack(state)
    coverage = next(row for row in facts(pack, 'coverage') if row['metric_path'] == PRICE)
    assert (coverage['valid_count'], coverage['total_records'], coverage['missing_count']) == (1, 3, 2)
    assert example(pack, PRICE)['raw_value'] == 0
    assert example(pack, PRICE)['display_value'] == '0'
    assert example(pack, PRICE)['coverage_total'] == 3
    assert example(pack, PRICE)['coverage_valid'] == 1
    assert example(pack, PRICE, 'synthetic-1')['raw_value'] is None
    assert example(pack, PRICE, 'synthetic-1')['display_value'] == '暂无'


def test_unknown_and_partial_metrics_never_become_directional_facts(synthetic_state):
    pack = build_fact_pack(synthetic_state)
    for path in (INDEX, LCB, TB, TB2, ANSWER):
        row = example(pack, path)
        assert row['direction'] == 'unknown'
        assert row['directional_observation_allowed'] is False
        assert row['assessment']['status'] in ('unconfirmed', 'changed')
    referenced = {fact_id for row in facts(pack, 'rule') for fact_id in row['evidence_fact_ids']}
    assert example(pack, INDEX)['id'] not in referenced
    assert all('综合智能指数' not in row['text'] for row in facts(pack, 'rule'))


def test_metric_mapping_contains_definition_units_formula_evidence_and_versions(synthetic_state):
    pack = build_fact_pack(synthetic_state)
    for row in facts(pack, 'coverage'):
        mapping = row['mapping']
        assert {'definition', 'raw_unit', 'display_unit', 'conversion_formula', 'verification_status',
                'official_sources', 'verified_at', 'metric_mapping_version', 'rule_version'} <= mapping.keys()
        assert mapping['metric_mapping_version'] == pack['metric_mapping_version']
        assert mapping['rule_version'] == pack['rule_version']
        assert isinstance(mapping['official_sources'], list)
    assert example(pack, PRICE)['coverage_fact_id'] in {row['id'] for row in facts(pack, 'coverage')}


def test_unknown_metadata_remains_null_and_supplied_free_text_not_transmitted(synthetic_state):
    pack = build_fact_pack(synthetic_state)
    metadata = example(pack, INDEX)['metadata']
    assert metadata['evaluation_version'] is None
    assert metadata['evaluation_date'] is None
    assert metadata['configuration'] is None
    assert '源站未提供' in metadata['note']
    synthetic_state['records'][0]['evaluation_config'] = {'instruction': 'SYNTHETIC_CONFIG_SECRET'}
    synthetic_state['records'][0]['evaluation_version'] = 'SYNTHETIC_VERSION_SECRET'
    synthetic_state['records'][0]['evaluation_date'] = 'SYNTHETIC_DATE_SECRET'
    encoded = canonical(build_fact_pack(synthetic_state))
    assert all(value not in encoded for value in ['SYNTHETIC_CONFIG_SECRET', 'SYNTHETIC_VERSION_SECRET',
                                                 'SYNTHETIC_DATE_SECRET'])


def test_supplied_evaluation_metadata_not_falsely_reported_missing_for_price(synthetic_state):
    synthetic_state['records'][0]['evaluation_version'] = 'SYNTHETIC_PRIVATE_VERSION'
    synthetic_state['records'][0]['evaluation_date'] = '2026-09-18'
    synthetic_state['records'][0]['evaluation_config'] = {'mode': 'SYNTHETIC_PRIVATE_CONFIG'}
    pack = build_fact_pack(synthetic_state)
    metadata = example(pack, PRICE)['metadata']
    assert all(metadata['provided_locally'].values())
    assert '源站未提供' not in metadata['note']
    assert 'SYNTHETIC_PRIVATE_VERSION' not in canonical(pack)


def test_missing_configuration_prevents_speed_rule_without_erasing_explicit_context(synthetic_state, monkeypatch):
    synthetic_state['records'][0]['reasoning_effort'] = 'high'
    synthetic_state['records'][1]['reasoning_effort'] = 'low'
    called = []
    original = fact_pack.generate_insights

    def watch(records, snapshot, paths):
        called.extend(paths)
        return original(records, snapshot, paths)

    monkeypatch.setattr(fact_pack, 'generate_insights', watch)
    pack = build_fact_pack(synthetic_state)
    assert not called
    assert all(row['directional_observation_allowed'] is False for row in facts(pack, 'metric_example'))
    assert not facts(pack, 'rule')


@pytest.mark.parametrize('unit', ['%', {'invalid': 'SYNTHETIC_UNIT_SECRET'}])
def test_conflicting_units_preserve_raw_without_conversion_or_direction(synthetic_state, unit):
    synthetic_state['snapshot']['metadata']['metric_units'] = {PRICE: unit}
    pack = build_fact_pack(synthetic_state)
    row = example(pack, PRICE)
    assert row['unit'] == '源站原值（单位声明待核对）'
    assert row['assessment']['unit_conflict'] is True
    assert row['direction'] == 'unknown'
    assert row['directional_observation_allowed'] is False
    assert 'SYNTHETIC_UNIT_SECRET' not in canonical(pack)
    assert not any(row['id'] in rule['evidence_fact_ids'] for rule in facts(pack, 'rule'))


def test_baseline_never_says_no_changes_and_does_not_copy_source_message(synthetic_state):
    pack = build_fact_pack(synthetic_state)
    change = facts(pack, 'changes')[0]
    assert change['status'] == 'baseline'
    assert change['latest_attempt_failed'] is False
    assert '初始基线' in change['text']
    assert '无数据变化' not in change['text']
    assert 'SYNTHETIC_UNTRUSTED_COMPARISON_TEXT' not in canonical(pack)


def test_ready_same_snapshot_uses_no_change_statement_but_different_snapshot_does_not(synthetic_state):
    state = synthetic_state
    state['comparison']['status'] = 'ready'
    state['comparison']['before'] = deepcopy(state['comparison']['after'])
    state['comparison']['after']['run']['id'] = 2
    pack = build_fact_pack(state)
    change = facts(pack, 'changes')[0]
    assert change['status'] == 'ready' and change['same_snapshot']
    assert '无数据变化' in change['text']
    state['comparison']['before']['snapshot']['content_hash'] = 'b' * 64
    state['comparison']['before']['snapshot']['id'] = 6
    change = facts(build_fact_pack(state), 'changes')[0]
    assert not change['same_snapshot']
    assert '无数据变化' not in change['text']


def test_failed_attempt_warns_first_without_copying_stored_error(synthetic_state):
    synthetic_state['latest_attempt'] = {'id': 2, 'status': 'failed', 'message': 'SYNTHETIC_SECRET',
                                         'error_code': 'SYNTHETIC_SECRET'}
    pack = build_fact_pack(synthetic_state)
    change = facts(pack, 'changes')[0]
    assert change['latest_attempt_failed'] is True
    assert change['status'] == 'baseline'
    assert change['text'].startswith('最近一次采集失败')
    assert '初始基线' in change['text']
    assert 'SYNTHETIC_SECRET' not in canonical(pack)


def test_unknown_status_is_unavailable_not_free_text(synthetic_state):
    synthetic_state['comparison'] = {'status': 'SYNTHETIC_SECRET', 'message': 'SYNTHETIC_SECRET'}
    change = facts(build_fact_pack(synthetic_state), 'changes')[0]
    assert change['status'] == 'unavailable'
    assert 'SYNTHETIC_SECRET' not in canonical(change)


def test_single_record_does_not_call_two_model_rules(synthetic_state, monkeypatch):
    synthetic_state['records'] = synthetic_state['records'][:1]
    monkeypatch.setattr(fact_pack, 'generate_insights', lambda *a: pytest.fail('Cannot compare one record'))
    pack = build_fact_pack(synthetic_state)
    assert pack['scope']['example_count'] == 1
    assert not facts(pack, 'rule')
    assert '不足两条' in facts(pack, 'limitations')[0]['text']


def test_rules_reuse_same_selected_records_and_bind_real_fact_ids(synthetic_state, monkeypatch):
    original = fact_pack.generate_insights
    calls = []

    def watch(records, snapshot, paths):
        calls.append((records, snapshot, paths))
        return original(records, snapshot, paths)

    monkeypatch.setattr(fact_pack, 'generate_insights', watch)
    pack = build_fact_pack(synthetic_state)
    assert len(calls) == 1
    assert [row['id'] for row in calls[0][0]] == ['synthetic-0', 'synthetic-1']
    assert calls[0][1]['id'] == pack['snapshot']['id']
    assert all(metrics.metric_for(path).verification_status == 'confirmed' for path in calls[0][2])
    example_ids = {row['id'] for row in facts(pack, 'metric_example')}
    assert facts(pack, 'rule')
    for row in facts(pack, 'rule'):
        assert row['evidence_fact_ids'] and set(row['evidence_fact_ids']) <= example_ids


def test_untrusted_record_envelope_and_unknown_fields_do_not_escape(synthetic_state):
    state = synthetic_state
    state['snapshot']['metadata']['instructions'] = '<script>SYNTHETIC_SECRET</script>'
    state['snapshot']['payload_json'] = 'SYNTHETIC_SECRET'
    state['metric_paths'].append('evaluations.SYNTHETIC_SECRET')
    for row in state['records']:
        row.update(name='SYNTHETIC_SECRET', slug='SYNTHETIC_SECRET', arbitrary='SYNTHETIC_SECRET')
        row['evaluations']['SYNTHETIC_SECRET'] = 99
    encoded = canonical(build_fact_pack(state))
    assert all(value not in encoded for value in ('SYNTHETIC_SECRET', '<script>', 'payload_json',
                                                  'SYNTHETIC_PRIVATE_NAME', 'SYNTHETIC_PRIVATE_SLUG'))


def test_source_identity_cannot_inject_arbitrary_text(synthetic_state):
    synthetic_state['snapshot']['source'] = 'SYNTHETIC_SECRET'
    with pytest.raises(ValueError, match='^本地事实包暂时无法生成，请保留原表与规则说明。$'):
        build_fact_pack(synthetic_state)


def test_stable_ids_are_preserved_in_full_and_not_merged_by_shared_prefix(synthetic_state):
    shared = 'synthetic-' + 'x' * 300
    synthetic_state['records'][0]['id'] = shared + '-a'
    synthetic_state['records'][1]['id'] = shared + '-b'
    synthetic_state['records'] = synthetic_state['records'][:2]
    pack = build_fact_pack(synthetic_state)
    assert {row['model_id'] for row in facts(pack, 'metric_example')} == {shared + '-a', shared + '-b'}
    assert len({row['id'] for row in pack['facts']}) == len(pack['facts'])


@pytest.mark.parametrize('mutation', ['snapshot', 'value', 'mapping_version', 'run_state'])
def test_relevant_input_changes_invalidate_fact_hash(synthetic_state, monkeypatch, mutation):
    original = build_fact_pack(synthetic_state)['fact_hash']
    if mutation == 'snapshot':
        synthetic_state['snapshot']['content_hash'] = 'b' * 64
    elif mutation == 'value':
        synthetic_state['records'][0]['pricing']['price_1m_input_tokens'] = 999
    elif mutation == 'mapping_version':
        monkeypatch.setattr(metrics, 'METRIC_MAPPING_VERSION', 'm2b-metrics-synthetic-new')
    else:
        synthetic_state['latest_attempt'] = {'id': 2, 'status': 'failed'}
    assert build_fact_pack(synthetic_state)['fact_hash'] != original


def test_oversized_pack_fails_closed_without_truncating_identity_or_echoing_input(synthetic_state):
    synthetic_state['records'][0]['id'] = 'SYNTHETIC_SECRET' * 100000
    with pytest.raises(ValueError) as error:
        build_fact_pack(synthetic_state)
    assert str(error.value) == '本地事实包暂时无法生成，请保留原表与规则说明。'


def test_final_serialized_size_limit_is_enforced_without_partial_pack(synthetic_state, monkeypatch):
    monkeypatch.setattr(fact_pack, 'MAX_PACK_BYTES', 100)
    with pytest.raises(ValueError, match='^本地事实包暂时无法生成，请保留原表与规则说明。$'):
        build_fact_pack(synthetic_state)


def test_all_allowed_fields_stay_bounded_and_do_not_export_whole_records(synthetic_state):
    synthetic_state['metric_paths'] = list(metrics.DISPLAY_METRICS)
    pack = build_fact_pack(synthetic_state)
    assert len(facts(pack, 'coverage')) == len(metrics.DISPLAY_METRICS)
    assert len(facts(pack, 'metric_example')) == 2 * len(fact_pack.EXAMPLE_PATHS)
    assert len(canonical(pack).encode('utf-8')) <= 128 * 1024
    assert len(pack['facts']) <= 64


def test_hash_rejects_nonfinite_or_unserializable_values_without_source_text():
    with pytest.raises(ValueError, match='^本地事实包暂时无法生成，请保留原表与规则说明。$'):
        compute_fact_hash({'SYNTHETIC_SECRET': float('nan')})


def test_empty_state_has_fixed_message():
    with pytest.raises(ValueError, match='^暂无有效快照，无法准备本地事实包。$'):
        build_fact_pack({'records': [], 'snapshot': None})


def test_fact_pack_never_opens_files_database_network_or_environment(synthetic_state, monkeypatch):
    import builtins
    import socket
    import sqlite3

    def forbidden(*a, **kw):
        pytest.fail('Fact pack must consume the supplied state without I/O')

    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    assert build_fact_pack(synthetic_state)['facts']


def acquisition(payload, day):
    return SimpleNamespace(valid=validate_payload(payload),
                           started_at=f'2026-09-{day:02d}T08:00:00+00:00',
                           collected_at=f'2026-09-{day:02d}T08:00:01+00:00',
                           http_status=200, attempts=1, snapshot_path=Path('synthetic-unused.json'))


def test_isolated_database_changes_and_failed_run_are_aggregated_without_records(tmp_path, payload):
    db = tmp_path / 'synthetic.sqlite3'
    store.record_success(db, acquisition(payload, 19))
    after = deepcopy(payload)
    after['data'][0]['pricing']['price_1m_input_tokens'] = 2
    after['data'][0]['private_notes'] = 'SYNTHETIC_PRIVATE_RENAME'
    store.record_success(db, acquisition(after, 20))
    store.record_failure(db, started_at='2026-09-21T08:00:00+00:00',
                         finished_at='2026-09-21T08:00:01+00:00', error_code='timeout',
                         message='SYNTHETIC_SECRET', attempts=1)
    state = store.read_dashboard(db)
    before = db.read_bytes()
    pack = build_fact_pack(state)
    change = facts(pack, 'changes')[0]
    assert change['status'] == 'ready'
    assert change['latest_attempt_failed'] is True
    assert change['counts']['value'] == 1 and change['counts']['metadata'] == 1
    assert db.read_bytes() == before
    assert 'SYNTHETIC_PRIVATE_RENAME' not in canonical(pack)
    assert 'SYNTHETIC_SECRET' not in canonical(pack)
    assert 'records' not in change and 'events' not in change
