"""Shared Streamlit selection with a non-widget shadow surviving page cleanup."""
from pathlib import Path

import streamlit as st


SHADOW_KEY = '_selected_model_ids'
ORIGIN_KEY = '_selected_model_origin'
DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def get_default_saved_ids(root: Path) -> list[str]:
    from .m2c_run import get_default_saved_ids as read_saved
    return read_saved(root)


def current_selection(state: dict, *, root: Path | None = None) -> list[str]:
    """Keep incomplete user edits; require 2–4 only before downstream analysis."""
    from .selection import choose_acceptance_ids, validate_selection

    by_id = {record['id']: record for record in state.get('records', [])}
    previous = st.session_state.get(SHADOW_KEY)
    if previous is None:
        previous = st.session_state.get('compare_models')
    if previous is None:
        try:
            saved = get_default_saved_ids(root if root is not None else DEFAULT_ROOT)
            previous = validate_selection(state, saved)
            st.session_state[ORIGIN_KEY] = 'saved'
        except Exception:
            try:
                previous = choose_acceptance_ids(state)
            except ValueError:
                previous = []
            st.session_state[ORIGIN_KEY] = 'technical'
    if (not isinstance(previous, list) or any(not isinstance(value, str) for value in previous)
            or len(previous) > 4 or len(set(previous)) != len(previous)):
        previous = []
        st.warning('请选择 2–4 条模型记录，已清除无效或超出上限的选择。')
    selected = [value for value in previous if value in by_id]
    st.session_state[SHADOW_KEY] = list(selected)
    return selected


def _remember(widget_key: str) -> None:
    st.session_state[SHADOW_KEY] = list(st.session_state.get(widget_key, []))
    st.session_state[ORIGIN_KEY] = 'manual'


def render_selection(state: dict, *, widget_key: str, root: Path | None = None) -> list[str]:
    selected = current_selection(state, root=root)
    by_id = {record['id']: record for record in state.get('records', [])}
    # The widget may have been removed by a different page; restore from the
    # separate key without treating widget deletion as the user's new choice.
    if st.session_state.get(widget_key) != selected:
        st.session_state[widget_key] = list(selected)
    chosen = st.multiselect(
        '选择对比模型', list(by_id), max_selections=4,
        format_func=lambda model_id: f"{by_id[model_id]['name']} · {by_id[model_id].get('slug') or '无 slug'} · {model_id}",
        key=widget_key, on_change=_remember, args=(widget_key,),
    )
    st.session_state[SHADOW_KEY] = list(chosen)
    st.caption('此选择与模型对比、AI 简报共用；切换组合或刷新不会生成简报。')
    origin = st.session_state.get(ORIGIN_KEY)
    if origin == 'saved':
        st.caption('当前默认采用本次已保存简报的模型组合。')
    elif origin == 'technical':
        st.caption('当前默认是技术验收样例：按关键字段覆盖和稳定 ID 确定，优先不同厂商；不是模型推荐或全市场排名。')
    return list(chosen)
