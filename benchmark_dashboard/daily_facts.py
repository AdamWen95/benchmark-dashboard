"""Pure all-record daily summaries and bounded AI inputs from one read view.

No files, settings, environment, clocks, databases or clients are accessed here.
Local details retain their identities; network facts contain aggregates and the
existing compact, aliased examples only.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256

from . import fact_pack, insights, metrics, selected_facts
from .changes import EVENT_TYPES, compare_runs
from .comparability import CONFIG_KEYS
from .selection import choose_acceptance_ids, selection_hash, validate_selection


PACK_VERSION = 'm3-daily-v1'
MAX_PACK_BYTES = 128 * 1024
MAX_FACTS = 64
MAX_CHANGE_DETAILS = 24
SAFE_ERROR = '日更事实包无法生成，请继续查看本地全量规则概况。'
OVERVIEW_ERROR = '日更概况暂时无法生成，请保留原表查看。'
_CONTEXT_KEYS = ('evaluation_version', 'evaluation_versions', 'metric_versions',
                 'evaluation_date', 'metric_units', 'units', 'prompt_options', *CONFIG_KEYS)
_RULES = (
    '全量指本次 Artificial Analysis 有效快照的所有记录，不等于全部市场模型。',
    '有限数值覆盖统计包含合法的 0，缺失、布尔、非法类型和非有限值不计入；不等于可靠实测覆盖率。',
    metrics.PERFORMANCE_ZERO_NOTICE + '；评测零分和零价格保持各自语义。',
    'partial/unknown 的编程指标仅展示源站原值与限制，不补分、不乘 100、不评选能力冠军。',
    '同一次采集不等于同一次测试；采集时间区间不是逐项评测时间或全天完整新闻。',
    '本次新收录记录不等于今天发布新模型，本次未返回不等于停服；记录变化不等于能力变化。',
    '公开源站测值不是公司实测；输入和输出价格分开看待，不推断总费用或综合性价比。',
)


def _hash(value) -> str:
    return sha256(fact_pack._canonical(value).encode('utf-8')).hexdigest()


def _records(state: dict) -> list[dict]:
    records = state.get('records')
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError(SAFE_ERROR)
    ids = [record.get('id') for record in records]
    if (any(not isinstance(identity, str) or not identity.strip() or len(identity) > 4096 for identity in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError(SAFE_ERROR)
    return sorted(records, key=lambda record: record['id'])


def _run(run) -> dict | None:
    if not isinstance(run, dict):
        return None
    return {'id': run.get('id') if type(run.get('id')) is int else None,
            'status': run.get('status') if run.get('status') in ('success', 'failed') else 'unknown',
            'started_at': fact_pack._time(run.get('started_at')),
            'finished_at': fact_pack._time(run.get('finished_at'))}


def _creators(records: list[dict]) -> list[dict]:
    counts, names = Counter(), {}
    for record in records:
        creator = record.get('model_creator')
        creator = creator if isinstance(creator, dict) else {}
        identity = creator.get('id') if isinstance(creator.get('id'), str) and creator['id'].strip() else None
        name = creator.get('name') if isinstance(creator.get('name'), str) and creator['name'].strip() else None
        key = ('id', identity) if identity else ('name', name) if name else ('unknown', '')
        counts[key] += 1
        names.setdefault(key, set()).add(name or '源站未提供')
    return [{'creator_id': key[1] if key[0] == 'id' else None,
             'creator_name': min(names[key]), 'count': count}
            for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _full_changes(state: dict, snapshot: dict | None) -> dict:
    comparison = state.get('comparison')
    comparison = comparison if isinstance(comparison, dict) else {'status': 'empty' if snapshot is None else 'unavailable'}
    result = compare_runs(comparison)
    if snapshot is None:
        return {'status': 'empty', 'message': '暂无成功采集时间点，缺少比较依据。',
                'counts': dict.fromkeys(EVENT_TYPES, 0), 'events': [], 'before': None, 'after': None,
                'latest_attempt_failed': (_run(state.get('latest_attempt')) or {}).get('status') == 'failed',
                'same_snapshot': False, 'comparison_scope': 'all_records'}
    summary = fact_pack._changes({**state, 'comparison': comparison}, snapshot, [PACK_VERSION, snapshot['content_hash']])
    # The same shared validation controls both complete local events and the
    # bounded network summary; an invalid binding cannot leak partial deltas.
    events = deepcopy(result['events']) if summary['status'] == 'ready' else []
    return {key: deepcopy(summary[key]) for key in (
        'status', 'counts', 'before', 'after', 'latest_attempt_failed', 'same_snapshot')} | {
        'message': summary['text'], 'events': events, 'comparison_scope': 'all_records'}


def _overview(state: dict) -> dict:
    records = _records(state)
    snapshot = fact_pack._snapshot(state['snapshot']) if state.get('snapshot') else None
    if records and snapshot is None:
        raise ValueError(OVERVIEW_ERROR)
    coverage = metrics.coverage_rows(records, sorted(metrics.DISPLAY_METRICS))
    for row in coverage:
        path = row['path']
        zero_count = sum(metrics.performance_zero_notice(record, path) is not None for record in records)
        row.update(performance_zero_count=zero_count,
                   verification_status=metrics.metric_for(path).verification_status,
                   unit=metrics.metric_for(path).unit,
                   quality_notice=metrics.PERFORMANCE_ZERO_NOTICE if zero_count else None)
    changes = _full_changes(state, snapshot)
    return {'source': fact_pack.SOURCE, 'snapshot': snapshot, 'total_records': len(records),
            'latest_attempt': _run(state.get('latest_attempt')),
            'latest_success': _run(state.get('last_success')),
            'creator_distribution': _creators(records), 'coverage': coverage,
            'changes': changes, 'rules': list(_RULES),
            'versions': {'pack_version': PACK_VERSION, 'metric_mapping_version': metrics.METRIC_MAPPING_VERSION,
                         'rule_version': insights.RULE_VERSION}}


def build_daily_overview(state: dict) -> dict:
    """Return all local counts and complete change events, never a selected view."""
    try:
        return _overview(state)
    except Exception:
        raise ValueError(OVERVIEW_ERROR) from None


def _selected_ids(state: dict, ids: list[str] | None) -> list[str]:
    if ids is not None:
        return validate_selection(state, ids)
    return choose_acceptance_ids(state) if len(state['records']) >= 2 else []


def _semantic_data_hash(state: dict) -> str:
    """Bind all allowed data locally without uploading records or source prose.

    Only collection/run bookkeeping is excluded. Evaluation dates, units,
    versions and configuration are meaningful and remain in this digest.
    """
    records = []
    for record in _records(state):
        context = {key: record[key] for key in _CONTEXT_KEYS if key in record}
        creator = record.get('model_creator')
        records.append({'id': record['id'], 'name': record.get('name'), 'slug': record.get('slug'),
                        'creator': {key: creator.get(key) for key in ('id', 'name')} if isinstance(creator, dict) else None,
                        'context': context,
                        'values': {path: metrics.value_at(record, path)
                                   if metrics.numeric_value(metrics.value_at(record, path)) is not None else None
                                   for path in sorted(metrics.DISPLAY_METRICS)}})
    snap = state['snapshot']
    metadata = snap.get('metadata')
    return _hash({'records': records,
                  'snapshot_context': {key: snap[key] for key in _CONTEXT_KEYS if key in snap},
                  'source_context': {key: metadata[key] for key in _CONTEXT_KEYS if key in metadata}
                  if isinstance(metadata, dict) else {}})


def _change_details(changes: dict) -> list[dict]:
    # All events count, including metadata not intended for network disclosure.
    # Unknown field names and raw record/metadata text never enter these groups.
    groups = Counter()
    for event in changes['events']:
        path = event.get('path')
        path = path if path in metrics.DISPLAY_METRICS else 'record_metadata' if event['type'] == 'metadata' else None
        status = event.get('comparability')
        status = status if status in ('comparable', 'unconfirmed', 'changed') else 'unconfirmed'
        groups[(event['type'], path, status)] += 1
    return [{'type': kind, 'metric_path': path, 'comparability': status, 'count': count}
            for (kind, path, status), count in sorted(groups.items(), key=lambda item: (
                EVENT_TYPES.index(item[0][0]), item[0][1] or '', item[0][2]))]


def _base_pack(state: dict, retained: list[str]) -> dict:
    if retained:
        # All known fields are covered even when entirely absent from the source
        # payload; absent cells must remain explicitly missing.
        return selected_facts.build_selected_fact_pack(
            {**state, 'metric_paths': sorted(metrics.DISPLAY_METRICS)}, retained)
    snapshot = fact_pack._snapshot(state['snapshot'])
    return {'schema_version': fact_pack.SCHEMA_VERSION, 'source': fact_pack.SOURCE, 'snapshot': snapshot,
            'metric_mapping_version': metrics.METRIC_MAPPING_VERSION, 'rule_version': insights.RULE_VERSION,
            'scope': {'input_mode': 'real', 'selected_count': 0, 'aliases': [],
                      'metadata': {}, 'official_sources': {}, 'notes': {}},
            'facts': [{'id': 'F1', 'kind': 'changes', 'text': '缺少比较依据。', 'status': 'unavailable',
                       'latest_attempt_failed': False},
                      {'id': 'F2', 'kind': 'limitations', 'text': '有效记录不足两条，不生成模型对比规则或上传逐模型例证。'}]}


def _candidate(state: dict, overview: dict, requested: list[str], retained: list[str],
               details: list[dict], detail_limit: int, data_hash: str, changes_hash: str) -> dict:
    pack = _base_pack(state, retained)
    scope = pack['scope']
    total = overview['total_records']
    selected_hash = selection_hash(state, retained) if retained else _hash([])
    requested_hash = selection_hash(state, requested) if requested else _hash([])
    kept_details = details[:detail_limit]
    omitted_events = sum(row['count'] for row in details[detail_limit:])
    scope.update(pack_version=PACK_VERSION, kind='daily', selection_hash=selected_hash,
                 selected_count=len(retained), total_records=total, omitted_records=total - len(retained),
                 all_records={'total_records': total, 'semantic_data_hash': data_hash,
                              'scope_definition': 'all valid records in this Artificial Analysis snapshot'},
                 selected_examples={'requested_count': len(requested), 'retained_count': len(retained),
                                    'omitted_from_selection': len(requested) - len(retained),
                                    'omitted_records': total - len(retained),
                                    'requested_selection_hash': requested_hash,
                                    'aliases': [f'R{i + 1}' for i in range(len(retained))],
                                    'selection_rule': 'explicit_order' if state.get('_daily_explicit_selection')
                                    else 'coverage_desc_stable_id_asc_prefer_different_creator',
                                    'purpose': 'technical examples only; not recommendations or best-model ranking'},
                 reduction={'applied': len(retained) < len(requested) or len(kept_details) < len(details),
                            'rule': 'reduce grouped change details first, then remove example selection tail; keep all totals and limitations',
                            'omitted_change_groups': len(details) - len(kept_details),
                            'omitted_change_events': omitted_events})
    scope['selection_rule'] = scope['selected_examples']['selection_rule']
    scope['difference_scope'] = 'selected_examples only; each later alias compared with R1, not historical changes'
    scope['fact_id_scheme'] = 'F1..Fn; full local identities derived from daily fact_hash and short ID'
    scope['coverage_metric_paths'] = sorted(metrics.DISPLAY_METRICS)
    scope['numeric_coverage_definition'] = _RULES[1]
    facts = pack['facts']
    for fact in facts:
        if fact['kind'] == 'coverage':
            fact['coverage_scope'] = 'all_records_and_selected_examples'
            for row in fact.get('metrics', []):
                full = next(item for item in overview['coverage'] if item['path'] == row['metric_path'])
                row.update(full_missing=full['missing_count'], full_performance_zero=full['performance_zero_count'])
        elif fact['kind'] in ('metric_example', 'rule'):
            fact['example_scope'] = 'selected_examples'
        elif fact['kind'] == 'changes':
            identity = fact['id']
            fact.clear()
            fact.update(id=identity, kind='changes', text=overview['changes']['message'],
                        **{key: deepcopy(overview['changes'][key]) for key in (
                            'status', 'counts', 'same_snapshot', 'latest_attempt_failed', 'before', 'after')},
                        latest_attempt=overview['latest_attempt'], comparison_scope='all_records',
                        detail_scope='all_records_aggregated_by_type_and_metric', details=kept_details,
                        detail_group_total=len(details), detail_group_included=len(kept_details),
                        omitted_event_count=omitted_events, semantic_changes_hash=changes_hash)
            fact['text'] += (f'全量七类计数完整保留；变化分组展示 {len(kept_details)}/{len(details)}，'
                             f'省略分组涉及 {omitted_events} 项事件，完整明细仅保留在本地概况。')
        elif fact['kind'] == 'limitations':
            fact['text'] = ''.join(_RULES) + (
                f'全量统计覆盖 {total} 条有效记录，逐模型例证保留 {len(retained)}/{len(requested)} 条请求选择，'
                f'未逐项发送 {total - len(retained)} 条记录；这是例证，不是排名。'
                '例证别名、真实名称、稳定 ID 及厂商标签映射仅在本地保留。')
            if not retained:
                fact['text'] += '有效记录不足两条，不生成模型对比规则或上传逐模型例证。'
    # Only distribution counts are transmitted. The complete labelled creator
    # distribution belongs to the local overview, not to the prompt surface.
    creator_counts = sorted((row['count'] for row in overview['creator_distribution']), reverse=True)
    facts.append({'id': f'F{len(facts) + 1}', 'kind': 'coverage', 'coverage_scope': 'all_records',
                  'text': (f'本次有效响应共 {total} 条记录、{len(creator_counts)} 个厂商标识组；'
                           '厂商分布在本地完整显示，输入仅含前 10 组数量及剩余总数，不代表市场份额。'),
                  'total_records': total, 'creator_group_count': len(creator_counts),
                  'creator_group_counts': creator_counts[:10],
                  'omitted_creator_groups': max(0, len(creator_counts) - 10),
                  'omitted_creator_records': sum(creator_counts[10:]),
                  'latest_attempt': overview['latest_attempt'], 'latest_success': overview['latest_success'],
                  'metrics': [{'metric_path': row['path'], 'finite': row['valid_count'],
                               'missing': row['missing_count'], 'performance_zero': row['performance_zero_count'],
                               'total': total, 'verification_status': row['verification_status']}
                              for row in overview['coverage']]})
    pack['fact_hash'] = fact_pack.compute_fact_hash(pack)
    return pack


def build_daily_fact_pack(state: dict, ids: list[str] | None = None) -> dict:
    """Keep full aggregate scope and at most four compact examples within bounds.

    Deterministic reduction retains all metric coverage and global event counts.
    If even the minimal representation is too large, no partial pack is returned.
    """
    try:
        overview = _overview(state)
        if not overview['snapshot'] or not overview['total_records']:
            raise ValueError(SAFE_ERROR)
        requested = _selected_ids(state, ids)
        state = {**state, '_daily_explicit_selection': ids is not None}
        details = _change_details(overview['changes'])
        data_hash = _semantic_data_hash(state)
        changes_hash = _hash(overview['changes']['events'])
        counts = range(len(requested), 1, -1) if requested else [0]
        limits = list(dict.fromkeys([min(len(details), MAX_CHANGE_DETAILS), min(len(details), 12), 0]))
        for count in counts:
            for detail_limit in limits:
                try:
                    pack = _candidate(state, overview, requested, requested[:count], details, detail_limit,
                                      data_hash, changes_hash)
                except ValueError:
                    break
                if len(pack['facts']) <= MAX_FACTS and len(fact_pack._canonical(pack).encode('utf-8')) <= MAX_PACK_BYTES:
                    return pack
        raise ValueError(SAFE_ERROR)
    except Exception:
        raise ValueError(SAFE_ERROR) from None


def daily_example_ids(state: dict, ids: list[str] | None = None) -> list[str]:
    """Return the exact retained local identities after the same pure size check."""
    pack = build_daily_fact_pack(state, ids)
    return _selected_ids(state, ids)[:pack['scope']['selected_examples']['retained_count']]


def semantic_daily_payload(pack: dict) -> dict:
    """Cache identity excludes only explicit collection/run bookkeeping.

    The caller must still validate and retain each original complete fact pack.
    Opaque all-record and event digests preserve nonselected changes without
    transmitting the full dataset. Evaluation date/configuration remain semantic.
    """
    try:
        if (pack['scope']['pack_version'] != PACK_VERSION or pack['scope']['kind'] != 'daily'
                or fact_pack.compute_fact_hash(pack) != pack['fact_hash']):
            raise ValueError(SAFE_ERROR)
        result = deepcopy(pack)
        result.pop('fact_hash')
        result.pop('snapshot')
        for fact in result['facts']:
            for key in ('latest_attempt', 'latest_success'):
                if key in fact:
                    run = fact[key]
                    fact[key] = {'status': run.get('status')} if isinstance(run, dict) else None
            if fact['kind'] == 'changes':
                fact.pop('before', None)
                fact.pop('after', None)
                if (fact['status'] == 'ready' and not any(fact['counts'].values())
                        and not fact['details'] and not fact['omitted_event_count']):
                    # Different source hashes can result solely from collection
                    # bookkeeping. The full original pack still discloses exact
                    # snapshot reuse; cache semantics use the complete data and
                    # event digests instead of this storage distinction.
                    fact.pop('same_snapshot', None)
                    fact['text'] = ('最近一次采集失败，当前展示旧快照，数据可能过期。'
                                    if fact['latest_attempt_failed'] else '')
                    fact['text'] += '最近两次成功采集未发现纳入比较的记录变化；全量七类计数均为 0。'
        return result
    except Exception:
        raise ValueError(SAFE_ERROR) from None
