"""Strict known-field validation; unknown source data is retained unchanged."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from .metrics import METRICS, FREE_METRICS, value_at
from .free_api import ContractError, is_free, validate_bundle


class DataValidationError(Exception):
    """Messages contain schema locations only, never response values."""


@dataclass
class ValidatedData:
    payload: dict
    records: list[dict]
    content_hash: str
    fields: dict[str, int]
    top_fields: list[str]
    coverage: dict[str, int]
    unknown_fields: list[str]
    warnings: list[str]


def is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def content_hash(payload: dict) -> str:
    # API record order is not a new data version. All metadata and values remain part of the hash.
    ordered = dict(payload, data=sorted(payload['data'], key=lambda row: row['id']))
    return hashlib.sha256(canonical_json(ordered).encode('utf-8')).hexdigest()


def validate_payload(payload: Any) -> ValidatedData:
    if not isinstance(payload, dict):
        raise DataValidationError('响应根节点必须为对象。')
    free = is_free(payload)
    if 'api_contract' in payload and not free:
        raise DataValidationError('不支持的数据契约版本。')
    if free:
        try:
            validate_bundle(payload)
        except (ContractError, TypeError, KeyError):
            raise DataValidationError('免费接口完整分页与汇总记录不一致。') from None
    known_metrics = FREE_METRICS if free else METRICS
    if 'status' in payload and (type(payload['status']) is not int or payload['status'] != 200):
        raise DataValidationError('响应内部 status 不是成功状态。')
    rows = payload.get('data')
    if not isinstance(rows, list) or not rows:
        raise DataValidationError('data 必须为非空模型列表。')
    if payload.get('prompt_options') is not None and not isinstance(payload['prompt_options'], dict):
        raise DataValidationError('prompt_options 必须为对象或空值。')
    fields: Counter = Counter()
    unknown: set[str] = set()
    warnings: set[str] = set()
    paths = set(known_metrics)
    seen: set[str] = set()
    known_top = {'id', 'name', 'slug', 'model_creator', 'evaluations', 'pricing',
                 'median_output_tokens_per_second', 'median_time_to_first_token_seconds',
                 'median_time_to_first_answer_token'}
    if free:
        known_top.update(('performance', 'release_date', 'artificial_analysis_intelligence_index_cost'))

    def inventory(obj: dict, prefix: str = '') -> None:
        for key, value in obj.items():
            path = f'{prefix}.{key}' if prefix else key
            fields[path] += 1
            if isinstance(value, dict):
                inventory(value, path)

    for index, row in enumerate(rows):
        location = f'data[{index}]'
        if not isinstance(row, dict):
            raise DataValidationError(f'{location} 必须为对象。')
        for key in ('id', 'name'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise DataValidationError(f'{location}.{key} 必须为非空字符串。')
        if row['id'] in seen:
            raise DataValidationError(f'{location}.id 重复；拒绝整批数据。')
        seen.add(row['id'])
        if row.get('slug') is not None and not isinstance(row['slug'], str):
            raise DataValidationError(f'{location}.slug 类型错误。')
        for key in ('model_creator', 'evaluations', 'pricing', *(['performance'] if free else [])):
            if free and not isinstance(row.get(key), dict):
                raise DataValidationError(f'{location}.{key} 必须为对象。')
            if row.get(key) is not None and not isinstance(row[key], dict):
                raise DataValidationError(f'{location}.{key} 必须为对象或空值。')
        for key, value in (row.get('model_creator') or {}).items():
            if key in {'id', 'name', 'slug'} and value is not None and not isinstance(value, str):
                raise DataValidationError(f'{location}.model_creator 标识字段类型错误。')
            if key not in {'id', 'name', 'slug'}:
                unknown.add(f'model_creator.{key}')
        for path in known_metrics:
            value = value_at(row, path)
            if value is not None and not is_number(value):
                raise DataValidationError(f'{location}.{path} 必须为有限数值或空值。')
            if value is not None and path.startswith(('pricing.', 'median_', 'performance.')) and value < 0:
                raise DataValidationError(f'{location}.{path} 不能为负数。')
        for group in ('evaluations', 'pricing', *(['performance'] if free else [])):
            for key, value in (row.get(group) or {}).items():
                path = f'{group}.{key}'
                if path not in known_metrics:
                    unknown.add(path)
                    if not free and (value is None or is_number(value)):
                        paths.add(path)
                    else:
                        warnings.add(f'{path} 为未知非数值字段；仅保留原始值，不参与数值展示。')
        unknown.update(key for key in row if key not in known_top)
        inventory(row)
    # Do not display mixed-type unknown columns as a metric.
    for path in tuple(paths - set(known_metrics)):
        if any(value_at(row, path) is not None and not is_number(value_at(row, path)) for row in rows):
            paths.remove(path)
    try:
        digest = content_hash(payload)
    except (ValueError, TypeError, OverflowError):
        raise DataValidationError('响应包含无法保存为标准 JSON 的值。') from None
    top_fields = {'api_contract', 'api_pages', 'tier', 'intelligence_index_version', 'data'} if free else {'status', 'data', 'prompt_options'}
    unknown.update(f'response.{key}' for key in payload if key not in top_fields)
    return ValidatedData(payload, rows, digest, dict(sorted(fields.items())), sorted(payload),
                         {path: sum(value_at(row, path) is not None for row in rows) for path in sorted(paths)},
                         sorted(unknown), sorted(warnings))
