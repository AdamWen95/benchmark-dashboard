"""Explicit, single-attempt daily process. Page readers never load credentials.

Date and grant guards live at a fixed location, independently of output paths.
A crash retains the guard; neither a restart nor changing the output filename
restores a consumed request. Future dates need a new explicit invocation/grant.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

from .acquisition import acquire, utc_now
from .analysis_config import load_analysis_settings
from .config import load_api_key
from .store import read_dashboard, record_failure, record_success
from .validation import canonical_json

SCHEMA = 'm3-daily-run-v1'
CONTROL = Path('data/daily_control')
SAFE_FAILURE = '本次处理失败；未自动重试。已保存的有效数据继续可用。'


class DailyError(ValueError):
    pass


@dataclass(frozen=True)
class DailyConfig:
    enabled: bool = False
    timezone: str = 'Asia/Shanghai'
    output_directory: str = 'data/daily_runs'


def load_daily_config(root: Path) -> DailyConfig:
    try:
        value = json.loads((root / 'daily_config.json').read_text(encoding='utf-8-sig'))
        if set(value) != {'enabled', 'timezone', 'output_directory'} or type(value['enabled']) is not bool:
            raise ValueError
        ZoneInfo(value['timezone'])
        output = Path(value['output_directory'])
        if output.is_absolute() or not output.parts or output.parts[0] != 'data' or '..' in output.parts:
            raise ValueError
        resolved = (root / output).resolve()
        if not resolved.is_relative_to(root.resolve() / 'data') or resolved == (root / CONTROL).resolve():
            raise ValueError
        return DailyConfig(**value)
    except (OSError, ValueError, TypeError, KeyError):
        raise DailyError('非秘密日更配置无效，请检查 daily_config.json。') from None


def _write(path: Path, value: dict, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(value) + '\n'
    if exclusive:
        with path.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _integrity(db: Path) -> None:
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise DailyError('本地数据库完整性检查失败，未发起请求。')
        if source.execute('PRAGMA foreign_key_check').fetchall():
            raise DailyError('本地数据库关联检查失败，未发起请求。')


def backup_database(db: Path, target: Path) -> None:
    """SQLite online backup API gives a consistent checkpoint, including WAL."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb'):
        pass
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    _integrity(target)


def preview_daily(root: Path, *, ids=None) -> dict:
    """No config, key, network session, writes, or consumed grant."""
    from .daily_facts import build_daily_fact_pack, build_daily_overview
    state = read_dashboard(root / 'data/dashboard.sqlite3')
    overview = build_daily_overview(state)
    pack = build_daily_fact_pack(state, ids)
    return {'mode': 'offline_preview', 'network_requests': 0,
            'notice': '程序规则 / 离线预览，非真实日更或新增 AI 输出。',
            'overview': overview, 'fact_pack': pack,
            'proposed_limits': {'collection': 1, 'analysis': 1, 'selected_examples': 4}}


def _paths(root, config, run_date, scope_id):
    run_id = 'daily-' + run_date
    control = root / CONTROL
    return {'run_id': run_id, 'control': control,
            'date_guard': control / (run_id + '.attempt.json'),
            'scope_guard': control / ('grant-' + sha256(scope_id.encode()).hexdigest() + '.json'),
            'active': control / 'active.lock',
            'result': root / config.output_directory / run_id / 'result.json',
            'backup': root / 'data/backups' / (run_id + '.sqlite3')}


def _validate_invocation(root, config, run_date, scope_id, enable_once, allow_analysis, confirm_limited_use, now):
    if type(enable_once) is not bool or type(allow_analysis) is not bool or type(confirm_limited_use) is not bool:
        raise DailyError('执行参数无效。')
    if not config.enabled and not enable_once:
        raise DailyError('日更默认关闭；需要本轮明确的 --enable-once 参数。')
    if not isinstance(scope_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', scope_id):
        raise DailyError('需要一个明确的新授权范围标识。')
    try:
        parsed = date.fromisoformat(run_date)
        if parsed.isoformat() != run_date or parsed != now.astimezone(ZoneInfo(config.timezone)).date():
            raise ValueError
    except (TypeError, ValueError):
        raise DailyError('真实运行日期必须为配置时区的本地今天；不能改日期恢复同轮额度。') from None
    if allow_analysis and not confirm_limited_use:
        raise DailyError('分析需要本轮限定用途与本地保存展示确认。')
    paths = _paths(root, config, run_date, scope_id)
    if any(paths[key].exists() for key in ('date_guard', 'scope_guard', 'active', 'result')):
        raise DailyError('本日、此授权或其他运行已被占用；停止，不自动恢复请求额度。')
    _integrity(root / 'data/dashboard.sqlite3')
    state = read_dashboard(root / 'data/dashboard.sqlite3')
    if not state['records'] or state['comparison']['status'] == 'unavailable':
        raise DailyError('本地历史数据无法可靠冻结，未发起请求。')
    for directory in (paths['control'], paths['result'].parent, paths['backup'].parent,
                      root / 'data/daily_briefings', root / 'data/snapshots'):
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as handle:
            handle.write(b'preflight')
            handle.flush()
            os.fsync(handle.fileno())
    return paths, state


def _publish(root: Path, path: Path, result: dict) -> None:
    _write(path, result)
    _write(root / CONTROL / 'latest.json', {
        'path': path.relative_to(root).as_posix(),
        'sha256': sha256(path.read_bytes()).hexdigest(), 'run_id': result['run_id']})


def read_latest_run(root: Path) -> dict | None:
    """Bounded, hash-checked local result read; no config or HTTP dependency."""
    try:
        root = root.resolve()
        pointer_path = root / CONTROL / 'latest.json'
        if pointer_path.stat().st_size > 4096:
            return None
        pointer = json.loads(pointer_path.read_text(encoding='utf-8'))
        relative = Path(pointer['path'])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root / 'data') or path.name != 'result.json':
            return None
        if path.stat().st_size > 32 * 1024 * 1024:
            return None
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != pointer['sha256']:
            return None
        result = json.loads(raw)
        if result.get('schema_version') != SCHEMA or result.get('run_id') != pointer['run_id']:
            return None
        result['result_path'] = str(path)
        return result
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _prepare_analysis(root: Path, pack: dict, settings_loader):
    from .briefing import check_settings, _validate_pack
    from .modex_client import request_body_size

    settings = (settings_loader or load_analysis_settings)(
        root, input_mode='real', enable_once=True, one_request_authorized=True,
        purpose_authorized=True, transmission_authorized=True)
    settings = replace(settings, data_use_confirmed=True)
    if check_settings(settings) is not None:
        raise DailyError('分析配置未通过预检查，未发起分析请求。')
    _validate_pack(pack, settings)
    if not settings.api_key:
        raise DailyError('分析密钥尚未配置，未发起分析请求。')
    request_body_size(pack, settings)
    return settings


def execute_daily(root: Path, *, run_date: str, scope_id: str, enable_once=False,
                  allow_analysis=False, confirm_limited_use=False, ids=None,
                  now=None, acquire_fn=None, client_factory=None,
                  source_key_loader=None, analysis_settings_loader=None,
                  analysis_only_on_change=False) -> dict:
    """One authorized run; optional scheduled policy reuses cache or skips unchanged data."""
    from .daily_facts import build_daily_fact_pack, build_daily_overview
    from .daily_briefing import generate_daily_briefing, read_daily_briefing
    from .modex_client import ModexClient, request_body_size
    from .selection import local_model_mapping
    if type(analysis_only_on_change) is not bool:
        raise DailyError('执行参数无效。')
    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    config = load_daily_config(root)
    paths, state = _validate_invocation(root, config, run_date, scope_id, enable_once,
                                         allow_analysis, confirm_limited_use, now)
    # Validate the existing fact pipeline before consuming any allowance. The
    # new post-collection pack is validated again before the one analysis call.
    initial_pack = build_daily_fact_pack(state, ids)
    previous_content_hash = state['snapshot']['content_hash']
    key = (source_key_loader or load_api_key)(root)
    settings = None
    if allow_analysis and not analysis_only_on_change:
        settings = _prepare_analysis(root, initial_pack, analysis_settings_loader)
    guard = {'schema_version': SCHEMA, 'run_id': paths['run_id'], 'scope_id': scope_id,
             'run_date': run_date, 'started_at': utc_now(), 'collection_attempts': 0,
             'analysis_attempts': 0, 'status': 'claimed', 'limits': {'collection': 1, 'analysis': int(allow_analysis)}}
    try:
        _write(paths['active'], guard, exclusive=True)
    except FileExistsError:
        raise DailyError('另一个日更进程正在运行；未发起请求。') from None
    # Re-check while holding the process lock: a concurrent preflight may have
    # observed an empty date before another process completed its entire run.
    try:
        if any(paths[key].exists() for key in ('date_guard', 'scope_guard', 'result')):
            raise DailyError('此日或此授权已被占用，未发起请求。')
        _write(paths['date_guard'], guard, exclusive=True)
        _write(paths['scope_guard'], guard, exclusive=True)
    except Exception:
        paths['active'].unlink(missing_ok=True)
        raise
    result = {**{k: guard[k] for k in ('schema_version', 'run_id', 'scope_id', 'run_date', 'started_at')},
              'timezone': config.timezone, 'finished_at': None,
              'collection': {'status': 'pending', 'attempts': 0},
              'analysis': {'status': 'not_started', 'attempts': 0, 'cached': False},
              'overview': build_daily_overview(state), 'fact_pack': None, 'model_mapping': [],
              'requested_model_ids': list(ids) if ids is not None else None}
    try:
        backup_database(root / 'data/dashboard.sqlite3', paths['backup'])
        result['backup_path'] = paths['backup'].relative_to(root).as_posix()
        guard.update(status='collection_started', collection_attempts=1)
        _write(paths['date_guard'], guard)
        result['collection'].update(status='running', attempts=1)
        _publish(root, paths['result'], result)
        try:
            acquisition = (acquire_fn or acquire)(root, started_at=guard['started_at'],
                                                   max_attempts=1, api_key=key)
            saved = record_success(root / 'data/dashboard.sqlite3', acquisition)
            result['collection'] = {'status': 'success', 'attempts': acquisition.attempts,
                                    'http_status': acquisition.http_status, **saved}
        except Exception as error:
            from .client import SourceError
            from .validation import DataValidationError
            safe = isinstance(error, (SourceError, DataValidationError))
            status = getattr(error, 'http_status', None) if safe else None
            code = getattr(error, 'code', 'invalid_schema') if safe else 'local_error'
            record_failure(root / 'data/dashboard.sqlite3', started_at=guard['started_at'],
                           finished_at=utc_now(), error_code=code, message=SAFE_FAILURE,
                           http_status=status, attempts=1)
            result['collection'] = {'status': 'failed', 'attempts': 1, 'http_status': status, 'error_code': code}
            result['analysis'].update(status='skipped_collection_failed', message=SAFE_FAILURE)
            state = read_dashboard(root / 'data/dashboard.sqlite3')
            result['overview'] = build_daily_overview(state)
            return result
        finally:
            del key
        # read_dashboard uses one SQLite transaction for the two successful
        # points. Every statistic, change, and example below shares this view.
        state = read_dashboard(root / 'data/dashboard.sqlite3')
        result['overview'] = build_daily_overview(state)
        pack = build_daily_fact_pack(state, ids)
        result['fact_pack'] = pack
        from .daily_facts import daily_example_ids
        selected_ids = daily_example_ids(state, ids)[:pack['scope']['selected_count']]
        result['model_mapping'] = local_model_mapping(state, selected_ids) if selected_ids else []
        _publish(root, paths['result'], result)
        if not allow_analysis:
            result['analysis'].update(status='disabled', message='本轮未授权分析；程序规则概况可用。')
            return result
        cached = read_daily_briefing(pack, root=root)
        if cached.get('status') == 'generated':
            result['analysis'] = {**cached, 'attempts': 0, 'cached': True}
            return result
        # A return to older stored content is still a change: new_snapshot only
        # describes storage deduplication, not the immediately previous data.
        if analysis_only_on_change and state['snapshot']['content_hash'] == previous_content_hash:
            result['analysis'].update(status='skipped_no_changes',
                message='本次采集的数据内容未变化，未请求 AI；已保存的旧正文仍可在历史日更审核中查看。')
            return result
        if settings is None:
            settings = _prepare_analysis(root, pack, analysis_settings_loader)
        from .briefing import _validate_pack
        _validate_pack(pack, settings)
        body_bytes = request_body_size(pack, settings)
        if body_bytes > 256 * 1024:
            raise DailyError('请求体超过本地边界，未调用分析模型。')
        guard.update(status='analysis_started', analysis_attempts=1)
        _write(paths['date_guard'], guard)
        result['analysis'].update(status='running', attempts=1, request_body_bytes=body_bytes)
        _publish(root, paths['result'], result)
        client = (client_factory or ModexClient)(settings)
        generated = generate_daily_briefing(pack, settings, client, root=root,
                                            run_id=paths['run_id'], scope_id=scope_id,
                                            retention_approved=confirm_limited_use,
                                            local_model_mapping=result['model_mapping'])
        result['analysis'] = {**generated, 'attempts': 1, 'cached': False,
                              'request_body_bytes': body_bytes}
        return result
    except Exception:
        # Never include arbitrary exception text: it may retain HTTP/config
        # values. A consumed attempt remains consumed after an unknown failure.
        if result['collection']['status'] == 'success':
            result['analysis'].update(status='failed', message=SAFE_FAILURE,
                                      attempts=guard['analysis_attempts'])
        elif result['collection']['status'] != 'failed':
            result['collection'].update(status='failed_or_unknown', attempts=guard['collection_attempts'])
            result['analysis'].update(status='skipped_collection_failed', message=SAFE_FAILURE)
        return result
    finally:
        result['finished_at'] = utc_now()
        guard.update(status='finished', finished_at=result['finished_at'],
                     collection_status=result['collection']['status'], analysis_status=result['analysis']['status'])
        _write(paths['date_guard'], guard)
        _write(paths['scope_guard'], guard)
        _publish(root, paths['result'], result)
        paths['active'].unlink(missing_ok=True)
