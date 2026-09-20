"""M2A history uses synthetic fixtures and temporary databases exclusively."""
from copy import deepcopy
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from benchmark_dashboard.changes import compare_runs
from benchmark_dashboard import store
from benchmark_dashboard.validation import validate_payload


INDEX = 'evaluations.artificial_analysis_intelligence_index'
PRICE = 'pricing.price_1m_input_tokens'
SPEED = 'median_output_tokens_per_second'


def acquisition(payload, day=19):
    return SimpleNamespace(valid=validate_payload(payload),
        started_at=f'2026-09-{day:02d}T08:00:00+00:00',
        collected_at=f'2026-09-{day:02d}T08:00:01+00:00',
        http_status=200, attempts=1, snapshot_path=Path('unused-synthetic.json'))


def record_pair(tmp_path, before, after):
    db = tmp_path / 'synthetic.sqlite3'
    store.record_success(db, acquisition(before))
    store.record_success(db, acquisition(after, day=20))
    state = store.read_dashboard(db)
    return db, state, compare_runs(state['comparison'])


def event_for(result, kind, path):
    return next(event for event in result['events'] if event['type'] == kind and event['path'] == path)


def confirmed(payload):
    data = deepcopy(payload)
    data['evaluation_version'] = 'synthetic-version-1'
    data['evaluation_config'] = {'reasoning': 'synthetic-fixed'}
    return data


def test_empty_and_one_success_are_honest_baseline(tmp_path, payload):
    db = tmp_path / 'synthetic.sqlite3'
    assert compare_runs(store.read_dashboard(db)['comparison'])['status'] == 'empty'
    assert not db.exists()
    store.initialize(db)
    assert store.read_dashboard(db)['comparison']['status'] == 'empty'
    store.record_success(db, acquisition(payload))
    state = store.read_dashboard(db)
    result = compare_runs(state['comparison'])
    assert result['status'] == 'baseline'
    assert result['message'] == '已建立初始基线，暂无历史可比较'
    assert result['events'] == []
    assert result['counts']['added'] == 0
    assert result['before'] is None
    assert result['after']['records'] == state['records']
    assert result['after']['snapshot']['metadata']['prompt_options'] == payload['prompt_options']
    assert 'data' not in result['after']['snapshot']['metadata']


def test_latest_two_runs_are_used_even_when_snapshot_reused(tmp_path, payload):
    db = tmp_path / 'synthetic.sqlite3'
    store.record_success(db, acquisition(payload))
    payload['data'][0]['pricing']['price_1m_input_tokens'] = 3
    second = store.record_success(db, acquisition(payload, 20))
    third = store.record_success(db, acquisition(payload, 21))
    before_read = db.read_bytes()
    state = store.read_dashboard(db)
    result = compare_runs(state['comparison'])
    assert result['status'] == 'ready'
    assert result['before']['run']['id'] == second['run_id']
    assert result['after']['run']['id'] == third['run_id']
    assert result['before']['snapshot']['id'] == result['after']['snapshot']['id']
    assert result['message'] == '与上次成功采集相比无数据变化'
    assert result['events'] == []
    assert state['snapshot_count'] == 2
    assert db.read_bytes() == before_read


def test_latest_failed_attempt_does_not_replace_successful_history(tmp_path, payload):
    db = tmp_path / 'synthetic.sqlite3'
    first = store.record_success(db, acquisition(payload))
    second = store.record_success(db, acquisition(payload, 20))
    failed = store.record_failure(db, started_at='2026-09-21T08:00:00+00:00',
        finished_at='2026-09-21T08:00:01+00:00', error_code='timeout',
        message='合成测试连接超时。', attempts=1)
    state = store.read_dashboard(db)
    assert state['latest_attempt']['id'] == failed
    assert state['latest_attempt']['status'] == 'failed'
    assert state['comparison']['status'] == 'ready'
    assert state['comparison']['before']['run']['id'] == first['run_id']
    assert state['comparison']['after']['run']['id'] == second['run_id']
    assert state['records'] == payload['data']


def test_stable_ids_distinguish_rename_same_name_addition_and_removal(tmp_path, payload):
    before = deepcopy(payload)
    removed = deepcopy(before['data'][0])
    removed['id'] = 'synthetic-removed'
    before['data'].append(removed)
    after = deepcopy(payload)
    after['data'][0]['name'] = '合成更名'
    new = deepcopy(after['data'][0])
    new['id'] = 'synthetic-new-id'
    after['data'].append(new)
    _, _, result = record_pair(tmp_path, before, after)
    assert result['counts']['added'] == 1
    assert result['counts']['removed'] == 1
    assert result['counts']['metadata'] == 1
    assert event_for(result, 'metadata', 'name')['model_id'] == payload['data'][0]['id']
    added = next(event for event in result['events'] if event['type'] == 'added')
    assert added['model_id'] == 'synthetic-new-id'
    assert '不等于模型今天发布' in added['reason']
    removed_event = next(event for event in result['events'] if event['type'] == 'removed')
    assert '不等于模型停服' in removed_event['reason']
    assert not any(event['type'] == 'value' for event in result['events'])
    assert all(event['absolute'] is None for event in result['events'])


def test_source_is_part_of_model_identity(tmp_path, payload):
    _, state, _ = record_pair(tmp_path, payload, deepcopy(payload))
    comparison = deepcopy(state['comparison'])
    comparison['after']['snapshot']['source'] = 'synthetic_other_source'
    result = compare_runs(comparison)
    assert result['counts']['added'] == result['counts']['removed'] == 1
    assert result['counts']['value'] == 0


def test_missing_filled_and_zero_are_distinct(tmp_path, payload):
    before = deepcopy(payload)
    after = deepcopy(payload)
    before['data'][0]['pricing']['price_1m_input_tokens'] = None
    after['data'][0]['pricing']['price_1m_output_tokens'] = None
    _, _, result = record_pair(tmp_path, before, after)
    filled = event_for(result, 'filled', PRICE)
    missing = event_for(result, 'missing', 'pricing.price_1m_output_tokens')
    assert filled['before'] is None and filled['after'] == 0
    assert missing['before'] == 2 and missing['after'] is None
    assert filled['absolute'] is None and missing['absolute'] is None
    assert '从 0 提升' in filled['reason'] and '跌为 0' in missing['reason']


def test_zero_baseline_has_absolute_but_no_relative_change(tmp_path, payload):
    after = deepcopy(payload)
    after['data'][0]['pricing']['price_1m_input_tokens'] = 2
    _, _, result = record_pair(tmp_path, payload, after)
    event = event_for(result, 'value', PRICE)
    assert event['comparability'] == 'comparable'
    assert event['before'] == 0 and event['after'] == 2
    assert event['absolute'] == '2'
    assert event['relative_percent'] is None
    assert event['delta_unit'] == '美元/百万 Token'


def test_confirmed_index_delta_is_points_and_relative_percentage(tmp_path, payload):
    before = confirmed(payload)
    before['data'][0]['evaluations']['artificial_analysis_intelligence_index'] = 20
    after = deepcopy(before)
    after['data'][0]['evaluations']['artificial_analysis_intelligence_index'] = 25
    _, _, result = record_pair(tmp_path, before, after)
    event = event_for(result, 'value', INDEX)
    assert event['comparability'] == 'comparable'
    assert event['absolute'] == '5'
    assert event['relative_percent'] == '25'
    assert event['delta_unit'] == '指数点'


def test_missing_evaluation_version_and_configuration_prohibit_directional_delta(tmp_path, payload):
    after = deepcopy(payload)
    after['data'][0]['evaluations']['artificial_analysis_intelligence_index'] = 10
    _, _, result = record_pair(tmp_path, payload, after)
    event = event_for(result, 'value', INDEX)
    assert event['comparability'] == 'unconfirmed'
    assert event['before'] == 0 and event['after'] == 10
    assert event['absolute'] is None and event['relative_percent'] is None
    assert '口径未充分确认' in event['reason']


@pytest.mark.parametrize('key,old,new,path', [
    ('metric_units', {PRICE: '美元/百万 Token'}, {PRICE: '合成不同单位'}, PRICE),
    ('evaluation_version', 'synthetic-v1', 'synthetic-v2', INDEX),
    ('evaluation_config', {'reasoning': 'low'}, {'reasoning': 'high'}, INDEX),
    ('prompt_options', {'prompt_length': 'medium'}, {'prompt_length': 'long'}, SPEED),
])
def test_context_change_is_recorded_even_when_metric_value_unchanged(tmp_path, payload, key, old, new, path):
    before = confirmed(payload)
    before[key] = old
    after = deepcopy(before)
    after[key] = new
    _, _, result = record_pair(tmp_path, before, after)
    event = event_for(result, 'context', path)
    assert event['before'] == event['after']
    assert event['comparability'] == 'changed'
    assert event['absolute'] is None and event['relative_percent'] is None
    assert not any(event['type'] == 'value' and event['path'] == path for event in result['events'])


def test_precision_noise_and_unrelated_envelope_metadata_do_not_create_changes(tmp_path, payload):
    before = deepcopy(payload)
    before['data'][0]['pricing']['price_1m_input_tokens'] = 0.3
    before['source_notes'] = {'irrelevant': 'synthetic-before'}
    after = deepcopy(before)
    after['data'][0]['pricing']['price_1m_input_tokens'] = 0.1 + 0.2
    after['source_notes']['irrelevant'] = 'synthetic-after'
    _, _, result = record_pair(tmp_path, before, after)
    assert result['events'] == []
    assert result['message'] == '与上次成功采集相比无数据变化'


def test_unknown_metric_changes_never_get_inferred_units_or_direction(tmp_path, payload):
    before = deepcopy(payload)
    before['data'][0]['evaluations']['synthetic_unknown'] = 0
    after = deepcopy(before)
    after['data'][0]['evaluations']['synthetic_unknown'] = 0.5
    _, _, result = record_pair(tmp_path, before, after)
    event = event_for(result, 'value', 'evaluations.synthetic_unknown')
    assert event['comparability'] == 'unconfirmed'
    assert event['absolute'] is None and event['relative_percent'] is None


def test_unknown_record_metadata_change_is_not_a_metric_change(tmp_path, payload):
    after = deepcopy(payload)
    after['data'][0]['synthetic_note'] = '仅合成测试注释'
    _, _, result = record_pair(tmp_path, payload, after)
    assert result['counts']['metadata'] == 1
    assert result['counts']['value'] == result['counts']['context'] == 0


@pytest.mark.parametrize('statement', [
    "UPDATE acquisition_runs SET snapshot_id = 999 WHERE id = 1",
    "UPDATE acquisition_runs SET finished_at = 'not-a-time' WHERE id = 1",
    "UPDATE acquisition_runs SET finished_at = '2026-09-19T08:00:01' WHERE id = 1",
    "UPDATE acquisition_runs SET finished_at = '2026-09-25T08:00:01+00:00' WHERE id = 1",
    "UPDATE snapshots SET source = 'synthetic-wrong-source' WHERE id = 1",
    "UPDATE snapshots SET prompt_options_json = '{}' WHERE id = 1",
    "UPDATE model_records SET raw_json = '{}' WHERE snapshot_id = 1",
])
def test_damaged_history_is_unavailable_without_guessing_or_changing_db(tmp_path, payload, statement):
    after = deepcopy(payload)
    after['data'][0]['pricing']['price_1m_input_tokens'] = 2
    db, _, _ = record_pair(tmp_path, payload, after)
    # Deliberately corrupt only this isolated synthetic fixture database.
    with sqlite3.connect(db) as connection:
        connection.execute(statement)
    original = db.read_bytes()
    state = store.read_dashboard(db)
    assert state['records'] == after['data']
    result = compare_runs(state['comparison'])
    assert result['status'] == 'unavailable'
    assert '缺少比较依据' in result['message']
    assert result['events'] == []
    assert db.read_bytes() == original


def test_deterministic_local_comparison_never_mutates_input(tmp_path, payload, monkeypatch):
    after = deepcopy(payload)
    after['data'][0]['pricing']['price_1m_input_tokens'] = 1.25
    _, state, result = record_pair(tmp_path, payload, after)
    original = deepcopy(state['comparison'])
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: pytest.fail('Unexpected network request'))
    assert compare_runs(state['comparison']) == result
    assert state['comparison'] == original


def test_numeric_representation_and_record_order_do_not_invent_changes(tmp_path, payload):
    before = deepcopy(payload)
    extra = deepcopy(before['data'][0])
    extra['id'] = 'synthetic-second'
    before['data'].append(extra)
    after = deepcopy(before)
    after['data'].reverse()
    for record in after['data']:
        record['pricing']['price_1m_input_tokens'] = 0.0
        record['median_output_tokens_per_second'] = 10.0
    _, _, result = record_pair(tmp_path, before, after)
    assert result['status'] == 'ready'
    assert result['events'] == []


@pytest.mark.parametrize('mutation', ['missing_source', 'duplicate_id'])
def test_incomplete_identity_never_invents_changes(tmp_path, payload, mutation):
    _, state, _ = record_pair(tmp_path, payload, deepcopy(payload))
    comparison = state['comparison']
    if mutation == 'missing_source':
        comparison['after']['snapshot'].pop('source')
    else:
        comparison['after']['records'].append(deepcopy(comparison['after']['records'][0]))
    result = compare_runs(comparison)
    assert result['status'] == 'unavailable'
    assert not result['events']
