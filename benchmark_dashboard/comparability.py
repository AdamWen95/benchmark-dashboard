"""Conservative, local-only comparability rules shared by changes and narration."""
import json
import math

from .metrics import metric_for, performance_zero_notice, PERFORMANCE_ZERO_NOTICE

CONFIG_KEYS = ('evaluation_config', 'test_config', 'inference_config', 'inference_parameters',
               'reasoning_config', 'reasoning_effort', 'configuration', 'config')


def _provided(value) -> bool:
    return value is not None and value != '' and value != {} and value != []


def _valid_version(value) -> bool:
    """A supplied container or boolean is not an understood version label."""
    if isinstance(value, str):
        return bool(value.strip())
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _valid_config(value) -> bool:
    """Accept an explicit label or a finite JSON configuration object only."""
    if isinstance(value, str):
        return bool(value.strip())
    if not isinstance(value, dict) or not value or not all(isinstance(key, str) for key in value):
        return False
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _mapped(container: dict, map_name: str, path: str):
    values = container.get(map_name)
    if not isinstance(values, dict):
        return {'invalid_metadata_type': True} if _provided(values) else None
    return values.get(path, values.get(path.split('.')[-1]))


def metric_context(path: str, record: dict, snapshot: dict) -> dict:
    """Inspect explicit metadata only. A publication date is never a test version."""
    meta = snapshot.get('metadata') or {}
    if not isinstance(meta, dict):
        meta = {}
    values = {'source': snapshot.get('source')}
    for scope, obj in [('record', record), ('snapshot', snapshot), ('source_metadata', meta)]:
        for map_name in ('metric_units', 'units'):
            values[f'{scope}.{map_name}'] = _mapped(obj, map_name, path)
        for key in CONFIG_KEYS:
            values[f'{scope}.{key}'] = obj.get(key)
        if path.startswith('evaluations.'):
            values[f'{scope}.evaluation_version'] = obj.get('evaluation_version')
            for map_name in ('metric_versions', 'evaluation_versions'):
                values[f'{scope}.{map_name}'] = _mapped(obj, map_name, path)
    if path.startswith('median_'):
        values['snapshot.prompt_options'] = snapshot.get('prompt_options', meta.get('prompt_options'))
        values['record.prompt_options'] = record.get('prompt_options')
    return values


def assess_metric(path: str, records: list[dict], snapshots: list[dict]) -> dict:
    """Classify as comparable / unconfirmed / changed without inferring model ability."""
    zero_count = sum(performance_zero_notice(record, path) is not None for record in records)
    quality = {'quality_status': 'unconfirmed_zero' if zero_count else 'not_flagged',
               'quality_reason': PERFORMANCE_ZERO_NOTICE if zero_count else None,
               'performance_zero_count': zero_count}
    if not records or len(records) != len(snapshots):
        return {'status': 'unconfirmed', 'reason': '缺少比较依据。', 'unit_conflict': False, **quality}
    contexts = [metric_context(path, record, snapshot) for record, snapshot in zip(records, snapshots)]
    metric = metric_for(path)
    expected_units = {metric.unit, getattr(metric, 'raw_unit', metric.unit)}
    unit_conflict = any(_provided(value) and (not isinstance(value, str) or value not in expected_units)
                        for context in contexts for key, value in context.items()
                        if key.endswith(('.metric_units', '.units')))
    changed = []
    incomplete = []
    for key in contexts[0]:
        values = [context[key] for context in contexts]
        present = [value for value in values if _provided(value)]
        if present and any(value != present[0] for value in present[1:]):
            changed.append(key)
        elif present and len(present) != len(values):
            incomplete.append(key)
    # In historical comparisons an existing ID's relabeling is meaningful;
    # different selected models are naturally allowed to have different names.
    identities = [(snapshot.get('source'), record.get('id')) for record, snapshot in zip(records, snapshots)]
    if len(records) > 1 and len(set(identities)) == 1:
        # Historical loss/addition of an explicit context is itself a context
        # change. Across different models, partial metadata remains merely
        # unconfirmed and must not imply a change over time.
        changed.extend(incomplete)
        for key in ('name', 'slug', 'model_creator'):
            if any(record.get(key) != records[0].get(key) for record in records[1:]):
                changed.append('模型名称/厂商/配置标签')
    if changed:
        return {'status': 'changed', 'unit_conflict': unit_conflict, **quality,
                'reason': '口径变化/待确认：来源、单位、版本、配置或同 ID 元数据存在差异；仅并列源站记录值，不计算可比增减。'
                          + (PERFORMANCE_ZERO_NOTICE + '。' if zero_count else '')}
    reasons = []
    if zero_count:
        reasons.append(PERFORMANCE_ZERO_NOTICE)
    if not all(_provided(context.get('source')) for context in contexts):
        reasons.append('来源信息不足')
    if not metric.confirmed or getattr(metric, 'direction', 'unknown') == 'unknown':
        reasons.append('单位或数值方向未确认')
    if unit_conflict:
        reasons.append('源站单位声明与本地已核验映射不一致')
    if incomplete:
        reasons.append('部分记录的单位、版本或运行配置源站未提供')
    version_suffixes = ('.evaluation_version', '.metric_versions', '.evaluation_versions')
    config_suffixes = tuple('.' + name for name in CONFIG_KEYS)
    if any(_provided(value) and not _valid_version(value)
           for context in contexts for key, value in context.items() if key.endswith(version_suffixes)):
        reasons.append('评测版本元数据格式未确认，不能作为已验证的比较依据')
    if any(_provided(value) and not _valid_config(value)
           for context in contexts for key, value in context.items() if key.endswith(config_suffixes)):
        reasons.append('运行配置元数据格式未确认，不能作为已验证的比较依据')
    if path.startswith('evaluations.'):
        for context in contexts:
            version = any(_valid_version(value) for key, value in context.items()
                          if key.endswith(version_suffixes))
            config = any(_valid_config(value) for key, value in context.items()
                         if key.endswith(config_suffixes))
            if not version or not config:
                reasons.append('评测版本或运行配置源站未提供')
                break
    if path.startswith('median_'):
        if not all(_provided(context.get('snapshot.prompt_options')) for context in contexts):
            reasons.append('速度/延迟测试参数源站未提供')
        if any(_provided(value) and (not isinstance(value, dict) or not _valid_config(value))
               for context in contexts for key, value in context.items() if key.endswith('.prompt_options')):
            reasons.append('速度/延迟测试参数元数据格式未确认')
    if reasons:
        return {'status': 'unconfirmed', 'unit_conflict': unit_conflict, **quality,
                'reason': '口径未充分确认，仅为源站记录值变化或所选记录值并列展示；' + '；'.join(dict.fromkeys(reasons)) + '。'}
    return {'status': 'comparable', 'unit_conflict': False, **quality,
            'reason': '已知单位与显式上下文一致，仅对该项源站报告值进行比较，不推断全面能力或实际总成本。'}
