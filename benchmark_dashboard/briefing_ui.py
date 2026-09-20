"""Read-only briefing view. No settings loader, generator, or network client."""
from pathlib import Path

import streamlit as st

from .briefing import AnalysisSettings, read_state, read_synthetic_state
from .fact_pack import build_fact_pack


DEFAULT_CACHE = Path(__file__).resolve().parents[1] / 'data' / 'analysis_results'
SYNTHETIC_CACHE = Path(__file__).resolve().parents[1] / '.cache' / 'modex-integration'
SECTION_TITLES = {
    'current': '当前数据能说明什么',
    'changes': '与上次成功采集相比发生了什么',
    'limitations': '使用限制',
}
FACT_LABELS = {'coverage': '覆盖情况', 'metric_example': '有限例证', 'rule': '规则观察',
               'changes': '采集变化', 'limitations': '使用限制'}


def _render_result(view: dict) -> None:
    st.caption(f"对应数据时间：{view.get('data_collected_at') or '未知'}；"
               f"生成时间：{view.get('generated_at') or '未知'}；"
               f"有效期至：{view.get('expires_at') or '未知'}。")
    metadata = view.get('result_metadata') or view
    st.caption(f"请求模型：{metadata.get('request_model') or '未知'}；"
               f"上游自报模型：{metadata.get('response_model') or '未知'}。自报名称不证明底层模型身份。")
    usage = metadata.get('usage')
    labels = {'prompt_tokens': '输入 Token', 'completion_tokens': '输出 Token', 'total_tokens': '总 Token'}
    usage_text = '、'.join(f'{label}：{usage.get(key, "未知")}' for key, label in labels.items()) if usage else '未知'
    st.caption(f'用量：{usage_text} · 费用：未知；不使用榜单价格估算 Modex 扣费。')
    http_status = metadata.get('http_status')
    elapsed = metadata.get('elapsed_seconds')
    if http_status is not None or elapsed is not None:
        st.caption(f'HTTP 状态：{http_status if http_status is not None else "未知"}；'
                   f'请求耗时：{elapsed if elapsed is not None else "未知"} 秒。')
    for section in view['result']['sections']:
        st.subheader(SECTION_TITLES[section['key']])
        for claim in section['claims']:
            st.text(claim['text'])
            st.caption('依据：' + '、'.join(claim['fact_ids']))
    st.info('结构和引用检查只能验证部分一致性，不能保证自然语言判断正确；请按事实编号人工复核。')


def show_synthetic_integration(*, cache_dir: Path | None = None) -> None:
    """Read the isolated integration cache without loading any key or real DB."""
    from .synthetic_briefing import build_synthetic_pack
    with st.expander('合成服务联调记录（独立）'):
        st.warning('合成输入、真实服务联调：此处仅用于接口验证，不是真实指标分析。')
        st.caption('仅显示显式命令保存的隔离结果；打开或刷新不会请求模型。')
        view = read_synthetic_state(cache_dir=cache_dir if cache_dir is not None else SYNTHETIC_CACHE)
        if view['status'] == 'generated' and view.get('result'):
            st.subheader('AI 辅助解读 · 待人工复核')
            _render_result(view)
        else:
            st.info(view['message'])
        st.caption('TTL 仅控制显示有效期，不会物理删除隔离文件。')
        for fact in build_synthetic_pack()['facts']:
            st.text(f"{fact['id']}：{fact['text']}")


def _show_legacy_briefing(state: dict, *, settings: AnalysisSettings,
                          cache_dir: Path | None = None) -> None:
    """Consume the dashboard's existing read transaction and cached results only.

    Settings injection is for isolated tests and a future approved integration.
    The real application always uses disabled defaults, irrespective of .env.
    """
    st.header('AI 简报')
    st.caption('Modex 适配已准备；真实指标分析默认关闭。AI 辅助解读需要人工复核。')
    settings = settings if settings is not None else AnalysisSettings()
    pack = None
    if state.get('records'):
        try:
            pack = build_fact_pack(state)
        except (ValueError, TypeError, KeyError):
            st.warning('本地事实整理暂不可用；原总览、对比、变化记录和规则说明仍可使用。')
    try:
        view = read_state(pack, settings, cache_dir=cache_dir if cache_dir is not None else DEFAULT_CACHE)
    except Exception:
        # Never expose corrupt cache data, a file path, credentials or an exception.
        view = {'status': 'failed', 'message': '简报结果暂不可用，请继续查看本地规则与原表。',
                'result': None}
    status = view['status']
    if status == 'failed':
        st.error(view['message'])
    elif status in ('stale', 'purpose_unconfirmed', 'authorization_required', 'protocol_unverified'):
        st.warning(view['message'])
    else:
        st.info(view['message'])
    st.caption('页面刷新、筛选和切换只读取本地数据或已有结果，不会生成简报或请求模型服务。')
    if not settings.data_use_confirmed:
        st.warning('分析用途与传输范围待负责人确认；运行时开关不是数据使用许可证明。')
    with st.expander('后续真实联调需要的条件'):
        st.write('在本机填写独立 Modex Key 后，还需明确允许单次联调。用途待确认时仅发送固定合成输入。')
        st.write('指定 gpt-5.6-sol，非流式 Chat Completions；真实协议兼容性与模型权限需实际联调核验。')
        st.write('真实指标分析另需用途和最小传输范围确认。页面不读取 .env；数据采集 Key 不能复用为分析 Key。')
    show_synthetic_integration()

    result = view.get('result')
    if status == 'generated' and result:
        st.subheader('AI 辅助解读 · 待人工复核')
        _render_result(view)
    elif status in ('failed', 'stale'):
        st.caption('失败、过期或不匹配的结果不作为当前快照的 AI 简报展示。')

    if pack is None:
        st.info('暂无可用的本地事实包。已有数据保持不变；本页不会自动采集或生成。')
        return
    snapshot, scope = pack['snapshot'], pack['scope']
    st.subheader('本地规则摘要（非 AI）')
    st.caption(f"快照 #{snapshot['id']} · 数据时间 {snapshot['collected_at']} · "
               f"全量 {scope['total_records']} 条；例证 {scope['example_count']} 条。")
    st.caption('例证按稳定 ID 字典序最多选 2 条，剩余记录不逐条传递；此范围不代表全市场排名。')
    for fact in pack['facts']:
        if fact['kind'] in ('rule', 'changes', 'limitations'):
            st.text(fact['text'])
    with st.expander('人工复核：查看最小分析事实与依据'):
        st.caption(f"事实包哈希：{pack['fact_hash']}；映射版本：{pack['metric_mapping_version']}；"
                   f"规则版本：{pack['rule_version']}。")
        st.caption('以下内容由本地程序整理，尚未发送给任何分析服务；不包含原始响应、完整模型记录或配置文件。')
        facts = {fact['id']: fact for fact in pack['facts']}
        selected = st.selectbox('选择事实编号', list(facts),
                                format_func=lambda key: f"{FACT_LABELS.get(facts[key]['kind'], '事实')} · {key}",
                                key='briefing_fact')
        st.json(facts[selected], expanded=True)


def build_selected_fact_pack(state: dict, selected_ids: list[str]) -> dict:
    from .selected_facts import build_selected_fact_pack as build
    return build(state, selected_ids)


def read_current_artifact(state: dict, selected_ids: list[str], *, root: Path) -> dict:
    from .m2c_run import read_current_artifact as read
    return read(state, selected_ids, root=root)


def read_historical_artifact(*, root: Path) -> dict:
    from .m2c_run import read_historical_artifact as read
    return read(root=root)


def _show_historical_briefing(*, root: Path) -> None:
    from .ui import _static_table
    from .metrics import PERFORMANCE_ZERO_NOTICE
    with st.expander('历史验收简报（原始 AI 输出）'):
        st.caption('独立审核入口：以下历史选择与当前选择分开。原文、依据、生成时间、版本、有效期及人工复核状态均不改写。')
        if not st.checkbox('查看已保存的历史原文', value=False, key='show_historical_briefing'):
            return
        view = read_historical_artifact(root=root)
        if view.get('status') != 'historical' or not view.get('result'):
            st.warning(view['message'])
            return
        st.warning(view['message'] + ' 本次未调用模型；用户人工内容复核仍待完成。')
        st.subheader('历史 AI 正文 · 非当前版本生成')
        _static_table([{'历史代号': row['alias'], '模型名称': row['name'],
                        '厂商': row['creator'], '稳定 ID': row['id']}
                       for row in view['local_model_mapping']], '历史简报模型映射', 230)
        pack = view['fact_pack']
        versions = view['artifact']['versions']
        st.caption(f"原规则版本：{versions['rule_version']}；原提示词版本：{versions['prompt_version']}；"
                   f"原事实包版本：{versions['pack_version']}。")
        has_zero = any(fact.get('metric_path', '').startswith('median_') and any(
            type(value.get('raw_value')) in (int, float) and value['raw_value'] == 0
            for value in fact.get('values', [])) for fact in pack['facts'])
        if has_zero:
            st.info('当前复核提示（程序规则，非原 AI 正文）：' + PERFORMANCE_ZERO_NOTICE
                    + '。旧正文中的 0 不证明真实速度相同、无输出、停服或即时响应；以下原文保持不变。')
        _render_result(view)
        facts = {fact['id']: fact for fact in pack['facts']}
        choice = st.selectbox('查看历史原文依据', list(facts), key='historical_briefing_fact')
        st.json({'snapshot': pack['snapshot'], 'fact_hash': pack['fact_hash'],
                 'versions': versions, 'fact': facts[choice]}, expanded=False)


def _show_selected_briefing(state: dict, *, root: Path) -> None:
    from .selection import local_model_mapping, validate_selection
    from .selection_ui import render_selection
    from .ui import _static_table

    st.header('AI 简报')
    st.info('本页新增生成未启用；已保存的本地简报可只读查看。')
    st.caption('查看历史简报不会获得新增请求额度；后续生成、数据留存和对外展示需按实际使用范围配置并确认。')
    st.caption('页面不读取密钥、不创建客户端；刷新、切页和选择组合均不会请求模型或源站。')
    if not state.get('records'):
        st.info('暂无可用的本地事实包。已有数据保持不变；本页不会自动采集或生成。')
        show_synthetic_integration()
        return
    selected_ids = render_selection(state, widget_key='briefing_models', root=root)
    try:
        selected_ids = validate_selection(state, selected_ids)
    except ValueError:
        st.info('请选择 2–4 条模型记录后查看对应简报与依据。')
        _show_historical_briefing(root=root)
        return
    mapping = local_model_mapping(state, selected_ids)
    st.subheader('本次所选模型与记录代号')
    _static_table([{'记录代号': row['alias'], '模型精确名称': row['name'],
                    '厂商': row['creator'], '稳定 ID': row['id']} for row in mapping],
                  '简报模型映射', 260)
    st.caption('R1–R4 按当前选择顺序映射到完整名称与稳定 ID；同名不同 ID 不合并。')
    pack = None
    try:
        pack = build_selected_fact_pack(state, selected_ids)
    except Exception:
        st.warning('本地事实整理暂不可用；原总览、对比、变化记录和规则说明仍可使用。')
    try:
        view = read_current_artifact(state, selected_ids, root=root)
    except Exception:
        view = {'status': 'failed', 'message': '本次本地简报暂不可读，请继续查看原表与规则。', 'result': None}
    status = view.get('status')
    if status == 'failed':
        st.error(view['message'])
    elif status in ('mismatch', 'stale'):
        st.warning(view['message'])
    else:
        st.info(view['message'])
    if status == 'generated' and view.get('result'):
        st.subheader('AI 辅助解读 · 待人工复核')
        _render_result(view)
        pack = view.get('fact_pack') or pack
    elif status in ('mismatch', 'stale', 'failed'):
        st.caption('失败、过期或不匹配的结果不作为当前组合的 AI 简报展示。历史验收产物可在本地报告中复核。')
    st.caption('有效期仅控制当前展示，不会物理删除文件；本次保存不建立长期批量缓存政策。')
    if pack is not None:
        scope, snapshot = pack['scope'], pack['snapshot']
        st.subheader('本地规则摘要（非 AI）')
        st.caption(f"数据采集时间：{snapshot['collected_at']}；快照 #{snapshot['id']}。"
                   f"全量覆盖分母 {scope['total_records']}；所选覆盖分母 {len(selected_ids)}。")
        for fact in pack['facts']:
            if fact['kind'] in ('rule', 'changes', 'limitations'):
                st.text(fact['text'])
        with st.expander('人工复核：查看最小分析事实与依据'):
            st.caption(f"快照哈希：{snapshot['content_hash']}；事实包哈希：{pack['fact_hash']}；"
                       f"指标映射：{pack['metric_mapping_version']}；规则：{pack['rule_version']}。")
            st.caption('以下为当前组合的程序事实；只包含所选记录的必要指标及覆盖统计，不包含其他模型明细或配置文件。')
            facts = {fact['id']: fact for fact in pack['facts']}
            selected_fact = st.selectbox('选择事实编号', list(facts),
                format_func=lambda key: f"{FACT_LABELS.get(facts[key]['kind'], '事实')} · {key}",
                key='selected_briefing_fact')
            from .selected_facts import fact_id_map
            st.caption('本地完整依据编号：' + fact_id_map(pack)[selected_fact])
            st.json({'fact': facts[selected_fact], 'snapshot': snapshot,
                     'shared_context': {key: scope[key] for key in ('notes', 'metadata', 'official_sources') if key in scope}},
                    expanded=True)
    else:
        st.info('暂无可用的本地事实包。')
    _show_historical_briefing(root=root)
    show_synthetic_integration()


def show_briefing(state: dict, *, settings: AnalysisSettings | None = None,
                  cache_dir: Path | None = None, root: Path | None = None) -> None:
    """Read a scoped M2C artifact by default; explicit settings support test fixtures."""
    if settings is not None:
        _show_legacy_briefing(state, settings=settings, cache_dir=cache_dir)
        return
    _show_selected_briefing(state, root=root if root is not None else DEFAULT_CACHE.parents[1])
