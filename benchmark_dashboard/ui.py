"""Read-only local dashboard. This module deliberately never loads API settings."""
from pathlib import Path
from html import escape
import json
import sqlite3

import pandas as pd
import streamlit as st

from benchmark_dashboard.metrics import (coverage_rows, format_value, format_raw_value, metric_for,
    normalized_value, value_at, performance_zero_notice, PERFORMANCE_ZERO_NOTICE)
from benchmark_dashboard.comparability import assess_metric
from benchmark_dashboard.changes import compare_runs
from benchmark_dashboard.insights import generate_insights, reported_programming_rows
from benchmark_dashboard.store import read_dashboard


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'dashboard.sqlite3'
SOURCE_URL = 'https://artificialanalysis.ai'
API_REFERENCE_URL = f'{SOURCE_URL}/api-reference'
API_ENDPOINT = f'{SOURCE_URL}/api/v2/data/llms/models'
DEFAULT_METRICS = [
    'evaluations.artificial_analysis_intelligence_index',
    'evaluations.artificial_analysis_coding_index',
    'evaluations.artificial_analysis_math_index',
    'pricing.price_1m_input_tokens',
    'pricing.price_1m_output_tokens',
    'median_output_tokens_per_second',
    'median_time_to_first_token_seconds',
]


def creator_name(record: dict) -> str:
    return (record.get('model_creator') or {}).get('name') or '源站未提供'


def get_current_selection(state: dict) -> list[str]:
    """Read the shared local session choice, never a key or a generation client."""
    from .selection_ui import current_selection
    return current_selection(state, root=ROOT)


def metric_label(path: str) -> str:
    metric = metric_for(path)
    return f'{metric.label}（{metric.unit}）'


def _static_table(rows: list[dict], label: str, height: int = 400) -> None:
    """Small read-only tables with no export action; source values are HTML escaped."""
    if not rows:
        st.caption('暂无记录。')
        return
    headers = list(rows[0])
    head = ''.join(f'<th scope="col">{escape(str(key))}</th>' for key in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{escape(str(row.get(key, "暂无")))}</td>' for key in headers)
                   + '</tr>' for row in rows)
    st.html(f'''<style>
    .bd-m2a-scroll {{overflow:auto;border:1px solid #DDE3EC;border-radius:8px;}}
    .bd-m2a {{border-collapse:collapse;width:100%;font-size:14px;line-height:1.5;}}
    .bd-m2a th,.bd-m2a td {{padding:9px 12px;border-bottom:1px solid #E7EBF1;
        text-align:left;vertical-align:top;min-width:110px;overflow-wrap:anywhere;}}
    .bd-m2a th {{position:sticky;top:0;background:#F3F6FA;}}
    </style><div class="bd-m2a-scroll" style="max-height:{height}px" role="region"
    aria-label="{escape(label)}" tabindex="0"><table class="bd-m2a"><thead><tr>{head}</tr>
    </thead><tbody>{body}</tbody></table></div>''')


def _metric_guide(state: dict, *, filtered: list[dict] | None = None, key: str) -> None:
    snapshot = state.get('snapshot') or {}
    paths = state.get('metric_paths') or []
    if not paths:
        return
    with st.expander('指标中文说明与覆盖率', expanded=False):
        st.caption(f"统计范围：全量快照，共 {len(state['records'])} 条模型记录；"
                   f"快照 #{snapshot.get('id', '暂无')}，采集时间 {snapshot.get('collected_at', '暂无')}。")
        if filtered is not None:
            st.caption(f'当前筛选范围：{len(filtered)} 条；全量与筛选分母分别计算。0 是有效数值，缺分不代表能力差。')
        else:
            st.caption('本表不受总览筛选影响；按同一快照的模型记录计数，0 是有效数值。')
        st.caption('覆盖率定义：有限数值字段数 / 模型记录数；含 0，不代表可靠实测覆盖率或可用于性能判断的比例。')
        full = coverage_rows(state['records'], paths)
        subset = {row['path']: row for row in coverage_rows(filtered, paths)} if filtered is not None else {}
        rows = []
        for item in full:
            row = {'中文指标': item['label'], '字段键': item['path'],
                   '全量有效 / 总数': f"{item['valid_count']} / {item['total']}",
                   '全量覆盖率': f"{item['coverage']:.1%}"}
            if filtered is not None:
                small = subset[item['path']]
                row.update({'筛选有效 / 总数': f"{small['valid_count']} / {small['total']}",
                            '筛选覆盖率': f"{small['coverage']:.1%}"})
            rows.append(row)
        _static_table(rows, '指标覆盖率')
        selected = st.selectbox('查看指标中文说明', paths, format_func=metric_label, key=f'guide_{key}')
        metric = metric_for(selected)
        directions = {'higher': '较高数值方向（仅限本指标已确认口径）',
                      'lower': '较低数值方向（仅限本指标已确认口径）', 'unknown': '方向未确认，不据此评优'}
        _static_table([
            {'说明项': '字段键', '内容': selected},
            {'说明项': '中文名称', '内容': metric.label},
            {'说明项': '源站原名', '内容': metric.source_name},
            {'说明项': '简短含义', '内容': metric.description},
            {'说明项': '原始单位', '内容': metric.raw_unit},
            {'说明项': '展示单位', '内容': metric.unit},
            {'说明项': '数值方向', '内容': directions.get(metric.direction, directions['unknown'])},
            {'说明项': '显示精度', '内容': f'{metric.precision} 位有效数字；保留数据库原始值'},
            {'说明项': '可比性说明', '内容': metric.comparability},
        ], '指标中文说明', 380)
        st.caption('源站未提供的评测版本、评测日期或逐模型运行配置均不推断；速度测试参数不等同评测配置。')


def _rule_insights(state: dict, selected: list[dict], paths: list[str]) -> None:
    st.subheader('对比解读（规则生成）')
    st.caption('规则生成，非 AI 分析。仅解释本次所选记录，依据与上方表格来自同一有效快照。')
    try:
        _reported_programming(state, selected)
        result = generate_insights(selected, state['snapshot'], paths)
    except Exception:
        st.warning('规则解读暂不可用；上方原始对比表仍可查看。')
        return
    for paragraph in result['paragraphs']:
        st.write(paragraph['category'])
        # Plain text: model names cannot introduce Markdown instructions/links.
        st.text(paragraph['text'])
    with st.expander('查看依据：快照、模型 ID 与指标键'):
        st.caption(f"规则版本：{result['rule_version']}；快照 #{result['snapshot_id']}；"
                   f"内容哈希：{result['content_hash']}")
        evidence_rows = [{
            '记录': row['record_label'], '源站精确名称': row['name'], '稳定 ID': row['model_id'],
            '字段键': row['metric_path'], '展示值': row['display_value'], '展示单位': row['unit'],
            '源站原值': row['raw_value'] if row['raw_value'] is not None else '暂无',
            '所选覆盖': f"{row['coverage']['valid']} / {row['coverage']['total']}",
            '快照编号': row['snapshot_id'], '内容哈希': row['content_hash'],
            '来源': row['source'], '配置标签': row['slug'], '口径与限制': row['comparability_reason'],
            '依据编号': row['id'],
        } for row in result['evidence']]
        _static_table(evidence_rows, '规则说明依据', 450)
        st.caption('证据仅包含本次所选记录；完整名称、配置及原始值可在下方对应完整记录中核对。')


def _reported_programming(state: dict, selected: list[dict]) -> None:
    st.subheader('已报告编程相关指标（原值）')
    st.caption('程序规则，非新增 AI 输出。包括综合智能参考值；原值并列不表示单位、方向或跨版本可比性已确认，同一次采集不等于同一次测试。')
    rows = reported_programming_rows(selected, state['snapshot'])
    aliases = {record['id']: f'R{index}' for index, record in enumerate(selected, 1)}
    by_path = {row['path']: row for row in rows}
    cells = {(row['path'], row['model_id']): row for row in rows}
    _static_table([{
        '指标': row['label'],
        **{f"{aliases[record['id']]} · {record['name']}": cells[(path, record['id'])]['display_value']
           for record in selected},
        '原值单位': row['unit'], '核验状态': row['verification_status'],
    } for path, row in by_path.items()], '已报告编程指标', 470)
    snapshot = state['snapshot']
    st.caption(f"来源：{snapshot['source']}；采集时间：{snapshot.get('collected_at') or '源站未提供'}；快照 #{snapshot['id']}。"
               '对应模型名称与稳定 ID 见上方同一选择的对比表；此处不按分数重排。')
    st.caption('缺失显示暂无，不补分、不乘 100、不推断开发效率、编程能力冠军或综合性价比。')
    with st.expander('查看编程指标来源与限制'):
        _static_table([{'指标': row['label'], '字段键': path, '核验状态': row['verification_status'],
                        '口径与限制': row['reason'], '来源': row['source'], '采集时间': row['collected_at']}
                       for path, row in by_path.items()], '编程指标依据与限制', 450)


def _changes_page(state: dict) -> None:
    st.header('变化记录')
    st.caption('比较最近两次成功采集运行；不跳过相同快照，不将采集日期当作发布日期或评测日期。')
    latest = state.get('latest_attempt') or {}
    if latest.get('status') == 'failed':
        st.error(f"最近尝试失败（{latest.get('started_at') or '时间未提供'}）：{safe_failure_message(latest)}")
        st.caption('以下仅为历史成功采集的比较，不能代表本次同步正常；现有数据可能过期。')
    comparison = state.get('comparison') or {'status': 'unavailable', 'message': '缺少比较依据',
                                            'before': None, 'after': None}
    try:
        result = compare_runs(comparison)
    except Exception:
        st.warning('变化比较暂不可用，缺少比较依据；现有有效数据保持不变。')
        return
    references = []
    for label, endpoint in [('基准成功采集', result.get('before')), ('最近成功采集', result.get('after'))]:
        if endpoint:
            run, snapshot = endpoint['run'], endpoint['snapshot']
            references.append({'比较位置': label, '来源': snapshot.get('source', '源站未提供'),
                               '运行编号': run['id'], '成功时间': run.get('finished_at', '源站未提供'),
                               '快照编号': snapshot['id'], '内容哈希': snapshot['content_hash']})
    if references:
        _static_table(references, '历史比较依据', 230)
    st.info(('历史成功采集比较：' if latest.get('status') == 'failed' else '') + result['message'])
    if result['status'] != 'ready':
        return
    labels = {'added': '新增记录', 'removed': '本次未返回', 'metadata': '元数据更新', 'value': '已有数值变化',
              'filled': '数据补齐', 'missing': '数据转缺失', 'context': '口径变化/待确认'}
    st.caption('规则生成，非 AI 分析。新增不等于今日发布；本次未返回不等于停服；数据补齐不等于从 0 提升。')
    st.text('；'.join(f'{label} {result["counts"].get(kind, 0)} 项' for kind, label in labels.items()))
    events = result['events']
    if not events:
        return
    kinds = st.multiselect('变更类型', list(labels), format_func=labels.get, key='change_types')
    search = st.text_input('搜索变化记录', key='change_search', placeholder='模型精确名称或稳定 ID')
    rows = []
    def show(value):
        if value is None:
            return '暂无'
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    for event in events:
        if kinds and event['type'] not in kinds:
            continue
        if search.strip().casefold() not in f"{event.get('name', '')} {event['model_id']}".casefold():
            continue
        rows.append({'类型': labels[event['type']], '模型': event.get('name', '暂无'),
                     '来源': event['source'], '稳定 ID': event['model_id'], '字段键': event.get('path') or '—',
                     '基准原值': show(event.get('before')), '本次原值': show(event.get('after')),
                     '展示单位差值': event.get('absolute') if event.get('absolute') is not None else '不计算',
                     '差值单位': event.get('delta_unit') or '—',
                     '相对变化（%）': event.get('relative_percent') if event.get('relative_percent') is not None else '不计算',
                     '口径与限制': event.get('reason') or '—'})
    st.caption(f'当前显示 {len(rows)} / {len(events)} 项变化。原值保留；基准为 0 或口径不足时不计算相对百分比。')
    _static_table(rows, '变化明细', 560)


def build_overview_html(records: list[dict], metric_paths: list[str], assessments: dict | None = None) -> str:
    """Render exact text safely; sorting is handled only by numeric Python controls.

    Streamlit's native numeric grid displays null cells as ``None`` even when
    Styler requests a replacement. Plain table cells preserve the required 暂无.
    """
    assessments = assessments or {}
    def conflict(path):
        return assessments.get(path, {}).get('unit_conflict', False)
    headers = ['源站精确名称', '厂商',
               *[(f'{metric_for(path).label}（源站原值 · 单位声明待核对）' if conflict(path)
                  else metric_label(path)) for path in metric_paths],
               '配置标签（slug）', '稳定 ID']
    header_html = ''.join(f'<th scope="col">{escape(header)}</th>' for header in headers)
    rows = []
    for record in records:
        values = [record['name'], creator_name(record),
                  *[((format_raw_value(value_at(record, path), path) if conflict(path)
                      else format_value(record, path)) + ('（测量含义待确认）' if performance_zero_notice(record, path) else ''))
                    for path in metric_paths],
                  record.get('slug') or '源站未提供', record['id']]
        cells = []
        for index, value in enumerate(values):
            cell_class = 'metric' if 2 <= index < 2 + len(metric_paths) else 'identity'
            cells.append(f'<td class="{cell_class}">{escape(str(value))}</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    return '''<style>
.bd-overview-scroll {overflow:auto; max-height:640px; border:1px solid #DDE3EC;
    border-radius:8px; width:100%;}
.bd-overview {border-collapse:separate; border-spacing:0; min-width:100%;
    font-size:14px; line-height:1.45; color:#17263B;}
.bd-overview th {position:sticky; top:0; z-index:1; background:#F3F6FA;
    font-weight:600; text-align:left; min-width:115px; max-width:170px;}
.bd-overview th, .bd-overview td {padding:10px 12px; border-bottom:1px solid #E7EBF1;
    vertical-align:top; overflow-wrap:anywhere;}
.bd-overview th:first-child, .bd-overview td:first-child {min-width:230px; max-width:260px;}
.bd-overview td.identity {min-width:115px; max-width:260px;}
.bd-overview td.metric {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.bd-overview tbody tr:nth-child(even) {background:#FAFBFD;}
.bd-overview tbody tr:hover {background:#F0F5FC;}
</style><div class="bd-overview-scroll" role="region" aria-label="模型指标总览" tabindex="0">
<table class="bd-overview"><thead><tr>''' + header_html + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def filter_and_sort(records: list[dict], creators: list[str], search: str,
                    sort_path: str, descending: bool) -> list[dict]:
    """Sort usable numbers; preserve uncertain performance zeros in a separate tail."""
    needle = search.strip().casefold()
    filtered = [record for record in records
                if (not creators or creator_name(record) in creators)
                and (not needle or needle in ' '.join([
                    record['name'], record['id'], record.get('slug') or '',
                    creator_name(record),
                ]).casefold())]
    present = [record for record in filtered if normalized_value(value_at(record, sort_path), sort_path) is not None
               and not performance_zero_notice(record, sort_path)]
    uncertain = [record for record in filtered if performance_zero_notice(record, sort_path)]
    missing = [record for record in filtered if normalized_value(value_at(record, sort_path), sort_path) is None]
    return sorted(present, key=lambda record: normalized_value(value_at(record, sort_path), sort_path), reverse=descending) + uncertain + missing


def evaluation_metadata(record: dict, key: str) -> str:
    """Only explicit evaluation metadata; release/collection dates are not substitutes."""
    value = record.get(key)
    return str(value) if value is not None and str(value).strip() else '源站未提供'


def safe_failure_message(run: dict) -> str:
    """Never render the stored free-text error or an unknown error code."""
    messages = {
        'authentication_failed': '鉴权失败。请在本机检查 API Key 配置。',
        'rate_limited': '请求限流。请稍后重新采集。',
        'timeout': '请求超时。请检查网络后重新采集。',
        'network_error': '网络连接失败。请检查网络后重新采集。',
        'upstream_unavailable': '数据源暂时不可用。请稍后重新采集。',
        'invalid_schema': '数据结构校验未通过，本次数据未替换有效快照。',
        'missing_api_key': '尚未配置 API Key。请在本机填写项目 .env 后重新采集。',
        'missing_config': '缺少本地配置。请在项目根目录创建 .env 并在本机填写 API Key。',
        'invalid_api_key': 'API Key 配置格式无效。请在本机检查配置。',
        'local_error': '本地采集或存储失败。请检查项目文件状态后重新采集。',
    }
    return messages.get(run.get('error_code'), '本次采集未成功；请查看本地脱敏检查报告并重新采集。')


def _time(run: dict | None, field: str) -> str:
    return str((run or {}).get(field) or '暂无')


def _empty_guidance() -> None:
    st.info('尚无有效数据。请先在项目根目录的 .env 中配置 API Key，再运行手动采集命令。')
    st.code('.\\.venv\\Scripts\\python.exe scripts\\collect.py', language='powershell')
    st.caption('采集成功后刷新本页。网页仅读取本地 SQLite，不会通过页面操作请求外部 API。')


def _overview(state: dict) -> None:
    st.header('模型总览')
    st.caption('按单项指标查看源站记录；排序不代表模型在所有场景下的表现。缺失值统一显示“暂无”。')
    records = state['records']
    if not records:
        _empty_guidance()
        return
    metric_paths = list(dict.fromkeys(DEFAULT_METRICS + state.get('metric_paths', [])))
    filter_column, search_column = st.columns([1, 2])
    with filter_column:
        creators = st.multiselect('厂商筛选', sorted({creator_name(record) for record in records}),
                                 key='creator_filter', placeholder='全部厂商')
    with search_column:
        search = st.text_input('搜索模型', key='model_search',
                               placeholder='精确名称、配置标签、稳定 ID 或厂商')
    sort_column, direction_column = st.columns([2, 1])
    with sort_column:
        sort_path = st.selectbox('排序指标', metric_paths, format_func=metric_label, key='sort_metric')
    with direction_column:
        direction = st.radio('数值顺序', ['从高到低', '从低到高'], horizontal=True,
                             key='sort_direction', help='两个方向均将缺失值放在最后。')
    with st.expander('自定义表格显示指标'):
        selected_metrics = st.multiselect('表格显示指标', metric_paths, default=DEFAULT_METRICS,
                                          format_func=metric_label, key='overview_metrics')
    assessments = {path: assess_metric(path, records, [state['snapshot']] * len(records))
                   for path in set(selected_metrics + [sort_path])}
    sort_conflict = assessments[sort_path].get('unit_conflict')
    # An absent key preserves input order while applying ordinary filters.
    filtered = filter_and_sort(records, creators, search, '' if sort_conflict else sort_path,
                               direction == '从高到低')
    if sort_conflict:
        st.warning('当前排序指标的源站单位声明待核对，已停用该项数值排序，保留快照顺序。')
    if any(performance_zero_notice(record, sort_path) for record in filtered):
        st.warning(PERFORMANCE_ZERO_NOTICE + '；这些记录不参与本项数值排序，单列在可排序记录之后，保留快照顺序。')
    st.caption(f'当前显示 {len(filtered)} / {len(records)} 条模型记录 · 缺失值始终排在最后')
    if not filtered:
        st.info('没有符合筛选条件的模型。请调整厂商或搜索关键词。')
        _metric_guide(state, filtered=filtered, key='overview')
        return
    st.html(build_overview_html(filtered, selected_metrics, assessments))
    if any(performance_zero_notice(record, path) for record in filtered for path in selected_metrics):
        st.caption(PERFORMANCE_ZERO_NOTICE + '。表格保留原值 0；不认定缺测、占位、停服或即时响应。')
    _metric_guide(state, filtered=filtered, key='overview')
    st.caption('指数使用源站原值。标注“口径未确认”的评测保持原值，不自动转为百分比。')
    with st.expander('查看完整模型记录与精确配置'):
        by_id = {record['id']: record for record in filtered}
        chosen_id = st.selectbox('完整记录', list(by_id),
                                 format_func=lambda record_id: f"{by_id[record_id]['name']} · {record_id}",
                                 key='detail_model')
        record = by_id[chosen_id]
        st.write(f"评测版本：{evaluation_metadata(record, 'evaluation_version')}")
        st.write(f"评测日期：{evaluation_metadata(record, 'evaluation_date')}")
        st.json(record, expanded=True)


def _comparison(state: dict) -> None:
    st.header('模型对比')
    st.caption('选择 2–4 条源站模型记录，按相同指标并排查看。名称相同、ID 不同的记录保持独立。')
    records = state['records']
    if not records:
        _empty_guidance()
        return
    by_id = {record['id']: record for record in records}
    from .selection_ui import render_selection
    chosen_ids = render_selection(state, widget_key='compare_models', root=ROOT)
    if not 2 <= len(chosen_ids) <= 4:
        st.info('请选择 2–4 条模型记录后查看对比。')
        return
    paths = list(dict.fromkeys(DEFAULT_METRICS + state.get('metric_paths', [])))
    selected_records = [by_id[record_id] for record_id in chosen_ids]
    assessments = {path: assess_metric(path, selected_records, [state['snapshot']] * len(selected_records))
                   for path in paths}
    comparison = {}
    for index, record_id in enumerate(chosen_ids, start=1):
        record = by_id[record_id]
        label = f"R{index} · {record['name']}"
        comparison[label] = {
            '源站精确名称': record['name'],
            '厂商': creator_name(record),
            '配置标签（slug）': record.get('slug') or '源站未提供',
            '稳定 ID': record_id,
            '评测版本': evaluation_metadata(record, 'evaluation_version'),
            '评测日期': evaluation_metadata(record, 'evaluation_date'),
            **{(f'{metric_for(path).label}（源站原值 · 单位声明待核对）'
                if assessments[path].get('unit_conflict') else metric_label(path)):
               ((format_raw_value(value_at(record, path), path) if assessments[path].get('unit_conflict')
                 else format_value(record, path)) + ('（测量含义待确认）' if performance_zero_notice(record, path) else ''))
               for path in paths},
        }
    st.dataframe(pd.DataFrame(comparison).rename_axis('指标 / 源站信息'),
                 width='stretch', height=650,
                 column_config={
                     '_index': st.column_config.TextColumn(width=280),
                     **{label: st.column_config.TextColumn(width=240) for label in comparison},
                 })
    st.caption('采集日期和模型发布日期均不代表评测日期。未确认量纲的指标保留原值；不生成“最佳模型”结论。')
    if any(performance_zero_notice(record, path) for record in selected_records for path in paths):
        st.warning(PERFORMANCE_ZERO_NOTICE + '。不将速度、延迟 0 描述为真实测量并列、停服或即时响应。')
    _rule_insights(state, [by_id[record_id] for record_id in chosen_ids], paths)
    for record_id in chosen_ids:
        record = by_id[record_id]
        with st.expander(f"完整记录：{record['name']} · {record_id}"):
            st.json(record, expanded=True)


def _source_status(state: dict) -> None:
    st.header('数据源状态')
    latest = state.get('latest_attempt') or {}
    last_success = state.get('last_success')
    snapshot = state.get('snapshot') or {}
    count_column, version_column = st.columns(2)
    count_column.metric('有效模型记录数', len(state['records']))
    version_column.metric('有效内容快照数', state.get('snapshot_count', 0))
    st.write(f"最后尝试时间：{_time(latest, 'started_at')}")
    st.write(f"最后成功时间：{_time(last_success, 'finished_at')}")
    st.write(f"当前快照采集时间：{snapshot.get('collected_at') or '暂无'}")
    if latest:
        http_status = latest.get('http_status')
        attempts = latest.get('attempts')
        st.write(f"最近 HTTP 状态：{http_status if type(http_status) is int else '暂无'}")
        st.write(f"最近请求尝试次数：{attempts if type(attempts) is int else '暂无'}")
    if latest.get('status') == 'success':
        st.success('最近更新成功。本页显示本地有效快照。')
    elif latest.get('status') == 'failed':
        st.error(safe_failure_message(latest))
    else:
        st.info('尚无采集运行记录。')
    if not state['records']:
        _empty_guidance()
    if snapshot:
        st.write(f"内容哈希：{snapshot.get('content_hash') or '暂无'}")
        _metric_guide(state, key='status')
        with st.expander('源站字段说明'):
            st.write('未知字段保留在完整记录中；未知非数值字段不参与排序。')
            st.json({'unknown_fields': snapshot.get('unknown_fields', []),
                     'warnings': snapshot.get('warnings', [])}, expanded=False)
    st.caption('手动更新：请在项目根目录运行下列命令。本轮不设置定时采集。')
    st.code('.\\.venv\\Scripts\\python.exe scripts\\collect.py', language='powershell')


def _provenance(state: dict) -> None:
    snapshot = state.get('snapshot') or {}
    latest = state.get('latest_attempt') or {}
    last_success = state.get('last_success')
    st.divider()
    st.markdown(f'数据来自 [Artificial Analysis]({SOURCE_URL}) · [官方 API 文档]({API_REFERENCE_URL})')
    st.caption(f"当前快照采集时间：{snapshot.get('collected_at') or '暂无'} · 来源：Artificial Analysis 官方 API")
    st.caption(f"最近成功采集时间：{_time(last_success, 'finished_at')}")
    if latest.get('status') == 'failed' and state.get('records'):
        status = '最近更新失败，正在展示旧快照，数据可能过期'
    elif latest.get('status') == 'failed':
        status = '最近更新失败，尚无有效数据'
    elif state.get('records'):
        status = '已加载本地有效快照'
    else:
        status = '尚无有效数据'
    st.caption(f'当前数据状态：{status}')
    st.caption(f'来源接口：{API_ENDPOINT}')
    st.info('以上公开价格、速度和延迟来自 Artificial Analysis 公开记录，不是公司网关或本地部署实测值；含义待确认的性能零值另行标注。')
    with st.expander('源站测试参数（prompt_options）', expanded=True):
        if snapshot.get('prompt_options') is not None:
            st.json(snapshot['prompt_options'], expanded=True)
        else:
            st.write('源站未提供')
    st.caption('本页面用于本机开发验证。缺失值不补零；未提供的评测版本与日期不推断。')


def main(db_path: 'Path | None' = None) -> None:
    # Imports inside the callable also make Streamlit AppTest.from_function self-contained.
    # Quote the annotation because AppTest extracts this function without module imports.
    from pathlib import Path
    from benchmark_dashboard import ui

    ui.st.set_page_config(page_title='模型指标面板', page_icon='📊', layout='wide',
                          initial_sidebar_state='expanded')
    ui.st.sidebar.title('模型指标面板')
    ui.st.sidebar.caption('Artificial Analysis · 本地数据')
    page = ui.st.sidebar.radio('页面', ['模型总览', '数据概况 / 日更简报', '模型对比', '数据源状态', '变化记录', 'AI 简报'], key='page')
    ui.st.sidebar.caption('筛选、对比和刷新均只读取本地数据库。')
    try:
        state = ui.read_dashboard(Path(db_path) if db_path is not None else ui.DEFAULT_DB)
    except (OSError, ui.sqlite3.Error, ValueError, TypeError):
        ui.st.error('本地数据库读取失败。请检查数据库路径和文件状态，页面未请求外部 API。')
        ui._provenance({})
        return
    latest = state.get('latest_attempt') or {}
    if latest.get('status') == 'failed' and state['records']:
        ui.st.warning('最近一次采集失败，当前继续展示上次有效快照，数据可能过期。详情见“数据源状态”。', icon='⚠️')
    pages = {'模型总览': ui._overview, '模型对比': ui._comparison, '数据源状态': ui._source_status,
             '变化记录': ui._changes_page}
    if page == '数据概况 / 日更简报':
        from benchmark_dashboard.daily_ui import show_daily
        show_daily(state, root=ui.ROOT)
    elif page == 'AI 简报':
        from benchmark_dashboard.briefing_ui import show_briefing
        show_briefing(state)
    else:
        pages[page](state)
    ui._provenance(state)
