"""Compact selected-record M2C facts; real identity mapping stays local.

All values, context and change calculations reuse the existing M2B/M2A rules.
This pure builder consumes one dashboard state and performs no I/O.
"""
import re
from hashlib import sha256

from . import fact_pack, insights, metrics
from .selection import selection_hash, validate_selection
from .comparability import CONFIG_KEYS


MAX_FACTS = 64
MAX_PACK_BYTES = 128 * 1024
PACK_VERSION = 'm2c-selected-v2'
SAFE_ERROR = '所选模型事实包无法生成，请核对选择或继续查看本地规则。'


def _compact(pack: dict) -> dict:
    """One canonical fact schema; no second network-only representation.

    Values and program differences remain complete. Repeated prose moves to a
    shared note registry; short fact references bind to the full local pack hash.
    """
    facts = pack['facts']
    # The shared limitations fact and source-context registry already retain
    # these restrictions. Keep the actual observed-value rule paragraphs.
    facts[:] = [fact for fact in facts if not (fact['kind'] == 'rule' and fact.get('category') == '证据不足')]
    identities = {fact['id']: f'F{index + 1}' for index, fact in enumerate(facts)}
    notes, note_index = {}, {}

    def note(text):
        if text not in note_index:
            key = f'N{len(notes) + 1}'
            note_index[text] = key
            notes[key] = text
        return note_index[text]

    for fact in facts:
        fact['id'] = identities[fact['id']]
        if 'coverage_fact_id' in fact:
            fact['coverage_fact_id'] = identities[fact['coverage_fact_id']]
        if 'evidence_fact_ids' in fact:
            fact['evidence_fact_ids'] = [identities[ref] for ref in fact['evidence_fact_ids']]
        if fact['kind'] == 'metric_example':
            mapping = fact['mapping']
            mapping['comparability_ref'] = note(mapping.pop('comparability_limit'))
            # Remove only exact duplicates. A unit conflict or a restriction
            # on comparison can differ from the verified dictionary meaning;
            # retaining that distinction is more important than saving bytes.
            if mapping.get('source_name') == fact['metric_path'].split('.')[-1]:
                mapping.pop('source_name')
            for key, fact_key in (('raw_unit', 'raw_unit'), ('display_unit', 'unit'), ('direction', 'direction')):
                if mapping.get(key) == fact[fact_key]:
                    mapping.pop(key, None)
            assessment = fact['assessment']
            assessment['reason_ref'] = note(assessment.pop('reason'))
            fact['text'] = mapping['label'] + '：所选原始记录及缺项见 values，差异见 differences。'
    pack['scope']['notes'] = notes
    pack['scope']['fact_id_scheme'] = 'F1..Fn; full IDs derived locally from fact_hash + short ID'
    pack['scope']['numeric_coverage_definition'] = (
        '有限数值覆盖率包含源站记录的 0，仅统计非布尔、非空、有限数值；不等于可靠实测覆盖率。')
    pack['scope']['value_semantics'] = (
        'values 保留每条所选记录的 raw_value、display_value、metadata_ref；null 是缺失，不补分。'
        'unit/raw_unit 为展示/源值单位；assessment.reason_ref 引用 notes，quality_reason 保留原提示。'
        'differences 仅含程序已准许的数值差异；空数组不代表数值相同。')
    return pack


def fact_id_map(pack: dict) -> dict[str, str]:
    """Pure local, unambiguous short-to-full references bound to the full hash.

    The map is evidence for local review; it is not repeated in the network pack.
    Historical v1 long identities are returned unchanged, never recalculated.
    """
    try:
        if fact_pack.compute_fact_hash(pack) != pack['fact_hash']:
            raise ValueError('hash mismatch')
        facts = pack['facts']
        if not isinstance(facts, list) or not facts or len({fact['id'] for fact in facts}) != len(facts):
            raise ValueError('invalid fact identities')
        if pack['scope']['pack_version'] == 'm2c-selected-v1':
            return {fact['id']: fact['id'] for fact in facts}
        if pack['scope']['pack_version'] != PACK_VERSION:
            raise ValueError('unknown input version')
        if [fact['id'] for fact in facts] != [f'F{index + 1}' for index in range(len(facts))]:
            raise ValueError('ambiguous short identities')
        return {fact['id']: 'fact-' + fact['kind'] + '-' + sha256(fact_pack._canonical(
            [PACK_VERSION, pack['fact_hash'], fact['id']]).encode('utf-8')).hexdigest() for fact in facts}
    except Exception:
        raise ValueError(SAFE_ERROR) from None


def _selected_changes(state: dict, chosen: list[str], paths: list[str], snapshot: dict,
                      identity: list) -> dict:
    """Reuse existing history rules only for selected IDs and included metrics."""
    comparison = state.get('comparison')
    comparison = dict(comparison) if isinstance(comparison, dict) else {'status': 'unavailable'}
    local_keys = {'id', 'name', 'slug', 'model_creator', 'evaluation_date', 'evaluation_version',
                  'metric_units', 'units', 'metric_versions', 'evaluation_versions', 'prompt_options', *CONFIG_KEYS}
    for key in ('before', 'after'):
        point = comparison.get(key)
        if not isinstance(point, dict):
            continue
        selected = []
        for record in point.get('records', []):
            if record.get('id') not in chosen:
                continue
            projected = {field: value for field, value in record.items() if field in local_keys}
            for path in paths:
                parts = path.split('.')
                if len(parts) == 1:
                    projected[path] = metrics.value_at(record, path)
                else:
                    projected.setdefault(parts[0], {})[parts[1]] = metrics.value_at(record, path)
            selected.append(projected)
        point_snapshot = point.get('snapshot')
        if isinstance(point_snapshot, dict):
            point_snapshot = {**point_snapshot, 'coverage': {path: None for path in paths}}
        comparison[key] = {**point, 'snapshot': point_snapshot, 'records': selected}
    result = fact_pack._changes({**state, 'comparison': comparison}, snapshot, identity)
    result['comparison_scope'] = 'current_selected_stable_ids_only'
    result['text'] += '历史范围仅为当前所选稳定 ID 及本事实包指标，未选记录的变化不计入。'
    return result


def _difference(path: str, rows: list[dict]) -> list[dict]:
    """Compare each later alias with R1, without generating every possible pair."""
    if rows[0]['raw_value'] is None:
        return []
    differences = []
    left = metrics.normalized_value(rows[0]['raw_value'], path)
    for row in rows[1:]:
        if row['raw_value'] is None:
            continue
        right = metrics.normalized_value(row['raw_value'], path)
        delta = metrics.numeric_delta(rows[0]['raw_value'], row['raw_value'], path)
        differences.append({'from': rows[0]['alias'], 'to': row['alias'],
                            'absolute': delta['absolute'], 'relative_percent': delta['relative_percent'],
                            'delta_unit': delta['delta_unit'],
                            'relation': 'equal' if right == left else 'higher' if right > left else 'lower'})
    return differences


def _build(state: dict, ids: list[str]) -> dict:
    chosen = validate_selection(state, ids)
    snapshot = fact_pack._snapshot(state['snapshot'])
    chosen_hash = selection_hash(state, chosen)
    indexed = {record['id']: record for record in state['records']}
    records = [indexed[model_id] for model_id in chosen]
    incoming = state.get('metric_paths') or []
    if not isinstance(incoming, list) or any(not isinstance(path, str) for path in incoming):
        raise ValueError(SAFE_ERROR)
    full_paths = sorted(set(incoming) & set(metrics.DISPLAY_METRICS))
    paths = sorted(set(full_paths) & set(fact_pack.EXAMPLE_PATHS))
    aliases = [f'R{index}' for index in range(1, len(records) + 1)]
    identity = [PACK_VERSION, fact_pack.SOURCE, snapshot['id'], snapshot['content_hash'], chosen_hash,
                metrics.METRIC_MAPPING_VERSION, insights.RULE_VERSION]
    full_coverage = {row['path']: row for row in metrics.coverage_rows(state['records'], full_paths)}
    selected_coverage = {row['path']: row for row in metrics.coverage_rows(records, full_paths)}
    coverage_id = fact_pack._id('coverage', identity)
    facts = [{'id': coverage_id, 'kind': 'coverage',
              'text': f'有限数值覆盖率分开统计：本地有效快照共 {len(state["records"])} 条，当前仅选择 {len(records)} 条；统计包含 0，不代表可靠实测覆盖。未选记录仅计入聚合分母。',
              'full_total': len(state['records']), 'selected_total': len(records),
              'metrics': [{'metric_path': path, 'label': metrics.metric_for(path).label,
                           'full_valid': full_coverage[path]['valid_count'],
                           'selected_valid': selected_coverage[path]['valid_count']}
                          for path in full_paths]}]
    metadata_registry, metadata_index = {}, {}
    source_registry, source_index = {}, {}
    metric_ids, assessments = {}, {}
    for path in paths:
        metric = metrics.metric_for(path)
        assessment = fact_pack._assessment(path, records, state['snapshot'])
        assessments[path] = assessment
        allowed = (metric.verification_status == 'confirmed' and assessment['status'] == 'comparable'
                   and not assessment.get('unit_conflict') and metric.direction != 'unknown')
        raw_only = metric.verification_status != 'confirmed' or assessment.get('unit_conflict')
        unit = '源站原值（单位声明待核对）' if assessment.get('unit_conflict') else metric.unit
        values = []
        for alias, record in zip(aliases, records):
            raw = metrics.value_at(record, path)
            raw = raw if metrics.numeric_value(raw) is not None else None
            metadata = fact_pack._metadata(record, state['snapshot'], path)
            key = fact_pack._canonical(metadata)
            if key not in metadata_index:
                reference = f'M{len(metadata_registry) + 1}'
                metadata_index[key] = reference
                metadata_registry[reference] = metadata
            values.append({'alias': alias, 'raw_value': raw,
                           'display_value': metrics.format_raw_value(raw, path) if raw_only else metrics.format_value(record, path),
                           'metadata_ref': metadata_index[key]})
        mapping = fact_pack._mapping(path)
        for key in ('metric_mapping_version', 'rule_version'):
            mapping.pop(key)
        source_refs = []
        for source in mapping.pop('official_sources'):
            if source not in source_index:
                reference = f'S{len(source_registry) + 1}'
                source_index[source] = reference
                source_registry[reference] = source
            source_refs.append(source_index[source])
        mapping['official_source_refs'] = source_refs
        differences = _difference(path, values) if allowed else []
        coverage = {'full': {'valid': full_coverage[path]['valid_count'], 'total': len(state['records'])},
                    'selected': {'valid': selected_coverage[path]['valid_count'], 'total': len(records)}}
        text = metric.label + '：' + '；'.join(f"{row['alias']}={row['display_value']}" for row in values)
        text += f'（{unit}，所选有效 {coverage["selected"]["valid"]}/{len(records)}）。'
        if differences:
            text += '以 R1 为数值差异基准：' + '；'.join(
                f"{row['to']}−{row['from']}={row['absolute']} {row['delta_unit']}" for row in differences) + '。'
            if any(row['relative_percent'] is None for row in differences):
                text += '基准为 0 时不计算相对变化。'
        if not allowed:
            text += '仅保留源站原值和缺项；核验或可比性不足，不作方向性判断。'
        metric_id = fact_pack._id('selected-metric', [*identity, path])
        metric_ids[path] = metric_id
        facts.append({'id': metric_id, 'kind': 'metric_example', 'metric_path': path, 'text': text,
                      'mapping': mapping, 'values': values, 'unit': unit,
                      'raw_unit': '单位声明待核对' if assessment.get('unit_conflict') else metric.raw_unit,
                      'coverage': coverage, 'coverage_fact_id': coverage_id, 'assessment': assessment,
                      'direction': metric.direction if allowed else 'unknown',
                      'directional_observation_allowed': allowed,
                      'difference_baseline': 'R1', 'differences': differences})
    eligible = [path for path in paths if metrics.metric_for(path).verification_status == 'confirmed'
                and assessments[path]['status'] == 'comparable' and not assessments[path].get('unit_conflict')
                and metrics.metric_for(path).direction != 'unknown']
    if eligible:
        narration = insights.generate_insights(records, state['snapshot'], eligible)
        evidence_paths = {row['id']: row['metric_path'] for row in narration['evidence']}
        for paragraph in narration['paragraphs']:
            references = list(dict.fromkeys(metric_ids[evidence_paths[key]] for key in paragraph['evidence_ids']))
            if references:
                facts.append({'id': fact_pack._id('selected-rule', [*identity, paragraph['category']]),
                              'kind': 'rule', 'category': paragraph['category'],
                              'text': re.sub(r'记录([1-4])', r'R\1', paragraph['text']),
                              'evidence_fact_ids': references})
    facts.append(_selected_changes(state, chosen, paths, snapshot, identity))
    limits = ('仅解释本次所选记录，不代表全市场或最佳模型排名。R1–R4 的真实名称及稳定 ID 只在本地映射中保存。'
              '缺分不等于能力差；partial/unknown 或单位、配置冲突项只保留原值，不猜百分比、版本或成绩。'
              '评测日期、版本或运行配置未提供的内容保留为空；源站公开价格、速度和延迟不是公司实测。'
              '输入、输出价格分别看待，不能据此推算实际总费用、Modex 扣费或综合性价比。'
              '速度或延迟为 0 时测量含义待确认，不判断性能优劣或真实相同；评测零分和零价格仍保留各自语义。'
              '数值差异仅比较同快照的所选值，不是同一次测试或历史能力变化；既有单次授权不产生新增请求额度。')
    facts.append({'id': fact_pack._id('limitations', identity), 'kind': 'limitations', 'text': limits,
                  'evidence_fact_ids': [coverage_id, *metric_ids.values()]})
    pack = {'schema_version': fact_pack.SCHEMA_VERSION, 'source': fact_pack.SOURCE, 'snapshot': snapshot,
            'metric_mapping_version': metrics.METRIC_MAPPING_VERSION, 'rule_version': insights.RULE_VERSION,
            'scope': {'pack_version': PACK_VERSION, 'input_mode': 'real', 'selection_rule': 'explicit_selection_order',
                      'selected_count': len(records), 'selection_hash': chosen_hash, 'aliases': aliases,
                      'total_records': len(state['records']), 'omitted_records': len(state['records']) - len(records),
                      'metric_paths': paths, 'coverage_metric_paths': full_paths,
                      'metadata': metadata_registry, 'official_sources': source_registry,
                      'difference_scope': 'Each later selected alias compared with R1 only; same snapshot.'},
            'facts': facts}
    pack = _compact(pack)
    pack['fact_hash'] = fact_pack.compute_fact_hash(pack)
    if len(facts) > MAX_FACTS or len(fact_pack._canonical(pack).encode('utf-8')) > MAX_PACK_BYTES:
        raise ValueError(SAFE_ERROR)
    return pack


def build_selected_fact_pack(state: dict, ids: list[str]) -> dict:
    """Freeze selected values, aliases, scope and existing versions without I/O."""
    try:
        return _build(state, ids)
    except Exception:
        raise ValueError(SAFE_ERROR) from None
