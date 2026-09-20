"""M3-only bounded artifacts and semantic reuse; no config, DB or HTTP loading.

The caller owns run authorization, inter-process exclusion and persistent request
budgets. This module makes at most one injected-client call and never retries.
Existing M2C files and caches are deliberately outside this module's directory.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import re
import tempfile

from . import briefing
from .briefing import AnalysisSettings


ARTIFACT_VERSION = 'm3-daily-artifact-v1'
FAILURE_VERSION = 'm3-daily-failure-v1'
MAX_ARTIFACT_BYTES = 512 * 1024
BASE_URL = 'https://hk.modex-ai.cloud/v1'
MODEL = 'gpt-5.6-sol'
PURPOSE = '本次全量公开记录统计与变化、最多四条例证，仅供本机日更简报复核。'
HISTORICAL_PROMPT_VERSIONS = frozenset({'m3-daily-prompt-v1'})


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value) -> str:
    return sha256(_canonical(value).encode('utf-8')).hexdigest()


def _settings() -> AnalysisSettings:
    # Validation and reading need no credential or fabricated generation grant.
    return AnalysisSettings(base_url=BASE_URL, model=MODEL, input_mode='real')


def _versions(pack: dict) -> dict:
    return {'schema_version': pack['schema_version'], 'pack_version': pack['scope']['pack_version'],
            'mapping_version': pack['metric_mapping_version'], 'rule_version': pack['rule_version'],
            'prompt_version': briefing.DAILY_PROMPT_VERSION}


def _validate_pack(pack: dict, settings: AnalysisSettings | None = None) -> dict:
    checked = briefing._validate_pack(pack, settings or _settings())
    if (checked['source'] != 'artificial_analysis' or checked['scope'].get('kind') != 'daily'
            or checked['scope'].get('pack_version') != 'm3-daily-v1'):
        raise ValueError('日更依据范围不匹配。')
    return checked


def _semantic_key(pack: dict, prompt_version: str) -> str:
    """Same daily scope/content/versions, independent of collection bookkeeping."""
    from .daily_facts import semantic_daily_payload
    checked = _validate_pack(pack)
    semantic = semantic_daily_payload(checked)
    return _digest({'kind': 'daily', 'source': checked['source'], 'semantic': semantic,
                    'versions': {**_versions(checked), 'prompt_version': prompt_version}, 'service': BASE_URL, 'model': MODEL})


def semantic_key(pack: dict) -> str:
    return _semantic_key(pack, briefing.DAILY_PROMPT_VERSION)


def _directory(root: Path) -> Path:
    root = Path(root).resolve()
    directory = root / 'data' / 'daily_briefings'
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('日更结果路径无效。')
    return directory


def _paths(root: Path, key: str) -> tuple[Path, Path]:
    directory = _directory(root)
    paths = directory / (key + '.json'), directory / (key + '.failure.json')
    if any(path.is_symlink() for path in paths):
        raise ValueError('日更结果路径无效。')
    return paths


def _empty(status: str, message: str, pack=None, **extra) -> dict:
    result = briefing._state(status, message, pack)
    result.update(kind='daily', artifact=None, artifact_path=None, fact_pack=None,
                  local_model_mapping=[], semantic_hash=None, semantic_reuse=False,
                  requested_fact_hash=pack.get('fact_hash') if isinstance(pack, dict) else None,
                  current_data_collected_at=pack.get('snapshot', {}).get('collected_at') if isinstance(pack, dict) else None,
                  versions=None)
    result.update(extra)
    return result


def _mapping(rows, pack: dict) -> list[dict]:
    if rows is None:
        rows = []
    if not isinstance(rows, list) or len(rows) > 4:
        raise ValueError('本地例证映射无效。')
    aliases = pack['scope'].get('aliases', [])
    if len(rows) != len(aliases):
        raise ValueError('本地例证映射范围不匹配。')
    if not aliases:
        return []
    ids = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or set(row) != {'alias', 'id', 'model_id', 'source', 'name', 'creator'}
                or row['alias'] != aliases[index] or row['source'] != pack['source']
                or row['model_id'] != row['id']
                or any(not isinstance(row[field], str) or len(row[field]) > 4096 for field in row)):
            raise ValueError('本地例证映射无效。')
        ids.append(row['id'])
    if (len(set(ids)) != len(ids)
            or _digest({'source': pack['source'], 'ids': ids}) != pack['scope'].get('selection_hash')):
        raise ValueError('本地例证映射绑定无效。')
    return json.loads(_canonical(rows))


def _generation(view: dict, now: datetime, ttl: int) -> dict:
    return {**{key: view.get(key) for key in briefing.METADATA_FIELDS},
            'generated_at': now.isoformat(), 'expires_at': (now + timedelta(seconds=ttl)).isoformat(),
            'human_review_status': 'pending'}


def _validate_artifact(artifact: dict) -> dict:
    expected = {'schema_version', 'kind', 'source', 'semantic_key', 'run_id', 'scope_id', 'purpose',
                'retention_approved', 'fact_pack', 'local_model_mapping', 'result', 'generation',
                'versions', 'artifact_hash'}
    if (not isinstance(artifact, dict) or set(artifact) != expected
            or artifact['schema_version'] != ARTIFACT_VERSION or artifact['kind'] != 'daily'
            or artifact['purpose'] != PURPOSE or artifact['retention_approved'] is not True):
        raise ValueError('日更结果结构无效。')
    if artifact['artifact_hash'] != _digest({key: value for key, value in artifact.items() if key != 'artifact_hash'}):
        raise ValueError('日更结果完整性校验失败。')
    _identity(artifact['run_id'])
    _identity(artifact['scope_id'])
    pack = _validate_pack(artifact['fact_pack'])
    versions = artifact['versions']
    if not isinstance(versions, dict):
        raise ValueError('日更结果版本无效。')
    prompt_version = versions.get('prompt_version')
    allowed_prompts = {*HISTORICAL_PROMPT_VERSIONS, briefing.DAILY_PROMPT_VERSION}
    if (not isinstance(prompt_version, str) or prompt_version not in allowed_prompts
            or artifact['source'] != pack['source']
            or versions != {**_versions(pack), 'prompt_version': prompt_version}
            or artifact['semantic_key'] != _semantic_key(pack, prompt_version)):
        raise ValueError('日更结果版本或语义绑定失败。')
    _mapping(artifact['local_model_mapping'], pack)
    briefing.validate_result(_canonical(artifact['result']), pack, _settings())
    generation = artifact['generation']
    if not isinstance(generation, dict) or set(generation) != {*briefing.METADATA_FIELDS, 'generated_at', 'expires_at', 'human_review_status'}:
        raise ValueError('日更运行元数据无效。')
    checked = briefing._metadata({key: generation[key] for key in briefing.METADATA_FIELDS}, _settings(), successful=True)
    if (checked['request_model'] != MODEL or checked['response_model'] != MODEL or checked['http_status'] != 200
            or generation['human_review_status'] != 'pending'):
        raise ValueError('日更服务绑定无效。')
    age = (briefing._time(generation['expires_at']) - briefing._time(generation['generated_at'])).total_seconds()
    if not 0 < age <= briefing.MAX_CACHE_TTL_SECONDS:
        raise ValueError('日更有效期无效。')
    if len(_canonical(artifact).encode('utf-8')) > MAX_ARTIFACT_BYTES:
        raise ValueError('日更结果超过保存边界。')
    return artifact


def _identity(value) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) is None:
        raise ValueError('日更运行标识无效。')
    return value


def _read(path: Path, limit=MAX_ARTIFACT_BYTES) -> dict:
    if path.stat().st_size > limit:
        raise ValueError('日更文件超过读取边界。')
    return briefing._load_json(path.read_text(encoding='utf-8'), limit)


def _failure(path: Path, key: str) -> dict | None:
    if not path.exists():
        return None
    value = _read(path, 16 * 1024)
    expected = {'schema_version', 'semantic_key', 'run_id', 'scope_id', 'attempted_at', 'metadata', 'failure_hash'}
    if (not isinstance(value, dict) or set(value) != expected or value['schema_version'] != FAILURE_VERSION
            or value['semantic_key'] != key
            or value['failure_hash'] != _digest({k: v for k, v in value.items() if k != 'failure_hash'})):
        raise ValueError('日更失败状态无效。')
    _identity(value['run_id'])
    _identity(value['scope_id'])
    briefing._time(value['attempted_at'])
    metadata = value['metadata']
    if not isinstance(metadata, dict) or set(metadata) != {*briefing.METADATA_FIELDS, 'error_code'}:
        raise ValueError('日更失败元数据无效。')
    briefing._metadata({k: metadata[k] for k in briefing.METADATA_FIELDS}, _settings())
    if metadata['error_code'] not in briefing.ERROR_MESSAGES:
        raise ValueError('日更错误类别无效。')
    return value


def _public(artifact: dict, pack: dict, path: Path, *, reused=False) -> dict:
    generation = artifact['generation']
    view = briefing._state('generated', '已读取日更 AI 简报；保留其原始依据、生成时间与人工待复核状态。',
                           artifact['fact_pack'], envelope={**generation, 'result': artifact['result'],
                           'result_metadata': generation}, hit=reused, persisted=True)
    view.update(kind='daily', artifact=artifact, artifact_path=str(path), fact_pack=artifact['fact_pack'],
                local_model_mapping=artifact['local_model_mapping'], semantic_hash=artifact['semantic_key'],
                semantic_reuse=pack['fact_hash'] != artifact['fact_pack']['fact_hash'],
                requested_fact_hash=pack['fact_hash'], current_data_collected_at=pack['snapshot']['collected_at'],
                versions=artifact['versions'])
    if view['semantic_reuse']:
        view['message'] = '当前日更语义依据一致，复用原简报；未重新生成，正文仍绑定原事实包与原数据时间。'
    return view


def read_daily_briefing(pack: dict, *, root: Path, now=None) -> dict:
    """Validate saved original hashes and reuse only a matching daily semantic key."""
    try:
        pack = _validate_pack(pack)
        key = semantic_key(pack)
        path, failure_path = _paths(root, key)
        instant = briefing._now(now)
        artifact = _validate_artifact(_read(path)) if path.exists() else None
        if artifact is not None and artifact['semantic_key'] != key:
            raise ValueError('日更文件名绑定无效。')
        failure = _failure(failure_path, key)
        if failure and (artifact is None or briefing._time(failure['attempted_at']) >= briefing._time(artifact['generation']['generated_at'])):
            if briefing._time(failure['attempted_at']) > instant:
                raise ValueError('日更失败时间无效。')
            return _empty('failed', briefing.ERROR_MESSAGES[failure['metadata']['error_code']], pack,
                          artifact_path=str(path) if artifact else None, semantic_hash=key,
                          history_path=str(path) if artifact else None, **failure['metadata'])
        if artifact is None:
            return _empty('not_generated', '当前全量日更范围尚无对应 AI 简报；规则概况仍可使用。', pack, semantic_hash=key)
        if briefing._time(artifact['generation']['generated_at']) > instant:
            raise ValueError('日更生成时间无效。')
        if instant >= briefing._time(artifact['generation']['expires_at']):
            return _empty('stale', '日更 AI 简报已过期，只能作为历史文件复核；未自动生成。', pack,
                          artifact_path=str(path), history_path=str(path), semantic_hash=key, result_stale=True,
                          generated_at=artifact['generation']['generated_at'], expires_at=artifact['generation']['expires_at'])
        return _public(artifact, pack, path, reused=True)
    except Exception:
        return _empty('failed', '日更结果或依据校验失败；保留原数据与规则概况。')


def read_daily_history(*, root: Path, limit: int = 5, now=None) -> list[dict]:
    """Bounded independent history, never labelled as current or regenerated."""
    if type(limit) is not int or not 1 <= limit <= 20:
        return []
    try:
        directory = _directory(root)
        history = directory / 'history'
        if history.is_symlink() or not history.resolve().is_relative_to(directory.resolve()):
            return []
        instant = briefing._now(now)
        candidates = list(directory.glob('*.json'))[:200] + list(history.glob('*.json'))[:200]
        validated = {}
        for path in candidates:
            if path.is_symlink() or re.fullmatch(r'[0-9a-f]{64}\.json', path.name) is None:
                continue
            try:
                artifact = _validate_artifact(_read(path))
                generation = artifact['generation']
                if briefing._time(generation['generated_at']) > instant:
                    continue
                view = _public(artifact, artifact['fact_pack'], path, reused=True)
                expired = instant >= briefing._time(generation['expires_at'])
                view.update(status='historical', historical=True, result_stale=expired,
                            message='历史日更原文，非本轮新生成；' + ('原有效期已过。' if expired else '保留原有效期。'))
                validated.setdefault(artifact['artifact_hash'], view)
            except Exception:
                continue
        return sorted(validated.values(), key=lambda view: briefing._time(view['generated_at']), reverse=True)[:limit]
    except Exception:
        return []


def _write(path: Path, value: dict) -> None:
    content = _canonical(value)
    if len(content.encode('utf-8')) > MAX_ARTIFACT_BYTES:
        raise ValueError('日更文件过大。')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.daily-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _probe(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=directory) as stream:
        stream.write(b'daily-preflight')
        stream.flush()
        os.fsync(stream.fileno())


def generate_daily_briefing(pack: dict, settings: AnalysisSettings, client, *, root: Path,
                            run_id: str, scope_id: str, retention_approved: bool,
                            local_model_mapping: list[dict] | None = None, now=None) -> dict:
    """The authorized caller must reserve its persistent budget before this call."""
    blocked = briefing.check_settings(settings)
    if blocked:
        return _empty(*blocked)
    if retention_approved is not True:
        return _empty('authorization_required', '本次日更结果的本地保存范围未确认。')
    try:
        if settings.input_mode != 'real' or settings.base_url.rstrip('/') != BASE_URL or settings.model != MODEL:
            raise ValueError('日更服务范围不匹配。')
        _identity(run_id)
        _identity(scope_id)
        pack = _validate_pack(pack, settings)
        mapping = _mapping(local_model_mapping, pack)
        if briefing._contains_secret([mapping, run_id, scope_id], settings):
            raise ValueError('不能保存敏感内容。')
        cached = read_daily_briefing(pack, root=root, now=now)
        if cached['status'] == 'generated':
            return cached
        key = semantic_key(pack)
        path, failure_path = _paths(root, key)
        directory = _directory(root)
        history = directory / 'history'
        if history.is_symlink() or not history.resolve().is_relative_to(directory.resolve()):
            raise ValueError('日更历史目录无效。')
        _probe(directory)
        _probe(history)
        previous = _validate_artifact(_read(path)) if path.exists() else None
    except Exception:
        return _empty('failed', '日更本地预检未通过，未调用分析客户端。')
    result = briefing.generate_briefing(pack, settings, client, cache_dir=None, now=now)
    instant = briefing._now(now)
    if result['status'] != 'generated':
        metadata = {key: result.get(key) for key in (*briefing.METADATA_FIELDS, 'error_code')}
        metadata['error_code'] = briefing._safe_error_code(metadata['error_code'])
        failure = {'schema_version': FAILURE_VERSION, 'semantic_key': key, 'run_id': run_id,
                   'scope_id': scope_id, 'attempted_at': instant.isoformat(), 'metadata': metadata}
        failure['failure_hash'] = _digest(failure)
        try:
            if not briefing._contains_secret(failure, settings):
                _write(failure_path, failure)
        except Exception:
            pass
        result.update(kind='daily', artifact=None, artifact_path=str(path) if previous else None,
                      fact_pack=None, local_model_mapping=[], semantic_hash=key,
                      requested_fact_hash=pack['fact_hash'], semantic_reuse=False)
        return result
    validated_output = False
    recovery = None
    try:
        artifact = {'schema_version': ARTIFACT_VERSION, 'kind': 'daily', 'source': pack['source'],
                    'semantic_key': key, 'run_id': run_id, 'scope_id': scope_id,
                    'purpose': PURPOSE, 'retention_approved': True, 'fact_pack': pack,
                    'local_model_mapping': mapping, 'result': result['result'],
                    'generation': _generation(result, instant, settings.cache_ttl_seconds), 'versions': _versions(pack)}
        artifact['artifact_hash'] = _digest(artifact)
        _validate_artifact(artifact)
        if briefing._contains_secret(artifact, settings):
            raise ValueError('不能保存敏感内容。')
        validated_output = True
        # Preserve an earlier successful result before replacing the current
        # semantic slot after its expiry. No old timestamps or body are edited.
        if previous:
            archived = history / (previous['artifact_hash'] + '.json')
            if not archived.exists():
                _write(archived, previous)
        # Recovery copy makes the same validated output available locally even
        # when a later current-slot/report/UI write fails. No regeneration needed.
        recovery = history / (artifact['artifact_hash'] + '.json')
        _write(recovery, artifact)
        _write(path, artifact)
        return _public(artifact, pack, path)
    except Exception:
        failed = _empty('failed', '本次日更输出保存或绑定校验失败；不得自动重试模型请求。', pack)
        failed.update({field: result.get(field) for field in briefing.METADATA_FIELDS})
        failed['error_code'] = 'cache_error'
        if validated_output:
            failed['artifact'] = artifact
            failed['recovery_path'] = str(recovery) if recovery is not None and recovery.exists() else None
        return failed


def render_daily_markdown(artifact: dict) -> str:
    """Complete validated AI prose, separately labelled original input evidence."""
    artifact = _validate_artifact(artifact)
    pack, generation = artifact['fact_pack'], artifact['generation']
    def plain(value):
        return re.sub(r'([\\`*_{}\[\]()#+.!|>\-])', r'\\\1', html.escape(str(value), quote=False))
    lines = ['# M3 日更简报', '', '真实 AI 辅助正文；用户人工内容复核待完成。', '',
             '来源：Artificial Analysis。本次有效响应的全量记录，不等于整个市场。', '',
             '原数据采集时间：' + plain(pack['snapshot']['collected_at']), '',
             'AI 原生成时间：' + plain(generation['generated_at']), '',
             '原有效期截止：' + plain(generation['expires_at']) + '。到期仅作为历史，不物理删除。', '',
             '## 本地例证映射', '', '下列记录仅为受限例证，不是最佳模型或能力前几名。', '']
    for row in artifact['local_model_mapping']:
        lines.append(f"- {plain(row['alias'])}：{plain(row['name'])}；{plain(row['creator'])}；ID {plain(row['id'])}。")
    titles = {'current': '当前公开数据概况', 'changes': '与上次成功采集相比的全量变化', 'limitations': '使用限制与证据不足'}
    for section in artifact['result']['sections']:
        lines += ['', '## ' + titles[section['key']], '']
        for claim in section['claims']:
            lines += [plain(claim['text']), '', '依据：' + '、'.join(plain(ref) for ref in claim['fact_ids']), '']
    lines += ['## 运行元数据', '',
              f"请求模型：{plain(generation['request_model'])}；上游自报模型：{plain(generation['response_model'])}；HTTP {generation['http_status']}。", '',
              f"耗时：{generation['elapsed_seconds'] if generation['elapsed_seconds'] is not None else '未知'} 秒；usage：{plain(_canonical(generation['usage']) if generation['usage'] is not None else '未知')}；费用：未知。", '',
              '模型自报名称不是底层模型身份的独立证明。结构和引用校验不代替人工内容审核。', '',
              '## 原始最小事实依据', '', '以下程序依据绑定原 AI 正文；缓存复用不篡改其哈希、采集时间或版本。', '']
    evidence = json.dumps({'versions': artifact['versions'], 'semantic_key': artifact['semantic_key'],
                           'fact_pack': pack}, ensure_ascii=False, indent=2, allow_nan=False)
    fence = '`' * max(3, max((len(match.group()) for match in re.finditer(r'`+', evidence)), default=0) + 1)
    return '\n'.join([*lines, fence + 'json', evidence, fence, ''])
