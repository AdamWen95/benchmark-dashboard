"""M3 daily page checks use only synthetic state and fake read-only artifacts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3

import pytest
import requests
from streamlit.testing.v1 import AppTest

from benchmark_dashboard import analysis_config, briefing, config, daily_ui, selection_ui, ui
from benchmark_dashboard.changes import EVENT_TYPES

READ_LATEST = daily_ui.read_latest_run
READ_DAILY = daily_ui.read_daily_briefing
READ_HISTORY = daily_ui.read_daily_history


@pytest.fixture
def state():
    records = [{'id': f'synthetic-daily-{i}', 'name': f'合成日更模型 {i}',
                'slug': f'fixture-{i}',
                'model_creator': {'id': f'vendor-{i % 2}', 'name': f'合成厂商 {i % 2}'},
                'evaluations': {'artificial_analysis_intelligence_index': 10 + i,
                                'artificial_analysis_coding_index': 20 + i,
                                'livecodebench': 0.1 + i / 10},
                'pricing': {'price_1m_input_tokens': i, 'price_1m_output_tokens': i + 1},
                'median_output_tokens_per_second': 0,
                'median_time_to_first_token_seconds': 0}
               for i in range(6)]
    paths = list(ui.DEFAULT_METRICS)
    snapshot = {'id': 12, 'source': 'artificial_analysis', 'content_hash': 'a' * 64,
                'collected_at': '2026-01-01T01:00:00+00:00', 'record_count': 6,
                'metadata': {}, 'prompt_options': {'prompt_length': 1000},
                'coverage': dict.fromkeys(paths, 6), 'warnings': [], 'unknown_fields': []}
    run = {'id': 15, 'source': 'artificial_analysis', 'status': 'success', 'snapshot_id': 12,
           'started_at': '2026-01-02T02:00:00+00:00', 'finished_at': '2026-01-02T02:00:01+00:00'}
    return {'records': records, 'snapshot': snapshot, 'latest_attempt': run, 'last_success': run,
            'snapshot_count': 2, 'metric_paths': paths,
            'comparison': {'status': 'baseline', 'before': None,
                           'after': {'run': run, 'snapshot': snapshot, 'records': records}}}


@pytest.fixture
def overview(state):
    return {'source': 'artificial_analysis', 'snapshot': state['snapshot'],
            'total_records': 6, 'latest_attempt': state['latest_attempt'],
            'latest_success': state['last_success'],
            'creator_distribution': [{'creator_id': f'vendor-{i}', 'creator_name': f'合成厂商 {i}',
                                      'count': 3} for i in range(2)],
            'coverage': [
                {'path': 'median_output_tokens_per_second', 'label': '输出速度',
                 'valid_count': 6, 'total': 6, 'missing_count': 0, 'coverage': 1.0,
                 'performance_zero_count': 6, 'verification_status': 'confirmed',
                 'unit': 'Token/秒', 'quality_notice': daily_ui.PERFORMANCE_ZERO_NOTICE},
                {'path': 'evaluations.livecodebench', 'label': 'LiveCodeBench 评测',
                 'valid_count': 5, 'total': 6, 'missing_count': 1, 'coverage': 5 / 6,
                 'performance_zero_count': 0, 'verification_status': 'partial',
                 'unit': '原值 · 口径未确认', 'quality_notice': '口径未确认'},
            ],
            'changes': {'status': 'baseline', 'message': '已建立初始基线，暂无历史可比较',
                        'counts': dict.fromkeys(EVENT_TYPES, 0), 'events': [],
                        'before': None, 'after': state['comparison']['after'],
                        'latest_attempt_failed': False, 'same_snapshot': False,
                        'comparison_scope': 'all_records'},
            'rules': ['全量程序统计来自 6 条合成记录，不受所选例证限制。'],
            'versions': {'metric_mapping_version': 'synthetic-metrics',
                         'rule_version': 'synthetic-rules', 'pack_version': 'm3-daily-v1'}}


@pytest.fixture
def saved_run(state, overview, tmp_path):
    from benchmark_dashboard.daily_facts import build_daily_fact_pack
    from benchmark_dashboard.selection import local_model_mapping
    ids = [record['id'] for record in state['records'][:2]]
    return {'schema_version': 'synthetic-daily-run', 'run_id': 'daily-2026-01-02',
            'scope_id': 'daily-2026-01-02', 'run_date': '2026-01-02', 'timezone': 'Asia/Shanghai',
            'started_at': '2026-01-02T02:00:00+00:00', 'finished_at': '2026-01-02T02:00:10+00:00',
            'collection': {'status': 'success', 'http_status': 200, 'attempts': 1,
                           'run_id': 15, 'snapshot_id': 12, 'new_snapshot': False, 'error_code': None},
            'analysis': {'status': 'generated', 'attempts': 1, 'cached': False},
            'overview': deepcopy(overview),
            'fact_pack': build_daily_fact_pack(state, ids),
            'model_mapping': local_model_mapping(state, ids),
            'result_path': str(tmp_path / 'daily-2026-01-02' / 'result.json')}


@pytest.fixture
def generated_view(saved_run, tmp_path):
    facts = [{'id': 'F1', 'kind': 'all_records', 'text': '6 条合成记录'},
             {'id': 'F2', 'kind': 'changes', 'text': '同一内容快照'},
             {'id': 'F3', 'kind': 'limitations', 'text': '测量含义待确认'}]
    return {'status': 'generated', 'message': '已读取匹配的日更结果。', 'kind': 'daily',
            'result': {'sections': [
                {'key': 'current', 'claims': [{'text': '完整合成日更第一部分：全量统计和代码原值。', 'fact_ids': ['F1']}]},
                {'key': 'changes', 'claims': [{'text': '完整合成日更第二部分：与上次成功采集相比无数据变化。', 'fact_ids': ['F2']}]},
                {'key': 'limitations', 'claims': [{'text': '完整合成日更第三部分：零值和口径未知。', 'fact_ids': ['F3']}]},
            ]},
            'fact_pack': {**deepcopy(saved_run['fact_pack']), 'facts': facts},
            'current_data_collected_at': '2026-01-01T01:00:00+00:00',
            'data_collected_at': '2026-01-01T01:00:00+00:00',
            'generated_at': '2026-01-02T03:00:00+00:00', 'expires_at': '2026-01-03T03:00:00+00:00',
            'request_model': 'gpt-5.6-sol', 'response_model': 'gpt-5.6-sol',
            'usage': {'prompt_tokens': 101, 'completion_tokens': 31, 'total_tokens': 132},
            'http_status': 200, 'elapsed_seconds': 1.1, 'semantic_reuse': False,
            'artifact_path': str(tmp_path / 'daily-result.json'),
            'local_model_mapping': [{'alias': 'R1', 'name': '合成日更模型 0', 'creator': '合成厂商 0',
                                     'model_id': 'synthetic-daily-0'}],
            'versions': {'pack_version': 'm3-daily-v1'}}


@pytest.fixture(autouse=True)
def isolated_boundaries(monkeypatch):
    original_generate = briefing.generate_briefing
    def forbidden(*args, **kwargs):
        pytest.fail('daily page attempted network, generation, private config or real DB access')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(requests.Session, '__init__', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(briefing, 'generate_briefing', forbidden)
    monkeypatch.setattr(config, 'load_api_key', forbidden)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    monkeypatch.setattr(selection_ui, 'get_default_saved_ids', lambda root: [])
    original_open = Path.open
    actual_env = (ui.ROOT / '.env').resolve()
    def guarded_open(path, *args, **kwargs):
        if path.resolve() == actual_env:
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: None)
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', forbidden)
    monkeypatch.setattr(daily_ui, 'read_daily_history', forbidden)
    return {'generate': original_generate, 'forbidden': forbidden}


def text(app):
    kinds = ('header', 'subheader', 'caption', 'text', 'info', 'warning', 'error', 'markdown', 'code')
    return '\n'.join(str(item.value) for kind in kinds for item in app.get(kind))


def html(app):
    return '\n'.join(item.proto.body for item in app.get('html'))


def start(monkeypatch, state, overview):
    monkeypatch.setattr(ui, 'read_dashboard', lambda path: deepcopy(state))
    monkeypatch.setattr(daily_ui, 'build_daily_overview', lambda current: deepcopy(overview))
    app = AppTest.from_function(ui.main, default_timeout=10).run()
    return app.radio(key='page').set_value('数据概况 / 日更简报').run()


def test_full_scope_coverage_zero_quality_and_three_collection_times(monkeypatch, state, overview):
    app = start(monkeypatch, state, overview)
    assert {metric.label: metric.value for metric in app.metric}['全量模型记录数'] == '6'
    assert '全量模型记录数' in [metric.label for metric in app.metric]
    for expected in ['不受对比页的 2–4 条选择影响', '有限数值覆盖不等于可靠实测覆盖率',
                     daily_ui.PERFORMANCE_ZERO_NOTICE, '暂无已保存的日更运行状态', '初始基线']:
        assert expected in text(app)
    for expected in ['合成厂商 0', '合成厂商 1', '6 / 6', '5 / 6', '100.0%', 'partial',
                     '2026-01-01T01:00:00+00:00', '2026-01-02T02:00:00+00:00',
                     '2026-01-02T02:00:01+00:00']:
        assert expected in html(app)
    assert not app.button and not app.exception


def test_seven_change_classes_have_full_counts_and_explicit_detail_cap(monkeypatch, state, overview, saved_run):
    changes = overview['changes']
    changes.update(status='changed', message='观察到源站记录变化。',
                   before=deepcopy(changes['after']))
    changes['before']['run']['id'] = 14
    changes['events'] = [
        {'type': EVENT_TYPES[i % 7], 'name': f'合成变更模型-{i}', 'model_id': f'change-{i}',
         'path': 'median_output_tokens_per_second', 'before': 0, 'after': i + 1,
         'absolute': None, 'delta_unit': None, 'comparability': 'unconfirmed',
         'reason': daily_ui.PERFORMANCE_ZERO_NOTICE}
        for i in range(107)]
    changes['counts'] = {kind: sum(event['type'] == kind for event in changes['events']) for kind in EVENT_TYPES}
    saved_run['analysis']['status'] = 'failed'
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    app = start(monkeypatch, state, overview)
    assert '全量变化事件共 107 条；此处展示前 100 条，省略 7 条' in text(app)
    assert str(Path(saved_run['result_path'])) in text(app)
    for expected in ['本次新收录记录', '本次未返回', '元数据更新', '已有数值变化',
                     '数据补齐', '数据转缺失', '口径变化/待确认', '合成变更模型-99']:
        assert expected in html(app)
    assert '合成变更模型-100' not in html(app)
    assert '本次新收录不等于今天发布' in text(app) and not app.exception


def test_saved_daily_renders_complete_three_parts_and_original_generation_time(monkeypatch, state, overview, saved_run, generated_view):
    calls = []
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    def read(pack, **kwargs):
        calls.append(pack)
        return deepcopy(generated_view)
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', read)
    app = start(monkeypatch, state, overview)
    for expected in ['第一部分', '第二部分', '第三部分', '2026-01-02T03:00:00+00:00',
                     'AI 依据的数据采集时间', 'AI 生成时间', 'gpt-5.6-sol', '输入 Token 101',
                     '费用未知', 'HTTP 状态：200', '待人工复核', '不是推荐或排名']:
        assert expected in text(app) or expected in str([item.label for item in app.expander])
    assert 'synthetic-daily-0' in html(app)
    app.selectbox(key='daily_fact').set_value('F3').run()
    app.run()
    assert len(calls) == 3 and not app.exception and not app.button


def test_skipped_unchanged_daily_keeps_old_body_in_history_only(
        monkeypatch, state, overview, saved_run, generated_view):
    saved_run['analysis'] = {'status': 'skipped_no_changes', 'attempts': 0, 'cached': False}
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    history = {**deepcopy(generated_view), 'status': 'historical', 'historical': True}
    monkeypatch.setattr(daily_ui, 'read_daily_history', lambda **kwargs: [deepcopy(history)])
    app = start(monkeypatch, state, overview)
    assert '数据内容未变化，已跳过 AI' in text(app)
    assert '记录的模型请求尝试数：0' in text(app)
    assert '未请求 AI，也未生成新正文' in text(app)
    assert '第一部分' not in text(app)
    app.checkbox(key='show_daily_history').check().run()
    assert '历史日更 AI 原文 · 非当前结果' in text(app)
    assert '第一部分' in text(app) and '非本轮新生成' in text(app)
    assert '2026-01-02T03:00:00+00:00' in text(app)
    assert '全量日更 AI 正文 · 待人工复核' not in text(app)
    assert not app.exception


@pytest.mark.parametrize('status', ['failed', 'stale', 'mismatch', 'not_generated'])
def test_unavailable_daily_never_shows_stray_old_body(monkeypatch, state, overview, saved_run, generated_view, status):
    generated_view['status'] = status
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', lambda *args, **kwargs: deepcopy(generated_view))
    app = start(monkeypatch, state, overview)
    assert '第一部分' not in text(app)
    assert '全量程序统计来自 6 条' in text(app) and '全量七类变化计数' in html(app)
    assert not app.exception


@pytest.mark.parametrize('failure', ['collection', 'analysis', 'snapshot', 'selected_scope'])
def test_saved_run_failure_or_wrong_scope_prevents_ai_read(monkeypatch, state, overview, saved_run, failure):
    if failure == 'collection':
        saved_run['collection']['status'] = 'failed'
        overview['latest_attempt'] = {**overview['latest_attempt'], 'status': 'failed'}
        overview['changes']['latest_attempt_failed'] = True
    elif failure == 'analysis':
        saved_run['analysis']['status'] = 'failed'
    elif failure == 'snapshot':
        saved_run['overview']['snapshot']['content_hash'] = 'b' * 64
    else:
        saved_run['fact_pack']['scope']['kind'] = 'selected'
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    app = start(monkeypatch, state, overview)
    assert '第一部分' not in text(app) and '全量程序统计来自 6 条' in text(app)
    assert not app.exception


def test_same_snapshot_and_semantic_reuse_preserve_times(monkeypatch, state, overview, saved_run, generated_view):
    overview['changes'].update(status='unchanged', same_snapshot=True,
                               message='与上次成功采集相比无数据变化')
    generated_view['semantic_reuse'] = True
    saved_run['analysis'].update(status='cached', cached=True, attempts=0)
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', lambda *args, **kwargs: deepcopy(generated_view))
    app = start(monkeypatch, state, overview)
    assert '与上次成功采集相比无数据变化' in text(app)
    assert '保留原生成时间与原依据；没有重新生成' in text(app)
    assert '2026-01-02T03:00:00+00:00' in text(app) and not app.exception


def test_daily_does_not_change_two_model_selection_or_fetch_selected_artifact(monkeypatch, state, overview):
    app = start(monkeypatch, state, overview)
    app.radio(key='page').set_value('模型对比').run()
    selected = ['synthetic-daily-5', 'synthetic-daily-2']
    app.multiselect(key='compare_models').set_value(selected).run()
    app.radio(key='page').set_value('数据概况 / 日更简报').run()
    assert {metric.label: metric.value for metric in app.metric}['全量模型记录数'] == '6'
    app.run()
    app.radio(key='page').set_value('模型对比').run()
    assert app.multiselect(key='compare_models').value == selected and not app.exception


def test_read_failure_is_redacted_and_keeps_local_statistics(monkeypatch, state, overview, saved_run):
    def broken(*args, **kwargs):
        raise ValueError('SYNTHETIC_PRIVATE_ERROR_DO_NOT_DISPLAY')
    monkeypatch.setattr(daily_ui, 'read_latest_run', broken)
    app = start(monkeypatch, state, overview)
    assert 'SYNTHETIC_PRIVATE_ERROR_DO_NOT_DISPLAY' not in text(app)
    assert '全量程序统计来自 6 条' in text(app)
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', broken)
    app.run()
    assert '暂不可读' in text(app) and 'SYNTHETIC_PRIVATE_ERROR_DO_NOT_DISPLAY' not in text(app)
    assert not app.exception


def test_source_values_are_escaped_and_not_ai_generated(monkeypatch, state, overview):
    overview['creator_distribution'][0]['creator_name'] = '<img src=x onerror=alert(1)>'
    app = start(monkeypatch, state, overview)
    assert '&lt;img src=x onerror=alert(1)&gt;' in html(app)
    assert '<img src=x onerror=alert(1)>' not in html(app)
    assert '全量程序规则（非 AI 正文）' in [item.label for item in app.expander]
    assert not app.exception


def test_empty_local_data_is_honest_and_does_not_generate(monkeypatch, state, overview):
    state['records'] = []
    overview.update(total_records=0, snapshot=None, creator_distribution=[], coverage=[],
                    latest_attempt=None, latest_success=None, rules=[])
    app = start(monkeypatch, state, overview)
    assert '尚无有效模型记录' in text(app) and '尚无对应的日更 AI 结果' in text(app)
    assert not app.exception


def test_real_overview_builder_contract_displays_full_fields_and_success_endpoint(monkeypatch, state):
    from benchmark_dashboard.daily_facts import build_daily_overview
    overview = build_daily_overview(deepcopy(state))
    assert len(overview['coverage']) == 23
    app = start(monkeypatch, state, overview)
    for expected in ['23', '数学指数', '代码指数', 'LiveCodeBench', 'TerminalBench',
                     '15', '12', '2026-01-02T02:00:01+00:00']:
        assert expected in html(app) or expected in [metric.value for metric in app.metric]
    assert '全量变化事件共 0 条' in text(app) and not app.exception


@pytest.mark.parametrize('foreign', ['view_kind', 'result_scope'])
def test_selected_result_cannot_impersonate_daily(monkeypatch, state, overview, saved_run, generated_view, foreign):
    if foreign == 'view_kind':
        generated_view['kind'] = 'selected'
    else:
        generated_view['fact_pack']['scope']['kind'] = 'selected'
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', lambda *args, **kwargs: deepcopy(generated_view))
    app = start(monkeypatch, state, overview)
    assert '第一部分' not in text(app) and '尚无对应的全量日更 AI 结果' in text(app)
    assert not app.exception


def test_later_failed_attempt_does_not_publish_older_successful_daily(monkeypatch, state, overview, saved_run):
    overview['latest_attempt'] = {**overview['latest_attempt'], 'status': 'failed'}
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    app = start(monkeypatch, state, overview)
    assert '上一份有效快照及其真实时间' in text(app)
    assert '没有可发布的当前 AI 简报' in text(app) and not app.exception


def test_history_is_explicit_read_only_and_retains_expired_original(monkeypatch, state, overview, generated_view):
    historical = deepcopy(generated_view)
    historical.update(status='historical', historical=True, result_stale=True)
    calls = []
    monkeypatch.setattr(daily_ui, 'read_daily_history', lambda **kwargs: calls.append(kwargs) or [deepcopy(historical)])
    app = start(monkeypatch, state, overview)
    assert calls == [] and '第一部分' not in text(app)
    app.checkbox(key='show_daily_history').check().run()
    for expected in ['历史日更 AI 原文 · 非当前结果', '原有效期已过', '未延长有效期',
                     '2026-01-02T03:00:00+00:00', '2026-01-03T03:00:00+00:00', '第一部分', '第二部分', '第三部分']:
        assert expected in text(app)
    assert '全量日更 AI 正文 · 待人工复核' not in text(app)
    app.selectbox(key='historical_daily_fact').set_value('F3').run()
    assert len(calls) == 2 and not app.exception


def test_same_snapshot_new_success_cannot_reuse_previous_nonempty_change_body(monkeypatch, state, saved_run):
    from benchmark_dashboard.daily_facts import build_daily_fact_pack, build_daily_overview
    original = deepcopy(state)
    before_records = deepcopy(state['records'])
    before_records[0]['pricing']['price_1m_input_tokens'] = 5
    old_snapshot = {**state['snapshot'], 'id': 11, 'content_hash': 'b' * 64}
    old_run = {**state['last_success'], 'id': 14, 'snapshot_id': 11,
               'started_at': '2026-01-01T02:00:00+00:00', 'finished_at': '2026-01-01T02:00:01+00:00'}
    original['comparison'] = {'status': 'ready',
        'before': {'run': old_run, 'snapshot': old_snapshot, 'records': before_records},
        'after': {'run': state['last_success'], 'snapshot': state['snapshot'], 'records': state['records']}}
    saved_run['overview'] = build_daily_overview(original)
    saved_run['fact_pack'] = build_daily_fact_pack(original, [row['model_id'] for row in saved_run['model_mapping']])
    assert sum(saved_run['overview']['changes']['counts'].values()) > 0
    current = deepcopy(original)
    current['last_success'] = {**state['last_success'], 'id': 16,
                               'started_at': '2026-01-03T02:00:00+00:00', 'finished_at': '2026-01-03T02:00:01+00:00'}
    current['latest_attempt'] = current['last_success']
    current['comparison'] = {'status': 'ready', 'before': original['comparison']['after'],
        'after': {'run': current['last_success'], 'snapshot': state['snapshot'], 'records': state['records']}}
    overview = build_daily_overview(current)
    assert overview['snapshot'] == saved_run['overview']['snapshot']
    assert sum(overview['changes']['counts'].values()) == 0
    monkeypatch.setattr(daily_ui, 'read_latest_run', lambda **kwargs: deepcopy(saved_run))
    app = start(monkeypatch, current, overview)
    assert '与上次成功采集相比无数据变化' in text(app)
    assert '两次成功采集的变化依据与保存日更不匹配' in text(app)
    assert not app.exception


def test_actual_saved_reader_contract_is_offline_and_page_refresh_preserves_files(
        monkeypatch, state, saved_run, tmp_path, isolated_boundaries):
    from benchmark_dashboard import daily_briefing, daily_run
    from benchmark_dashboard.daily_facts import build_daily_overview
    instant = datetime.now(timezone.utc) - timedelta(seconds=1)
    settings = briefing.AnalysisSettings(enabled=True, data_use_confirmed=True,
        base_url='https://hk.modex-ai.cloud/v1', model='gpt-5.6-sol', api_key='offline-fake-key',
        purpose_authorized=True, transmission_authorized=True, real_integration_authorized=True,
        protocol_verified=True, input_mode='real')
    class FakeClient:
        calls = 0
        def generate(self, pack, *, prompt, timeout_seconds):
            self.calls += 1
            refs = {kind: next(fact['id'] for fact in pack['facts'] if fact['kind'] == kind)
                    for kind in ('coverage', 'changes', 'limitations')}
            output = {'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
                      'fact_hash': pack['fact_hash'], 'model': settings.model,
                      'sections': [
                        {'key': 'current', 'claims': [{'text': '隔离真实读取合同：6 条合成记录的全量统计。',
                                                     'fact_ids': [refs['coverage']]}]},
                        {'key': 'changes', 'claims': [{'text': '初始基线，暂无历史可比较。',
                                                     'fact_ids': [refs['changes']]}]},
                        {'key': 'limitations', 'claims': [{'text': '口径和零值测量含义未知，非公司实测。',
                                                         'fact_ids': [refs['limitations']]}]}]}
            return briefing.AnalysisResponse(json.dumps(output, ensure_ascii=False), settings.model, settings.model,
                usage={'prompt_tokens': 101, 'completion_tokens': 31, 'total_tokens': 132},
                http_status=200, elapsed_seconds=0.1)
    fake = FakeClient()
    monkeypatch.setattr(briefing, 'generate_briefing', isolated_boundaries['generate'])
    result = daily_briefing.generate_daily_briefing(saved_run['fact_pack'], settings, fake,
        root=tmp_path, run_id='offline-ui-daily', scope_id='offline-ui-scope', retention_approved=True,
        local_model_mapping=saved_run['model_mapping'], now=instant)
    assert result['status'] == 'generated' and fake.calls == 1
    monkeypatch.setattr(briefing, 'generate_briefing', isolated_boundaries['forbidden'])
    saved_run['schema_version'] = daily_run.SCHEMA
    daily_run._publish(tmp_path, tmp_path / 'data/daily_runs/offline-ui/result.json', saved_run)
    protected = {path: sha256(path.read_bytes()).hexdigest() for path in (tmp_path / 'data').rglob('*.json')}
    monkeypatch.setattr(ui, 'ROOT', tmp_path)
    monkeypatch.setattr(daily_ui, 'read_latest_run', READ_LATEST)
    monkeypatch.setattr(daily_ui, 'read_daily_briefing', READ_DAILY)
    monkeypatch.setattr(daily_ui, 'read_daily_history', READ_HISTORY)
    app = start(monkeypatch, state, build_daily_overview(state))
    assert '隔离真实读取合同' in text(app) and '输入 Token 101' in text(app)
    assert str(tmp_path / 'data/daily_runs/offline-ui/result.json') in text(app)
    app.checkbox(key='show_daily_history').check().run()
    assert '历史日更 AI 原文 · 非当前结果' in text(app)
    app.run()
    assert fake.calls == 1 and not app.exception
    assert protected == {path: sha256(path.read_bytes()).hexdigest() for path in (tmp_path / 'data').rglob('*.json')}
