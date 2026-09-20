"""Bounded, deterministic M2B facts from one supplied local dashboard state.

No database, files, settings, environment variables, network, or model client is
read here. Source prose stays local; only selected numeric fields, stable IDs,
program-generated summaries and curated metric definitions enter the pack.
"""
from datetime import datetime
from hashlib import sha256
import json
import re

from . import insights, metrics
from .changes import EVENT_LABELS, EVENT_TYPES, compare_runs
from .comparability import CONFIG_KEYS, assess_metric, metric_context
from .insights import generate_insights


SCHEMA_VERSION = 'm2b-facts-v1'
MAX_FACTS = 64
MAX_PACK_BYTES = 128 * 1024
MAX_EXAMPLES = 2
SOURCE = 'artificial_analysis'
EMPTY_ERROR = '暂无有效快照，无法准备本地事实包。'
SAFE_ERROR = '本地事实包暂时无法生成，请保留原表与规则说明。'
EXAMPLE_PATHS = (
    *insights.ABILITY_PATHS, *insights.PRICE_SPEED_PATHS,
    'evaluations.livecodebench', 'evaluations.terminalbench_hard',
    'evaluations.terminalbench_v2_1', 'median_time_to_first_answer_token',
)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def compute_fact_hash(pack: dict) -> str:
    """SHA-256 of canonical UTF-8 JSON, excluding only the fact_hash field."""
    try:
        return sha256(_canonical({key: value for key, value in pack.items()
                                  if key != 'fact_hash'}).encode('utf-8')).hexdigest()
    except Exception:
        raise ValueError(SAFE_ERROR) from None


def _id(kind: str, identity: list) -> str:
    return 'fact-' + kind + '-' + sha256(_canonical(identity).encode('utf-8')).hexdigest()


def _present(value) -> bool:
    return value is not None and value != '' and value != {} and value != []


def _time(value) -> str | None:
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _snapshot(snapshot: dict) -> dict:
    if (not isinstance(snapshot, dict) or snapshot.get('source') != SOURCE
            or type(snapshot.get('id')) is not int or snapshot['id'] <= 0
            or not isinstance(snapshot.get('content_hash'), str)
            or re.fullmatch(r'[0-9a-f]{64}', snapshot['content_hash']) is None
            or _time(snapshot.get('collected_at')) is None):
        raise ValueError(SAFE_ERROR)
    # The source collection string is an already validated timestamp. Preserve
    # it so the snapshot identity matches the existing dashboard exactly.
    return {key: snapshot[key] for key in ('id', 'content_hash', 'collected_at')}


def _mapping(path: str) -> dict:
    metric = metrics.metric_for(path)
    verified = metric.verification_status == 'confirmed'
    return {'label': metric.label, 'source_name': metric.source_name,
            'definition': metric.description, 'raw_unit': metric.raw_unit,
            'display_unit': metric.unit, 'conversion_formula': metric.conversion_formula,
            'direction': metric.direction if verified else 'unknown',
            'verification_status': metric.verification_status,
            'official_sources': list(metric.official_sources), 'verified_at': metric.verified_at,
            'comparability_limit': metric.comparability,
            'metric_mapping_version': metrics.METRIC_MAPPING_VERSION, 'rule_version': insights.RULE_VERSION}


def _assessment(path: str, selected: list[dict], snapshot: dict) -> dict:
    assessment = dict(assess_metric(path, selected, [snapshot] * len(selected)))
    if metrics.metric_for(path).verification_status != 'confirmed':
        if assessment['status'] != 'changed':
            assessment['status'] = 'unconfirmed'
        assessment['reason'] += ' M2B 字段编码或定义尚未完整核验，仅保留原值，不作方向性判断。'
    return assessment


def _metadata(record: dict, snapshot: dict, path: str) -> dict:
    context = metric_context(path, record, snapshot)
    metadata = snapshot.get('metadata')
    scopes = (record, snapshot, metadata if isinstance(metadata, dict) else {})
    version = any(_present(value) for key, value in context.items()
                  if key.endswith(('.evaluation_version', '.metric_versions', '.evaluation_versions')))
    # A record-level version can be present even for a pricing field. Presence
    # is disclosed independently from whether it affects that metric's rules.
    version = version or any(_present(scope.get('evaluation_version')) for scope in scopes)
    config = any(_present(value) for key, value in context.items()
                 if key.endswith(tuple('.' + name for name in CONFIG_KEYS)))
    date = any(_present(scope.get('evaluation_date')) for scope in scopes)
    available = {'evaluation_version': version, 'evaluation_date': date, 'configuration': config}
    missing = [label for key, label in [('evaluation_version', '评测版本'),
               ('evaluation_date', '评测日期'), ('configuration', '运行配置')] if not available[key]]
    note = ('、'.join(missing) + '源站未提供。') if missing else ''
    note += '已提供的版本或配置仅在本地判定可比性，具体内容不纳入事实包；速度测试参数不能替代评测配置。'
    return {'evaluation_version': None, 'evaluation_date': None, 'configuration': None,
            'provided_locally': available, 'note': note}


def _point_summary(point: dict | None) -> dict | None:
    if not isinstance(point, dict):
        return None
    snapshot = _snapshot(point.get('snapshot'))
    run = point.get('run')
    if not isinstance(run, dict) or type(run.get('id')) is not int or run.get('status') != 'success':
        raise ValueError(SAFE_ERROR)
    return {'run_id': run['id'], 'finished_at': _time(run.get('finished_at')),
            'snapshot_id': snapshot['id'], 'content_hash': snapshot['content_hash']}


def _changes(state: dict, snapshot: dict, identity: list) -> dict:
    comparison = state.get('comparison')
    if not isinstance(comparison, dict):
        comparison = {'status': 'unavailable'}
    result = compare_runs(comparison)
    status = result.get('status')
    if status not in ('baseline', 'ready', 'unavailable', 'empty'):
        status = 'unavailable'
    counts = {key: value if type(value) is int and value >= 0 else 0
              for key in EVENT_TYPES for value in [result.get('counts', {}).get(key, 0)]}
    before = after = None
    same_snapshot = False
    try:
        before = _point_summary(result.get('before'))
        after = _point_summary(result.get('after'))
        if after and (after['snapshot_id'] != snapshot['id'] or after['content_hash'] != snapshot['content_hash']):
            status = 'unavailable'
        if status == 'ready':
            if not before or not after or before['run_id'] == after['run_id']:
                status = 'unavailable'
            else:
                same_snapshot = (before['snapshot_id'], before['content_hash']) == (
                    after['snapshot_id'], after['content_hash'])
    except (TypeError, ValueError, KeyError):
        status, before, after = 'unavailable', None, None
    latest = state.get('latest_attempt')
    latest = latest if isinstance(latest, dict) else {}
    failed = latest.get('status') == 'failed'
    latest_summary = {'id': latest.get('id') if type(latest.get('id')) is int else None,
                      'status': latest.get('status') if latest.get('status') in ('success', 'failed') else 'unknown',
                      'started_at': _time(latest.get('started_at')), 'finished_at': _time(latest.get('finished_at'))}
    if status != 'ready':
        counts = dict.fromkeys(EVENT_TYPES, 0)
    messages = {'baseline': '已建立初始基线，暂无历史可比较。',
                'unavailable': '缺少比较依据，无法可靠比较最近两次成功采集。',
                'empty': '暂无成功采集时间点，缺少比较依据。'}
    if status != 'ready':
        text = messages[status]
    elif any(counts.values()):
        text = '最近两次成功采集的记录比较：' + '；'.join(
            f'{EVENT_LABELS[key]} {counts[key]} 项' for key in EVENT_TYPES if counts[key]) + '。'
        text += '记录变化不等于模型能力变化；新增不等于今天发布，本次未返回不等于停服。'
    elif same_snapshot:
        text = '与上次成功采集相比无数据变化；两次成功运行复用同一内容快照。'
    else:
        text = '最近两次成功采集未发现纳入比较的记录变化；内容快照不同，不能据此认定全部数据相同。'
    if failed:
        text = '最近一次采集失败，当前展示旧快照，数据可能过期。' + text
    return {'id': _id('changes', identity), 'kind': 'changes', 'text': text,
            'status': status, 'counts': counts, 'same_snapshot': same_snapshot,
            'latest_attempt_failed': failed, 'latest_attempt': latest_summary,
            'before': before, 'after': after}


def _build(state: dict) -> dict:
    snapshot = _snapshot(state['snapshot'])
    records = state['records']
    if not isinstance(records, list) or not records or any(not isinstance(record, dict) for record in records):
        raise ValueError(SAFE_ERROR)
    ids = [record.get('id') for record in records]
    # Reject unreasonable identities; never truncate or merge an actual ID.
    if (any(not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 4096 for model_id in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError(SAFE_ERROR)
    records = sorted(records, key=lambda record: record['id'])
    selected = records[:MAX_EXAMPLES]
    incoming_paths = state.get('metric_paths') or []
    if not isinstance(incoming_paths, list) or any(not isinstance(path, str) for path in incoming_paths):
        raise ValueError(SAFE_ERROR)
    paths = sorted(set(incoming_paths) & set(metrics.DISPLAY_METRICS))
    example_paths = sorted(set(paths) & set(EXAMPLE_PATHS))
    identity = [SOURCE, snapshot['id'], snapshot['content_hash'],
                metrics.METRIC_MAPPING_VERSION, insights.RULE_VERSION]
    facts = []
    coverages = {}
    for row in metrics.coverage_rows(records, paths):
        path = row['path']
        fact_id = _id('coverage', [*identity, path])
        fact = {'id': fact_id, 'kind': 'coverage', 'metric_path': path,
                'text': f"{metrics.metric_for(path).label}：全量快照有效记录 {row['valid_count']}/{row['total']}；缺失 {row['missing_count']} 条。",
                'valid_count': row['valid_count'], 'total_records': row['total'],
                'missing_count': row['missing_count'], 'coverage_ratio': row['coverage'], 'mapping': _mapping(path)}
        facts.append(fact)
        coverages[path] = fact
    assessments = {path: _assessment(path, selected, state['snapshot']) for path in example_paths}
    example_lookup = {}
    for selected_index, record in enumerate(selected, start=1):
        for path in example_paths:
            metric = metrics.metric_for(path)
            assessment = assessments[path]
            allowed = (metric.verification_status == 'confirmed' and assessment['status'] == 'comparable'
                       and not assessment.get('unit_conflict') and metric.direction != 'unknown')
            raw = metrics.value_at(record, path)
            raw = raw if metrics.numeric_value(raw) is not None else None
            uncertain = metric.verification_status != 'confirmed' or assessment.get('unit_conflict')
            display = metrics.format_raw_value(raw, path) if uncertain else metrics.format_value(record, path)
            unit = '源站原值（单位声明待核对）' if assessment.get('unit_conflict') else metric.unit
            fact_id = _id('example', [*identity, record['id'], path])
            example_lookup[(record['id'], path)] = fact_id
            facts.append({'id': fact_id, 'kind': 'metric_example', 'source': SOURCE,
                          'model_id': record['id'], 'record_label': f'记录{selected_index}', 'metric_path': path,
                          'text': f'例证记录{selected_index}的{metric.label}：{display}（{unit}）；仅为披露范围内的源站记录值。',
                          'raw_value': raw, 'display_value': display,
                          'raw_unit': '单位声明待核对' if assessment.get('unit_conflict') else metric.raw_unit,
                          'unit': unit, 'direction': metric.direction if allowed else 'unknown',
                          'directional_observation_allowed': allowed, 'assessment': assessment,
                          'coverage_valid': coverages[path]['valid_count'], 'coverage_total': len(records),
                          'coverage_fact_id': coverages[path]['id'],
                          'metadata': _metadata(record, state['snapshot'], path),
                          'metric_mapping_version': metrics.METRIC_MAPPING_VERSION,
                          'rule_version': insights.RULE_VERSION})
    # Retain original local metadata during rule evaluation; never make missing
    # context look comparable by deleting it. Only approved paths enter rules.
    eligible = [path for path in example_paths if metrics.metric_for(path).verification_status == 'confirmed'
                and assessments[path]['status'] == 'comparable' and not assessments[path].get('unit_conflict')
                and metrics.metric_for(path).direction != 'unknown']
    if len(selected) == MAX_EXAMPLES and eligible:
        rule_result = generate_insights(selected, state['snapshot'], eligible)
        rule_evidence = {row['id']: (row['model_id'], row['metric_path']) for row in rule_result['evidence']}
        for paragraph in rule_result['paragraphs']:
            references = list(dict.fromkeys(example_lookup[rule_evidence[ref]] for ref in paragraph['evidence_ids']))
            if references:
                facts.append({'id': _id('rule', [*identity, paragraph['category']]), 'kind': 'rule',
                              'category': paragraph['category'], 'text': paragraph['text'],
                              'evidence_fact_ids': references, 'rule_version': insights.RULE_VERSION})
    facts.append(_changes(state, snapshot, identity))
    limits = ('例证按稳定 ID 字典序选取最多两条，仅用于可追溯说明，不是全市场排名；'
              '缺分不等于能力差，未知或冲突单位不作转换和方向性判断；'
              '评测版本、日期和配置缺失时不能推断能力变化；源站记录不是公司实测；'
              '输入和输出价格分别报告，没有用量假设不能推导总费用或综合性价比；'
              '本事实包不代表使用许可或真实模型服务联调已获确认。')
    if len(selected) < MAX_EXAMPLES:
        limits += '有效例证不足两条，不生成模型对比规则。'
    elif not eligible:
        limits += '当前例证没有核验及可比性均满足条件的指标，不生成方向性规则。'
    facts.append({'id': _id('limitations', identity), 'kind': 'limitations', 'text': limits,
                  'evidence_fact_ids': [fact['id'] for fact in facts if fact['kind'] == 'coverage']})
    pack = {'schema_version': SCHEMA_VERSION, 'source': SOURCE, 'snapshot': snapshot,
            'metric_mapping_version': metrics.METRIC_MAPPING_VERSION, 'rule_version': insights.RULE_VERSION,
            'scope': {'selection_rule': 'stable_id_lexicographic_first_2', 'total_records': len(records),
                      'example_count': len(selected), 'omitted_records': len(records) - len(selected),
                      'example_ids': [{'source': SOURCE, 'model_id': record['id']} for record in selected],
                      'metric_paths': paths, 'example_metric_paths': example_paths,
                      'excluded_metric_count': len(set(incoming_paths) - set(paths))},
            'facts': facts}
    pack['fact_hash'] = compute_fact_hash(pack)
    if len(facts) > MAX_FACTS or len(_canonical(pack).encode('utf-8')) > MAX_PACK_BYTES:
        raise ValueError(SAFE_ERROR)
    return pack


def build_fact_pack(state: dict) -> dict:
    """Prepare bounded facts; fixed public failures never echo source content."""
    if isinstance(state, dict) and not state.get('records') and not state.get('snapshot'):
        raise ValueError(EMPTY_ERROR)
    try:
        return _build(state)
    except Exception:
        raise ValueError(SAFE_ERROR) from None
