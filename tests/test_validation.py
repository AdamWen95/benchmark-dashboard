from copy import deepcopy
import pytest
from benchmark_dashboard.metrics import format_value, metric_for
from benchmark_dashboard.validation import DataValidationError, validate_payload


def test_zero_missing_unknown_preserved(payload):
    row = payload['data'][0]
    row['future_metadata'] = {'version': 'synthetic-v1'}
    row['evaluations']['future_score'] = 0.75
    valid = validate_payload(payload)
    assert valid.coverage['evaluations.artificial_analysis_intelligence_index'] == 1
    assert valid.coverage['evaluations.artificial_analysis_math_index'] == 0
    assert 'future_metadata' in valid.unknown_fields
    assert 'evaluations.future_score' in valid.unknown_fields
    assert format_value(row, 'evaluations.artificial_analysis_intelligence_index') == '0'
    assert format_value(row, 'evaluations.artificial_analysis_math_index') == '暂无'
    assert format_value(row, 'evaluations.future_score') == '0.75'
    assert valid.payload == payload


@pytest.mark.parametrize('bad', [None, [], {}, {'data': []}, {'data': 'bad'}, {'data': [None]}])
def test_invalid_root_and_empty(bad):
    with pytest.raises(DataValidationError):
        validate_payload(bad)


@pytest.mark.parametrize('path,bad', [('id', None), ('id', 1), ('name', ''), ('slug', 4),
    ('evaluations', []), ('pricing', 'bad'), ('model_creator', False),
    ('median_output_tokens_per_second', True), ('median_output_tokens_per_second', -1)])
def test_wrong_record_type(payload, path, bad):
    payload['data'][0][path] = bad
    with pytest.raises(DataValidationError):
        validate_payload(payload)


@pytest.mark.parametrize('bad', ['0.5', True, [], float('nan'), float('inf')])
def test_wrong_metric_type(payload, bad):
    payload['data'][0]['evaluations']['gpqa'] = bad
    with pytest.raises(DataValidationError):
        validate_payload(payload)


def test_duplicate_id_rejected_even_if_name_different(payload):
    duplicate = deepcopy(payload['data'][0])
    duplicate['name'] = '另一个名字'
    payload['data'].append(duplicate)
    with pytest.raises(DataValidationError, match='重复'):
        validate_payload(payload)


def test_same_name_different_ids_and_order_independent_hash(payload):
    other = deepcopy(payload['data'][0])
    other['id'] = 'synthetic-model-2'
    payload['data'].append(other)
    digest = validate_payload(payload).content_hash
    payload['data'].reverse()
    assert validate_payload(payload).content_hash == digest


def test_unknown_mixed_types_are_not_numeric_metrics(payload):
    payload['data'][0]['evaluations']['future'] = 3
    other = deepcopy(payload['data'][0])
    other['id'] = 'synthetic-model-2'
    other['evaluations']['future'] = {'score': 3}
    payload['data'].append(other)
    valid = validate_payload(payload)
    assert 'evaluations.future' not in valid.coverage
    assert valid.warnings


def test_units_are_explicit_and_do_not_guess_scale(payload):
    assert metric_for('pricing.price_1m_input_tokens').unit == '美元/百万 Token'
    assert metric_for('median_output_tokens_per_second').unit == 'Token/秒'
    assert metric_for('median_time_to_first_token_seconds').unit == '秒'
    assert metric_for('evaluations.gpqa').multiplier == 1
    assert not metric_for('evaluations.gpqa').confirmed
    assert format_value(payload['data'][0], 'evaluations.gpqa') == '0.5'


def test_error_does_not_echo_values(payload):
    payload['data'][0]['evaluations']['gpqa'] = 'SYNTHETIC_SECRET_DO_NOT_PRINT'
    with pytest.raises(DataValidationError) as error:
        validate_payload(payload)
    assert 'SYNTHETIC_SECRET_DO_NOT_PRINT' not in str(error.value)


def test_tiny_nonzero_value_does_not_round_to_zero(payload):
    payload['data'][0]['pricing']['price_1m_input_tokens'] = 0.000000001
    valid = validate_payload(payload)
    text = format_value(valid.records[0], 'pricing.price_1m_input_tokens')
    assert text not in {'0', '暂无'}
    assert float(text) == 0.000000001
