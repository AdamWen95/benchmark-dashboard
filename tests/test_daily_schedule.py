"""Schedule boundaries use local fixtures and fake execution only."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from benchmark_dashboard import daily_schedule
from benchmark_dashboard.daily_run import DailyError


@pytest.fixture
def scheduled_root(tmp_path):
    plan = json.loads((Path(__file__).resolve().parents[1] / 'daily_schedule.example.json').read_text())
    plan['starts_on'] = '2026-09-21'
    plan['scope_prefix'] = 'synthetic-schedule'
    (tmp_path / 'daily_schedule.json').write_text(json.dumps(plan))
    (tmp_path / 'daily_config.json').write_text(json.dumps({
        'enabled': True, 'timezone': 'Asia/Shanghai', 'output_directory': 'data/daily_runs',
    }))
    return tmp_path


def moment(day=21, hour=1, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def test_preview_uses_beijing_time_and_never_loads_keys(scheduled_root, monkeypatch):
    from benchmark_dashboard import config, analysis_config
    def forbidden(*args, **kwargs):
        pytest.fail('preview read credentials or executed a run')
    monkeypatch.setattr(config, 'load_api_key', forbidden)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    monkeypatch.setattr(daily_schedule, 'execute_daily', forbidden)
    result = daily_schedule.schedule_preview(scheduled_root, now=moment(20, 10))
    assert result['next_scheduled_at'] == '2026-09-21T09:00:00+08:00'
    assert result['network_requests'] == 0
    assert not (scheduled_root / 'data').exists()


def test_preview_next_date_after_schedule_time(scheduled_root):
    assert daily_schedule.schedule_preview(scheduled_root, now=moment())['next_scheduled_at'] == '2026-09-22T09:00:00+08:00'


@pytest.mark.parametrize('now', [moment(20, 10), moment(21, 0, 59)])
def test_no_execution_before_first_date_or_daily_time(scheduled_root, now):
    result = daily_schedule.execute_scheduled(scheduled_root, now=now, execute_fn=lambda *a, **k: pytest.fail('early run'))
    assert result == {'status': 'not_due', 'network_requests': 0}


def test_disabled_switch_blocks_execution_and_check(scheduled_root):
    path = scheduled_root / 'daily_config.json'
    value = json.loads(path.read_text())
    value['enabled'] = False
    path.write_text(json.dumps(value))
    assert daily_schedule.execute_scheduled(scheduled_root, now=moment())['status'] == 'disabled'
    assert daily_schedule.schedule_preview(scheduled_root, now=moment())['next_scheduled_at'] is None
    with pytest.raises(DailyError, match='总开关'):
        daily_schedule.check_schedule(scheduled_root)


@pytest.mark.parametrize('change', [
    {'collection_max_attempts': 2}, {'analysis_max_attempts': True}, {'retry_failed': True},
    {'analysis_only_on_change': False}, {'timezone': 'UTC'}, {'daily_time': '08:00'},
    {'scope_prefix': '../unsafe'}, {'starts_on': 'not-a-date'}, {'extra': 'unexpected'},
])
def test_unauthorized_plan_changes_fail_closed(scheduled_root, change):
    path = scheduled_root / 'daily_schedule.json'
    plan = json.loads(path.read_text())
    path.write_text(json.dumps({**plan, **change}))
    with pytest.raises(DailyError):
        daily_schedule.execute_scheduled(scheduled_root, now=moment())


def test_timezone_mismatch_rejected(scheduled_root):
    path = scheduled_root / 'daily_config.json'
    value = json.loads(path.read_text())
    value['timezone'] = 'UTC'
    path.write_text(json.dumps(value))
    with pytest.raises(DailyError, match='时区'):
        daily_schedule.execute_scheduled(scheduled_root, now=moment())


@pytest.mark.parametrize('analysis,expected', [('generated', 'completed'), ('skipped_no_changes', 'completed'), ('failed', 'failed')])
def test_execution_uses_shared_guards_and_single_attempt_policy(scheduled_root, analysis, expected):
    calls = []
    def execute(root, **kwargs):
        calls.append((root, kwargs))
        return {'run_id': 'daily-2026-09-21', 'collection': {'status': 'success', 'attempts': 1},
                'analysis': {'status': analysis, 'attempts': 0 if analysis == 'skipped_no_changes' else 1}}
    result = daily_schedule.execute_scheduled(scheduled_root, now=moment(), execute_fn=execute)
    assert result['status'] == expected
    assert calls == [(scheduled_root.resolve(), {
        'run_date': '2026-09-21', 'scope_id': 'synthetic-schedule-2026-09-21',
        'allow_analysis': True, 'confirm_limited_use': True, 'analysis_only_on_change': True, 'now': moment(),
    })]


def test_claim_failure_is_not_retried(scheduled_root):
    calls = []
    def blocked(*args, **kwargs):
        calls.append(1)
        raise DailyError('本日已被占用')
    with pytest.raises(DailyError):
        daily_schedule.execute_scheduled(scheduled_root, now=moment(), execute_fn=blocked)
    assert calls == [1]


def test_public_templates_allow_offline_preview_but_keep_execution_disabled(tmp_path, monkeypatch):
    from benchmark_dashboard import config, analysis_config

    def forbidden(*args, **kwargs):
        pytest.fail('public defaults must not read credentials or execute a run')

    root = Path(__file__).resolve().parents[1]
    for name in ('daily_config', 'daily_schedule'):
        (tmp_path / f'{name}.json').write_bytes((root / f'{name}.example.json').read_bytes())
    monkeypatch.setattr(config, 'load_api_key', forbidden)
    monkeypatch.setattr(analysis_config, 'load_analysis_settings', forbidden)
    preview = daily_schedule.schedule_preview(tmp_path, now=moment())
    assert preview['enabled'] is False
    assert preview['next_scheduled_at'] is None
    assert preview['network_requests'] == 0
    assert daily_schedule.execute_scheduled(tmp_path, now=moment(), execute_fn=forbidden) == {
        'status': 'disabled', 'network_requests': 0,
    }
    assert not (tmp_path / 'data').exists()


def test_existing_lock_blocks_install_check_before_credentials(scheduled_root):
    lock = scheduled_root / 'data/daily_control/active.lock'
    lock.parent.mkdir(parents=True)
    lock.write_text('keep-existing-lock')
    with pytest.raises(DailyError, match='运行锁'):
        daily_schedule.check_schedule(scheduled_root)
    assert lock.read_text() == 'keep-existing-lock'


def load_cli():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_scheduled_daily.py'
    spec = importlib.util.spec_from_file_location('schedule_cli_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_cli_is_preview_and_execution_errors_are_sanitized(scheduled_root, monkeypatch, capsys):
    cli = load_cli()
    monkeypatch.setattr(cli, 'ROOT', scheduled_root)
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'schedule_preview'
    def failure(*args, **kwargs):
        raise RuntimeError('synthetic-private-value')
    monkeypatch.setattr(daily_schedule, 'execute_scheduled', failure)
    assert cli.main(['--execute']) == 1
    output = capsys.readouterr().out
    assert 'synthetic-private-value' not in output
    assert json.loads(output)['status'] == 'failed'
