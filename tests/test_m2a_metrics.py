"""M2A metric rules use synthetic records only; no credentials or real database."""
from decimal import Decimal

import pytest

from benchmark_dashboard import metrics


EXISTING_PATHS = {
    'evaluations.artificial_analysis_intelligence_index',
    'evaluations.artificial_analysis_coding_index',
    'evaluations.artificial_analysis_math_index',
    *{f'evaluations.{key}' for key in ('mmlu_pro', 'gpqa', 'hle', 'livecodebench',
                                       'scicode', 'math_500', 'aime')},
    *{f'pricing.{key}' for key in ('price_1m_input_tokens', 'price_1m_output_tokens',
                                  'price_1m_blended_3_to_1')},
    'median_output_tokens_per_second', 'median_time_to_first_token_seconds',
    'median_time_to_first_answer_token',
}
EXTRA_PATHS = {f'evaluations.{key}' for key in ('aime_25', 'ifbench', 'lcr', 'tau2',
                                               'tau_banking', 'terminalbench_hard',
                                               'terminalbench_v2_1')}
INDEX = 'evaluations.artificial_analysis_intelligence_index'


def test_existing_validation_contract_and_complete_display_dictionary():
    assert set(metrics.METRICS) == EXISTING_PATHS
    assert set(metrics.DISPLAY_METRICS) == EXISTING_PATHS | EXTRA_PATHS
    assert metrics.Metric('a', '测试', '单位', 1, True).multiplier == 1
    for path in EXISTING_PATHS | EXTRA_PATHS:
        entry = metrics.metric_for(path)
        assert entry.source_name and entry.description and entry.raw_unit and entry.comparability
        assert entry.precision == 8
        assert entry.direction in {'higher', 'lower', 'unknown'}
        assert any('\u4e00' <= character <= '\u9fff' for character in entry.label)
    for path in EXTRA_PATHS:
        assert metrics.metric_for(path).direction == 'unknown'
        assert metrics.metric_for(path).confirmed is False


@pytest.mark.parametrize('value', [None, True, False, '0', '1.5', [], {}, float('nan'),
                                  float('inf'), -float('inf'), Decimal('NaN'), Decimal('Infinity')])
def test_invalid_values_are_not_numbers(value):
    assert metrics.numeric_value(value) is None
    assert metrics.normalized_value(value, INDEX) is None
    assert metrics.format_value({'evaluations': {'artificial_analysis_intelligence_index': value}}, INDEX) == '暂无'


@pytest.mark.parametrize('value,expected', [(0, Decimal('0')), (0.5, Decimal('0.5')),
                                           (Decimal('1.25'), Decimal('1.25')), (-2, Decimal('-2'))])
def test_numeric_values_preserve_zero_and_decimals(value, expected):
    assert metrics.numeric_value(value) == expected


def test_coverage_uses_passed_snapshot_only_and_zero_is_valid():
    records = [{'evaluations': {'gpqa': value}} for value in (0, None, '0.8', float('inf'), 0.5)]
    row = metrics.coverage_rows(records, ['evaluations.gpqa'])[0]
    assert row == {'path': 'evaluations.gpqa', 'label': metrics.metric_for('evaluations.gpqa').label,
                   'valid_count': 2, 'total': 5, 'missing_count': 3, 'coverage': 0.4}
    assert metrics.coverage_rows(records[:1], ['evaluations.gpqa'])[0]['coverage'] == 1
    empty = metrics.coverage_rows([], ['evaluations.gpqa'])[0]
    assert (empty['valid_count'], empty['total'], empty['missing_count'], empty['coverage']) == (0, 0, 0, 0)


def test_unknown_metric_is_conservative_and_raw_scale_is_preserved():
    entry = metrics.metric_for('evaluations.future')
    assert entry.confirmed is False and entry.direction == 'unknown'
    assert entry.multiplier == 1
    assert metrics.format_value({'evaluations': {'future': .75}}, entry.path) == '0.75'
    assert metrics.numeric_delta(.5, .75, entry.path)['delta_unit'] == '原值差（单位未确认）'


def test_eight_significant_digits_remove_binary_noise_but_retain_tiny_values():
    assert metrics.normalized_value(.1 + .2, INDEX) == metrics.normalized_value(.3, INDEX)
    assert metrics.normalized_value(1.234567841, INDEX) == metrics.normalized_value(1.234567849, INDEX)
    assert metrics.normalized_value(1e-20, INDEX) == Decimal('1e-20')
    assert metrics.normalized_value(123456789, INDEX) == Decimal('123456790')
    assert metrics.format_value({'evaluations': {'artificial_analysis_intelligence_index': -.0}}, INDEX) == '0'


def test_numeric_delta_retains_raw_values_and_separates_index_points_from_relative_percent():
    row = metrics.numeric_delta(20, 25, INDEX)
    assert row == {'before': 20, 'after': 25, 'absolute': '5', 'relative_percent': '25',
                   'delta_unit': '指数点'}
    noisy = metrics.numeric_delta(.3, .1 + .2, INDEX)
    assert noisy['after'] == .1 + .2 and noisy['absolute'] == '0'
    assert metrics.numeric_delta(0, 1, INDEX)['relative_percent'] is None
    assert metrics.numeric_delta(None, 1, INDEX)['absolute'] is None
    assert metrics.numeric_delta(None, 1, INDEX)['relative_percent'] is None


def test_confirmed_ratio_has_percentage_point_difference_without_guessing_real_units(monkeypatch):
    path = 'evaluations.synthetic_confirmed_ratio'
    monkeypatch.setitem(metrics.METRICS, path, metrics.Metric(path, '合成比例', '%', 100, True))
    assert metrics.normalized_value(.25, path) == Decimal('25')
    assert metrics.format_value({'evaluations': {'synthetic_confirmed_ratio': .25}}, path) == '25'
    result = metrics.numeric_delta(.25, .3, path)
    assert result['absolute'] == '5' and result['relative_percent'] == '20'
    assert result['delta_unit'] == '百分点'
    unconfirmed = metrics.numeric_delta(.25, .3, 'evaluations.gpqa')
    assert unconfirmed['absolute'] == '0.05'
    assert unconfirmed['delta_unit'] == '原值差（单位未确认）'


def test_display_added_metadata_does_not_reclassify_unknown_validation_fields(payload):
    from benchmark_dashboard.validation import validate_payload
    payload['data'][0]['evaluations']['aime_25'] = .25
    validated = validate_payload(payload)
    assert 'evaluations.aime_25' in validated.unknown_fields
    assert validated.coverage['evaluations.aime_25'] == 1
