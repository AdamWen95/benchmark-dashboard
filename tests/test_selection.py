"""M2C selection uses synthetic records; no filesystem or network is involved."""
from copy import deepcopy

import pytest

from benchmark_dashboard.selection import (choose_acceptance_ids, local_model_mapping,
                                           selection_hash, validate_selection)


def state():
    return {'snapshot': {'source': 'artificial_analysis'}, 'records': [
        {'id': 'b', 'name': '同名', 'model_creator': {'id': 'creator-a', 'name': '甲'},
         'pricing': {'price_1m_input_tokens': 0, 'price_1m_output_tokens': 1}},
        {'id': 'a', 'name': '同名', 'model_creator': {'id': 'creator-a', 'name': '甲'},
         'pricing': {'price_1m_input_tokens': 2, 'price_1m_output_tokens': 3}},
        {'id': 'c', 'name': '另一条', 'model_creator': {'id': 'creator-b', 'name': '乙'},
         'pricing': {'price_1m_input_tokens': 0}},
        {'id': 'd', 'name': '另一条', 'model_creator': {'id': 'creator-c', 'name': '丙'}}]}


def test_valid_selection_preserves_order_and_distinct_same_names():
    assert validate_selection(state(), ['b', 'a']) == ['b', 'a']
    assert validate_selection(state(), ['d', 'b', 'c', 'a']) == ['d', 'b', 'c', 'a']
    mapping = local_model_mapping(state(), ['b', 'a'])
    assert [row['alias'] for row in mapping] == ['R1', 'R2']
    assert [row['id'] for row in mapping] == ['b', 'a']
    assert all(row['name'] == '同名' for row in mapping)
    assert all(row['creator'] == '甲' for row in mapping)
    assert all(row['source'] == 'artificial_analysis' and row['model_id'] == row['id'] for row in mapping)


@pytest.mark.parametrize('ids', [[], ['a'], ['a', 'a'], ['a', 'unknown'], ['a', None],
                              ['a', 'b', 'c', 'd', 'other'], ('a', 'b')])
def test_invalid_selection_returns_only_fixed_message(ids):
    with pytest.raises(ValueError, match='^请选择当前快照中 2–4 条不同的有效模型记录。$'):
        validate_selection(state(), ids)


def test_default_is_coverage_then_id_and_prefers_different_creator():
    original = state()
    assert choose_acceptance_ids(original) == ['a', 'c']
    original['records'].reverse()
    assert choose_acceptance_ids(original) == ['a', 'c']
    for record in original['records']:
        record['model_creator'] = {'id': 'only-creator', 'name': '同一厂商'}
    assert choose_acceptance_ids(original) == ['a', 'b']


def test_unknown_creator_does_not_count_as_a_confirmed_different_creator():
    original = state()
    original['records'][2].pop('model_creator')
    assert choose_acceptance_ids(original) == ['a', 'd']


def test_creator_id_name_representation_difference_does_not_prove_diversity():
    original = state()
    original['records'][2]['model_creator'] = {'name': '甲'}
    assert choose_acceptance_ids(original) == ['a', 'd']


def test_zero_is_covered_unknown_fields_and_score_size_do_not_influence_selection():
    original = state()
    original['records'][1]['pricing']['price_1m_input_tokens'] = None
    original['records'][2]['evaluations'] = {'not_in_whitelist': 999999999}
    assert choose_acceptance_ids(original) == ['b', 'c']


def test_selection_hash_binds_order_and_source_not_name():
    original = state()
    first = selection_hash(original, ['a', 'b'])
    assert first != selection_hash(original, ['b', 'a'])
    renamed = deepcopy(original)
    renamed['records'][0]['name'] = '本地新名称'
    assert selection_hash(renamed, ['a', 'b']) == first


def test_duplicate_state_ids_are_not_silently_merged():
    original = state()
    original['records'].append(deepcopy(original['records'][0]))
    with pytest.raises(ValueError):
        validate_selection(original, ['a', 'b'])


def test_long_stable_ids_are_not_truncated():
    original = state()
    original['records'][0]['id'] = 'synthetic-' + 'x' * 300 + '-a'
    original['records'][1]['id'] = 'synthetic-' + 'x' * 300 + '-b'
    ids = [row['id'] for row in original['records'][:2]]
    assert [row['id'] for row in local_model_mapping(original, ids)] == ids
