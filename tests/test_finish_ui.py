"""Finish UI checks use only synthetic records and injected local history."""
from copy import deepcopy

from benchmark_dashboard import briefing_ui, ui
from benchmark_dashboard.metrics import PERFORMANCE_ZERO_NOTICE
from tests.test_m2c_ui import generated_view, isolated_boundaries, screen, start, state


def test_zero_performance_is_annotated_and_excluded_from_both_sort_directions():
    path = 'median_time_to_first_token_seconds'
    records = [{'id': str(i), 'name': f'fixture{i}', path: value,
                'pricing': {'price_1m_input_tokens': value}} for i, value in enumerate([0, 3, None, 1])]
    assert [r['id'] for r in ui.filter_and_sort(records, [], '', path, False)] == ['3', '1', '0', '2']
    assert [r['id'] for r in ui.filter_and_sort(records, [], '', path, True)] == ['1', '3', '0', '2']
    assert ui.filter_and_sort(records, [], '', 'pricing.price_1m_input_tokens', False)[0]['id'] == '0'
    html = ui.build_overview_html(records, [path, 'pricing.price_1m_input_tokens'])
    assert '0（测量含义待确认）' in html and '<td class="metric">0</td>' in html
    assert records[0][path] == 0


def test_reported_programming_table_preserves_raw_partial_values_and_current_selection(monkeypatch, state):
    state['records'][0]['evaluations'].update(artificial_analysis_coding_index=0, livecodebench=0.406)
    state['records'][1]['evaluations'].update(artificial_analysis_coding_index=43.5, livecodebench=None)
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('模型对比').run()
    app.multiselect(key='compare_models').set_value(['synthetic-0', 'synthetic-1']).run()
    html = '\n'.join(item.proto.body for item in app.get('html'))
    for value in ['已报告编程指标', '0.406', '43.5', 'partial', '暂无', 'synthetic-0', 'synthetic-1']:
        assert value in html
    text = screen(app)
    assert '同一次采集不等于同一次测试' in text
    assert PERFORMANCE_ZERO_NOTICE in text
    assert not app.exception


def test_history_is_explicit_independent_and_keeps_original_claim_with_separate_review_note(monkeypatch, state):
    ids = ['synthetic-0', 'synthetic-1']
    view = generated_view(state, ids)
    original = '历史原文：输出速度为0，保持逐字不变。'
    view['result']['sections'][0]['claims'][0]['text'] = original
    view.update(status='historical', message='历史验收原文，非当前版本生成；原有效期已过。',
                result_stale=True, artifact={'versions': {'rule_version': 'm2a-rules-v1',
                    'prompt_version': 'm2c-prompt-v1', 'pack_version': 'm2c-selected-v1'}})
    calls = []
    monkeypatch.setattr(briefing_ui, 'read_historical_artifact',
                        lambda **kwargs: calls.append(True) or deepcopy(view))
    app = start(monkeypatch, state)
    app.radio(key='page').set_value('AI 简报').run()
    assert calls == [] and original not in screen(app)
    app.checkbox(key='show_historical_briefing').check().run()
    text = screen(app)
    assert original in text and '历史 AI 正文 · 非当前版本生成' in text
    assert '当前复核提示（程序规则，非原 AI 正文）' in text
    assert PERFORMANCE_ZERO_NOTICE in text and '原有效期已过' in text
    assert '用户人工内容复核仍待完成' in text
    app.multiselect(key='briefing_models').set_value(['synthetic-2', 'synthetic-3']).run()
    assert original in screen(app)  # still explicitly history, never current-selection output
    html = '\n'.join(item.proto.body for item in app.get('html'))
    assert '历史简报模型映射' in html and all(value in html for value in ids)
    assert not app.exception
