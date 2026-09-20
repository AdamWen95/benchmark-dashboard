"""Configured daily schedule with bounded requests; no work at import time.

The systemd timer supplies the clock. The existing daily process remains the
authority for exclusive date claims and request limits, including manual runs.
"""
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .daily_run import DailyError, execute_daily, load_daily_config, preview_daily, _integrity

SCHEDULE_KEYS = {
    'schema_version', 'timezone', 'daily_time', 'starts_on', 'scope_prefix',
    'collection_max_attempts', 'analysis_max_attempts', 'analysis_only_on_change', 'retry_failed',
}
SUCCESS_ANALYSIS = {'generated', 'skipped_no_changes'}


def load_schedule(root: Path) -> dict:
    try:
        path = root / 'daily_schedule.json'
        if path.stat().st_size > 8192:
            raise ValueError
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict) or set(value) != SCHEDULE_KEYS:
            raise ValueError
        if (value['schema_version'] != 'daily-schedule-v1'
                or value['timezone'] != 'Asia/Shanghai' or value['daily_time'] != '09:00'
                or value['analysis_only_on_change'] is not True or value['retry_failed'] is not False):
            raise ValueError
        for key in ('collection_max_attempts', 'analysis_max_attempts'):
            if type(value[key]) is not int or value[key] != 1:
                raise ValueError
        if date.fromisoformat(value['starts_on']).isoformat() != value['starts_on']:
            raise ValueError
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,59}', value['scope_prefix']):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise DailyError('每日调度配置无效，请检查 daily_schedule.json。') from None


def schedule_preview(root: Path, *, now=None) -> dict:
    """Read only nonsecret configuration. No database, credentials or network."""
    config = load_daily_config(root)
    plan = load_schedule(root)
    if config.timezone != plan['timezone']:
        raise DailyError('日更配置与调度时区不一致。')
    local_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(plan['timezone']))
    first_date = max(local_now.date(), date.fromisoformat(plan['starts_on']))
    next_run = datetime.combine(first_date, time(9), ZoneInfo(plan['timezone']))
    if next_run <= local_now:
        next_run += timedelta(days=1)
    return {'status': 'schedule_preview', 'enabled': config.enabled,
            'timezone': plan['timezone'], 'daily_time': plan['daily_time'],
            'starts_on': plan['starts_on'], 'next_scheduled_at': next_run.isoformat() if config.enabled else None,
            'limits': {'collection': 1, 'analysis': 1}, 'analysis_only_on_change': True,
            'retry_failed': False, 'catch_up': False, 'network_requests': 0}


def check_schedule(root: Path) -> dict:
    """Explicit local credential/configuration check; no HTTP or business writes."""
    from .analysis_config import load_analysis_settings
    from .briefing import check_settings, _validate_pack
    from .config import load_api_key
    from .modex_client import request_body_size
    result = schedule_preview(root)
    if not result['enabled']:
        raise DailyError('日更总开关未启用，不能启用定时器。')
    if (root / 'data/daily_control/active.lock').exists():
        raise DailyError('存在日更运行锁；需先核查，不能自动解除。')
    _integrity(root / 'data/dashboard.sqlite3')
    pack = preview_daily(root)['fact_pack']
    source_key = load_api_key(root)
    if not source_key:
        raise DailyError('采集密钥未配置。')
    del source_key
    settings = load_analysis_settings(root, input_mode='real', enable_once=True,
                                     one_request_authorized=True, purpose_authorized=True,
                                     transmission_authorized=True)
    settings = replace(settings, data_use_confirmed=True)
    if check_settings(settings) is not None or not settings.api_key:
        raise DailyError('分析配置未通过预检查。')
    _validate_pack(pack, settings)
    if request_body_size(pack, settings) > 256 * 1024:
        raise DailyError('分析请求体超过允许大小。')
    del settings
    return {**result, 'status': 'ready', 'credentials_valid': True}


def execute_scheduled(root: Path, *, now=None, execute_fn=None) -> dict:
    """One explicit trigger. All request guards are shared with manual runs."""
    root = root.resolve()
    plan = load_schedule(root)
    config = load_daily_config(root)
    if config.timezone != plan['timezone']:
        raise DailyError('日更配置与调度时区不一致。')
    if not config.enabled:
        return {'status': 'disabled', 'network_requests': 0}
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo(plan['timezone']))
    if local_now.date() < date.fromisoformat(plan['starts_on']) or local_now.time() < time(9):
        return {'status': 'not_due', 'network_requests': 0}
    run_date = local_now.date().isoformat()
    # execute_daily validates and persists date, grant and process claims before
    # making requests. Do not delete or replace old claims to make a run succeed.
    result = (execute_fn or execute_daily)(
        root, run_date=run_date, scope_id=plan['scope_prefix'] + '-' + run_date,
        allow_analysis=True, confirm_limited_use=True, analysis_only_on_change=True, now=now,
    )
    success = result['collection']['status'] == 'success' and result['analysis']['status'] in SUCCESS_ANALYSIS
    return {'status': 'completed' if success else 'failed', 'run_id': result['run_id'],
            'collection_status': result['collection']['status'],
            'collection_attempts': result['collection']['attempts'],
            'analysis_status': result['analysis']['status'],
            'analysis_attempts': result['analysis']['attempts'],
            'analysis_cached': result['analysis'].get('cached', False)}
