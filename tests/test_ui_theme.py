"""Summary cards use supplied synthetic state and safely render source text."""
from html.parser import HTMLParser

import pytest


class SummaryReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())


@pytest.mark.parametrize('state', [
    {},
    {'records': [], 'snapshot': None},
    {'records': [], 'snapshot': {}},
])
def test_summary_empty_state_reports_zero_counts_and_unavailable_snapshot(state):
    from benchmark_dashboard.theme import build_summary_html

    reader = SummaryReader()
    reader.feed(build_summary_html(state))

    assert reader.text.count('0') == 2
    assert '暂无' in reader.text
    assert 'None' not in reader.text


def test_summary_snapshot_source_text_cannot_inject_html():
    from benchmark_dashboard.theme import build_summary_html

    source_time = '2026-09-23 <img src=x onerror="alert(1)"><script>alert(2)</script> & "原始时间"'
    state = {'records': [], 'snapshot': {'collected_at': source_time}}
    html = build_summary_html(state)
    reader = SummaryReader()
    reader.feed(html)

    assert source_time in reader.text
    assert not {'img', 'script'} & set(reader.tags)
    assert not any(name.lower().startswith('on') for name, _ in reader.attributes)
    assert '&lt;img' in html
    assert '&lt;script&gt;' in html
    assert '&amp;' in html
