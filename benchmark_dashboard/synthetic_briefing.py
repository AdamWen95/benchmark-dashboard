"""Fixed, small integration input. No business data, filesystem, or credentials."""
from hashlib import sha256
import json

from .fact_pack import compute_fact_hash


def build_synthetic_pack() -> dict:
    """Return a fresh, deterministic fixture; never derive it from the real DB."""
    pack = {
        'schema_version': 'm2b-facts-v1',
        'source': 'synthetic_modex',
        'snapshot': {
            'id': 'synthetic-modex-baseline',
            'content_hash': sha256(b'modex-synthetic-baseline-v1').hexdigest(),
            'collected_at': '2026-01-01T00:00:00+00:00',
        },
        'metric_mapping_version': 'm2b-metrics-v1',
        'rule_version': 'm2a-rules-v1',
        'scope': {'input_mode': 'synthetic', 'total_records': 2, 'example_count': 0,
                  'omitted_records': 2, 'selection_rule': 'fixed_synthetic_fixture'},
        'facts': [
            {'id': 'synthetic-coverage', 'kind': 'coverage',
             'text': '合成输入仅有两条虚构记录，一条有数值、一条缺失；覆盖率为 1/2，即 50%。缺失不代表能力差。',
             'total_records': 2, 'valid_count': 1, 'missing_count': 1},
            {'id': 'synthetic-changes', 'kind': 'changes', 'status': 'baseline',
             'latest_attempt_failed': False, 'counts': {},
             'text': '合成输入仅有一次成功时间点，已建立初始基线，暂无历史可比较。'},
            {'id': 'synthetic-limitations', 'kind': 'limitations',
             'text': '这些数值完全虚构，仅用于接口联调，不含真实评测或公司实测数据，不能用于模型选择；指标单位、版本和配置未知。'},
        ],
    }
    pack['fact_hash'] = compute_fact_hash(pack)
    return json.loads(json.dumps(pack, ensure_ascii=False))
