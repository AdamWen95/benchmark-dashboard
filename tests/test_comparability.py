"""Synthetic metadata only: no credentials, source requests or real DB access."""
from copy import deepcopy
import pytest

from benchmark_dashboard.comparability import assess_metric

INDEX = 'evaluations.artificial_analysis_intelligence_index'
PRICE = 'pricing.price_1m_input_tokens'
SPEED = 'median_output_tokens_per_second'


def contexts():
    record = {'id': 'synthetic-1', 'name': '合成', 'slug': 'synthetic',
              'model_creator': {'id': 'creator', 'name': '合成厂商'},
              'evaluation_version': 'synthetic-v1', 'evaluation_config': {'effort': 'high'}}
    snapshot = {'source': 'artificial_analysis', 'prompt_options': {'prompt_length': 1000}, 'metadata': {}}
    return [record, deepcopy(record)], [snapshot, deepcopy(snapshot)]


def test_equal_explicit_context_is_comparable():
    records, snapshots = contexts()
    assert assess_metric(INDEX, records, snapshots)['status'] == 'comparable'
    assert assess_metric(PRICE, records, snapshots)['status'] == 'comparable'


@pytest.mark.parametrize('key,new', [('evaluation_version', 'v2'),
    ('evaluation_config', {'effort': 'low'}), ('name', '新名称'), ('slug', 'new-slug'),
    ('model_creator', {'id': 'other'})])
def test_explicit_context_and_same_id_identity_updates_block_delta(key, new):
    records, snapshots = contexts()
    records[1][key] = new
    assert assess_metric(INDEX, records, snapshots)['status'] == 'changed'


def test_unknown_version_or_configuration_does_not_claim_comparable():
    records, snapshots = contexts()
    for record in records:
        record.pop('evaluation_version')
        record.pop('evaluation_config')
    result = assess_metric(INDEX, records, snapshots)
    assert result['status'] == 'unconfirmed'
    assert '源站未提供' in result['reason']


def test_prompt_parameters_only_apply_to_speed_not_evaluation_config():
    records, snapshots = contexts()
    snapshots[1]['prompt_options'] = {'prompt_length': 10000}
    assert assess_metric(SPEED, records, snapshots)['status'] == 'changed'
    assert assess_metric(INDEX, records, snapshots)['status'] == 'comparable'


def test_explicit_unit_change_blocks_delta():
    records, snapshots = contexts()
    snapshots[0]['metadata']['metric_units'] = {PRICE: '美元/百万 Token'}
    snapshots[1]['metadata']['metric_units'] = {PRICE: '美元/千 Token'}
    assert assess_metric(PRICE, records, snapshots)['status'] == 'changed'


def test_same_but_contradictory_source_unit_is_unconfirmed():
    records, snapshots = contexts()
    for snapshot in snapshots:
        snapshot['metadata']['metric_units'] = {PRICE: '未核验单位'}
    assert assess_metric(PRICE, records, snapshots)['status'] == 'unconfirmed'


def test_malformed_unit_declaration_is_unconfirmed():
    records, snapshots = contexts()
    for snapshot in snapshots:
        snapshot['metadata']['metric_units'] = {PRICE: {'unexpected': 'unit-object'}}
    assert assess_metric(PRICE, records, snapshots)['status'] == 'unconfirmed'


def test_unknown_field_never_claims_comparable():
    records, snapshots = contexts()
    assert assess_metric('evaluations.new_unknown', records, snapshots)['status'] == 'unconfirmed'


def test_different_ids_names_not_metadata_rename():
    records, snapshots = contexts()
    records[1].update(id='synthetic-2', name='另一个模型', slug='another')
    assert assess_metric(INDEX, records, snapshots)['status'] == 'comparable'


def test_json_order_unrelated_metadata_and_acquisition_time_irrelevant():
    records, snapshots = contexts()
    records[0]['evaluation_config'] = {'a': 1, 'b': 2}
    records[1]['evaluation_config'] = {'b': 2, 'a': 1}
    snapshots[1]['collected_at'] = '2026-01-02'
    snapshots[1]['metadata']['irrelevant'] = 'changed'
    assert assess_metric(INDEX, records, snapshots)['status'] == 'comparable'


def test_mismatched_source_and_missing_context_fail_closed():
    records, snapshots = contexts()
    snapshots[1]['source'] = 'another-source'
    assert assess_metric(INDEX, records, snapshots)['status'] == 'changed'
    assert assess_metric(INDEX, [], [])['status'] == 'unconfirmed'


@pytest.mark.parametrize('field', ['metric_versions', 'evaluation_versions'])
@pytest.mark.parametrize('value', [['invalid-list'], 'not-a-mapping', True, 3])
def test_malformed_version_map_is_not_evidence_of_comparability(field, value):
    records, snapshots = contexts()
    for record in records:
        record.pop('evaluation_version')
        record[field] = deepcopy(value)
    result = assess_metric(INDEX, records, snapshots)
    assert result['status'] == 'unconfirmed'
    assert '评测版本元数据格式未确认' in result['reason']


@pytest.mark.parametrize('value', [{'unexpected': 'object'}, ['v1'], True, float('inf'), '   '])
def test_malformed_version_value_is_unconfirmed_even_beside_a_valid_version(value):
    records, snapshots = contexts()
    for record in records:
        record['metric_versions'] = {INDEX: deepcopy(value)}
    result = assess_metric(INDEX, records, snapshots)
    assert result['status'] == 'unconfirmed'
    assert '评测版本元数据格式未确认' in result['reason']


@pytest.mark.parametrize('value', [['not-a-config'], True, 42, {'temperature': float('inf')}, '   '])
def test_malformed_configuration_never_confirms_evaluation_comparability(value):
    records, snapshots = contexts()
    for record in records:
        record['evaluation_config'] = deepcopy(value)
    result = assess_metric(INDEX, records, snapshots)
    assert result['status'] == 'unconfirmed'
    assert '运行配置元数据格式未确认' in result['reason']


@pytest.mark.parametrize('value', ['not-an-options-object', True, ['unexpected']])
def test_malformed_prompt_options_do_not_confirm_speed(value):
    records, snapshots = contexts()
    for snapshot in snapshots:
        snapshot['prompt_options'] = deepcopy(value)
    result = assess_metric(SPEED, records, snapshots)
    assert result['status'] == 'unconfirmed'
    assert '测试参数元数据格式未确认' in result['reason']


def test_finite_numeric_version_and_explicit_configuration_label_remain_supported():
    records, snapshots = contexts()
    for record in records:
        record['evaluation_version'] = 2
        record['evaluation_config'] = 'synthetic-fixed-configuration'
    assert assess_metric(INDEX, records, snapshots)['status'] == 'comparable'


@pytest.mark.parametrize('field', ['evaluation_version', 'evaluation_config'])
@pytest.mark.parametrize('missing', [None, {}, [], ''])
def test_same_identity_context_becoming_missing_is_a_historical_change(field, missing):
    records, snapshots = contexts()
    records[1][field] = missing
    assert assess_metric(INDEX, records, snapshots)['status'] == 'changed'
    records.reverse()
    assert assess_metric(INDEX, records, snapshots)['status'] == 'changed'


def test_different_id_partial_context_remains_unconfirmed():
    records, snapshots = contexts()
    records[1]['id'] = 'synthetic-other-model'
    records[1].pop('evaluation_version')
    assert assess_metric(INDEX, records, snapshots)['status'] == 'unconfirmed'


@pytest.mark.parametrize('path,scope,key,value', [
    (INDEX, 'metadata', 'evaluation_version', 'synthetic-version'),
    (INDEX, 'metadata', 'evaluation_config', {'reasoning': 'fixed'}),
    (PRICE, 'metadata', 'metric_units', {PRICE: '美元/百万 Token'}),
    (SPEED, 'snapshot', 'prompt_options', {'prompt_length': 1000}),
])
def test_context_disappearing_emits_event_even_when_score_unchanged(path, scope, key, value):
    from benchmark_dashboard.changes import compare_runs

    records, snapshots = contexts()
    for record in records:
        record['evaluations'] = {'artificial_analysis_intelligence_index': 0}
        record['pricing'] = {'price_1m_input_tokens': 0}
        record[SPEED] = 10
    target_before = snapshots[0] if scope == 'snapshot' else snapshots[0]['metadata']
    target_after = snapshots[1] if scope == 'snapshot' else snapshots[1]['metadata']
    target_before[key] = value
    target_after.pop(key, None)
    points = [{'run': {'id': index}, 'snapshot': snapshot, 'records': [record]}
              for index, (record, snapshot) in enumerate(zip(records, snapshots), start=1)]
    result = compare_runs({'status': 'ready', 'before': points[0], 'after': points[1]})
    event = next(event for event in result['events'] if event['type'] == 'context' and event['path'] == path)
    assert event['before'] == event['after']
    assert event['absolute'] is None and event['relative_percent'] is None
    assert result['message'] != '与上次成功采集相比无数据变化'


def test_context_change_never_invents_metric_events_for_two_missing_values():
    from benchmark_dashboard.changes import compare_runs

    records, snapshots = contexts()
    snapshots[0]['metadata']['evaluation_version'] = 'synthetic-version'
    points = [{'run': {'id': index}, 'snapshot': snapshot, 'records': [record]}
              for index, (record, snapshot) in enumerate(zip(records, snapshots), start=1)]
    result = compare_runs({'status': 'ready', 'before': points[0], 'after': points[1]})
    assert not result['events']
