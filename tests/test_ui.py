"""UI acceptance uses synthetic records only; no local credentials or real DB."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import ast
import socket

import pytest
import requests
from streamlit.testing.v1 import AppTest


@pytest.fixture
def dashboard_state():
    records = []
    for index, score in enumerate([0, None, 12.5, 4]):
        records.append({
            'id': f'synthetic-{index}',
            'name': f'合成模型 {index}（精确配置）',
            'slug': f'synthetic-config-{index}',
            'model_creator': {'id': f'creator-{index % 2}', 'name': f'测试厂商 {index % 2}'},
            'release_date': '2020-01-01',
            'evaluations': {'artificial_analysis_intelligence_index': score, 'gpqa': 0.5},
            'pricing': {'price_1m_input_tokens': 0, 'price_1m_output_tokens': index + 1},
            'median_output_tokens_per_second': 20 + index,
            'median_time_to_first_token_seconds': 0,
        })
    run = {'id': 1, 'started_at': '2026-01-01T01:00:00+00:00',
           'finished_at': '2026-01-01T01:00:01+00:00', 'status': 'success',
           'snapshot_id': 1, 'http_status': 200, 'attempts': 1, 'error_code': None, 'message': None}
    return {'records': records, 'snapshot': {
        'id': 1, 'content_hash': 'synthetic-hash',
        'collected_at': '2026-01-01T01:00:01+00:00',
        'endpoint': 'https://artificialanalysis.ai/api/v2/data/llms/models',
        'prompt_options': {'prompt_length': 'medium', 'parallel_queries': 1},
        'coverage': {'evaluations.artificial_analysis_intelligence_index': 3},
        'unknown_fields': ['release_date'], 'warnings': [],
    }, 'latest_attempt': run, 'last_success': run, 'snapshot_count': 1,
        'metric_paths': ['evaluations.artificial_analysis_intelligence_index',
                         'evaluations.gpqa', 'pricing.price_1m_input_tokens',
                         'pricing.price_1m_output_tokens', 'median_output_tokens_per_second',
                         'median_time_to_first_token_seconds']}


def start_app(monkeypatch, dashboard_state):
    from benchmark_dashboard import ui

    monkeypatch.setattr(ui, 'read_dashboard', lambda path: deepcopy(dashboard_state))
    return AppTest.from_function(ui.main, default_timeout=10).run()


def rendered_text(app):
    kinds = ('markdown', 'caption', 'info', 'warning', 'error', 'success', 'text', 'code')
    return '\n'.join(str(element.value) for kind in kinds for element in app.get(kind))


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.tags = []
        self.row = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'tr':
            self.row = []
        elif tag in {'td', 'th'}:
            self.cell = ''

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in {'td', 'th'}:
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr':
            self.rows.append(self.row)


def overview_rows(app):
    parser = TableReader()
    parser.feed(app.get('html')[0].proto.body)
    return [dict(zip(parser.rows[0], row)) for row in parser.rows[1:]]


def test_overview_filter_sort_and_missing_last(monkeypatch, dashboard_state):
    app = start_app(monkeypatch, dashboard_state)
    assert not app.exception
    rows = overview_rows(app)
    assert [row['稳定 ID'] for row in rows] == ['synthetic-2', 'synthetic-3', 'synthetic-0', 'synthetic-1']
    score_column = next(column for column in rows[0] if column.startswith('综合智能指数'))
    assert [row[score_column] for row in rows] == ['12.5', '4', '0', '暂无']
    assert not app.dataframe  # No native text-column sorting can override numeric controls.

    app.radio(key='sort_direction').set_value('从低到高').run()
    assert [row['稳定 ID'] for row in overview_rows(app)] == ['synthetic-0', 'synthetic-3', 'synthetic-2', 'synthetic-1']
    app.multiselect(key='creator_filter').set_value(['测试厂商 0']).run()
    assert {row['厂商'] for row in overview_rows(app)} == {'测试厂商 0'}
    app.text_input(key='model_search').set_value('synthetic-config-2').run()
    assert [row['稳定 ID'] for row in overview_rows(app)] == ['synthetic-2']
    app.text_input(key='model_search').set_value('不存在的模型').run()
    assert '没有符合筛选条件的模型' in rendered_text(app)
    assert not app.exception


def test_overview_html_escapes_all_source_text_and_preserves_precise_names(dashboard_state):
    from benchmark_dashboard.ui import build_overview_html

    record = dashboard_state['records'][0]
    record['name'] = '<img src=x onerror="alert(1)"> 精确名称 & 配置'
    record['id'] = '<script>alert(1)</script>'
    record['slug'] = '" onclick="alert(1)'
    record['model_creator']['name'] = '<a href="https://example.invalid">厂商</a>'
    metric = 'evaluations.<script>source-field</script>'
    html = build_overview_html([record], [metric])
    parser = TableReader()
    parser.feed(html)
    assert not {'img', 'script', 'a'} & set(parser.tags)
    assert parser.rows[1][0] == record['name']
    assert parser.rows[1][1] == record['model_creator']['name']
    assert parser.rows[1][-2] == record['slug']
    assert parser.rows[1][-1] == record['id']
    assert parser.rows[1][2] == '暂无'
    assert '&lt;script&gt;' in html
    assert '&quot;' in html


def test_compare_two_four_and_metadata(monkeypatch, dashboard_state):
    app = start_app(monkeypatch, dashboard_state)
    app.radio(key='page').set_value('模型对比').run()
    assert not app.exception
    assert len(app.dataframe[0].value.columns) == 2
    assert app.multiselect(key='compare_models').proto.max_selections == 4
    app.multiselect(key='compare_models').set_value([record['id'] for record in dashboard_state['records']]).run()
    frame = app.dataframe[0].value
    assert len(frame.columns) == 4
    assert frame.loc['源站精确名称'].tolist() == [record['name'] for record in dashboard_state['records']]
    assert frame.loc['稳定 ID'].tolist() == [record['id'] for record in dashboard_state['records']]
    assert set(frame.loc['评测日期']) == {'源站未提供'}
    assert set(frame.loc['评测版本']) == {'源站未提供'}
    gpqa_row = next(index for index in frame.index if index.startswith('GPQA'))
    assert set(frame.loc[gpqa_row]) == {'0.5'}
    assert '口径未确认' in gpqa_row
    app.multiselect(key='compare_models').set_value(['synthetic-0']).run()
    assert '请选择 2–4 条模型记录' in rendered_text(app)
    assert not app.exception


def test_comparison_rejects_more_than_four_records(monkeypatch, dashboard_state):
    fifth = deepcopy(dashboard_state['records'][0])
    fifth.update(id='synthetic-4', name='第五条合成模型')
    dashboard_state['records'].append(fifth)
    app = start_app(monkeypatch, dashboard_state)
    app.radio(key='page').set_value('模型对比').run()
    app.multiselect(key='compare_models').set_value([record['id'] for record in dashboard_state['records']]).run()
    assert '请选择 2–4 条模型记录' in rendered_text(app)
    assert not app.dataframe
    assert not app.exception


@pytest.mark.parametrize('page', ['模型总览', '模型对比', '数据源状态'])
def test_each_page_has_source_collection_and_prompt_options(monkeypatch, dashboard_state, page):
    app = start_app(monkeypatch, dashboard_state)
    app.radio(key='page').set_value(page).run()
    text = rendered_text(app)
    assert 'Artificial Analysis' in text
    assert 'https://artificialanalysis.ai' in text
    assert '2026-01-01T01:00:01+00:00' in text
    assert '公司网关或本地部署' in text
    assert any('prompt_options' in expander.label for expander in app.expander)
    assert any('prompt_length' in str(block.value) for block in app.json)
    assert not app.exception


@pytest.mark.parametrize('page', ['模型总览', '模型对比', '数据源状态'])
def test_first_open_without_data(monkeypatch, page):
    state = {'records': [], 'snapshot': None, 'latest_attempt': None, 'last_success': None,
             'snapshot_count': 0, 'metric_paths': []}
    app = start_app(monkeypatch, state)
    app.radio(key='page').set_value(page).run()
    assert '尚无有效数据' in rendered_text(app)
    assert 'scripts\\collect.py' in rendered_text(app)
    assert len(app.dataframe) == 0
    assert not app.exception


def test_failed_update_preserves_old_data_and_redacts_errors(monkeypatch, dashboard_state):
    dashboard_state['latest_attempt'] = {
        'status': 'failed', 'started_at': '2026-01-02T00:00:00+00:00', 'finished_at': None,
        'message': 'SYNTHETIC_DO_NOT_RENDER_SECRET', 'error_code': 'SYNTHETIC_DO_NOT_RENDER_SECRET',
        'http_status': 429, 'attempts': 3,
    }
    app = start_app(monkeypatch, dashboard_state)
    assert '数据可能过期' in rendered_text(app)
    assert len(overview_rows(app)) == 4
    app.radio(key='page').set_value('数据源状态').run()
    text = rendered_text(app)
    assert '2026-01-02T00:00:00+00:00' in text
    assert '2026-01-01T01:00:01+00:00' in text
    assert 'SYNTHETIC_DO_NOT_RENDER_SECRET' not in text
    assert not app.exception


@pytest.mark.parametrize(('error_code', 'safe_message'), [
    ('authentication_failed', '鉴权失败'),
    ('SYNTHETIC_DO_NOT_RENDER_SECRET', '本次采集未成功'),
])
def test_first_failed_collection_is_visible_outside_expanders(monkeypatch, error_code, safe_message):
    state = {
        'records': [], 'snapshot': None, 'last_success': None, 'snapshot_count': 0,
        'metric_paths': [], 'latest_attempt': {
            'status': 'failed', 'error_code': error_code,
            'message': 'SYNTHETIC_DO_NOT_RENDER_SECRET',
        },
    }
    app = start_app(monkeypatch, state)

    def visible_alerts(block):
        alerts = []
        for child in getattr(block, 'children', {}).values():
            if child.type == 'expander':
                continue
            if child.type in {'error', 'warning'}:
                alerts.append(child.value)
            alerts.extend(visible_alerts(child))
        return alerts

    alerts = visible_alerts(app.main)
    assert any('采集失败' in message and '尚无有效数据' in message
               and safe_message in message for message in alerts)
    assert 'SYNTHETIC_DO_NOT_RENDER_SECRET' not in rendered_text(app)
    assert not app.exception


@pytest.mark.parametrize(('error_code', 'message'), [
    ('authentication_failed', '鉴权失败'), ('rate_limited', '请求限流'),
    ('timeout', '请求超时'), ('network_error', '网络连接失败'),
    ('upstream_unavailable', '数据源暂时不可用'), ('invalid_schema', '数据结构校验未通过'),
    ('missing_api_key', '尚未配置 API Key'), ('missing_config', '缺少本地配置'),
    ('invalid_api_key', 'API Key 配置格式无效'), ('local_error', '本地采集或存储失败'),
])
def test_known_failure_codes_have_readable_fixed_messages(error_code, message):
    from benchmark_dashboard.ui import safe_failure_message

    assert message in safe_failure_message({'error_code': error_code, 'message': 'DO_NOT_RENDER'})


def test_repeated_success_has_separate_success_and_snapshot_time(monkeypatch, dashboard_state):
    dashboard_state['last_success'] = dict(dashboard_state['last_success'],
                                          finished_at='2026-01-03T01:00:00+00:00')
    app = start_app(monkeypatch, dashboard_state)
    text = rendered_text(app)
    assert '最近成功采集时间：2026-01-03T01:00:00+00:00' in text
    assert '当前快照采集时间：2026-01-01T01:00:01+00:00' in text
    assert '当前数据状态：已加载本地有效快照' in text


def test_page_interactions_do_not_use_network(monkeypatch, dashboard_state):
    def forbidden(*args, **kwargs):
        raise AssertionError('UI must not request any network resource')

    original_connect = socket.socket.connect

    def only_app_test_loopback(sock, address):
        # Windows asyncio's own socketpair uses loopback when AppTest starts.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return original_connect(sock, address)
        return forbidden()

    monkeypatch.setattr(socket.socket, 'connect', only_app_test_loopback)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    app = start_app(monkeypatch, dashboard_state)
    app.multiselect(key='creator_filter').set_value(['测试厂商 0']).run()
    app.radio(key='page').set_value('模型对比').run()
    app.multiselect(key='compare_models').set_value(['synthetic-0', 'synthetic-3']).run()
    app.radio(key='page').set_value('数据源状态').run()
    assert not app.exception


def test_missing_local_database_opens_without_credentials(tmp_path):
    from benchmark_dashboard.ui import main

    database = tmp_path / 'never-created.sqlite3'
    app = AppTest.from_function(main, args=(database,), default_timeout=10).run()
    assert not app.exception
    assert '尚无有效数据' in rendered_text(app)
    assert not database.exists()


def test_ui_does_not_import_secrets_or_acquisition():
    root = Path(__file__).resolve().parents[1]
    forbidden = {'config', 'client', 'acquisition', 'requests', 'httpx', 'dotenv'}
    for relative in ['app.py', 'benchmark_dashboard/ui.py']:
        tree = ast.parse((root / relative).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or '']
            else:
                continue
            assert not any(forbidden & set(name.split('.')) for name in imported)
