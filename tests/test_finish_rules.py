"""Finish rules use only synthetic records and never inspect saved project data."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from benchmark_dashboard import insights, metrics
from benchmark_dashboard.comparability import assess_metric


SPEED = 'median_output_tokens_per_second'
LATENCY = 'median_time_to_first_token_seconds'
ANSWER = 'median_time_to_first_answer_token'
INDEX = 'evaluations.artificial_analysis_intelligence_index'
PRICE = 'pricing.price_1m_input_tokens'
NOTICE = '源站记录为 0，测量含义待确认，暂不用于性能优劣判断'


def snapshot():
    return {'id': 91, 'source': 'artificial_analysis', 'content_hash': 'synthetic-finish',
            'collected_at': '2026-01-01T00:00:00Z',
            'prompt_options': {'prompt_length': 'medium'}, 'metadata': {}}


def pair(path, values=(0, 0)):
    records = [{'id': 'synthetic-a', 'name': '合成甲'},
               {'id': 'synthetic-b', 'name': '合成乙'}]
    for record, value in zip(records, values):
        if '.' in path:
            group, field = path.split('.')
            record[group] = {field: value}
        else:
            record[path] = value
    return records


@pytest.mark.parametrize('path', [SPEED, LATENCY, ANSWER])
def test_performance_zero_retained_but_never_ranked_tied_or_subtracted(path):
    records, snap = pair(path), snapshot()
    original = deepcopy(records)
    assert metrics.performance_zero_notice(records[0], path) == NOTICE
    assert metrics.is_unconfirmed_performance_zero(Decimal('-0'), path)
    assessment = assess_metric(path, records, [snap, snap])
    assert assessment['status'] == 'unconfirmed'
    assert assessment['quality_status'] == 'unconfirmed_zero'
    assert assessment['quality_reason'] == NOTICE
    assert assessment['performance_zero_count'] == 2
    result = insights.generate_insights(records, snap, [path])
    text = next(row['text'] for row in result['paragraphs'] if row['category'] == '价格与速度')
    assert NOTICE in text and '记录1 0' in text and '记录2 0' in text
    assert '从高到低' not in text and '从低到高' not in text and '（并列）' not in text
    assert '有限数值覆盖 2/2' in text
    assert all(row['raw_value'] == 0 and row['display_value'] == '0' for row in result['evidence'])
    assert all(row['quality_notice'] == NOTICE for row in result['evidence'])
    assert metrics.coverage_rows(records, [path])[0]['valid_count'] == 2
    for before, after in [(0, 0), (0, 12), (12, 0)]:
        delta = metrics.numeric_delta(before, after, path)
        assert delta['before'] == before and delta['after'] == after
        assert delta['absolute'] is None and delta['relative_percent'] is None
    assert records == original


def test_quality_notice_survives_context_change_and_nonzero_is_not_certified():
    records, snap = pair(SPEED, (0, 12)), snapshot()
    records[0]['reasoning_effort'] = 'low'
    records[1]['reasoning_effort'] = 'high'
    assessment = assess_metric(SPEED, records, [snap, snap])
    assert assessment['status'] == 'changed'
    assert assessment['quality_reason'] == NOTICE and NOTICE in assessment['reason']
    assert metrics.performance_zero_notice(records[1], SPEED) is None
    records = pair(SPEED, (12, 15))
    snap.pop('prompt_options')
    result = insights.generate_insights(records, snap, [SPEED])
    text = next(row['text'] for row in result['paragraphs'] if row['category'] == '价格与速度')
    assert '暂不用于性能优劣判断' in text
    assert '从高到低' not in text and '从低到高' not in text


@pytest.mark.parametrize('path', [INDEX, PRICE])
def test_evaluation_and_price_zero_keep_their_distinct_semantics(path):
    records = pair(path, (0, 1))
    assert metrics.performance_zero_notice(records[0], path) is None
    assert not metrics.is_unconfirmed_performance_zero(0, path)
    assert metrics.numeric_delta(0, 1, path)['absolute'] == '1'
    assessment = assess_metric(path, records, [snapshot(), snapshot()])
    assert assessment['quality_status'] == 'not_flagged'
    assert assessment['performance_zero_count'] == 0
    assert metrics.coverage_rows(records, [path])[0]['valid_count'] == 2


@pytest.mark.parametrize('value', [None, False, '0', float('nan'), 0.0000000001])
def test_quality_check_does_not_coerce_missing_invalid_or_tiny_numbers(value):
    assert not metrics.is_unconfirmed_performance_zero(value, SPEED)


def test_programming_values_and_missing_cells_remain_visible_without_conversion(monkeypatch):
    records = pair(INDEX, (0, 1.5))
    records[0]['evaluations'].update(artificial_analysis_coding_index=3.5, livecodebench=.406,
                                     terminalbench_hard=0, terminalbench_v2_1=None)
    records[1]['evaluations'].update(artificial_analysis_coding_index=4.5, livecodebench=.798,
                                     terminalbench_hard=.2, terminalbench_v2_1=.3)
    for record in records:
        record.update(evaluation_version='synthetic-v1', evaluation_config={'mode': 'same'})
    path = 'evaluations.livecodebench'
    monkeypatch.setitem(metrics.METRICS, path, replace(metrics.metric_for(path), multiplier=100))
    snap, original = snapshot(), deepcopy(records)
    rows = insights.reported_programming_rows(records, snap)
    assert len(rows) == 10
    first_lcb = next(row for row in rows if row['model_id'] == 'synthetic-a' and row['path'] == path)
    assert first_lcb['raw_value'] == .406 and first_lcb['display_value'] == '0.406'
    assert first_lcb['verification_status'] == 'partial'
    assert first_lcb['source'] == snap['source'] and first_lcb['collected_at'] == snap['collected_at']
    assert first_lcb['snapshot_id'] == snap['id'] and first_lcb['content_hash'] == snap['content_hash']
    assert '同一次采集不等于同一次测试' in first_lcb['reason']
    missing = next(row for row in rows if row['model_id'] == 'synthetic-a' and row['path'].endswith('terminalbench_v2_1'))
    assert missing['display_value'] == '暂无' and missing['raw_value'] is None
    result = insights.generate_insights(records, snap, list(insights.PROGRAMMING_PATHS))
    text = next(row['text'] for row in result['paragraphs'] if row['category'] == '能力指标')
    for label in ('代码指数', '综合智能指数', 'LiveCodeBench', 'TerminalBench Hard', 'TerminalBench v2.1'):
        assert label in text
    assert '0.406' in text and '0.798' in text and '暂无' in text
    assert '从高到低' not in text and '从低到高' not in text and '冠军' not in text
    assert records == original
    assert result['rule_version'] == 'm2c-finish-rules-v1'


def test_confirmed_nonzero_speed_and_zero_price_still_support_reported_value_order():
    for path, values in [(SPEED, (20, 10)), (PRICE, (0, 1))]:
        result = insights.generate_insights(pair(path, values), snapshot(), [path])
        text = next(row['text'] for row in result['paragraphs'] if row['category'] == '价格与速度')
        assert '从高到低' in text or '从低到高' in text
        assert NOTICE not in text
