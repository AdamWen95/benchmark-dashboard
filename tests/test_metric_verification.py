"""M2B dictionary semantics with synthetic records; no IO or service calls."""
from copy import deepcopy
from decimal import Decimal

import pytest

from benchmark_dashboard import metrics
from benchmark_dashboard.comparability import assess_metric, metric_context


CONFIRMED = {
    'pricing.price_1m_input_tokens', 'pricing.price_1m_output_tokens',
    'median_output_tokens_per_second', 'median_time_to_first_token_seconds',
}
PARTIAL = {
    *{f'evaluations.artificial_analysis_{name}_index'
      for name in ('intelligence', 'coding', 'math')},
    'pricing.price_1m_blended_3_to_1', 'median_time_to_first_answer_token',
    'evaluations.livecodebench', 'evaluations.terminalbench_hard',
    'evaluations.terminalbench_v2_1',
}


@pytest.mark.parametrize('path', sorted(CONFIRMED | PARTIAL))
def test_checked_definitions_record_direct_sources_and_independent_status(path):
    metric = metrics.metric_for(path)
    expected = 'confirmed' if path in CONFIRMED else 'partial'
    assert metric.verification_status == expected
    assert metric.verified_at == '2026-09-19'
    assert isinstance(metric.official_sources, tuple)
    assert metrics.API_REFERENCE in metric.official_sources
    assert all(source.startswith('https://') for source in metric.official_sources)
    assert metric.conversion_formula == 'display_value = raw_value'
    assert metric.multiplier == 1


def test_unknown_and_future_fields_do_not_inherit_verification_from_neighbours():
    for path in ('evaluations.gpqa', 'evaluations.tau2', 'evaluations.future_field'):
        metric = metrics.metric_for(path)
        assert metric.verification_status == 'unknown'
        assert metric.verified_at is None
        assert metric.official_sources == ()
        assert metric.direction == 'unknown'
        assert not metric.confirmed
    assert metrics.METRIC_MAPPING_VERSION == 'm2b-metrics-v1'


def test_partial_is_not_the_historical_unit_display_confirmation():
    path = 'evaluations.artificial_analysis_intelligence_index'
    metric = metrics.metric_for(path)
    assert metric.confirmed and metric.unit == '指数原值'
    assert metric.verification_status != 'confirmed'
    assert metrics.format_value({'evaluations': {'artificial_analysis_intelligence_index': .25}}, path) == '0.25'


def test_first_answer_field_stays_distinct_and_direction_unknown():
    first = metrics.metric_for('median_time_to_first_token_seconds')
    answer = metrics.metric_for('median_time_to_first_answer_token')
    assert first.path != answer.path
    assert first.verification_status == 'confirmed' and first.unit == '秒'
    assert answer.verification_status == 'partial' and not answer.confirmed
    assert answer.raw_unit == '未确认' and answer.direction == 'unknown'
    snapshot = {'source': 'artificial_analysis', 'prompt_options': {'prompt_length': 1000}}
    record = {'id': 'synthetic', first.path: 0, answer.path: .25}
    assert assess_metric(answer.path, [record], [snapshot])['status'] == 'unconfirmed'
    assert metrics.format_value(record, first.path) == '0'
    assert metrics.format_value(record, answer.path) == '0.25'


def test_priority_unconfirmed_values_preserve_raw_scale_and_zero():
    for path in PARTIAL:
        assert metrics.normalized_value(.25, path) == Decimal('.25')
        assert metrics.normalized_value(0, path) == Decimal(0)
        assert metrics.normalized_value(None, path) is None
        assert metrics.normalized_value('0', path) is None
        assert metrics.normalized_value(True, path) is None


def test_definitions_never_backfill_source_version_configuration_or_data(payload):
    record = payload['data'][0]
    snapshot = {'source': 'artificial_analysis', 'prompt_options': payload['prompt_options'],
                'collected_at': '2026-09-19', 'metadata': {}}
    before = deepcopy((record, snapshot))
    path = 'evaluations.artificial_analysis_intelligence_index'
    metric = metrics.metric_for(path)
    context = metric_context(path, record, snapshot)
    assert metric.verified_at
    assert context['record.evaluation_version'] is None
    assert context['source_metadata.evaluation_version'] is None
    assert context['record.evaluation_config'] is None
    assert assess_metric(path, [record], [snapshot])['status'] == 'unconfirmed'
    assert (record, snapshot) == before


def test_confirmed_unit_does_not_override_an_explicit_source_unit_conflict():
    path = 'pricing.price_1m_input_tokens'
    record = {'id': 'synthetic', 'pricing': {'price_1m_input_tokens': 0},
              'metric_units': {path: '美元/千 Token'}}
    snapshot = {'source': 'artificial_analysis'}
    before = deepcopy(record)
    assessment = assess_metric(path, [record], [snapshot])
    assert metrics.metric_for(path).verification_status == 'confirmed'
    assert assessment['status'] == 'unconfirmed' and assessment['unit_conflict']
    assert record == before


def test_new_verification_never_expands_original_validation_allowlist(payload):
    from benchmark_dashboard.validation import validate_payload

    payload['data'][0]['evaluations']['terminalbench_v2_1'] = .25
    payload['data'][0]['evaluations']['terminalbench_hard'] = 0
    before = deepcopy(payload)
    validated = validate_payload(payload)
    assert 'evaluations.terminalbench_v2_1' not in metrics.METRICS
    assert 'evaluations.terminalbench_hard' not in metrics.METRICS
    assert 'evaluations.terminalbench_v2_1' in validated.unknown_fields
    assert 'evaluations.terminalbench_hard' in validated.unknown_fields
    assert validated.coverage['evaluations.terminalbench_hard'] == 1
    assert len(metrics.METRICS) == 16 and len(metrics.DISPLAY_METRICS) == 23
    assert payload == before
