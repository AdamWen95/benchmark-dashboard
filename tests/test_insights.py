"""Rules are exercised with synthetic records only; no API or real DB access."""
from copy import deepcopy
import json

import pytest

from benchmark_dashboard.insights import generate_insights


INDEX = 'evaluations.artificial_analysis_intelligence_index'
CODING = 'evaluations.artificial_analysis_coding_index'
MATH = 'evaluations.artificial_analysis_math_index'
INPUT = 'pricing.price_1m_input_tokens'
OUTPUT = 'pricing.price_1m_output_tokens'
SPEED = 'median_output_tokens_per_second'
LATENCY = 'median_time_to_first_token_seconds'
UNKNOWN = 'evaluations.synthetic_unknown'
PATHS = [INDEX, CODING, MATH, INPUT, OUTPUT, SPEED, LATENCY]


@pytest.fixture
def selection():
    records = [
        {'id': 'synthetic-a', 'name': '合成甲', 'slug': 'synthetic-high',
         'evaluations': {'artificial_analysis_intelligence_index': 30,
                         'artificial_analysis_coding_index': 0,
                         'artificial_analysis_math_index': None},
         'pricing': {'price_1m_input_tokens': 0, 'price_1m_output_tokens': 2},
         'median_output_tokens_per_second': 20, 'median_time_to_first_token_seconds': 0.25},
        {'id': 'synthetic-b', 'name': '合成乙', 'slug': 'synthetic-low',
         'evaluations': {'artificial_analysis_intelligence_index': 20,
                         'artificial_analysis_coding_index': None,
                         'artificial_analysis_math_index': 50},
         'pricing': {'price_1m_input_tokens': 1, 'price_1m_output_tokens': 1},
         'median_output_tokens_per_second': 10, 'median_time_to_first_token_seconds': 0.5},
    ]
    snapshot = {'id': 17, 'source': 'artificial_analysis', 'content_hash': 'synthetic-digest',
                'collected_at': '2026-01-01T01:00:00Z', 'metadata': {},
                'endpoint': 'https://artificialanalysis.ai/api/v2/data/llms/models',
                'prompt_options': {'prompt_length': 'medium'}}
    return records, snapshot


def paragraph(result, category):
    return next(row['text'] for row in result['paragraphs'] if row['category'] == category)


def test_same_input_and_rule_version_are_deterministic_without_mutation(selection):
    records, snapshot = selection
    before = deepcopy(selection)
    result = generate_insights(records, snapshot, PATHS)
    assert result == generate_insights(deepcopy(records), deepcopy(snapshot), list(PATHS))
    assert selection == before
    assert result['rule_version'] == 'm2c-finish-rules-v1'
    assert result['snapshot_id'] == snapshot['id']
    assert result['content_hash'] == snapshot['content_hash']
    assert len(result['paragraphs']) <= 3
    assert len({row['category'] for row in result['paragraphs']}) == len(result['paragraphs'])
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_partial_coverage_counts_zero_and_does_not_impute_missing(selection):
    result = generate_insights(*selection, PATHS)
    text = paragraph(result, '能力指标')
    assert '覆盖 1/2' in text
    assert '缺分不等于能力差' in paragraph(result, '证据不足')
    coding = [row for row in result['evidence'] if row['metric_path'] == CODING]
    assert {row['coverage']['valid'] for row in coding} == {1}
    assert {row['coverage']['total'] for row in coding} == {2}
    assert next(row for row in coding if row['model_id'] == 'synthetic-a')['raw_value'] == 0
    missing = next(row for row in coding if row['model_id'] == 'synthetic-b')
    assert missing['raw_value'] is None
    assert missing['display_value'] == '暂无'


def test_ties_report_equal_values_not_arbitrary_winner(selection):
    records, snapshot = selection
    records[1]['pricing']['price_1m_output_tokens'] = 2.0000000001
    result = generate_insights(records, snapshot, [OUTPUT])
    assert '（并列）' in paragraph(result, '价格与速度')
    assert '记录1' in paragraph(result, '价格与速度')
    assert '记录2' in paragraph(result, '价格与速度')


def test_insufficient_context_allows_only_explicit_report_value_order(selection):
    result = generate_insights(*selection, [INDEX])
    text = paragraph(result, '能力指标')
    assert '在本次所选记录、该项已报告值中' in text
    assert '口径未充分确认' in text
    assert '不是能力排名' in text
    assert '评测版本' in paragraph(result, '证据不足')
    assert '源站未提供' in paragraph(result, '证据不足')


@pytest.mark.parametrize('key, first, second', [
    ('evaluation_version', 'v1', 'v2'),
    ('reasoning_effort', 'high', 'low'),
    ('evaluation_config', {'mode': 'a'}, {'mode': 'b'}),
])
def test_changed_version_or_configuration_never_ranks(selection, key, first, second):
    records, snapshot = selection
    records[0][key] = first
    records[1][key] = second
    result = generate_insights(records, snapshot, [INDEX])
    text = paragraph(result, '能力指标')
    assert '口径变化/待确认' in text
    assert '仅并列' in text
    assert '从高到低' not in text and '从低到高' not in text
    assert '30' in text and '20' in text


def test_explicit_context_does_not_upgrade_partial_index_verification(selection):
    records, snapshot = selection
    for record in records:
        record.update(evaluation_version='synthetic-v1', evaluation_config={'mode': 'same'})
    result = generate_insights(records, snapshot, [INDEX])
    text = paragraph(result, '能力指标')
    assert '从高到低' not in text and '从低到高' not in text
    assert '逐字段核验未完成' in text
    assert all(row['verification_status'] == 'partial' for row in result['evidence'])
    assert text.index('记录1') < text.index('记录2')
    assert '能力最强' not in text and '全面' not in text


def test_prices_and_speed_separate_no_blended_cost_claim(selection):
    result = generate_insights(*selection, PATHS)
    text = paragraph(result, '价格与速度')
    assert all(label in text for label in ['输入价格', '输出价格', '输出速度', '首 Token 延迟'])
    assert '实际总费用' in paragraph(result, '证据不足')
    assert '公司实测' in paragraph(result, '证据不足')
    assert '性价比最佳' not in text


def test_unknown_direction_has_no_best_or_order(selection):
    records, snapshot = selection
    for i, record in enumerate(records):
        record['evaluations']['synthetic_unknown'] = i
    result = generate_insights(records, snapshot, [UNKNOWN])
    assert all(row['category'] == '证据不足' for row in result['paragraphs'])
    text = paragraph(result, '证据不足')
    assert '方向未确认' in text
    assert '从高到低' not in text and '从低到高' not in text


def test_all_missing_reports_no_numeric_order(selection):
    records, snapshot = selection
    for record in records:
        record['evaluations']['artificial_analysis_intelligence_index'] = None
    result = generate_insights(records, snapshot, [INDEX])
    text = paragraph(result, '能力指标')
    assert '覆盖 0/2' in text
    assert '暂无有效数值' in text
    assert '从高到低' not in text


def test_evidence_has_full_identity_and_only_selected_snapshot_records(selection):
    records, snapshot = selection
    result = generate_insights(records, snapshot, PATHS)
    evidence = {row['id']: row for row in result['evidence']}
    assert len(evidence) == len(result['evidence']) == len(PATHS) * len(records)
    for row in evidence.values():
        assert row['snapshot_id'] == snapshot['id']
        assert row['content_hash'] == snapshot['content_hash']
        assert row['source'] == snapshot['source']
        assert row['model_id'] in {record['id'] for record in records}
        assert row['metric_path'] in PATHS
        assert {'raw_value', 'display_value', 'unit', 'coverage'} <= row.keys()
        assert row['reference'] == {'snapshot_id': snapshot['id'], 'source': snapshot['source'],
                                     'model_id': row['model_id'], 'metric_path': row['metric_path']}
    for row in result['paragraphs']:
        assert set(row['evidence_ids']) <= evidence.keys()
        if row['category'] != '证据不足':
            assert row['evidence_ids']


def test_evidence_identity_changes_with_snapshot_and_content(selection):
    records, snapshot = selection
    first = generate_insights(records, snapshot, [INDEX])
    snapshot['id'] += 1
    snapshot['content_hash'] = 'synthetic-other'
    second = generate_insights(records, snapshot, [INDEX])
    assert {row['id'] for row in first['evidence']}.isdisjoint(row['id'] for row in second['evidence'])


def test_same_names_do_not_merge_ids_and_labels_are_retained(selection):
    records, snapshot = selection
    records[1]['name'] = records[0]['name']
    result = generate_insights(records, snapshot, [INDEX])
    assert len(result['evidence']) == 2
    assert {row['model_id'] for row in result['evidence']} == {'synthetic-a', 'synthetic-b'}
    assert {row['slug'] for row in result['evidence']} == {'synthetic-high', 'synthetic-low'}
    assert '名称和配置标签' in paragraph(result, '证据不足')


@pytest.mark.parametrize('count', [0, 1, 5])
def test_invalid_number_of_records_is_fixed_error(selection, count):
    _, snapshot = selection
    records = [{'id': f'synthetic-{i}', 'name': '合成'} for i in range(count)]
    with pytest.raises(ValueError, match='^请选择 2–4 条稳定 ID 不重复的模型记录。$'):
        generate_insights(records, snapshot, [INDEX])


@pytest.mark.parametrize('invalid_id', [None, '', 'synthetic-a', 123])
def test_invalid_or_duplicate_id_rejected(selection, invalid_id):
    records, snapshot = selection
    records[1]['id'] = invalid_id
    with pytest.raises(ValueError, match='^请选择 2–4 条稳定 ID 不重复的模型记录。$'):
        generate_insights(records, snapshot, [INDEX])


@pytest.mark.parametrize('field', ['id', 'source', 'content_hash'])
def test_missing_snapshot_identity_is_fixed_error(selection, field):
    records, snapshot = selection
    snapshot.pop(field)
    with pytest.raises(ValueError, match='^当前快照依据不完整，暂时无法生成规则说明。$'):
        generate_insights(records, snapshot, [INDEX])


def test_generation_failure_does_not_expose_exception_text(selection, monkeypatch):
    from benchmark_dashboard import insights

    def fail(*args):
        raise RuntimeError('SYNTHETIC_SECRET_SENTINEL source content must not be displayed')

    monkeypatch.setattr(insights, 'assess_metric', fail)
    with pytest.raises(ValueError) as error:
        generate_insights(*selection, [INDEX])
    assert str(error.value) == '规则说明暂时无法生成，请保留原表查看。'
    assert 'SYNTHETIC_SECRET_SENTINEL' not in str(error.value)


def test_invalid_nonfinite_values_not_imputed_as_zero(selection):
    records, snapshot = selection
    records[0]['evaluations']['artificial_analysis_intelligence_index'] = float('nan')
    records[1]['evaluations']['artificial_analysis_intelligence_index'] = 'unparseable'
    result = generate_insights(records, snapshot, [INDEX])
    assert all(row['display_value'] == '暂无' for row in result['evidence'])
    assert all(row['raw_value'] is None for row in result['evidence'])
    assert '覆盖 0/2' in paragraph(result, '能力指标')
    json.dumps(result, allow_nan=False)


def test_duplicate_metric_paths_do_not_duplicate_evidence_or_text(selection):
    first = generate_insights(*selection, [INDEX, INPUT])
    assert first == generate_insights(*selection, [INDEX, INDEX, INPUT, INPUT])


@pytest.mark.parametrize('count', [3, 4])
def test_three_or_four_records_share_one_paragraph_per_category(selection, count):
    records, snapshot = selection
    for i in range(2, count):
        record = deepcopy(records[0])
        record.update(id=f'synthetic-{i}', name=f'合成{i}')
        records.append(record)
    result = generate_insights(records, snapshot, PATHS)
    assert len(result['paragraphs']) == 3
    assert len(result['evidence']) == count * len(PATHS)
    assert all(row['coverage']['total'] == count for row in result['evidence'])
    assert all(len(row['text']) < 1000 for row in result['paragraphs'])


def test_tiny_positive_values_are_not_rounded_to_zero(selection):
    records, snapshot = selection
    records[0]['pricing']['price_1m_input_tokens'] = 0.000000000123
    result = generate_insights(records, snapshot, [INPUT])
    row = next(row for row in result['evidence'] if row['model_id'] == 'synthetic-a')
    assert row['display_value'] not in ('0', '暂无')
    assert row['raw_value'] == 0.000000000123


def test_known_metric_with_unknown_direction_does_not_rank(selection, monkeypatch):
    from dataclasses import replace
    from benchmark_dashboard import metrics

    monkeypatch.setitem(metrics.METRICS, INDEX, replace(metrics.METRICS[INDEX], direction='unknown'))
    result = generate_insights(*selection, [INDEX])
    text = paragraph(result, '能力指标')
    assert '数值方向未确认，仅并列' in text
    assert '从高到低' not in text and '从低到高' not in text


@pytest.mark.parametrize('unit', ['%', {'unexpected': 'unit-object'}, ['%']])
def test_conflicting_or_invalid_source_unit_is_raw_without_sorting(selection, unit):
    records, snapshot = selection
    snapshot['metadata']['metric_units'] = {INDEX: unit}
    result = generate_insights(records, snapshot, [INDEX])
    text = paragraph(result, '能力指标')
    assert '源站原值（单位声明待核对）' in text
    assert '指数原值' not in text
    assert '从高到低' not in text and '从低到高' not in text
    assert '数值排列' not in text
    assert '记录1 30' in text and '记录2 20' in text
    assert '源站单位声明与本地映射存在冲突，不作数值排序或单位转换' in paragraph(result, '证据不足')
    assert all(row['unit'] == '源站原值（单位声明待核对）' for row in result['evidence'])
    assert all(row['raw_unit'] == '单位声明待核对' for row in result['evidence'])
    assert all(row['assessment']['unit_conflict'] for row in result['evidence'])
    assert all(row['comparability_reason'] for row in result['evidence'])


def test_different_unit_declarations_do_not_use_one_shared_unit(selection):
    records, snapshot = selection
    records[0]['metric_units'] = {INDEX: '比例'}
    records[1]['metric_units'] = {INDEX: '%'}
    result = generate_insights(records, snapshot, [INDEX])
    text = paragraph(result, '能力指标')
    assert '源站原值（单位声明待核对）' in text
    assert '从高到低' not in text
    assert '口径变化/待确认' in text
    assert all(row['assessment']['unit_conflict'] for row in result['evidence'])


def test_conflicting_units_never_apply_local_display_multiplier(selection, monkeypatch):
    from dataclasses import replace
    from benchmark_dashboard import metrics

    records, snapshot = selection
    monkeypatch.setitem(metrics.METRICS, INDEX, replace(metrics.METRICS[INDEX],
                                                       multiplier=100, raw_unit='比例', unit='%'))
    records[0]['evaluations']['artificial_analysis_intelligence_index'] = 0.2
    records[1]['evaluations']['artificial_analysis_intelligence_index'] = 0.4
    snapshot['metadata']['metric_units'] = {INDEX: '指数原值'}
    result = generate_insights(records, snapshot, [INDEX])
    assert {row['display_value'] for row in result['evidence']} == {'0.2', '0.4'}
    assert {row['raw_value'] for row in result['evidence']} == {0.2, 0.4}
    text = paragraph(result, '能力指标')
    assert '记录1 0.2' in text and '记录2 0.4' in text
    assert '从高到低' not in text and '从低到高' not in text


def test_invalid_unit_map_structure_is_not_silently_ignored(selection):
    records, snapshot = selection
    snapshot['metadata']['metric_units'] = ['not-a-mapping']
    result = generate_insights(records, snapshot, [INDEX])
    assert all(row['assessment']['unit_conflict'] for row in result['evidence'])
    assert '源站原值（单位声明待核对）' in paragraph(result, '能力指标')


def test_rules_have_no_network_or_storage_dependency(selection, monkeypatch):
    import socket
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError('Offline rules must not use network or database')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    assert generate_insights(*selection, PATHS)['paragraphs']
    from benchmark_dashboard.insights import reported_programming_rows
    assert len(reported_programming_rows(*selection)) == 10
