"""One scoped M2C attempt and its read-only local review artifact.

No configuration or business database is loaded here. The caller supplies one
already-read dashboard state, explicit IDs and a client. The exclusive attempt
record is durable before generation and is never removed to permit a retry.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import re
import tempfile

from . import briefing
from .briefing import AnalysisSettings


SCOPE_ID = 'm2c-first-real-20260920'
ARTIFACT_VERSION = 'm2c-artifact-v1'
MAX_ARTIFACT_BYTES = 512 * 1024
MAX_ATTEMPT_BYTES = 32 * 1024
BASE_URL = 'https://hk.modex-ai.cloud/v1'
MODEL = 'gpt-5.6-sol'
PURPOSE = '仅本次所选 2–4 条既有记录，发送最小事实供 Modex 生成一份本地复核简报。'
DISPLAY_SCOPE = '仅本机网页和本地文件展示本次产物；不包含持续生成、批量缓存或多人发布授权。'
HISTORICAL_VERSIONS = {
    'metric_mapping_version': 'm2b-metrics-v1', 'rule_version': 'm2a-rules-v1',
    'prompt_version': 'm2c-prompt-v1', 'fact_schema_version': 'm2b-facts-v1',
    'pack_version': 'm2c-selected-v1',
}


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _instant(now=None) -> datetime:
    return briefing._now(now)


def _fail(message='本轮本地检查未通过，未发起新请求。', *, status='failed', **extra) -> dict:
    state = briefing._state(status, message)
    state.update(artifact=None, fact_pack=None, local_model_mapping=[], scope_id=SCOPE_ID,
                 authorization_scope=None, request_reserved=False)
    state.update(extra)
    return state


def _paths(root: Path) -> dict:
    root = Path(root).resolve()
    directory = root / 'data' / 'analysis_results'
    reports = root / 'reports'
    paths = {'directory': directory, 'reports': reports,
             'artifact': directory / (SCOPE_ID + '.json'),
             'recovery': directory / (SCOPE_ID + '.recovery.json'),
             'attempt': directory / (SCOPE_ID + '.attempt.json')}
    for path in paths.values():
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('invalid local artifact path')
    return paths


def scoped_settings(settings: AnalysisSettings, *, confirmed_scope: bool,
                    retention_approved: bool) -> AnalysisSettings:
    """Apply only this explicit scope's data-use grant to an immutable copy."""
    if confirmed_scope is not True or retention_approved is not True:
        raise ValueError('本次限定用途及本地保存展示范围未确认。') from None
    if (not isinstance(settings, AnalysisSettings) or settings.input_mode != 'real'
            or settings.base_url.rstrip('/') != BASE_URL or settings.model != MODEL):
        raise ValueError('本次仅支持指定 Modex 服务、模型及真实输入模式。') from None
    scoped = replace(settings, data_use_confirmed=True)
    blocked = briefing.check_settings(scoped)
    if blocked:
        raise ValueError(blocked[1]) from None
    if any(ord(character) < 33 or ord(character) > 126 for character in scoped.api_key):
        raise ValueError('分析配置格式需要核对。') from None
    return scoped


def _readonly_settings() -> AnalysisSettings:
    # No enabled flag, key or fabricated generation authorization is needed to
    # validate a saved result which already carries its narrowly scoped grant.
    return AnalysisSettings(base_url=BASE_URL, model=MODEL, input_mode='real')


def _selection_hash(ids: list[str]) -> str:
    return sha256(_canonical({'source': 'artificial_analysis', 'ids': ids}).encode('utf-8')).hexdigest()


def _validate_ids(ids) -> list[str]:
    if (not isinstance(ids, list) or not 2 <= len(ids) <= 4
            or any(not isinstance(value, str) or not value.strip() or len(value) > 4096 for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('invalid selection')
    return list(ids)


def _versions(pack: dict) -> dict:
    return {'metric_mapping_version': pack['metric_mapping_version'], 'rule_version': pack['rule_version'],
            'prompt_version': briefing.PROMPT_VERSION, 'fact_schema_version': pack['schema_version'],
            'pack_version': pack['scope'].get('pack_version')}


def _scope(pack: dict, ids: list[str]) -> dict:
    return {'scope_id': SCOPE_ID, 'confirmed': True, 'retention_approved': True,
            'purpose': PURPOSE, 'display_scope': DISPLAY_SCOPE, 'input_mode': 'real',
            'selected_ids': ids, 'selection_hash': _selection_hash(ids),
            'snapshot_hash': pack['snapshot']['content_hash'], 'fact_hash': pack['fact_hash'],
            'endpoint': BASE_URL + '/chat/completions', 'model': MODEL, 'maximum_posts': 1}


def _generation(state: dict, *, generated_at=None, expires_at=None) -> dict:
    return {**{key: state.get(key) for key in briefing.METADATA_FIELDS},
            'generated_at': generated_at or state.get('generated_at'),
            'expires_at': expires_at or state.get('expires_at'),
            'human_review_status': 'pending', 'human_review_notice': briefing.HUMAN_REVIEW_NOTICE}


def _validate_mapping(mapping, ids: list[str], source: str) -> None:
    if not isinstance(mapping, list) or len(mapping) != len(ids):
        raise ValueError('invalid local mapping')
    for index, row in enumerate(mapping):
        if (not isinstance(row, dict) or set(row) != {'alias', 'id', 'model_id', 'name', 'creator', 'source'}
                or row['alias'] != f'R{index + 1}' or row['id'] != ids[index] or row['model_id'] != ids[index]
                or row['source'] != source or not isinstance(row['name'], str)
                or not isinstance(row['creator'], str)):
            raise ValueError('invalid local mapping')


def _validate_artifact(value: dict) -> dict:
    expected = {'schema_version', 'authorization_scope', 'selected_ids', 'local_model_mapping', 'fact_pack',
                'result', 'generation', 'versions', 'fact_count', 'fact_pack_bytes', 'request_body_bytes',
                'request_body_bytes_source', 'request_body_bytes_preflight', 'request_body_bytes_match',
                'status', 'selection_method', 'selection_reason'}
    if not isinstance(value, dict) or set(value) != expected or value['schema_version'] != ARTIFACT_VERSION:
        raise ValueError('invalid artifact')
    if value['status'] != 'generated':
        raise ValueError('not a successful artifact')
    if value['selection_method'] not in ('explicit_ids', 'technical_acceptance_fallback'):
        raise ValueError('invalid selection method')
    if value['selection_reason'] != _selection_reason(value['selection_method']):
        raise ValueError('invalid selection reason')
    ids = _validate_ids(value['selected_ids'])
    settings = _readonly_settings()
    pack = briefing._validate_pack(value['fact_pack'], settings)
    if (pack['source'] != 'artificial_analysis'
            or pack['scope'].get('selection_hash') != _selection_hash(ids)
            or value['authorization_scope'] != _scope(pack, ids)):
        raise ValueError('artifact binding mismatch')
    from . import insights, metrics
    from .selected_facts import PACK_VERSION
    current_versions = {**_versions(pack), 'metric_mapping_version': metrics.METRIC_MAPPING_VERSION,
                        'rule_version': insights.RULE_VERSION, 'pack_version': PACK_VERSION}
    versions = value['versions']
    if versions != HISTORICAL_VERSIONS and versions != current_versions:
        raise ValueError('unsupported artifact version')
    if (any(versions[key] != pack[key] for key in ('metric_mapping_version', 'rule_version'))
            or versions['fact_schema_version'] != pack['schema_version']):
        raise ValueError('artifact version binding mismatch')
    if versions['pack_version'] != pack['scope'].get('pack_version'):
        raise ValueError('artifact pack version binding mismatch')
    mapping = value['local_model_mapping']
    _validate_mapping(mapping, ids, pack['source'])
    briefing.validate_result(_canonical(value['result']), pack, settings)
    generation = value['generation']
    required_generation = {*briefing.METADATA_FIELDS, 'generated_at', 'expires_at',
                           'human_review_status', 'human_review_notice'}
    if not isinstance(generation, dict) or set(generation) != required_generation:
        raise ValueError('invalid generation metadata')
    briefing._metadata({key: generation[key] for key in briefing.METADATA_FIELDS}, settings, successful=True)
    if (generation['request_model'] != MODEL or generation['response_model'] != MODEL
            or generation['http_status'] != 200 or generation['human_review_status'] != 'pending'
            or generation['human_review_notice'] != briefing.HUMAN_REVIEW_NOTICE):
        raise ValueError('invalid service binding')
    duration = (briefing._time(generation['expires_at']) - briefing._time(generation['generated_at'])).total_seconds()
    if not 0 < duration <= briefing.MAX_CACHE_TTL_SECONDS:
        raise ValueError('invalid expiry')
    if (type(value['fact_count']) is not int or value['fact_count'] != len(pack['facts'])
            or type(value['fact_pack_bytes']) is not int
            or value['fact_pack_bytes'] != len(_canonical(pack).encode('utf-8'))
            or type(value['request_body_bytes']) is not int or not 0 < value['request_body_bytes'] <= 2 * 1024 * 1024
            or type(value['request_body_bytes_preflight']) is not int
            or not 0 < value['request_body_bytes_preflight'] <= 2 * 1024 * 1024
            or type(value['request_body_bytes_match']) is not bool
            or value['request_body_bytes_match'] != (value['request_body_bytes'] == value['request_body_bytes_preflight'])
            or value['request_body_bytes_source'] not in ('transport', 'preflight')):
        raise ValueError('invalid byte counts')
    if len(_canonical(value).encode('utf-8')) > MAX_ARTIFACT_BYTES:
        raise ValueError('artifact exceeds limit')
    return value


def _read_json(path: Path, limit=MAX_ARTIFACT_BYTES) -> dict:
    if path.stat().st_size > limit:
        raise ValueError('file exceeds limit')
    return briefing._load_json(path.read_text(encoding='utf-8'), limit)


def _saved(paths: dict) -> tuple[dict | None, Path | None]:
    # The primary artifact is authoritative. Only an absent primary can recover
    # from the identical validated output kept before the primary atomic write.
    for path in (paths['artifact'], paths['recovery']):
        if path.exists():
            return _validate_artifact(_read_json(path)), path
    return None, None


def get_default_saved_ids(root: Path) -> list[str]:
    """Read IDs only from a bounded, fully validated scoped result; no DB/key."""
    try:
        artifact, _ = _saved(_paths(root))
        return list(artifact['selected_ids']) if artifact else []
    except Exception:
        return []


def _public(artifact: dict, paths: dict, *, recovered=False) -> dict:
    generation = artifact['generation']
    result = briefing._state('generated', '已读取本次限定用途的本地 AI 简报，请人工复核。',
                             artifact['fact_pack'], envelope={**generation, 'result': artifact['result'],
                                                             'result_metadata': generation,
                                                             'last_attempt_at': generation['generated_at']},
                             hit=True, persisted=True)
    result.update(artifact=artifact, fact_pack=artifact['fact_pack'],
                  local_model_mapping=artifact['local_model_mapping'], authorization_scope=artifact['authorization_scope'],
                  scope_id=SCOPE_ID, artifact_path=str(paths['recovery'] if recovered else paths['artifact']),
                  attempt_path=str(paths['attempt']),
                  request_reserved=True, request_body_bytes=artifact['request_body_bytes'], recovered=recovered)
    return result


def read_current_artifact(state: dict, selected_ids: list[str], *, root: Path, now=None) -> dict:
    """Current display requires exact snapshot, selection, facts and versions."""
    try:
        paths = _paths(root)
        artifact, loaded_path = _saved(paths)
        if artifact is None:
            return _fail('本次所选组合尚未生成本地简报。', status='not_generated',
                         artifact_path=str(paths['artifact']), attempt_path=str(paths['attempt']))
        from .selected_facts import build_selected_fact_pack
        from .selection import local_model_mapping
        ids = _validate_ids(selected_ids)
        pack = build_selected_fact_pack(state, ids)
        if (ids != artifact['selected_ids'] or _versions(pack) != artifact['versions']
                or _canonical(pack) != _canonical(artifact['fact_pack'])
                or local_model_mapping(state, ids) != artifact['local_model_mapping']):
            return _fail('所选组合、快照、事实或口径已变化；该组合尚未生成对应简报。', status='mismatch',
                         artifact_path=str(paths['artifact']))
        instant = _instant(now)
        if instant < briefing._time(artifact['generation']['generated_at']):
            raise ValueError('future generation time')
        if instant >= briefing._time(artifact['generation']['expires_at']):
            result = _fail('本次验收简报已过期；可在历史验收文件中复核，不标记为当前有效结果。', status='stale',
                           artifact_path=str(paths['artifact']))
            result['result_stale'] = True
            return result
        return _public(artifact, paths, recovered=loaded_path == paths['recovery'])
    except Exception:
        return _fail('本地简报或当前依据校验失败；原表格和规则说明仍可使用。')


def read_historical_artifact(*, root: Path, now=None) -> dict:
    """Validate the original saved envelope without claiming current-version reuse.

    The historical allowlist preserves its original hash, prompt/rule versions,
    expiry and review status. This reader neither rebuilds nor writes any file.
    """
    try:
        paths = _paths(root)
        artifact, loaded_path = _saved(paths)
        if artifact is None:
            return _fail('暂无已保存的历史验收简报。', status='not_generated')
        instant = _instant(now)
        if instant < briefing._time(artifact['generation']['generated_at']):
            raise ValueError('future generation time')
        view = _public(artifact, paths, recovered=loaded_path == paths['recovery'])
        expired = instant >= briefing._time(artifact['generation']['expires_at'])
        view.update(status='historical', result_stale=expired, historical=True,
                    message='历史验收原文，非当前版本生成；' + ('原有效期已过。' if expired else '保留原有效期。'))
        return view
    except Exception:
        return _fail('历史验收简报校验失败；请保留原文件，不作为可用简报展示。')


def _atomic_write(path: Path, value: dict) -> None:
    serialized = _canonical(value)
    if len(serialized.encode('utf-8')) > MAX_ARTIFACT_BYTES:
        raise ValueError('output too large')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.m2c-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _probe(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(mode='w+b', dir=directory) as stream:
        stream.write(b'm2c-local-preflight')
        stream.flush()
        os.fsync(stream.fileno())


def _reserve(path: Path, attempt: dict) -> None:
    # Exclusive creation is the cross-process guard. Even a partial record
    # consumes the slot, and failures never remove it or silently retry.
    with path.open('x', encoding='utf-8') as stream:
        stream.write(_canonical(attempt))
        stream.flush()
        os.fsync(stream.fileno())


def _selection_reason(method: str) -> str:
    if method == 'explicit_ids':
        return '使用明确选择的稳定记录 ID，保留选择顺序，不按名称合并。'
    return '技术验收样例按白名单关键字段覆盖数和稳定 ID 确定性排序，优先不同厂商；不是最佳模型或全市场排名。'


def run_once(state: dict, selected_ids: list[str], settings: AnalysisSettings, client, *, root: Path,
             confirmed_scope: bool, retention_approved: bool = False,
             request_body_bytes: int | None = None, selection_method: str = 'explicit_ids', now=None) -> dict:
    """Execute at most one generation for this scope across all invocations."""
    try:
        scoped = scoped_settings(settings, confirmed_scope=confirmed_scope, retention_approved=retention_approved)
        from .selected_facts import build_selected_fact_pack
        from .selection import local_model_mapping
        from .modex_client import ModexClient
        ids = _validate_ids(selected_ids)
        pack = build_selected_fact_pack(state, ids)
        briefing._validate_pack(pack, scoped)
        mapping = local_model_mapping(state, ids)
        _validate_mapping(mapping, ids, pack['source'])
        minimum = len(_canonical({'pack': pack, 'mapping': mapping, 'scope': _scope(pack, ids)}).encode('utf-8'))
        if minimum + briefing.MAX_RESPONSE_BYTES + 8192 > MAX_ARTIFACT_BYTES:
            raise ValueError('local evidence exceeds save limit')
        if briefing._contains_secret(mapping, scoped):
            raise ValueError('sensitive local mapping')
        if selection_method not in ('explicit_ids', 'technical_acceptance_fallback'):
            raise ValueError('invalid selection method')
        if pack['scope'].get('selection_hash') != _selection_hash(ids):
            raise ValueError('selection hash mismatch')
        if isinstance(client, ModexClient):
            if client._settings != scoped or client._used or client._closed:
                raise ValueError('client settings or lifecycle mismatch')
            client._preflight(pack, briefing.build_prompt(pack, scoped), scoped.timeout_seconds)
        if request_body_bytes is None:
            from .modex_client import request_body_size
            request_body_bytes = request_body_size(pack, scoped)
        if type(request_body_bytes) is not int or not 0 < request_body_bytes <= 2 * 1024 * 1024:
            raise ValueError('invalid request body bytes')
        paths = _paths(root)
        current = read_current_artifact(state, ids, root=root, now=now)
        if current['status'] == 'generated':
            if current.get('recovered'):
                _atomic_write(paths['artifact'], current['artifact'])
                current['artifact_path'] = str(paths['artifact'])
            current['recovered'] = True
            return current
        if paths['attempt'].exists() or paths['artifact'].exists() or paths['recovery'].exists():
            return _fail('本轮尝试已占用或已有产物；不会再次请求模型服务。', status='already_attempted',
                         attempt_path=str(paths['attempt']), artifact_path=str(paths['artifact']), request_reserved=True)
        _probe(paths['directory'])
        _probe(paths['reports'])
        instant = _instant(now)
        attempt = {'schema_version': 'm2c-attempt-v1', 'scope_id': SCOPE_ID, 'status': 'reserved',
                   'authorization_scope': _scope(pack, ids), 'reserved_at': instant.isoformat(),
                   'finished_at': None, 'request_body_bytes': request_body_bytes, 'metadata': None}
        _reserve(paths['attempt'], attempt)
    except FileExistsError:
        return _fail('本轮尝试已被另一执行占用；不会再次请求模型服务。', status='already_attempted', request_reserved=True)
    except Exception:
        return _fail()
    generated = briefing.generate_briefing(pack, scoped, client, cache_dir=None, now=now)
    finished = _instant(now)
    attempt['finished_at'] = finished.isoformat()
    attempt['metadata'] = {key: generated.get(key) for key in (*briefing.METADATA_FIELDS, 'error_code')}
    if briefing._contains_secret(attempt, scoped):
        attempt['metadata'] = {'error_code': 'client_error'}
    if generated['status'] != 'generated':
        attempt['status'] = 'failed'
        try:
            _atomic_write(paths['attempt'], attempt)
        except Exception:
            pass  # The durable reserved record still prevents another request.
        generated.update(artifact=None, fact_pack=None, local_model_mapping=[], scope_id=SCOPE_ID,
                         authorization_scope=attempt['authorization_scope'], request_reserved=True,
                         attempt_path=str(paths['attempt']), artifact_path=str(paths['artifact']))
        return generated
    try:
        actual_bytes = getattr(client, 'last_request_body_bytes', None)
        byte_source = 'transport' if type(actual_bytes) is int and actual_bytes > 0 else 'preflight'
        count = actual_bytes if byte_source == 'transport' else request_body_bytes
        artifact = {'schema_version': ARTIFACT_VERSION, 'status': 'generated',
                    'authorization_scope': _scope(pack, ids), 'selected_ids': ids,
                    'local_model_mapping': mapping, 'fact_pack': pack, 'result': generated['result'],
                    'generation': _generation(generated, generated_at=finished.isoformat(),
                                              expires_at=(finished + timedelta(seconds=scoped.cache_ttl_seconds)).isoformat()),
                    'versions': _versions(pack), 'fact_count': len(pack['facts']),
                    'fact_pack_bytes': len(_canonical(pack).encode('utf-8')),
                    'request_body_bytes': count, 'request_body_bytes_source': byte_source,
                    'request_body_bytes_preflight': request_body_bytes,
                    'request_body_bytes_match': count == request_body_bytes,
                    'selection_method': selection_method, 'selection_reason': _selection_reason(selection_method)}
        _validate_artifact(artifact)
        if briefing._contains_secret(artifact, scoped):
            raise ValueError('sensitive output')
        saved = False
        for path in (paths['recovery'], paths['artifact']):
            try:
                _atomic_write(path, artifact)
                saved = True
            except Exception:
                continue
        attempt['status'] = 'generated' if saved else 'save_failed'
        try:
            _atomic_write(paths['attempt'], attempt)
        except Exception:
            pass
        if not saved:
            failure = _fail('本次输出已校验但本地保存失败；本轮请求已耗尽，禁止重发。', request_reserved=True)
            failure.update({key: generated.get(key) for key in briefing.METADATA_FIELDS})
            failure.update(artifact=artifact, persisted=False, error_code='cache_error')
            return failure
        result = _public(artifact, paths, recovered=not paths['artifact'].exists())
        result['cache_hit'] = False
        return result
    except Exception:
        attempt['status'] = 'save_failed'
        try:
            _atomic_write(paths['attempt'], attempt)
        except Exception:
            pass
        failure = _fail('本次响应的保存依据未通过检查；本轮请求已耗尽，不会重发。', request_reserved=True,
                        attempt_path=str(paths['attempt']))
        failure.update({key: generated.get(key) for key in briefing.METADATA_FIELDS})
        failure['error_code'] = 'cache_error'
        return failure


def _markdown(value) -> str:
    text = html.escape(str(value), quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>\-])', r'\\\1', text)


def render_markdown(artifact: dict) -> str:
    """Full validated prose and minimal evidence, escaped for safe Markdown."""
    artifact = _validate_artifact(artifact)
    generation = artifact['generation']
    lines = ['# M2C 首份真实数据简报', '', 'AI 辅助解读，需人工复核。用户人工内容复核待完成。', '',
             f"数据采集时间：{_markdown(artifact['fact_pack']['snapshot']['collected_at'])}", '',
             f"生成时间：{_markdown(generation['generated_at'])}", '',
             f"有效期截止：{_markdown(generation['expires_at'])}。到期仅失去当前有效状态，不物理删除本次审核文件。", '',
             '## 所选记录与范围', '', _markdown(PURPOSE), '', _markdown(DISPLAY_SCOPE), '']
    lines += [_markdown(artifact['selection_reason']), '']
    for row in artifact['local_model_mapping']:
        lines.append(f"- {_markdown(row['alias'])}：{_markdown(row['name'])}；厂商 {_markdown(row['creator'])}；稳定 ID {_markdown(row['id'])}。")
    headings = {'current': '当前数据能说明什么', 'changes': '与上次成功采集相比发生了什么', 'limitations': '使用限制'}
    for section in artifact['result']['sections']:
        lines += ['', '## ' + headings[section['key']], '']
        for claim in section['claims']:
            lines += [_markdown(claim['text']), '', '事实引用：' + '、'.join(_markdown(ref) for ref in claim['fact_ids']), '']
    lines += ['## 运行摘要', '',
              f"请求模型：{_markdown(generation['request_model'])}；上游自报模型：{_markdown(generation['response_model'])}。", '',
              f"HTTP：{generation['http_status']}；耗时：{generation['elapsed_seconds'] if generation['elapsed_seconds'] is not None else '未知'} 秒。", '',
              'usage：' + _markdown(_canonical(generation['usage']) if generation['usage'] is not None else '未知') + '；费用：未知。', '',
              f"事实数 {artifact['fact_count']}；事实包 {artifact['fact_pack_bytes']} 字节；请求体 {artifact['request_body_bytes']} 字节（不是 Token 数或费用）。", '',
              f"请求体预检 {artifact['request_body_bytes_preflight']} 字节；与生成阶段记录{'一致' if artifact['request_body_bytes_match'] else '不一致，需复核编码或客户端变更'}。", '',
              '上游自报模型名不是底层模型身份的独立证明。结构与引用检查不能完全验证自然语言结论。', '',
              '## 最小依据与版本', '', '以下为本次实际使用的程序事实，不是完整源站响应或数据库。', '']
    evidence = json.dumps({'versions': artifact['versions'], 'authorization_scope': artifact['authorization_scope'],
                           'fact_pack': artifact['fact_pack']}, ensure_ascii=False, indent=2, allow_nan=False)
    longest = max((len(match.group()) for match in re.finditer(r'`+', evidence)), default=0)
    fence = '`' * max(3, longest + 1)
    lines += [fence + 'json', evidence, fence, '']
    return '\n'.join(lines)
