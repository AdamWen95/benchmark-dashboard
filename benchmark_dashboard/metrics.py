"""One metric dictionary and deterministic numeric rules for local displays.

The validation allowlist remains separate from extra display descriptions: adding
an explanation must not silently change how the acquisition validates a field.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from typing import Any


METRIC_MAPPING_VERSION = 'm2b-metrics-v1'
PERFORMANCE_PATHS = frozenset({
    'median_output_tokens_per_second',
    'median_time_to_first_token_seconds',
    'median_time_to_first_answer_token',
})
PERFORMANCE_ZERO_NOTICE = '源站记录为 0，测量含义待确认，暂不用于性能优劣判断'
VERIFICATION_DATE = '2026-09-19'
API_REFERENCE = 'https://artificialanalysis.ai/api-reference'
METHODOLOGY = 'https://artificialanalysis.ai/methodology'
INTELLIGENCE_METHODOLOGY = 'https://artificialanalysis.ai/methodology/intelligence-benchmarking'
PERFORMANCE_METHODOLOGY = 'https://artificialanalysis.ai/methodology/performance-benchmarking'
LIVECODEBENCH_REFERENCE = 'https://github.com/LiveCodeBench/LiveCodeBench'


@dataclass(frozen=True)
class Metric:
    path: str
    label: str
    unit: str
    multiplier: float = 1.0
    confirmed: bool = True
    source_name: str = ''
    description: str = '源站报告值；含义和编码量纲待确认。'
    raw_unit: str = '未确认'
    direction: str = 'unknown'
    precision: int = 8
    comparability: str = '源站未提供完整测试版本、评测时间及运行配置；仅展示记录值，不作能力趋势结论。'
    # M2B verification is stricter than the historical display-unit flag above.
    # partial means that concepts are documented but the precise API field
    # definition/encoding remains incomplete. It does not authorize analysis.
    verification_status: str = 'unknown'
    official_sources: tuple[str, ...] = ()
    verified_at: str | None = None
    conversion_formula: str = 'display_value = raw_value'


def _index(key: str, label: str, source_name: str, description: str) -> Metric:
    return Metric(f'evaluations.{key}', label, '指数原值', source_name=source_name,
                  description=description, raw_unit='指数原值', direction='higher')


def _unconfirmed(key: str, source_name: str) -> Metric:
    descriptions = {
        'livecodebench': '源站代码生成任务报告值；方法页说明 pass@1，但 API 编码比例、题目时间窗口及版本未确认。',
        'terminalbench_hard': '源站终端任务 Hard 子集报告值；方法页说明任务测试判定，但 API 编码和本地记录版本未确认。',
        'terminalbench_v2_1': '源站 Terminal-Bench 相关字段；方法页存在 2.1 说明，尚无该 API 键对应版本及编码的直接契约。',
    }
    return Metric(f'evaluations.{key}', f'{source_name} 评测', '原值 · 口径未确认',
                  confirmed=False, source_name=key,
                  description=descriptions.get(key,
                      f'源站 {source_name} 项目的报告值；逐字段定义、单位及方向未充分确认。'))


METRICS = {
    m.path: m for m in [
        _index('artificial_analysis_intelligence_index', '综合智能指数',
               'Artificial Analysis Intelligence Index', '源站综合智能指数报告值；并非百分比或本项目自定义总分。'),
        _index('artificial_analysis_coding_index', '代码指数',
               'Artificial Analysis Coding Index', '源站编程相关指数报告值；不能代表所有实际开发任务表现。'),
        _index('artificial_analysis_math_index', '数学指数',
               'Artificial Analysis Math Index', '源站数学相关指数报告值；不能代表所有实际数学任务表现。'),
        *[_unconfirmed(key, label)
          for key, label in [('mmlu_pro', 'MMLU-Pro'), ('gpqa', 'GPQA'), ('hle', 'HLE'),
                             ('livecodebench', 'LiveCodeBench'), ('scicode', 'SciCode'),
                             ('math_500', 'MATH-500'), ('aime', 'AIME')]],
        Metric('pricing.price_1m_input_tokens', '输入价格', '美元/百万 Token',
               source_name='price_1m_input_tokens', raw_unit='美元/百万 Token', direction='lower',
               description='源站报告的每百万输入 Token 价格；不含输出用量假设，不能单独推导总费用。',
               comparability='仅比较源站公开价格记录；并非公司渠道报价或实际账单，其他计费条件可能不同。'),
        Metric('pricing.price_1m_output_tokens', '输出价格', '美元/百万 Token',
               source_name='price_1m_output_tokens', raw_unit='美元/百万 Token', direction='lower',
               description='源站报告的每百万输出 Token 价格；需与输入价格分开看待。',
               comparability='仅比较源站公开价格记录；并非公司渠道报价或实际账单，其他计费条件可能不同。'),
        Metric('pricing.price_1m_blended_3_to_1', '混合价格（字段名 3:1）', '美元/百万 Token',
               source_name='price_1m_blended_3_to_1', raw_unit='美元/百万 Token', direction='lower',
               description='源站字段名标注 3:1 的混合价格；该字段计算公式尚未直接确认，保留原值。',
               comparability='当前网页另有 7:2:1 混合定义，不能替换此历史字段口径；不重算，不推导总费用或性价比。'),
        Metric('median_output_tokens_per_second', '输出速度', 'Token/秒',
               source_name='median_output_tokens_per_second', raw_unit='Token/秒', direction='higher',
               description='源站报告的输出 Token 速度中位数；不代表公司渠道或自建部署表现。',
               comparability='须同时考虑源站 prompt_options 等速度测试条件；不同配置不能推断真实使用快慢。'),
        Metric('median_time_to_first_token_seconds', '首 Token 延迟', '秒',
               source_name='median_time_to_first_token_seconds', raw_unit='秒', direction='lower',
               description='源站报告的首个 Token 延迟中位数；不等于完整回答耗时。',
               comparability='须同时考虑源站 prompt_options 等延迟测试条件；不代表员工实际网络和渠道延迟。'),
        Metric('median_time_to_first_answer_token', '首回答 Token 延迟', '原值 · 口径未确认',
               confirmed=False, source_name='median_time_to_first_answer_token', direction='unknown',
               description='方法页区分首回答 Token 与首 Token；API 字段的编码单位和方向未直接确认，保留原值。'),
    ]
}

# These fields were observed in the local M0 data but are still unknown fields
# under the original API validation contract. No new source/field is introduced.
DISPLAY_METRICS = {
    **METRICS,
    **{metric.path: metric for metric in (
        _unconfirmed('aime_25', 'AIME 25'),
        _unconfirmed('ifbench', 'IFBench'),
        _unconfirmed('lcr', 'LCR'),
        _unconfirmed('tau2', 'Tau2'),
        _unconfirmed('tau_banking', 'Tau Banking'),
        _unconfirmed('terminalbench_hard', 'TerminalBench Hard'),
        _unconfirmed('terminalbench_v2_1', 'TerminalBench v2.1'),
    )},
}


# Record only checked official definitions. No source data or version/config
# metadata is rewritten, and extra display paths stay outside METRICS.
_VERIFICATION = {
    **{f'evaluations.{key}': ('partial', (API_REFERENCE, INTELLIGENCE_METHODOLOGY))
       for key in ('artificial_analysis_intelligence_index',
                   'artificial_analysis_coding_index', 'artificial_analysis_math_index')},
    'evaluations.livecodebench': (
        'partial', (API_REFERENCE, INTELLIGENCE_METHODOLOGY, LIVECODEBENCH_REFERENCE)),
    'evaluations.terminalbench_hard': ('partial', (API_REFERENCE, INTELLIGENCE_METHODOLOGY)),
    'evaluations.terminalbench_v2_1': ('partial', (API_REFERENCE, INTELLIGENCE_METHODOLOGY)),
    'pricing.price_1m_input_tokens': ('confirmed', (API_REFERENCE, METHODOLOGY)),
    'pricing.price_1m_output_tokens': ('confirmed', (API_REFERENCE, METHODOLOGY)),
    'pricing.price_1m_blended_3_to_1': ('partial', (API_REFERENCE, METHODOLOGY)),
    'median_output_tokens_per_second': ('confirmed', (API_REFERENCE, PERFORMANCE_METHODOLOGY)),
    'median_time_to_first_token_seconds': ('confirmed', (API_REFERENCE, PERFORMANCE_METHODOLOGY)),
    'median_time_to_first_answer_token': ('partial', (API_REFERENCE, PERFORMANCE_METHODOLOGY)),
}
for _path, (_status, _sources) in _VERIFICATION.items():
    _metric = replace(DISPLAY_METRICS[_path], verification_status=_status,
                      official_sources=_sources, verified_at=VERIFICATION_DATE)
    DISPLAY_METRICS[_path] = _metric
    if _path in METRICS:
        METRICS[_path] = _metric


def value_at(record: dict, path: str) -> Any:
    value = record
    for part in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def metric_for(path: str) -> Metric:
    return METRICS.get(path) or DISPLAY_METRICS.get(path) or Metric(
        path, f'未确认指标（{path}）', '原值 · 口径未确认', confirmed=False, source_name=path)


def numeric_value(value: Any) -> Decimal | None:
    """Accept finite numeric types only; never coerce strings or booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    return number if number.is_finite() else None


def is_unconfirmed_performance_zero(value: Any, path: str) -> bool:
    """Flag only exact raw numeric zero in the three performance fields.

    This does not classify the source zero as missing, a placeholder, or a real
    measurement. Evaluation scores and prices retain their separate semantics.
    """
    return path in PERFORMANCE_PATHS and numeric_value(value) == Decimal(0)


def performance_zero_notice(record: dict, path: str) -> str | None:
    return (PERFORMANCE_ZERO_NOTICE
            if is_unconfirmed_performance_zero(value_at(record, path), path) else None)


def _round(number: Decimal, precision: int) -> Decimal:
    with localcontext() as context:
        context.prec = precision
        context.rounding = ROUND_HALF_EVEN
        rounded = +number
        return Decimal(0) if rounded == 0 else rounded


def normalized_value(value: Any, path: str) -> Decimal | None:
    """Return the displayed scale rounded to explicit significant digits.

    This same value drives ties, deltas and display, so binary float noise below
    display precision cannot generate an apparently meaningful change.
    """
    number = numeric_value(value)
    if number is None:
        return None
    metric = metric_for(path)
    with localcontext() as context:
        context.prec = max(40, len(number.as_tuple().digits) + 10)
        context.rounding = ROUND_HALF_EVEN
        scaled = number * Decimal(str(metric.multiplier))
    return _round(scaled, metric.precision)


def _decimal_text(number: Decimal, precision: int = 8, grouped: bool = False) -> str:
    number = _round(number, precision)
    if number == 0:
        return '0'
    if -4 <= number.adjusted() < precision:
        result = format(number, ',f' if grouped else 'f')
        return result.rstrip('0').rstrip('.') if '.' in result else result
    with localcontext() as context:
        context.prec = precision
        return str(number.normalize()).lower()


def numeric_delta(before: Any, after: Any, path: str) -> dict:
    """Keep original values; serialize deterministic display-scale differences."""
    metric = metric_for(path)
    if not metric.confirmed:
        delta_unit = '原值差（单位未确认）'
    elif metric.multiplier == 100 and ('%' in metric.unit or '百分比' in metric.unit or '百分率' in metric.unit):
        delta_unit = '百分点'
    elif metric.unit == '指数原值':
        delta_unit = '指数点'
    else:
        delta_unit = metric.unit
    result = {'before': before, 'after': after, 'absolute': None,
              'relative_percent': None, 'delta_unit': delta_unit}
    if (is_unconfirmed_performance_zero(before, path)
            or is_unconfirmed_performance_zero(after, path)):
        return result
    left, right = normalized_value(before, path), normalized_value(after, path)
    if left is None or right is None:
        return result
    with localcontext() as context:
        context.prec = 40
        context.rounding = ROUND_HALF_EVEN
        difference = right - left
        result['absolute'] = _decimal_text(difference, metric.precision)
        if left != 0:
            result['relative_percent'] = _decimal_text(difference / abs(left) * 100, metric.precision)
    return result


def coverage_rows(records: list[dict], paths: list[str]) -> list[dict]:
    """Count finite numeric values, including zero; not reliable measurements."""
    total = len(records)
    result = []
    for path in paths:
        valid_count = sum(numeric_value(value_at(record, path)) is not None for record in records)
        result.append({'path': path, 'label': metric_for(path).label, 'valid_count': valid_count,
                       'total': total, 'missing_count': total - valid_count,
                       'coverage': valid_count / total if total else 0.0})
    return result


def format_value(record: dict, path: str) -> str:
    number = normalized_value(value_at(record, path), path)
    if number is None:
        return '暂无'
    return _decimal_text(number, metric_for(path).precision, grouped=True)


def format_raw_value(value: Any, path: str) -> str:
    """Format a raw source number without applying any uncertain unit conversion."""
    number = numeric_value(value)
    return '暂无' if number is None else _decimal_text(number, metric_for(path).precision, grouped=True)
