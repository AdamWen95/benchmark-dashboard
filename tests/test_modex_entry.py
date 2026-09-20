"""Synthetic command/UI integration; no real config, database or transport."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from streamlit.testing.v1 import AppTest

from benchmark_dashboard import analysis_config, briefing, briefing_ui, fact_pack, modex_client, store
from benchmark_dashboard.synthetic_briefing import build_synthetic_pack
from scripts import generate_briefing as command


@pytest.fixture(autouse=True)
def no_network_or_private_env(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected network/private config access')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    original_open = Path.open
    private = Path(__file__).resolve().parents[1] / '.env'
    def guarded(path, *args, **kwargs):
        if path.resolve() == private.resolve():
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)


def settings(**kwargs):
    return replace(briefing.AnalysisSettings(
        enabled=True, base_url='https://hk.modex-ai.cloud/v1', model='gpt-5.6-sol',
        api_key='SYNTHETIC_MODEX_KEY', input_mode='synthetic',
        real_integration_authorized=True, protocol_verified=True, timeout_seconds=120,
    ), **kwargs)


def response(pack):
    return json.dumps({
        'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
        'fact_hash': pack['fact_hash'], 'model': 'gpt-5.6-sol',
        'sections': [
            {'key': key, 'claims': [{'text': next(f['text'] for f in pack['facts'] if f['kind'] == kind),
                                   'fact_ids': [next(f['id'] for f in pack['facts'] if f['kind'] == kind)]}]}
            for key, kind in [('current', 'coverage'), ('changes', 'changes'), ('limitations', 'limitations')]]
    }, ensure_ascii=False)


class FakeModexClient:
    def __init__(self, setting):
        self.calls = 0
        self.closed = False

    def generate(self, pack, *, prompt, timeout_seconds):
        self.calls += 1
        return briefing.AnalysisResponse(response(pack), 'gpt-5.6-sol', 'gpt-5.6-sol',
                                        {'prompt_tokens': 17, 'completion_tokens': 23, 'total_tokens': 40},
                                        200, 0.125)

    def close(self):
        self.closed = True


@pytest.mark.parametrize('argv,code', [([], 2), (['--check'], 0), (['--synthetic-once'], 2),
    (['--real-once'], 2), (['--real-once', '--allow-one-request'], 2),
    (['--real-once', '--allow-one-request', '--save-synthetic'], 2),
    (['--synthetic-once', '--allow-one-request', '--confirm-purpose'], 2)])
def test_unauthorised_preflight_never_opens_config_or_db(monkeypatch, argv, code):
    forbidden = Mock(side_effect=AssertionError('No private I/O permitted'))
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    monkeypatch.setattr(store, 'read_dashboard', forbidden)
    monkeypatch.setattr(modex_client, 'ModexClient', forbidden)
    assert command.main(argv) == code
    forbidden.assert_not_called()


@pytest.mark.parametrize('setting', [settings(enabled=False), settings(api_key=''),
    settings(input_mode='real', data_use_confirmed=False)])
def test_config_gate_precedes_db_and_client(monkeypatch, setting):
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', lambda *a, **kw: setting)
    forbidden = Mock(side_effect=AssertionError('Gate must stop before DB/client'))
    monkeypatch.setattr(store, 'read_dashboard', forbidden)
    monkeypatch.setattr(modex_client, 'ModexClient', forbidden)
    assert command.main(['--synthetic-once', '--allow-one-request']) == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize('save', [False, True])
def test_synthetic_entry_calls_fake_once_never_reads_database_and_isolates_cache(monkeypatch, tmp_path, capsys, save):
    monkeypatch.setattr(command, 'ROOT', tmp_path)
    loader = Mock(return_value=settings())
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', loader)
    db = Mock(side_effect=AssertionError('Synthetic mode must never read real data'))
    monkeypatch.setattr(store, 'read_dashboard', db)
    fake = FakeModexClient(settings())
    monkeypatch.setattr(modex_client, 'ModexClient', lambda _: fake)
    argv = ['--synthetic-once', '--allow-one-request', '--enable-once', '--show-result']
    if save:
        argv += ['--save-synthetic']
    assert command.main(argv) == 0
    assert fake.calls == 1 and fake.closed
    db.assert_not_called()
    assert loader.call_args.kwargs['purpose_authorized'] is False
    assert loader.call_args.kwargs['transmission_authorized'] is False
    output = capsys.readouterr().out
    assert 'SYNTHETIC_MODEX_KEY' not in output
    assert '合成输入、真实服务联调' in output and '初始基线' in output
    assert '"http_status": 200' in output and '"total_tokens": 40' in output
    assert not (tmp_path / 'data').exists()
    assert (tmp_path / '.cache/modex-integration').exists() is save
    assert len(list(tmp_path.rglob('*.json'))) == (1 if save else 0)


def test_real_entry_uses_existing_pack_only_in_memory(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(command, 'ROOT', tmp_path)
    approved = settings(input_mode='real', data_use_confirmed=True,
                        purpose_authorized=True, transmission_authorized=True)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', lambda *a, **kw: approved)
    pack = build_synthetic_pack()
    pack['source'] = 'artificial_analysis'
    pack['fact_hash'] = fact_pack.compute_fact_hash(pack)
    reader = Mock(return_value={'synthetic_test_only': True})
    monkeypatch.setattr(store, 'read_dashboard', reader)
    monkeypatch.setattr(fact_pack, 'build_fact_pack', lambda _: pack)
    fake = FakeModexClient(approved)
    monkeypatch.setattr(modex_client, 'ModexClient', lambda _: fake)
    assert command.main(['--real-once', '--allow-one-request', '--enable-once',
                         '--confirm-purpose', '--confirm-transmission']) == 0
    reader.assert_called_once_with(tmp_path / 'data/dashboard.sqlite3')
    assert fake.calls == 1 and fake.closed and not list(tmp_path.rglob('*'))
    assert '仅保留于本次内存' in capsys.readouterr().out


def test_error_is_redacted_and_does_not_retry(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(command, 'ROOT', tmp_path)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', lambda *a, **kw: settings())
    fake = FakeModexClient(settings())
    fake.generate = Mock(side_effect=RuntimeError('SYNTHETIC_MODEX_KEY raw sensitive body'))
    monkeypatch.setattr(modex_client, 'ModexClient', lambda _: fake)
    assert command.main(['--synthetic-once', '--allow-one-request', '--show-result']) == 1
    assert fake.generate.call_count == 1 and fake.closed
    output = capsys.readouterr().out
    assert 'SYNTHETIC_MODEX_KEY' not in output and 'raw sensitive body' not in output
    assert not list(tmp_path.rglob('*'))


def test_cleanup_error_never_exposes_transport_details(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(command, 'ROOT', tmp_path)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', lambda *a, **kw: settings())
    fake = FakeModexClient(settings())
    fake.close = Mock(side_effect=RuntimeError('SYNTHETIC_MODEX_KEY cleanup raw details'))
    monkeypatch.setattr(modex_client, 'ModexClient', lambda _: fake)
    assert command.main(['--synthetic-once', '--allow-one-request']) == 0
    assert fake.calls == 1
    output = capsys.readouterr().out
    assert 'SYNTHETIC_MODEX_KEY' not in output and 'cleanup raw details' not in output


def isolated_view(cache):
    import streamlit as st
    from benchmark_dashboard.briefing_ui import show_synthetic_integration
    st.warning('离线验收：合成输入与假传输响应，不代表真实服务已调用。')
    show_synthetic_integration(cache_dir=cache)


def test_isolated_page_rerun_only_reads_cache(monkeypatch, tmp_path):
    fake = FakeModexClient(settings())
    view = briefing.generate_briefing(build_synthetic_pack(), settings(), fake, cache_dir=tmp_path)
    assert view['status'] == 'generated'
    before = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    forbidden = Mock(side_effect=AssertionError('Read-only page'))
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    monkeypatch.setattr(store, 'read_dashboard', forbidden)
    monkeypatch.setattr(modex_client, 'ModexClient', forbidden)
    app = AppTest.from_function(isolated_view, args=(tmp_path,), default_timeout=10).run()
    assert not app.exception
    text = '\n'.join(str(x.value) for kind in ('text', 'caption', 'subheader', 'warning') for x in app.get(kind))
    assert '合成输入、真实服务联调' in text and '初始基线' in text
    assert '总 Token：40' in text and '费用：未知' in text
    assert 'SYNTHETIC_MODEX_KEY' not in text
    app.run()
    assert not app.exception and fake.calls == 1
    forbidden.assert_not_called()
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
