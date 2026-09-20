"""Read-only full-snapshot overview and saved daily briefing; no key or client."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from .changes import EVENT_LABELS, EVENT_TYPES
from .metrics import PERFORMANCE_ZERO_NOTICE, metric_for


DETAIL_LIMIT = 100
CELL_LIMIT = 1000
SECTION_TITLES = {
    'current': '当前公开数据概况',
    'changes': '与上次成功采集相比的变化',
    'limitations': '使用限制与证据不足',
}


def build_daily_overview(state: dict) -> dict:
    from .daily_facts import build_daily_overview as build
    return build(state)


def read_latest_run(*, root: Path) -> dict | None:
    from .daily_run import read_latest_run as read
    return read(root)


def read_daily_briefing(pack: dict, *, root: Path) -> dict:
    from .daily_briefing import read_daily_briefing as read
    return read(pack, root=root)


def read_daily_history(*, root: Path) -> list[dict]:
    from .daily_briefing import read_daily_history as read
    return read(root=root, limit=5)


def _matching_current_pack(state: dict, run: dict) -> dict:
    """Rebuild from the same current read view; a snapshot hash alone is insufficient."""
    from .daily_facts import build_daily_fact_pack
    from .daily_briefing import semantic_key

    saved = run['fact_pack']
    scope = saved['scope']
    examples = scope['selected_examples']
    mapping = run.get('model_mapping') or []
    saved_ids = [row['model_id'] for row in mapping]
    if ((run.get('overview') or {}).get('snapshot') or {}).get('content_hash') != saved['snapshot']['content_hash']:
        raise ValueError('日更保存依据不一致')
    if len(saved_ids) != examples['retained_count']:
        raise ValueError('日更例证依据不完整')
    if examples['selection_rule'] == 'explicit_order':
        requested_ids = run.get('requested_model_ids')
        requested_ids = requested_ids if isinstance(requested_ids, list) else saved_ids
        if len(requested_ids) != examples['requested_count']:
            # A reduced explicit selection has an unknown tail. Do not invent it.
            raise ValueError('原始显式例证选择无法完整恢复')
        current = build_daily_fact_pack(state, requested_ids)
    else:
        current = build_daily_fact_pack(state, None)
    if (current['scope']['selection_hash'] != scope['selection_hash']
            or semantic_key(current) != semantic_key(saved)):
        raise ValueError('当前全量数据或变化依据不匹配')
    return current


def _value(value) -> str:
    if value is None:
        return '暂无'
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text if len(text) <= CELL_LIMIT else text[:CELL_LIMIT] + '…（显示截断）'


def _run_time(point: dict | None, field: str) -> str:
    return (point or {}).get(field) or '暂无'


def _overview(overview: dict) -> None:
    from .ui import _static_table

    snapshot = overview.get('snapshot') or {}
    creators = overview.get('creator_distribution', [])
    coverage = overview.get('coverage', [])
    columns = st.columns(3)
    columns[0].metric('全量模型记录数', overview['total_records'])
    columns[1].metric('厂商分组数', len(creators))
    columns[2].metric('指标字段数', len(coverage))
    st.caption(f"统计范围：{overview.get('source') or '暂无来源'} 本次有效响应中的全部记录，"
               '不受对比页的 2–4 条选择影响，不等于市场全部模型。厂商信息缺失时单列分组。')
    latest, success = overview.get('latest_attempt'), overview.get('latest_success')
    _static_table([
        {'时间含义': '最近采集尝试开始', '时间': _run_time(latest, 'started_at')},
        {'时间含义': '最近采集尝试结束', '时间': _run_time(latest, 'finished_at')},
        {'时间含义': '最近成功采集结束', '时间': _run_time(success, 'finished_at')},
        {'时间含义': '当前快照数据采集时间', '时间': snapshot.get('collected_at') or '暂无'},
    ], '全量数据时间', 245)
    st.caption(f"当前快照：#{snapshot.get('id', '暂无')}；内容哈希：{snapshot.get('content_hash') or '暂无'}。"
               '采集时间不是逐项评测发生时间，AI 生成时间在正文区单独显示。')
    if (latest or {}).get('status') == 'failed':
        st.warning('最近一次采集失败；以下仍是上一份有效快照及其真实时间，不能视为本次同步成功。')
    if not overview['total_records']:
        st.info('尚无有效模型记录。本页不会自动采集或生成。')
    with st.expander('厂商分布（全量记录）'):
        _static_table([{'厂商': row['creator_name'], '厂商 ID': row['creator_id'] or '源站未提供',
                        '记录数': row['count']} for row in creators], '全量厂商分布')
    st.subheader('指标覆盖与测量质量（全量记录）')
    st.caption('有限数值覆盖 = 有限数值字段数 / 全量模型记录数，合法 0 计入；'
               '缺失、非法类型和非有限值不计入。有限数值覆盖不等于可靠实测覆盖率。')
    st.info(PERFORMANCE_ZERO_NOTICE + '。这类性能 0 不用于排序、优劣、并列或差值判断；'
            '评测零分和零价格仍按各自字段语义保留。')
    _static_table([{
        '指标': row['label'], '字段键': row['path'], '单位': row['unit'],
        '核验状态': row['verification_status'],
        '有限数值 / 全量': f"{row['valid_count']} / {row['total']}",
        '有限数值覆盖率': f"{row['coverage']:.1%}", '缺失或无效': row['missing_count'],
        '测量含义未知的性能零值': row['performance_zero_count'],
        '质量限制': row.get('quality_notice') or '数值存在不代表版本、配置或测量质量已确认',
    } for row in coverage], '全量指标覆盖与质量', 500)
    st.caption('partial / unknown 字段保留源站原值，不升级口径、不自动转为百分比、不评选能力冠军。'
               '原值表与单字段报告值排序见“模型总览”；独立交互比较见“模型对比”。')
    with st.expander('全量程序规则（非 AI 正文）'):
        for rule in overview.get('rules', []):
            st.text(rule)


def _changes(overview: dict) -> None:
    from .ui import _static_table

    changes = overview['changes']
    st.subheader('最近两次成功采集之间的全量变化')
    st.caption('范围：所有记录；按来源 + 稳定 ID 比较最近两次成功运行，允许关联同一内容快照。'
               '以下是采集区间的源站记录变化，不是过去整天完整新闻或模型能力变化。')
    if changes.get('latest_attempt_failed'):
        st.warning('最近采集尝试失败；下列成功运行之间的历史比较不代表当前同步成功。')
    st.info(changes.get('message') or '缺少比较依据')
    periods = []
    for key, label in [('before', '上次成功采集'), ('after', '最近成功采集')]:
        point = changes.get(key) or {}
        run, snapshot = point.get('run') or {}, point.get('snapshot') or {}
        periods.append({'比较端点': label, '运行 ID': point.get('run_id') or run.get('id', '暂无'),
                        '成功时间': point.get('finished_at') or run.get('finished_at') or run.get('started_at') or '暂无',
                        '快照 ID': point.get('snapshot_id') or snapshot.get('id', '暂无'),
                        '内容哈希': point.get('content_hash') or snapshot.get('content_hash') or '暂无'})
    _static_table(periods, '全量变化的采集区间', 220)
    labels = {**EVENT_LABELS, 'added': '本次新收录记录'}
    counts = changes.get('counts') or {}
    _static_table([{'变化类型': labels[key], '完整事件数': counts.get(key, 0)}
                   for key in EVENT_TYPES], '全量七类变化计数', 355)
    events = changes.get('events', [])
    shown = events[:DETAIL_LIMIT]
    st.caption(f'全量变化事件共 {len(events)} 条；此处展示前 {len(shown)} 条，'
               f'省略 {max(0, len(events) - len(shown))} 条。七类计数始终是完整计数，'
               f'不是变化模型数；单格超过 {CELL_LIMIT} 字时仅截断页面显示。')
    if shown:
        with st.expander('全量变化明细（有界显示）', expanded=True):
            _static_table([{
                '类型': labels.get(event['type'], event['type']), '模型': event.get('name', '暂无'),
                '稳定 ID': event.get('model_id', '暂无'),
                '字段': metric_for(event['path']).label if event.get('path') else '记录',
                '字段键': event.get('path') or '—',
                '源站前值': _value(event.get('before')), '源站后值': _value(event.get('after')),
                '可比性': event.get('comparability') or '未知',
                '绝对差值': _value(event.get('absolute')),
                '差值单位': event.get('delta_unit') or '未确认 / 不计算',
                '限制': event.get('reason') or '源站记录变化不代表能力变化',
            } for event in shown], '全量变化有界明细', 500)
    st.caption('本次新收录不等于今天发布；本次未返回不等于停服。补齐不算从 0 提升，'
               '转缺失不算跌为 0；口径未知或含义不明的性能零值保留原值，不计算差值。')


def _saved_run(run: dict | None) -> None:
    if not run:
        st.info('暂无已保存的日更运行状态；当前规则概况来自本地有效数据，不冒充联网日更。')
        return
    collection, analysis = run.get('collection') or {}, run.get('analysis') or {}
    labels = {'success': '成功', 'failed': '失败', 'running': '进行中', 'pending': '未执行',
              'not_started': '未执行', 'skipped': '已跳过', 'generated': '已生成',
              'cached': '复用已保存结果', 'disabled': '未启用', 'not_generated': '尚未生成',
              'failed_or_unknown': '失败或响应未知', 'skipped_collection_failed': '采集失败，已跳过分析',
              'skipped_no_changes': '数据内容未变化，已跳过 AI'}
    st.caption(f"最近日更运行：{run.get('run_id') or '暂无'}；运行日期：{run.get('run_date') or '暂无'}；"
               f"时区：{run.get('timezone') or '暂无'}；开始：{run.get('started_at') or '暂无'}；"
               f"结束：{run.get('finished_at') or '暂无'}。")
    st.caption(f"该次采集状态：{labels.get(collection.get('status'), '状态待核对')}；"
               f"记录的源站请求尝试数：{collection.get('attempts', '未知')}；"
               f"该次分析状态：{labels.get(analysis.get('status'), '状态待核对')}；"
               f"记录的模型请求尝试数：{analysis.get('attempts', '未知')}。")
    if collection.get('status') in ('failed', 'failed_or_unknown'):
        st.error('该次日更采集失败；未把旧数据标为今日已同步成功，本页保留上一份有效数据。')
    elif analysis.get('status') == 'failed':
        st.warning('该次日更 AI 生成失败；最新指标表和程序规则仍可使用，旧 AI 不替换为新成功。')
    path = run.get('result_path')
    if path:
        st.caption('该次已保存日更的完整运行与全量变化证据路径（overview.changes.events；范围以文件为准）：')
        st.code(str(path), language=None)
    else:
        st.caption('此读取状态未提供已保存的完整变化证据路径；可在“变化记录”查看更多本地明细。')


def _render_result(view: dict, *, historical: bool = False) -> None:
    st.subheader('历史日更 AI 原文 · 非当前结果' if historical else '全量日更 AI 正文 · 待人工复核')
    current_time = '' if historical else f"当前数据采集时间：{view.get('current_data_collected_at') or '未知'}；"
    st.caption(f"AI 依据的数据采集时间：{view.get('data_collected_at') or '未知'}；"
               + current_time +
               f"AI 生成时间：{view.get('generated_at') or '未知'}；"
               f"原有效期至：{view.get('expires_at') or '未知'}。")
    if not historical and view.get('semantic_reuse'):
        st.info('复用相同范围及语义输入的已保存日更结果，保留原生成时间与原依据；没有重新生成。')
    st.caption(f"请求模型：{view.get('request_model') or '未知'}；"
               f"上游自报模型：{view.get('response_model') or '未知'}。自报名称不证明底层模型身份。")
    usage = view.get('usage') or {}
    st.caption(f"用量：输入 Token {usage.get('prompt_tokens', '未知')}；"
               f"输出 Token {usage.get('completion_tokens', '未知')}；总 Token {usage.get('total_tokens', '未知')}。"
               '费用未知，不使用榜单价格估算模型服务扣费。')
    st.caption(f"HTTP 状态：{view.get('http_status') or '未知'}；"
               f"请求耗时：{view.get('elapsed_seconds') if view.get('elapsed_seconds') is not None else '未知'} 秒。")
    for section in view['result']['sections']:
        st.subheader(SECTION_TITLES[section['key']])
        for claim in section['claims']:
            st.text(claim['text'])
            st.caption('依据：' + '、'.join(claim['fact_ids']))
    st.info('AI 辅助解读，需人工复核。结构及引用校验不等于用户已完成内容验收；原 AI 正文完整保留。')
    mapping = view.get('local_model_mapping') or []
    if mapping:
        from .ui import _static_table
        with st.expander('日更逐模型例证映射（至多 4 条，不是推荐或排名）'):
            _static_table([{'代号': row['alias'], '模型名称': row['name'],
                            '厂商': row['creator'], '稳定 ID': row.get('model_id') or row.get('id')}
                           for row in mapping], '日更例证本地映射', 260)
    pack = view.get('fact_pack') or {}
    if pack.get('facts'):
        with st.expander('日更正文依据（全量聚合与有限例证分开）'):
            facts = {fact['id']: fact for fact in pack['facts']}
            selected = st.selectbox('查看历史日更事实编号' if historical else '查看日更事实编号', list(facts),
                                    key='historical_daily_fact' if historical else 'daily_fact')
            st.json({'fact': facts[selected], 'scope': pack.get('scope'),
                     'snapshot': pack.get('snapshot'), 'versions': view.get('versions')}, expanded=True)
    if view.get('artifact_path'):
        st.caption('已保存的日更 AI 正文及依据路径：')
        st.code(str(view['artifact_path']), language=None)


def _briefing(state: dict, overview: dict, run: dict | None, *, root: Path) -> None:
    st.subheader('已保存的日更简报')
    st.caption('全量日更、用户所选模型简报和历史验收简报分别标记；本页只读取日更类型。'
               '页面刷新、筛选和切换不采集、不生成、不读取密钥。')
    if not run:
        st.info('尚无对应的日更 AI 结果，程序概况可继续查看。')
        return
    collection, analysis = run.get('collection') or {}, run.get('analysis') or {}
    if ((overview.get('latest_attempt') or {}).get('status') == 'failed'
            or collection.get('status') != 'success'
            or analysis.get('status') in ('failed', 'running', 'pending')):
        st.info('该次日更没有可发布的当前 AI 简报；旧结果保留为历史，不冒充本次成功。')
        return
    if analysis.get('status') == 'skipped_no_changes':
        st.info('该次采集的数据内容未变化，未请求 AI，也未生成新正文。'
                '已保存的旧正文仍可在“历史日更审核”中查看，保留原始依据和生成时间。')
        return
    pack = run.get('fact_pack') or {}
    current = overview.get('snapshot') or {}
    if not current:
        st.warning('当前没有有效快照；旧日更正文不作为当前结果。')
        return
    if (pack.get('scope') or {}).get('kind') != 'daily':
        st.info('尚无对应的全量日更 AI 结果；所选模型或历史验收简报不能代替日更简报。')
        return
    try:
        pack = _matching_current_pack(state, run)
    except Exception:
        st.warning('当前全量数据、例证或两次成功采集的变化依据与保存日更不匹配；'
                   '尚无对应 AI 结果，规则概况可用，旧正文仅供历史审核。')
        return
    try:
        view = read_daily_briefing(pack, root=root)
    except Exception:
        st.error('已保存的日更简报暂不可读；本地规则概况仍可使用，页面不会重试生成。')
        return
    status = view.get('status')
    result_scope = ((view.get('fact_pack') or {}).get('scope') or {})
    if (status == 'generated' and view.get('kind') == 'daily'
            and result_scope.get('kind') == 'daily' and view.get('result')):
        st.info(view.get('message') or '已读取匹配的全量日更简报。')
        _render_result(view)
    elif status == 'failed':
        st.error('日更简报读取或生成失败；本地概况保持可用，没有发布旧结果冒充成功。')
    elif status in ('stale', 'mismatch'):
        st.warning('已保存的日更简报已过期或范围、版本不匹配；不作为当前结果展示。')
    else:
        st.info('尚无对应的全量日更 AI 结果，程序概况可继续查看。')
    st.caption('有效期到期不触发生成，也不物理删除历史证据；所选模型与历史验收原文仍在各自入口。')


def _history(*, root: Path) -> None:
    with st.expander('历史日更审核（独立只读入口）'):
        st.caption('只读取日更类型的已保存原文，最多展示 5 份已通过校验的产物。'
                   '历史正文、原始依据、生成时间、版本和有效期不改写，不代表当前范围的新结果。')
        if not st.checkbox('查看已保存的历史日更原文', value=False, key='show_daily_history'):
            return
        try:
            views = read_daily_history(root=root)
        except Exception:
            st.warning('历史日更文件暂不可读；未重试生成，也未影响当前程序概况。')
            return
        views = [view for view in views if view.get('status') == 'historical'
                 and view.get('kind') == 'daily' and view.get('result')
                 and ((view.get('fact_pack') or {}).get('scope') or {}).get('kind') == 'daily']
        if not views:
            st.info('暂无可校验的历史日更原文；所选模型和 M2C 历史验收简报不在此范围。')
            return
        index = st.selectbox('选择历史日更产物', list(range(len(views))),
                             format_func=lambda value: f"{views[value].get('generated_at') or '时间未知'} · 历史日更 {value + 1}",
                             key='historical_daily_artifact')
        view = views[index]
        st.warning('历史 AI 原文，非当前结果、非本轮新生成。' +
                   ('原有效期已过；保留供审核，未延长有效期。' if view.get('result_stale') else '保留原有效期。') +
                   '用户人工内容复核仍待完成。')
        st.caption('原版本：' + json.dumps(view.get('versions') or {}, ensure_ascii=False, sort_keys=True))
        _render_result(view, historical=True)


def show_daily(state: dict, *, root: Path | None = None) -> None:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    st.header('数据概况 / 日更简报')
    st.caption('本地只读视图：全量程序统计与已保存 AI 正文分开；此页面没有采集或生成操作。')
    try:
        overview = build_daily_overview(state)
    except Exception:
        st.error('全量概况暂不可用，请查看原模型总览与数据源状态；页面未请求外部服务。')
        return
    try:
        run = read_latest_run(root=root)
    except Exception:
        run = None
        st.warning('已保存的日更运行状态暂不可读；仍显示本地有效数据的程序概况。')
    _saved_run(run)
    _overview(overview)
    _changes(overview)
    _briefing(state, overview, run, root=root)
    _history(root=root)
