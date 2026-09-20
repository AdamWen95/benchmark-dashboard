"""Isolated synthetic AI preparation acceptance. Never load real data or .env."""
from copy import deepcopy
import json
from pathlib import Path
import socket
from unittest.mock import Mock

import pytest
import requests
from streamlit.testing.v1 import AppTest

from benchmark_dashboard import briefing_ui, ui
from benchmark_dashboard.briefing import AnalysisSettings, generate_briefing
from benchmark_dashboard.fact_pack import build_fact_pack
from scripts import generate_briefing as command


@pytest.fixture
def synthetic_state():
    paths = ['evaluations.artificial_analysis_intelligence_index', 'evaluations.livecodebench',
             'pricing.price_1m_input_tokens', 'pricing.price_1m_output_tokens']
    records = [{'id': f'synthetic-{i}', 'name': f'隔离测试模型{i}', 'slug': f'isolated-{i}',
                'model_creator': {'name': '合成厂商'},
                'evaluations': {'artificial_analysis_intelligence_index': value, 'livecodebench': None},
                'pricing': {'price_1m_input_tokens': i, 'price_1m_output_tokens': i + 1}}
               for i, value in enumerate([0, None, 12])]
    snapshot = {'id': 1, 'source': 'artificial_analysis', 'content_hash': '1' * 64,
                'collected_at': '2026-01-01T00:00:01+00:00', 'metadata': {},
                'record_count': len(records), 'prompt_options': {'prompt_length': 1000},
                'coverage': dict.fromkeys(paths, 0), 'unknown_fields': [], 'warnings': []}
    run = {'id': 1, 'source': snapshot['source'], 'snapshot_id': 1, 'status': 'success',
           'started_at': '2026-01-01T00:00:00+00:00', 'finished_at': snapshot['collected_at']}
    return {'records': records, 'snapshot': snapshot, 'metric_paths': paths, 'latest_attempt': run,
            'last_success': run, 'snapshot_count': 1,
            'comparison': {'status': 'baseline', 'message': '已建立初始基线，暂无历史可比较',
                           'before': None, 'after': {'run': run, 'snapshot': snapshot, 'records': records}}}


def screen_text(app):
    return '\n'.join(str(item.value) for kind in
                     ('header', 'subheader', 'markdown', 'caption', 'text', 'info', 'error', 'warning', 'code')
                     for item in app.get(kind))


def show_isolated(state, settings, cache_dir):
    from benchmark_dashboard.briefing_ui import show_briefing
    show_briefing(state, settings=settings, cache_dir=cache_dir)


def enabled_settings():
    # Synthetic flags authorise the injected fake in this isolated test only.
    return AnalysisSettings(enabled=True, data_use_confirmed=True, base_url='https://analysis.invalid',
                            model='synthetic-model', api_key='SYNTHETIC_ANALYSIS_SECRET',
                            purpose_authorized=True, transmission_authorized=True,
                            real_integration_authorized=True, protocol_verified=True)


class FakeClient:
    def __init__(self):
        self.calls = 0

    def generate(self, pack, *, prompt, timeout_seconds):
        self.calls += 1
        choose = lambda kind: next(fact for fact in pack['facts'] if fact['kind'] == kind)
        sections = []
        for key, kind in [('current', 'coverage'), ('changes', 'changes'), ('limitations', 'limitations')]:
            fact = choose(kind)
            sections.append({'key': key, 'claims': [{'text': fact['text'], 'fact_ids': [fact['id']]}]})
        return json.dumps({'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
                           'fact_hash': pack['fact_hash'], 'model': 'synthetic-model',
                           'sections': sections}, ensure_ascii=False)


def test_live_defaults_disabled_without_reading_config_network_or_cache(monkeypatch, synthetic_state, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('Forbidden external or private access')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    original_open = Path.open
    actual_secret = (Path(__file__).resolve().parents[1] / '.env').resolve()
    def guarded_open(path, *args, **kwargs):
        if path.resolve() == actual_secret:
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    # Even stray process values must not silently enable this preparation UI.
    monkeypatch.setenv('ANALYSIS_ENABLED', 'true')
    monkeypatch.setenv('ANALYSIS_API_KEY', 'SYNTHETIC_PROCESS_SECRET')
    reader = Mock(side_effect=lambda path: deepcopy(synthetic_state))
    monkeypatch.setattr(ui, 'read_dashboard', reader)
    cache = tmp_path / 'results-not-created'
    monkeypatch.setattr(briefing_ui, 'DEFAULT_CACHE', cache)
    app = AppTest.from_function(ui.main, default_timeout=10).run()
    reader.reset_mock()
    app.radio(key='page').set_value('AI 简报').run()
    reader.assert_called_once()
    assert not app.exception
    content = screen_text(app)
    assert '新增生成未启用' in content and '查看历史简报不会获得新增请求额度' in content
    assert '初始基线' in content and '本地规则摘要（非 AI）' in content
    assert 'SYNTHETIC_PROCESS_SECRET' not in content
    assert not cache.exists()
    assert not app.button  # no hidden generation action on reruns
    for page in ('模型总览', '模型对比', '数据源状态', '变化记录', 'AI 简报'):
        app.radio(key='page').set_value(page).run()
        assert not app.exception
    assert not cache.exists()


def test_empty_database_page_does_not_create_any_storage(tmp_path):
    database = tmp_path / 'missing.sqlite3'
    app = AppTest.from_function(ui.main, args=(database,), default_timeout=10).run()
    app.radio(key='page').set_value('AI 简报').run()
    assert not app.exception and not database.exists()
    assert '未启用' in screen_text(app) and '暂无可用的本地事实包' in screen_text(app)


def test_fact_failure_is_redacted_and_does_not_hide_other_pages(monkeypatch, synthetic_state):
    monkeypatch.setattr(briefing_ui, 'build_selected_fact_pack', Mock(side_effect=ValueError('SYNTHETIC_SECRET')))
    monkeypatch.setattr(ui, 'read_dashboard', lambda path: deepcopy(synthetic_state))
    app = AppTest.from_function(ui.main, default_timeout=10).run()
    app.radio(key='page').set_value('AI 简报').run()
    assert not app.exception
    assert 'SYNTHETIC_SECRET' not in screen_text(app)
    assert '本地事实整理暂不可用' in screen_text(app)
    app.radio(key='page').set_value('模型对比').run()
    assert not app.exception and app.dataframe


def test_cached_synthetic_briefing_read_and_refresh_never_generate(synthetic_state, tmp_path):
    pack, settings, client = build_fact_pack(synthetic_state), enabled_settings(), FakeClient()
    result = generate_briefing(pack, settings, client, cache_dir=tmp_path)
    assert result['status'] == 'generated'
    app = AppTest.from_function(show_isolated, args=(synthetic_state, settings, tmp_path), default_timeout=10).run()
    assert not app.exception
    text = screen_text(app)
    for required in ('AI 辅助解读 · 待人工复核', '当前数据能说明什么',
                     '与上次成功采集相比发生了什么', '使用限制', '初始基线', '人工复核'):
        assert required in text
    assert 'SYNTHETIC_ANALYSIS_SECRET' not in text
    app.run()
    assert not app.exception and client.calls == 1


@pytest.mark.parametrize('status', ['failed', 'stale'])
def test_failed_or_stale_briefing_keeps_local_rules(monkeypatch, synthetic_state, tmp_path, status):
    monkeypatch.setattr(briefing_ui, 'read_state', lambda *args, **kwargs:
                        {'status': status, 'message': '隔离失败或过期状态', 'result': None})
    app = AppTest.from_function(show_isolated, args=(synthetic_state, AnalysisSettings(), tmp_path), default_timeout=10).run()
    assert not app.exception
    assert '本地规则摘要（非 AI）' in screen_text(app) and '初始基线' in screen_text(app)
    assert not any(item.value == 'AI 辅助解读 · 待人工复核' for item in app.subheader)


def test_preflight_never_opens_data_or_private_config(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError('Preflight must do no I/O')
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    assert command.main(['--check']) == 0
    assert command.main([]) == 2
    captured = capsys.readouterr()
    assert '未启用' in captured.out and '真实联调未授权' in captured.out
    assert not captured.err
