"""Presentation-only styling for the read-only Streamlit dashboard."""
from html import escape

import streamlit as st


STYLES = """
<style>
:root {
    --bd-ink: #18263e;
    --bd-muted: #5f6f85;
    --bd-line: #e3e9f2;
    --bd-accent: #315fce;
    --bd-surface: #ffffff;
}
.stApp { background: #f6f8fc; }
[data-testid="stHeader"] { background: rgba(246,248,252,.96); }
[data-testid="stMainBlockContainer"] {
    max-width: 1600px;
    padding: 2.8rem 2.5rem 3rem;
}
h1, h2, h3 { color: var(--bd-ink); letter-spacing: -.035em; }
h2 { font-size: 1.9rem !important; font-weight: 720 !important; }
h3 { font-size: 1.18rem !important; }
[data-testid="stCaptionContainer"] { color: var(--bd-muted); line-height: 1.65; }
[data-testid="stSidebar"] {
    background: #fff;
    border-right: 1px solid var(--bd-line);
}
[data-testid="stSidebarUserContent"] { padding: 1.2rem 1.15rem 2rem; }
.bd-brand { display: flex; align-items: center; gap: 11px; margin: 0 0 32px; }
.bd-brand-mark {
    display: grid; place-items: center; width: 39px; height: 39px;
    border-radius: 12px; background: #315fce; color: white;
    font-size: 22px; font-weight: 750; box-shadow: 0 4px 10px #315fce20;
}
.bd-brand-title { font-size: 16px; font-weight: 750; color: var(--bd-ink); }
.bd-brand-subtitle { color: #5f6f85; font-size: 10px; letter-spacing: .13em; margin-top: 3px; }
.bd-nav-label, .bd-eyebrow {
    color: #5f6f85; font-size: 10px; font-weight: 650;
    letter-spacing: .16em; margin-bottom: 10px;
}
.bd-eyebrow { margin-top: 8px; margin-bottom: -3px; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] { gap: 7px; width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div { width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] {
    display: flex; width: 100%; box-sizing: border-box;
    margin: 0; padding: 11px 13px; border: 1px solid transparent;
    border-radius: 9px; transition: background .15s;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child { display: none; }
[data-testid="stSidebar"] [data-testid="stRadioOption"]::before {
    color: #5f6f85; font-size: 10px; font-weight: 600; margin-right: 12px;
    font-variant-numeric: tabular-nums;
}
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div:nth-child(1) label::before { content: '01'; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div:nth-child(2) label::before { content: '02'; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div:nth-child(3) label::before { content: '03'; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div:nth-child(4) label::before { content: '04'; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div:nth-child(5) label::before { content: '05'; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div:nth-child(6) label::before { content: '06'; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] p { font-size: 14px; color: #52627a; }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover { background: #f5f7fc; }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:checked) {
    background: #ecf2ff; border-color: #dde7fc;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:checked) p {
    color: #2856bb; font-weight: 650;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:focus-visible) {
    outline: 2px solid #315fce; outline-offset: 2px;
}
.bd-sidebar-note {
    border-top: 1px solid var(--bd-line); margin-top: 28px; padding: 20px 8px 0;
    color: #5f6f85; font-size: 12px; line-height: 1.8;
}
.bd-sidebar-note strong { color: #43546d; font-weight: 600; }
.bd-summary { display: grid; grid-template-columns: 1fr 1fr 1.5fr; gap: 16px; margin: 5px 0 14px; }
.bd-stat {
    background: var(--bd-surface); border: 1px solid var(--bd-line);
    border-radius: 13px; padding: 16px 21px; box-shadow: 0 3px 10px #18305203;
}
.bd-stat-label { color: #5f6f85; font-size: 12px; margin-bottom: 9px; }
.bd-stat-value { color: #1c3153; font-size: 30px; font-weight: 700; line-height: 1.3; font-variant-numeric: tabular-nums; }
.bd-stat-time { font-size: 16px; line-height: 1.7; overflow-wrap: anywhere; }
.bd-stat-note { color: #5f6f85; font-size: 11px; margin-top: 7px; }
.st-key-overview_filters {
    padding: 14px 20px 8px; background: white; border: 1px solid var(--bd-line);
    border-radius: 13px; margin-bottom: 7px;
}
.st-key-overview_filters [data-testid="stVerticalBlock"] { gap: .65rem; }
[data-testid="stWidgetLabel"] p { font-size: 12px; font-weight: 550; color: #53637a; }
[data-testid="stExpander"] { background: #fff; border-radius: 10px; }
[data-testid="stExpander"] details { border-color: var(--bd-line); }
[data-testid="stExpander"] summary { font-size: 13px; }
[data-testid="stMetric"] {
    padding: 18px 21px; background: white; border: 1px solid var(--bd-line); border-radius: 12px;
}
[data-testid="stMetricValue"] { font-size: 1.8rem; color: #203956; }
[data-testid="stAlert"] { border-radius: 10px; }
[data-testid="stDataFrame"] { border-radius: 11px; overflow: hidden; }
.bd-footer { color: #5f6f85; font-size: 11px; letter-spacing: .02em; padding-top: 16px; border-top: 1px solid var(--bd-line); margin-top: 25px; }
.bd-m2a-scroll { background: white; border-color: var(--bd-line) !important; border-radius: 11px !important; }
.bd-m2a { color: var(--bd-ink); }
.bd-m2a th { background: #f5f7fb !important; color: #5d6e85; font-size: 12px; font-weight: 600; z-index: 1; }
.bd-m2a th, .bd-m2a td { padding: 12px 15px !important; border-color: #edf1f6 !important; }
.bd-m2a tbody tr:hover { background: #f6f9ff; }
@media (max-width: 1000px) {
    [data-testid="stMainBlockContainer"] { padding: 2.8rem 1.3rem 2rem; }
    .bd-summary { gap: 10px; }
    .bd-stat { padding: 16px; }
}
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] { padding: 3.5rem 1rem 2rem; }
    h2 { font-size: 1.6rem !important; }
    .bd-summary { grid-template-columns: 1fr 1fr; }
    .bd-stat:last-child { grid-column: 1 / -1; }
    .bd-stat-value { font-size: 26px; }
    .bd-stat-time { font-size: 15px; }
    .st-key-overview_filters { padding: 14px; }
}
@media (prefers-reduced-motion: reduce) {
    [data-testid="stSidebar"] [data-testid="stRadioOption"] { transition: none; }
}
</style>
"""


def apply_theme() -> None:
    # Markdown keeps presentation out of the semantic HTML table stream.
    st.markdown(STYLES, unsafe_allow_html=True)


def render_brand() -> None:
    st.sidebar.markdown(
        '<div class="bd-brand"><div class="bd-brand-mark" aria-hidden="true">B</div>'
        '<div><div class="bd-brand-title">模型指标面板</div>'
        '<div class="bd-brand-subtitle">BENCHMARK / INSIGHTS</div></div></div>'
        '<div class="bd-nav-label">工作空间 · WORKSPACE</div>', unsafe_allow_html=True,
    )


def build_summary_html(state: dict) -> str:
    """Summarize the full snapshot, preserving and escaping its original time."""
    records = state.get('records') or []
    creators = {(record.get('model_creator') or {}).get('name') or '源站未提供'
                for record in records}
    collected_at = (state.get('snapshot') or {}).get('collected_at') or '暂无'
    cards = [
        ('模型记录', str(len(records)), '本次快照收录的全部记录', ''),
        ('厂商分组', str(len(creators)), '按厂商名称分组，含未提供', ''),
        ('快照采集时间', str(collected_at), '保留来源时区 · 非评测日期', ' bd-stat-time'),
    ]
    return '<div class="bd-summary" aria-label="当前快照概况">' + ''.join(
        f'<div class="bd-stat"><div class="bd-stat-label">{escape(label)}</div>'
        f'<div class="bd-stat-value{style}">{escape(value)}</div>'
        f'<div class="bd-stat-note">{escape(note)}</div></div>'
        for label, value, note, style in cards
    ) + '</div>'
