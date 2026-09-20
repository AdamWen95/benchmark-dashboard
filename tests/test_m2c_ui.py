"""M2C UI uses in-memory records and read-only fake artifacts, never real APIs."""
from copy import deepcopy
from pathlib import Path
import socket

import pytest
import requests
from streamlit.testing.v1 import AppTest

from benchmark_dashboard import briefing, briefing_ui, selection_ui, ui


@pytest.fixture
def state():
    records = [{'id': f'synthetic-{index}', 'name': f'合成模型 {index}', 'slug': f'fixture-{index}',
                'model_creator': {'id': f'creator-{index % 2}', 'name': f'合成厂商 {index % 2}'},
                'evaluations': {'artificial_analysis_intelligence_index': 10 + index},
                'pricing': {'price_1m_input_tokens': index, 'price_1m_output_tokens': index + 1},
                'median_output_tokens_per_second': 10 + index,
                'median_time_to_first_token_seconds': 0}
               for index in range(4)]
    paths = ['evaluations.artificial_analysis_intelligence_index', 'pricing.price_1m_input_tokens',
             'pricing.price_1m_output_tokens', 'median_output_tokens_per_second',
             'median_time_to_first_token_seconds']
    snapshot = {'id': 1, 'source': 'artificial_analysis', 'content_hash': '1' * 64,
                'collected_at': '2026-01-01T01:00:00+00:00', 'record_count': 4,
                'metadata': {}, 'prompt_options': {'prompt_length': 1000},
                'coverage': dict.fromkeys(paths, 4), 'warnings': [], 'unknown_fields': []}
    run = {'id': 1, 'source': 'artificial_analysis', 'status': 'success', 'snapshot_id': 1,
           'started_at': '2026-01-01T01:00:00+00:00', 'finished_at': '2026-01-01T01:00:01+00:00'}
    return {'records': records, 'snapshot': snapshot, 'latest_attempt': run, 'last_success': run,
            'snapshot_count': 1, 'metric_paths': paths,
            'comparison': {'status': 'baseline', 'before': None,
                           'after': {'run': run, 'snapshot': snapshot, 'records': records}}}


def screen(app):
    return '\n'.join(str(item.value) for kind in ('header', 'subheader', 'caption', 'text',
                     'info', 'warning', 'error', 'markdown') for item in app.get(kind))


@pytest.fixture(autouse=True)
def isolated_boundaries(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('UI attempted generation, network or private configuration access')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(briefing, 'generate_briefing', forbidden)
    original_open = Path.open
    actual_env = (ui.ROOT / '.env').resolve()
    def guarded_open(path, *args, **kwargs):
        if path.resolve() == actual_env:
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    monkeypatch.setattr(selection_ui, 'get_default_saved_ids', lambda root: [])
    monkeypatch.setattr(briefing_ui, 'read_synthetic_state', lambda **kwargs:
                        {'status': 'not_generated', 'message': '隔离合成场景未生成。', 'result': None})
    monkeypatch.setattr(briefing_ui, 'read_current_artifact', lambda *args, **kwargs:
                        {'status': 'not_generated', 'message': '该组合尚未生成简报。', 'result': None})


def start(monkeypatch, state):
    monkeypatch.setattr(ui, 'read_dashboard', lambda path: deepcopy(state))
    return AppTest.from_function(ui.main, default_timeout=10).run()


def generated_view(state, ids):
    from benchmark_dashboard.selection import local_model_mapping
    pack = briefing_ui.build_selected_fact_pack(state, ids)
    changes = next(fact for fact in pack['facts'] if fact['kind'] == 'changes')
    limit = next(fact for fact in pack['facts'] if fact['kind'] == 'limitations')
    current = next(fact for fact in pack['facts'] if fact['kind'] not in ('changes', 'limitations'))
    return {'status': 'generated', 'message': '已读取本次限定授权的本地简报。',
            'result': {'sections': [
                {'key': 'current', 'claims': [{'text': '当前组合的合成正文完整显示。', 'fact_ids': [current['id']]}]},
                {'key': 'changes', 'claims': [{'text': '初始基线，暂无历史可比较。', 'fact_ids': [changes['id']]}]},
                {'key': 'limitations', 'claims': [{'text': '未知口径与配置保持未知，非公司实测。', 'fact_ids': [limit['id']]}]},
            ]}, 'fact_pack': pack, 'local_model_mapping': local_model_mapping(state, ids),
            'data_collected_at': state['snapshot']['collected_at'],
            'generated_at': '2026-01-02T02:00:00+00:00', 'expires_at': '2026-01-03T02:00:00+00:00',
            'request_model': 'gpt-5.6-sol', 'response_model': 'gpt-5.6-sol',
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
            'http_status': 200, 'elapsed_seconds': 1.25}


def test_comparison_and_briefing_share_two_and_four_selection_across_page_cleanup(monkeypatch, state):
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('模型对比').run()
    ids = ['synthetic-3', 'synthetic-1']
    app.multiselect(key='compare_models').set_value(ids).run()
    assert app.dataframe[0].value.loc['稳定 ID'].tolist() == ids
    app.radio(key='page').set_value('模型总览').run()
    app.radio(key='page').set_value('AI 简报').run()
    assert app.multiselect(key='briefing_models').value == ids
    four = ['synthetic-1', 'synthetic-0', 'synthetic-3', 'synthetic-2']
    app.multiselect(key='briefing_models').set_value(four).run()
    app.radio(key='page').set_value('数据源状态').run()
    app.radio(key='page').set_value('模型对比').run()
    assert app.multiselect(key='compare_models').value == four
    assert app.dataframe[0].value.loc['稳定 ID'].tolist() == four
    assert len(app.dataframe[0].value.columns) == 4 and not app.exception


def test_new_session_uses_saved_ids_but_never_overwrites_current_user_choice(monkeypatch, state):
    saved = ['synthetic-2', 'synthetic-0']
    monkeypatch.setattr(selection_ui, 'get_default_saved_ids', lambda root: saved)
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('AI 简报').run()
    assert app.multiselect(key='briefing_models').value == saved
    assert '本次已保存简报的模型组合' in screen(app)
    chosen = ['synthetic-1', 'synthetic-3']
    app.multiselect(key='briefing_models').set_value(chosen).run()
    app.radio(key='page').set_value('变化记录').run()
    app.radio(key='page').set_value('AI 简报').run()
    assert app.multiselect(key='briefing_models').value == chosen
    reopened = start(monkeypatch, state)
    reopened.radio(key='page').set_value('模型对比').run()
    assert reopened.multiselect(key='compare_models').value == saved
    assert not app.exception and not reopened.exception


def test_incomplete_selection_survives_switch_and_does_not_read_artifact(monkeypatch, state):
    calls = []
    monkeypatch.setattr(briefing_ui, 'read_current_artifact', lambda state, ids, **kwargs:
        calls.append(ids) or {'status': 'not_generated', 'message': '该组合尚未生成。', 'result': None})
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('模型对比').run()
    app.multiselect(key='compare_models').set_value(['synthetic-1']).run()
    app.radio(key='page').set_value('AI 简报').run()
    assert app.multiselect(key='briefing_models').value == ['synthetic-1']
    assert '请选择 2–4 条模型记录' in screen(app) and calls == []
    assert not app.exception


def test_authorized_result_is_visible_with_generation_off_and_complete_evidence(monkeypatch, state):
    ids = ['synthetic-0', 'synthetic-1']
    monkeypatch.setattr(selection_ui, 'get_default_saved_ids', lambda root: ids)
    view = generated_view(state, ids)
    calls = []
    def read(current, selected, **kwargs):
        calls.append(list(selected))
        assert current['snapshot'] == state['snapshot']
        return deepcopy(view)
    monkeypatch.setattr(briefing_ui, 'read_current_artifact', read)
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('AI 简报').run()
    text = screen(app)
    for expected in ['新增生成未启用', 'AI 辅助解读', '当前组合的合成正文完整显示', '初始基线',
                     '使用限制', '2026-01-01T01:00:00+00:00', '2026-01-02T02:00:00+00:00',
                     'gpt-5.6-sol', '输入 Token：100', 'HTTP 状态：200', '请求耗时：1.25', '人工复核']:
        assert expected in text
    assert '用途与传输范围待负责人确认' not in text
    html = '\n'.join(item.proto.body for item in app.get('html'))
    assert all(value in html for value in ['R1', 'R2', '合成模型 0', '合成模型 1', *ids])
    facts = view['fact_pack']['facts']
    app.selectbox(key='selected_briefing_fact').set_value(facts[-1]['id']).run()
    app.run()
    assert calls == [ids, ids, ids] and not app.exception
    assert not app.button


def test_selection_mismatch_does_not_render_old_result(monkeypatch, state):
    saved = ['synthetic-0', 'synthetic-1']
    view = generated_view(state, saved)
    monkeypatch.setattr(selection_ui, 'get_default_saved_ids', lambda root: saved)
    def read(current, ids, **kwargs):
        return deepcopy(view) if ids == saved else {
            'status': 'mismatch', 'message': '该组合尚未生成；保存结果的所选记录不匹配。',
            'result': view['result']}  # defense: even stray old content stays hidden
    monkeypatch.setattr(briefing_ui, 'read_current_artifact', read)
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('AI 简报').run()
    assert '当前组合的合成正文完整显示' in screen(app)
    app.multiselect(key='briefing_models').set_value(['synthetic-2', 'synthetic-3']).run()
    assert '该组合尚未生成' in screen(app)
    assert '当前组合的合成正文完整显示' not in screen(app)
    app.multiselect(key='briefing_models').set_value(saved).run()
    assert '当前组合的合成正文完整显示' in screen(app) and not app.exception


@pytest.mark.parametrize('status', ['stale', 'failed', 'mismatch'])
def test_unavailable_result_keeps_local_rules_and_original_pages(monkeypatch, state, status):
    view = generated_view(state, ['synthetic-0', 'synthetic-1'])
    view.update(status=status, message='隔离结果过期、失败或组合失配。')
    monkeypatch.setattr(briefing_ui, 'read_current_artifact', lambda *args, **kwargs: deepcopy(view))
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('AI 简报').run()
    assert '当前组合的合成正文完整显示' not in screen(app)
    assert '本地规则摘要（非 AI）' in screen(app) and '初始基线' in screen(app)
    for page in ['模型总览', '模型对比', '数据源状态', '变化记录']:
        app.radio(key='page').set_value(page).run()
        assert not app.exception


def test_reader_failure_is_redacted_and_fact_rendering_remains_available(monkeypatch, state):
    def broken(*args, **kwargs):
        raise ValueError('SYNTHETIC_PRIVATE_ERROR_NOT_FOR_DISPLAY')
    monkeypatch.setattr(briefing_ui, 'read_current_artifact', broken)
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('AI 简报').run()
    assert 'SYNTHETIC_PRIVATE_ERROR_NOT_FOR_DISPLAY' not in screen(app)
    assert '暂不可读' in screen(app) and '本地规则摘要（非 AI）' in screen(app)
    assert not app.exception
