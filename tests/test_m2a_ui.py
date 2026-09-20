"""M2A uses independent synthetic UI state. Never load credentials or real data."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import socket

import pytest
import requests
from streamlit.testing.v1 import AppTest

from benchmark_dashboard import ui


@pytest.fixture
def m2a_state():
    paths = ['evaluations.artificial_analysis_intelligence_index', 'pricing.price_1m_input_tokens',
             'median_output_tokens_per_second']
    records = [{'id': f'synthetic-{i}', 'name': f'合成记录{i}', 'slug': f'test-{i}',
                'model_creator': {'name': '甲厂商' if i < 2 else '乙厂商'},
                'evaluations': {'artificial_analysis_intelligence_index': value},
                'pricing': {'price_1m_input_tokens': i}, 'median_output_tokens_per_second': 10}
               for i, value in enumerate([0, None, 12, 12])]
    snapshot = {'id': 1, 'source': 'artificial_analysis', 'content_hash': 'synthetic-hash',
                'collected_at': '2026-01-01T00:00:00+00:00', 'metadata': {},
                'prompt_options': {'prompt_length': 1000}, 'coverage': {path: 0 for path in paths}}
    run = {'id': 1, 'status': 'success', 'source': 'artificial_analysis', 'snapshot_id': 1,
           'started_at': '2026-01-01T00:00:00+00:00', 'finished_at': '2026-01-01T00:00:00+00:00'}
    return {'records': records, 'snapshot': snapshot, 'metric_paths': paths, 'latest_attempt': run,
            'last_success': run, 'snapshot_count': 1,
            'comparison': {'status': 'baseline', 'message': '已建立初始基线，暂无历史可比较',
                           'before': None, 'after': {'run': run, 'snapshot': snapshot, 'records': records}}}


def start(monkeypatch, state):
    monkeypatch.setattr(ui, 'read_dashboard', lambda path: deepcopy(state))
    return AppTest.from_function(ui.main, default_timeout=10).run()


def text(app):
    kinds = ('markdown', 'caption', 'info', 'warning', 'error', 'success', 'text')
    content = '\n'.join(str(e.value) for kind in kinds for e in app.get(kind))
    return content + '\n' + '\n'.join(e.proto.body for e in app.get('html'))


def test_coverage_computes_from_records_not_cached_counts(monkeypatch, m2a_state):
    app = start(monkeypatch, m2a_state)
    assert not app.exception
    content = text(app)
    assert '全量快照' in content and '3 / 4' in content and '75.0%' in content
    app.multiselect(key='creator_filter').set_value(['甲厂商']).run()
    content = text(app)
    assert '当前筛选' in content and '1 / 2' in content and '50.0%' in content
    assert '3 / 4' in content  # all-snapshot denominator stays unchanged
    assert '原始单位' in content and '显示精度' in content


def test_overview_noise_ties_keep_input_order(m2a_state):
    path = 'evaluations.artificial_analysis_intelligence_index'
    records = m2a_state['records'][:2]
    records[0]['evaluations']['artificial_analysis_intelligence_index'] = 0.3
    records[1]['evaluations']['artificial_analysis_intelligence_index'] = 0.1 + 0.2
    assert ui.filter_and_sort(records, [], '', path, True) == records
    assert ui.filter_and_sort(records, [], '', path, False) == records


def test_baseline_is_not_new_model_publication_or_no_change(monkeypatch, m2a_state):
    app = start(monkeypatch, m2a_state)
    app.radio(key='page').set_value('变化记录').run()
    assert not app.exception
    content = text(app)
    assert '已建立初始基线，暂无历史可比较' in content
    assert '与上次成功采集相比无数据变化' not in content
    assert '今日新增' not in content


def test_failure_warning_precedes_history_without_exposing_message(monkeypatch, m2a_state):
    m2a_state['latest_attempt'] = {'id': 2, 'status': 'failed', 'error_code': 'timeout',
                                 'message': 'SYNTHETIC_SECRET', 'started_at': '2026-01-02'}
    app = start(monkeypatch, m2a_state)
    app.radio(key='page').set_value('变化记录').run()
    assert '数据可能过期' in text(app) and '请求超时' in text(app)
    assert 'SYNTHETIC_SECRET' not in text(app)
    assert '与上次成功采集相比无数据变化' not in text(app)


def test_rules_two_four_and_evidence_share_one_read(monkeypatch, m2a_state):
    reader = Mock(side_effect=lambda path: deepcopy(m2a_state))
    monkeypatch.setattr(ui, 'read_dashboard', reader)
    app = AppTest.from_function(ui.main, default_timeout=10).run()
    reader.reset_mock()
    app.radio(key='page').set_value('模型对比').run()
    reader.assert_called_once()
    assert '规则生成，非 AI 分析' in text(app)
    assert any('查看依据' in e.label for e in app.expander)
    assert 'synthetic-hash' in text(app) and 'synthetic-0' in text(app)
    assert 'pricing.price_1m_input_tokens' in text(app)
    app.multiselect(key='compare_models').set_value([r['id'] for r in m2a_state['records']]).run()
    assert not app.exception
    assert '并列' in text(app)
    assert len(app.dataframe[0].value.columns) == 4


def test_rule_generation_failure_retains_original_table(monkeypatch, m2a_state):
    monkeypatch.setattr(ui, 'generate_insights', Mock(side_effect=RuntimeError('SYNTHETIC_SECRET')))
    app = start(monkeypatch, m2a_state)
    app.radio(key='page').set_value('模型对比').run()
    assert not app.exception and len(app.dataframe[0].value.columns) == 2
    assert '规则解读暂不可用' in text(app)
    assert 'SYNTHETIC_SECRET' not in text(app)


def test_conflicting_unit_is_raw_in_table_and_rules(monkeypatch, m2a_state):
    path = 'evaluations.artificial_analysis_intelligence_index'
    for record in m2a_state['records']:
        record['metric_units'] = {path: '%'}
    m2a_state['records'][0]['evaluations']['artificial_analysis_intelligence_index'] = 0.5
    app = start(monkeypatch, m2a_state)
    app.radio(key='page').set_value('模型对比').run()
    assert not app.exception
    frame = app.dataframe[0].value
    index = next(label for label in frame.index if '单位声明待核对' in label)
    assert frame.loc[index].iloc[0] == '0.5'
    assert '不作数值排序或单位转换' in text(app)


def test_overview_conflicting_unit_disables_numeric_order(monkeypatch, m2a_state):
    path = 'evaluations.artificial_analysis_intelligence_index'
    for record in m2a_state['records']:
        record['metric_units'] = {path: '%'}
    app = start(monkeypatch, m2a_state)
    assert not app.exception
    content = text(app)
    assert '已停用该项数值排序' in content and '源站原值 · 单位声明待核对' in content
    table = app.get('html')[0].proto.body
    assert table.index('合成记录0') < table.index('合成记录2')


def test_m2a_no_network_or_real_config_read(monkeypatch, m2a_state):
    def forbidden(*args, **kwargs):
        raise AssertionError('M2A must stay local')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    original_open = Path.open
    secret_path = (Path(__file__).resolve().parents[1] / '.env').resolve()
    def guarded_open(path, *args, **kwargs):
        if path.resolve() == secret_path:
            return forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    app = start(monkeypatch, m2a_state)
    for page in ['模型对比', '数据源状态', '变化记录', '模型总览']:
        app.radio(key='page').set_value(page).run()
        assert not app.exception


def test_changes_empty_database_keeps_it_absent(tmp_path):
    database = tmp_path / 'absent.sqlite3'
    app = AppTest.from_function(ui.main, args=(database,), default_timeout=10).run()
    app.radio(key='page').set_value('变化记录').run()
    assert not app.exception and not database.exists()
    assert '基线' in text(app) or '尚无' in text(app)


def test_ready_change_details_and_filter_use_synthetic_points(monkeypatch, m2a_state):
    before = deepcopy(m2a_state['comparison']['after'])
    after = deepcopy(before)
    after['run'].update(id=2, snapshot_id=2, finished_at='2026-01-02T00:00:00+00:00')
    after['snapshot'].update(id=2, content_hash='synthetic-new-hash')
    after['records'][0]['pricing']['price_1m_input_tokens'] = 2
    after['records'][1]['evaluations']['artificial_analysis_intelligence_index'] = 0
    m2a_state['comparison'] = {'status': 'ready', 'message': '', 'before': before, 'after': after}
    app = start(monkeypatch, m2a_state)
    app.radio(key='page').set_value('变化记录').run()
    assert not app.exception
    content = text(app)
    assert '已有数值变化' in content and '数据补齐' in content
    assert 'synthetic-new-hash' in content and 'pricing.price_1m_input_tokens' in content
    app.multiselect(key='change_types').set_value(['filled']).run()
    assert not app.exception
    assert '当前显示 1 / 2 项变化' in text(app)


def test_repeated_success_snapshot_does_not_skip_to_older_changes(monkeypatch, m2a_state):
    before = deepcopy(m2a_state['comparison']['after'])
    after = deepcopy(before)
    after['run'].update(id=2, finished_at='2026-01-02T00:00:00+00:00')
    m2a_state['comparison'] = {'status': 'ready', 'message': '', 'before': before, 'after': after}
    app = start(monkeypatch, m2a_state)
    app.radio(key='page').set_value('变化记录').run()
    assert not app.exception
    assert '与上次成功采集相比无数据变化' in text(app)
    assert not any(e.key == 'change_types' for e in app.multiselect)
