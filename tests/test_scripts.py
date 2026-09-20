"""CLI integration uses synthetic snapshots only, in a project-local test directory."""
import json
from unittest.mock import Mock
import pytest
from benchmark_dashboard.acquisition import Acquisition, save_last_check, atomic_write
from benchmark_dashboard.client import API_URL, SourceError
from benchmark_dashboard.validation import validate_payload
from benchmark_dashboard.store import read_dashboard
from scripts import check_aa, collect, run_local


def m0_snapshot(tmp_path, payload):
    valid = validate_payload(payload)
    path = tmp_path / 'data' / 'snapshots' / f'{valid.content_hash}.json'
    atomic_write(path, json.dumps({'source': 'artificial_analysis', 'endpoint': API_URL,
                                  'content_hash': valid.content_hash, 'payload': payload}))
    result = Acquisition(valid, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:01+00:00', 200, 1, path)
    save_last_check(tmp_path, result)
    return result


def test_from_m0_and_failure_keep_real_storage_contract(tmp_path, monkeypatch, payload, capsys):
    monkeypatch.setattr(collect, 'ROOT', tmp_path)
    m0_snapshot(tmp_path, payload)
    fetch = Mock(side_effect=AssertionError('offline tests must not call API'))
    monkeypatch.setattr(collect, 'acquire', fetch)
    assert collect.main(['--from-m0']) == 0
    assert collect.main(['--from-m0']) == 0
    fetch.assert_not_called()
    database = tmp_path / 'data' / 'dashboard.sqlite3'
    before = read_dashboard(database)
    assert before['snapshot_count'] == 1
    fetch.side_effect = SourceError('authentication_failed', 'HTTP 401：认证失败。', 401, 1)
    assert collect.main([]) == 1
    after = read_dashboard(database)
    assert after['records'] == before['records']
    assert after['snapshot_count'] == 1
    assert after['latest_attempt']['status'] == 'failed'


def test_modified_m0_hash_is_rejected(tmp_path, payload):
    result = m0_snapshot(tmp_path, payload)
    envelope = json.loads(result.snapshot_path.read_text())
    envelope['payload']['data'][0]['name'] = '篡改的合成名称'
    result.snapshot_path.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(collect.SafeError):
        collect.from_m0(tmp_path)


def test_m0_error_report_failure_never_prints_exception_chain(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(check_aa, 'ROOT', tmp_path)
    monkeypatch.setattr(check_aa, 'acquire', Mock(side_effect=RuntimeError('SYNTHETIC_SECRET_INTERNAL_ERROR')))
    monkeypatch.setattr(check_aa, 'write_m0_failure', Mock(side_effect=OSError('SYNTHETIC_SECRET_DISK')))
    assert check_aa.main() == 1
    captured = capsys.readouterr()
    assert 'SYNTHETIC_SECRET' not in captured.out + captured.err
    assert '无法保存' in captured.out


def test_local_launch_forces_loopback_and_usage_stats_off(monkeypatch):
    socket = Mock()
    monkeypatch.setattr(run_local.socket, 'socket', Mock(return_value=socket))
    socket.__enter__ = Mock(return_value=socket)
    socket.__exit__ = Mock(return_value=False)
    call = Mock(return_value=0)
    monkeypatch.setattr(run_local.subprocess, 'call', call)
    assert run_local.main([]) == 0
    socket.bind.assert_called_once_with(('127.0.0.1', 8502))
    args = call.call_args.args[0]
    assert '--server.address=127.0.0.1' in args
    assert '--browser.gatherUsageStats=false' in args


def test_occupied_port_does_not_start_or_kill_any_service(monkeypatch, capsys):
    socket = Mock()
    socket.__enter__ = Mock(return_value=socket)
    socket.__exit__ = Mock(return_value=False)
    socket.bind.side_effect = OSError('synthetic occupied port')
    monkeypatch.setattr(run_local.socket, 'socket', Mock(return_value=socket))
    call = Mock()
    monkeypatch.setattr(run_local.subprocess, 'call', call)
    assert run_local.main(['--port', '8502']) == 1
    call.assert_not_called()
    assert '未终止任何服务' in capsys.readouterr().out
