"""Deterministic Chinese templates over one selected local snapshot.

No source fetching, configuration loading, model service, storage, or wall clock
is used here. The caller passes the exact records already used by its table.
"""
from hashlib import sha256
import json

from .comparability import CONFIG_KEYS, assess_metric
from .metrics import (PERFORMANCE_PATHS, PERFORMANCE_ZERO_NOTICE, format_raw_value,
                      format_value, metric_for, normalized_value, numeric_value,
                      performance_zero_notice, value_at)


RULE_VERSION = 'm2c-finish-rules-v1'
SELECTION_ERROR = '请选择 2–4 条稳定 ID 不重复的模型记录。'
SNAPSHOT_ERROR = '当前快照依据不完整，暂时无法生成规则说明。'
GENERATION_ERROR = '规则说明暂时无法生成，请保留原表查看。'
ABILITY_PATHS = (
    'evaluations.artificial_analysis_intelligence_index',
    'evaluations.artificial_analysis_coding_index',
    'evaluations.artificial_analysis_math_index',
)
PRICE_SPEED_PATHS = (
    'pricing.price_1m_input_tokens',
    'pricing.price_1m_output_tokens',
    'median_output_tokens_per_second',
    'median_time_to_first_token_seconds',
)
# Keep the original exported groups stable for the existing fact allowlist.
# Reported-but-unconfirmed programming fields must remain visible in rules.
PROGRAMMING_PATHS = (
    'evaluations.artificial_analysis_coding_index',
    'evaluations.artificial_analysis_intelligence_index',
    'evaluations.livecodebench',
    'evaluations.terminalbench_hard',
    'evaluations.terminalbench_v2_1',
)
REPORTED_ABILITY_PATHS = tuple(dict.fromkeys((*ABILITY_PATHS, *PROGRAMMING_PATHS)))
REPORTED_PRICE_SPEED_PATHS = (*PRICE_SPEED_PATHS, 'median_time_to_first_answer_token')


def _provided(value) -> bool:
    return value is not None and value != '' and value != {} and value != []


def _has_context(record: dict, snapshot: dict, keys: tuple[str, ...]) -> bool:
    metadata = snapshot.get('metadata')
    scopes = (record, snapshot, metadata if isinstance(metadata, dict) else {})
    return any(_provided(scope.get(key)) for scope in scopes for key in keys)


def _evidence(records: list[dict], snapshot: dict, paths: list[str], assessments: dict) -> list[dict]:
    evidence = []
    for path in paths:
        metric = metric_for(path)
        assessment = assessments[path]
        unit_conflict = assessment.get('unit_conflict', False)
        raw_only = (unit_conflict or metric.verification_status != 'confirmed'
                    or assessment['status'] != 'comparable')
        numbers = [(numeric_value(value_at(record, path)) if raw_only
                    else normalized_value(value_at(record, path), path)) for record in records]
        coverage = {'valid': sum(number is not None for number in numbers), 'total': len(records)}
        for index, (record, number) in enumerate(zip(records, numbers), start=1):
            identity = [RULE_VERSION, snapshot['id'], snapshot['content_hash'],
                        snapshot['source'], record['id'], path]
            digest = sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            evidence.append({
                'id': 'evidence-' + digest.hexdigest(),
                'snapshot_id': snapshot['id'], 'content_hash': snapshot['content_hash'],
                'source': snapshot['source'], 'model_id': record['id'],
                'record_label': f'记录{index}', 'name': record.get('name') or '源站未提供',
                'slug': record.get('slug') or '源站未提供', 'metric_path': path,
                'metric_label': metric.label, 'source_name': metric.source_name,
                # Invalid and non-finite input is missing, never a fabricated 0.
                # Valid values retain the original number; normalized values are
                # used solely for deterministic comparison and display.
                'raw_value': value_at(record, path) if number is not None else None,
                'display_value': ((format_raw_value(value_at(record, path), path) if raw_only
                                   else format_value(record, path)) if number is not None else '暂无'),
                'unit': '源站原值（单位声明待核对）' if unit_conflict else metric.unit,
                'raw_unit': '单位声明待核对' if unit_conflict else metric.raw_unit,
                'assessment': dict(assessment), 'comparability_reason': assessment['reason'],
                'verification_status': metric.verification_status,
                'quality_notice': performance_zero_notice(record, path),
                'coverage': dict(coverage),
                'reference': {'snapshot_id': snapshot['id'], 'source': snapshot['source'],
                              'model_id': record['id'], 'metric_path': path},
            })
    return evidence


def _sentence(path: str, records: list[dict], snapshot: dict, rows: list[dict]) -> tuple[str, str]:
    metric = metric_for(path)
    covered = rows[0]['coverage']['valid']
    prefix = f"{metric.label}（{rows[0]['unit']}，有限数值覆盖 {covered}/{len(records)}）："
    assessment = rows[0]['assessment']
    status = assessment['status']
    if not covered:
        return prefix + '暂无有效数值。', status
    values = '；'.join(f"{row['record_label']} {row['display_value']}" for row in rows)
    if assessment.get('unit_conflict'):
        notice = PERFORMANCE_ZERO_NOTICE + '。' if assessment.get('performance_zero_count') else ''
        return prefix + '口径变化/待确认，仅展示源站原值：' + values + '。' + notice, status
    if assessment.get('performance_zero_count'):
        return prefix + '源站原值：' + values + '。' + PERFORMANCE_ZERO_NOTICE + '。', status
    if path in PERFORMANCE_PATHS and status != 'comparable':
        return prefix + '源站原值：' + values + '。口径未充分确认，暂不用于性能优劣判断。', status
    present = [(normalized_value(value_at(record, path), path), row)
               for record, row in zip(records, rows) if row['display_value'] != '暂无']
    if status == 'changed' or metric.direction == 'unknown':
        note = '口径变化/待确认，仅并列' if status == 'changed' else '数值方向未确认，仅并列'
        return prefix + note + '源站原值：' + values + '。', status
    if metric.verification_status != 'confirmed':
        return prefix + '仅并列源站原值：' + values + '；逐字段核验未完成，不作能力排序。', 'unconfirmed'
    if covered == 1:
        row = present[0][1]
        return prefix + f"仅{row['record_label']}报告 {row['display_value']}，不足以排序。", status
    reverse = metric.direction == 'higher'
    ordered = sorted(present, key=lambda item: item[0], reverse=reverse)
    groups = []
    group_number = None
    for number, row in ordered:
        if not groups or number != group_number:
            groups.append([row])
            group_number = number
        else:
            groups[-1].append(row)
    descriptions = []
    for group in groups:
        label = '、'.join(row['record_label'] for row in group)
        tie = '（并列）' if len(group) > 1 else ''
        descriptions.append(f"{label} {group[0]['display_value']}{tie}")
    direction = '从高到低' if reverse else '从低到高'
    return prefix + direction + '为 ' + ' → '.join(descriptions) + '。', status


def _generate(records: list[dict], snapshot: dict, metric_paths: list[str]) -> dict:
    # One repeated field must not produce duplicate conclusions or evidence.
    paths = list(dict.fromkeys(metric_paths))
    assessments = {path: assess_metric(path, records, [snapshot] * len(records)) for path in paths}
    evidence = _evidence(records, snapshot, paths, assessments)
    by_path = {path: [row for row in evidence if row['metric_path'] == path] for path in paths}
    paragraphs = []
    changed = False
    unconfirmed = False
    for category, eligible in [('能力指标', REPORTED_ABILITY_PATHS),
                               ('价格与速度', REPORTED_PRICE_SPEED_PATHS)]:
        selected = [path for path in paths if path in eligible]
        if not selected:
            continue
        sentences = []
        ids = []
        category_unconfirmed = False
        for path in selected:
            sentence, status = _sentence(path, records, snapshot, by_path[path])
            sentences.append(sentence)
            ids.extend(row['id'] for row in by_path[path])
            changed = changed or status == 'changed'
            category_unconfirmed = category_unconfirmed or status == 'unconfirmed'
        unconfirmed = unconfirmed or category_unconfirmed
        text = '在本次所选记录、该项已报告值中，' + ''.join(sentences)
        if category_unconfirmed:
            text += '口径未充分确认，以上均为源站记录值展示，不是能力排名。'
        paragraphs.append({'category': category, 'text': text, 'evidence_ids': ids})
    limits = []
    if any(row['coverage']['valid'] < len(records) for row in evidence):
        limits.append('部分指标缺项，缺分不等于能力差')
    missing = []
    for label, keys in [('评测版本', ('evaluation_version', 'evaluation_versions', 'metric_versions')),
                        ('评测日期', ('evaluation_date',)), ('运行配置', CONFIG_KEYS)]:
        if any(not _has_context(record, snapshot, keys) for record in records):
            missing.append(label)
    if missing:
        limits.append('、'.join(missing) + '：部分或全部记录源站未提供')
    if any(assessment.get('unit_conflict') for assessment in assessments.values()):
        limits.append('源站单位声明与本地映射存在冲突，不作数值排序或单位转换')
    if any(assessment.get('performance_zero_count') for assessment in assessments.values()):
        limits.append(PERFORMANCE_ZERO_NOTICE)
        limits.append('有限数值覆盖包含原始 0，不代表可靠实测覆盖')
    if changed:
        limits.append('存在单位、版本或配置差异，不能据此判定能力优劣')
    elif unconfirmed:
        limits.append('可比性未充分确认，原值展示不代表能力排名')
    if any(metric_for(path).direction == 'unknown' for path in paths):
        limits.append('部分指标单位或数值方向未确认，仅保留原值，不选择最佳')
    limits.extend(['名称和配置标签保持原样，差异不合并',
                   '同一次采集不等于同一次测试',
                   '公开源站测值，非公司实测',
                   '输入与输出价格各自比较，不能据此推导实际总费用或综合性价比'])
    paragraphs.append({'category': '证据不足', 'text': '；'.join(limits) + '。',
                       'evidence_ids': [row['id'] for row in evidence]})
    return {'rule_version': RULE_VERSION, 'snapshot_id': snapshot['id'],
            'content_hash': snapshot['content_hash'], 'paragraphs': paragraphs, 'evidence': evidence}


def _validate_inputs(records: list[dict], snapshot: dict) -> None:
    if not isinstance(records, list) or not 2 <= len(records) <= 4:
        raise ValueError(SELECTION_ERROR)
    identities = [record.get('id') if isinstance(record, dict) else None for record in records]
    if (any(not isinstance(identity, str) or not identity.strip() for identity in identities)
            or len(set(identities)) != len(identities)):
        raise ValueError(SELECTION_ERROR)
    if (not isinstance(snapshot, dict)
            or any(not _provided(snapshot.get(key)) for key in ('id', 'source', 'content_hash'))):
        raise ValueError(SNAPSHOT_ERROR)


def reported_programming_rows(records: list[dict], snapshot: dict) -> list[dict]:
    """Local non-AI view of selected raw programming values and their limits.

    A cell remains present when its value is missing or its mapping is partial.
    No unit multiplier, ability order, storage read, or external call is used.
    """
    _validate_inputs(records, snapshot)
    try:
        assessments = {path: assess_metric(path, records, [snapshot] * len(records))
                       for path in PROGRAMMING_PATHS}
        rows = []
        for index, record in enumerate(records, start=1):
            for path in PROGRAMMING_PATHS:
                metric, assessment = metric_for(path), assessments[path]
                raw = value_at(record, path)
                raw = raw if numeric_value(raw) is not None else None
                verification = ('逐字段核验未完成；' if metric.verification_status != 'confirmed' else '')
                rows.append({
                    'alias': f'R{index}', 'model_id': record['id'],
                    'name': record.get('name') or '源站未提供', 'path': path, 'label': metric.label,
                    'raw_value': raw, 'display_value': format_raw_value(raw, path),
                    'unit': ('源站原值（单位声明待核对）' if assessment['unit_conflict']
                             else metric.unit),
                    'verification_status': metric.verification_status,
                    'assessment': dict(assessment),
                    'reason': verification + assessment['reason']
                              + '同一次采集不等于同一次测试；仅展示原值，不推断编程能力或开发效率。',
                    'source': snapshot['source'], 'collected_at': snapshot.get('collected_at'),
                    'snapshot_id': snapshot['id'], 'content_hash': snapshot['content_hash'],
                })
        return rows
    except Exception:
        raise ValueError(GENERATION_ERROR) from None


def generate_insights(records: list[dict], snapshot: dict, metric_paths: list[str]) -> dict:
    """Return at most three paragraphs with snapshot-bound per-cell evidence.

    Record labels correspond to the caller's selection/table order. Every
    evidence identity includes the rule version, snapshot hash, source, model ID
    and metric path, so an updated snapshot cannot reuse an old explanation.
    Errors are fixed public messages, never source values or exception details.
    """
    _validate_inputs(records, snapshot)
    try:
        return _generate(records, snapshot, metric_paths)
    except Exception:
        raise ValueError(GENERATION_ERROR) from None
