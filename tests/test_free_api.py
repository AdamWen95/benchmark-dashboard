"""Free API contracts use synthetic pages and real local persistence only."""
from copy import deepcopy

import pytest

from benchmark_dashboard.client import SourceError, fetch_models
from tests.test_client import Response, Session, TEST_KEY


def page(number=1, total=1, identity='free-1'):
    return {'tier': 'free', 'intelligence_index_version': 5,
            'pagination': {'page': number, 'page_size': 1, 'total_pages': total,
                           'has_more': number < total},
            'data': [{'id': identity, 'name': '合成免费模型', 'slug': identity,
                      'release_date': None, 'model_creator': {'id': 'creator', 'name': '合成厂商'},
                      'evaluations': {'artificial_analysis_intelligence_index': 42,
                                      'artificial_analysis_coding_index': None,
                                      'artificial_analysis_agentic_index': None,
                                      'artificial_analysis_finance_and_accounting_index': 0},
                      'artificial_analysis_intelligence_index_cost': {'total_cost': None,
                          'cost_per_task': {'total_cost': None}},
                      'pricing': {'price_1m_input_tokens': 0, 'price_1m_output_tokens': 2},
                      'performance': {'median_output_tokens_per_second': 12.5,
                                      'median_time_to_first_token_seconds': 0,
                                      'median_time_to_first_answer_token_seconds': 1.5,
                                      'median_end_to_end_response_time_seconds': 5.2}}]}


def test_reads_all_free_pages_and_keeps_original_responses():
    pages = [page(1, 2), page(2, 2, 'free-2')]
    session = Session(*(Response(payload=item) for item in pages))
    result, status, requests = fetch_models(TEST_KEY, session=session, max_attempts=1)
    assert [item['id'] for item in result['data']] == ['free-1', 'free-2']
    assert result['api_pages'] == pages
    assert result['api_contract'] == 'aa-language-free-v1'
    assert (status, requests) == (200, 2)
    assert [call[1]['params'] for call in session.calls] == [{'page': 1}, {'page': 2}]
    assert all(call[0] == 'https://artificialanalysis.ai/api/v2/language/models/free'
               for call in session.calls)


def test_second_page_failure_has_total_request_count_and_no_retry():
    session = Session(Response(payload=page(1, 2)), Response(503))
    with pytest.raises(SourceError) as error:
        fetch_models(TEST_KEY, session=session, max_attempts=1)
    assert (error.value.code, error.value.attempts) == ('upstream_unavailable', 2)
    assert len(session.calls) == 2


@pytest.mark.parametrize('mutation', ['duplicate', 'version', 'tier', 'page', 'total', 'has_more'])
def test_inconsistent_pages_are_rejected(mutation):
    second = page(2, 2, 'free-2')
    if mutation == 'duplicate':
        second['data'][0]['id'] = 'free-1'
    elif mutation == 'version':
        second['intelligence_index_version'] = 6
    elif mutation == 'tier':
        second['tier'] = 'pro'
    elif mutation == 'page':
        second['pagination']['page'] = 1
    elif mutation == 'total':
        second['pagination']['total_pages'] = 3
    else:
        second['pagination']['has_more'] = True
    session = Session(Response(payload=page(1, 2)), Response(payload=second))
    with pytest.raises(SourceError) as error:
        fetch_models(TEST_KEY, session=session, max_attempts=1)
    assert error.value.code == 'invalid_schema'
    assert error.value.attempts == 2


def test_oversized_batch_stops_after_first_page():
    session = Session(Response(payload=page(1, 11)))
    with pytest.raises(SourceError) as error:
        fetch_models(TEST_KEY, session=session, max_attempts=1)
    assert error.value.code == 'request_budget_exceeded'
    assert error.value.attempts == 1


def test_rate_limit_is_not_retried_or_switched_to_another_endpoint():
    session = Session(Response(429))
    with pytest.raises(SourceError) as error:
        fetch_models(TEST_KEY, session=session)
    assert error.value.code == 'rate_limited'
    assert error.value.attempts == 1
    assert len(session.calls) == 1


def bundle(source=None, version=5):
    from benchmark_dashboard.free_api import merge_pages
    item = page()
    item['intelligence_index_version'] = version
    if source is not None:
        item['data'] = deepcopy(source['data'])
        item['pagination']['page_size'] = len(item['data'])
        for row in item['data']:
            row['performance'] = {key: row.pop(key) for key in (
                'median_output_tokens_per_second', 'median_time_to_first_token_seconds') if key in row}
            row['evaluations'] = {'artificial_analysis_intelligence_index':
                                 row['evaluations'].get('artificial_analysis_intelligence_index')}
    return merge_pages([item])


def save(db, payload, when='2026-11-02T01:00:00+00:00'):
    from benchmark_dashboard.acquisition import Acquisition
    from benchmark_dashboard.store import record_success
    from benchmark_dashboard.validation import validate_payload
    return record_success(db, Acquisition(validate_payload(payload), when, when, 200, 1, db.parent / 'fake.json'))


def test_free_performance_and_indices_reach_storage_and_daily_facts(tmp_path):
    from benchmark_dashboard.store import read_dashboard
    from benchmark_dashboard.daily_facts import build_daily_fact_pack, build_daily_overview
    from benchmark_dashboard.metrics import metric_for, performance_zero_notice
    data = bundle()
    original = deepcopy(data)
    db = tmp_path / 'dashboard.sqlite3'
    save(db, data)
    state = read_dashboard(db)
    assert state['snapshot']['endpoint'].endswith('/language/models/free')
    assert state['records'] == data['data']
    assert 'performance.median_output_tokens_per_second' in state['metric_paths']
    assert 'median_output_tokens_per_second' not in state['metric_paths']
    assert 'evaluations.gpqa' not in state['metric_paths']
    assert metric_for('performance.median_time_to_first_answer_token_seconds').unit == '秒'
    assert performance_zero_notice(state['records'][0], 'performance.median_time_to_first_token_seconds')
    overview = build_daily_overview(state)
    coverage = {item['path']: item for item in overview['coverage']}
    assert coverage['performance.median_output_tokens_per_second']['valid_count'] == 1
    assert 'evaluations.gpqa' not in coverage
    pack = build_daily_fact_pack(state)
    assert 'performance.median_output_tokens_per_second' in pack['scope']['coverage_metric_paths']
    assert original == data


@pytest.mark.parametrize('path,value', [('performance', []),
    ('performance.median_output_tokens_per_second', -1),
    ('performance.median_time_to_first_token_seconds', True),
    ('performance.median_time_to_first_answer_token_seconds', '1.5')])
def test_invalid_free_fields_reject_entire_batch(path, value):
    from benchmark_dashboard.free_api import merge_pages
    from benchmark_dashboard.validation import validate_payload, DataValidationError
    item = page()
    parts = path.split('.')
    target = item['data'][0]
    for key in parts[:-1]:
        target = target[key]
    target[parts[-1]] = value
    with pytest.raises(DataValidationError):
        validate_payload(merge_pages([item]))


def test_legacy_history_stays_intact_and_transition_is_baseline(tmp_path, payload):
    import sqlite3
    from benchmark_dashboard.store import read_dashboard
    from benchmark_dashboard.validation import content_hash
    from benchmark_dashboard.changes import compare_runs
    db = tmp_path / 'dashboard.sqlite3'
    digest = content_hash(payload)
    save(db, payload, '2026-11-01T01:00:00+00:00')
    save(db, bundle())
    state = read_dashboard(db)
    assert state['comparison']['status'] == 'baseline'
    assert compare_runs(state['comparison'])['events'] == []
    with sqlite3.connect(db) as conn:
        stored = conn.execute('SELECT content_hash, endpoint FROM snapshots WHERE id=1').fetchone()
    assert stored == (digest, 'https://artificialanalysis.ai/api/v2/data/llms/models')


def test_index_version_change_is_not_a_score_jump(tmp_path):
    from benchmark_dashboard.store import read_dashboard
    db = tmp_path / 'dashboard.sqlite3'
    save(db, bundle(), '2026-11-01T01:00:00+00:00')
    save(db, bundle(version=6))
    assert read_dashboard(db)['comparison']['status'] == 'baseline'


def test_same_contract_changed_speed_is_detected(tmp_path):
    from benchmark_dashboard.free_api import merge_pages
    from benchmark_dashboard.store import read_dashboard
    from benchmark_dashboard.changes import compare_runs
    db = tmp_path / 'dashboard.sqlite3'
    save(db, bundle(), '2026-11-01T01:00:00+00:00')
    item = page()
    item['data'][0]['performance']['median_output_tokens_per_second'] = 15
    save(db, merge_pages([item]))
    events = compare_runs(read_dashboard(db)['comparison'])['events']
    assert any(item['type'] == 'value' and item['path'] == 'performance.median_output_tokens_per_second'
               for item in events)
    assert all(item['absolute'] is None for item in events)  # unknown performance workload


def test_free_ui_displays_available_metrics_and_actual_source(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from benchmark_dashboard import ui
    db = tmp_path / 'data/dashboard.sqlite3'
    save(db, bundle())
    monkeypatch.setattr(ui, 'DEFAULT_DB', db)
    monkeypatch.setattr(ui, 'ROOT', tmp_path)
    app = AppTest.from_function(ui.main).run(timeout=15)
    assert not app.exception
    assert any('免费版数据' in item.value for item in app.info)
    displayed = app.multiselect(key='overview_metrics').value
    assert 'performance.median_output_tokens_per_second' in displayed
    assert 'evaluations.artificial_analysis_coding_index' not in displayed
    assert any('/language/models/free' in item.value for item in app.caption)


def test_selected_free_fact_pack_includes_new_performance(tmp_path):
    from benchmark_dashboard.free_api import merge_pages
    from benchmark_dashboard.store import read_dashboard
    from benchmark_dashboard.selected_facts import build_selected_fact_pack
    db = tmp_path / 'dashboard.sqlite3'
    save(db, merge_pages([page(1, 2), page(2, 2, 'free-2')]))
    pack = build_selected_fact_pack(read_dashboard(db), ['free-1', 'free-2'])
    assert 'performance.median_output_tokens_per_second' in pack['scope']['metric_paths']


def test_raw_page_corruption_fails_closed_when_reading(tmp_path):
    import json
    import sqlite3
    from benchmark_dashboard.store import read_dashboard
    db = tmp_path / 'dashboard.sqlite3'
    data = bundle()
    save(db, data)
    data['api_pages'][0]['data'][0]['id'] = 'tampered'
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE snapshots SET payload_json=?', (json.dumps(data),))
    assert read_dashboard(db)['comparison']['status'] == 'unavailable'


def test_free_rules_describe_new_speed_and_industry_values(tmp_path):
    from benchmark_dashboard.free_api import merge_pages
    from benchmark_dashboard.store import read_dashboard
    from benchmark_dashboard.insights import generate_insights
    db = tmp_path / 'dashboard.sqlite3'
    save(db, merge_pages([page(1, 2), page(2, 2, 'free-2')]))
    state = read_dashboard(db)
    result = generate_insights(state['records'], state['snapshot'], state['metric_paths'])
    text = '\n'.join(item['text'] for item in result['paragraphs'])
    assert '输出速度' in text and '12.5' in text
    assert '财务与会计能力指数' in text
    assert '暂不用于性能优劣判断' in text


def test_retry_budget_is_shared_by_all_pages():
    import requests
    session = Session(*(Response(payload=page(n, 10, f'model-{n}')) for n in range(1, 10)),
                      requests.Timeout('synthetic timeout'))
    with pytest.raises(SourceError) as error:
        fetch_models(TEST_KEY, session=session, sleep=lambda _: None, max_attempts=3)
    assert error.value.attempts == 10
    assert len(session.calls) == 10
