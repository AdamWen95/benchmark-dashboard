"""Versioned Free API boundary. Legacy URLs are for persisted history only."""
from copy import deepcopy
import hashlib
import json
import math

API_URL = 'https://artificialanalysis.ai/api/v2/language/models/free'
LEGACY_URL = 'https://artificialanalysis.ai/api/v2/data/llms/models'
CONTRACT = 'aa-language-free-v1'
MAX_REQUESTS = 10
NOTICE = ('当前为免费版数据：提供综合智能指数、行业能力指数、输入/输出价格及性能中位数；'
          '不提供完整单项评测和混合价格。代码指数已停止发布；历史值不作为当前值补齐。')
BASELINE_NOTICE = '数据接口或智能指数版本已变化，本次建立新比较基线；历史数据保留，不计算跨口径增减。'


class ContractError(ValueError):
    def __init__(self, code='invalid_schema'):
        self.code = code
        super().__init__('免费接口分页或数据契约校验失败；未发布部分数据。')


def is_free(payload: dict) -> bool:
    return payload.get('api_contract') == CONTRACT


def snapshot_is_free(snapshot: dict | None) -> bool:
    return is_free((snapshot or {}).get('metadata') or {})


def validate_pages(pages: list[dict], *, complete: bool) -> None:
    if not isinstance(pages, list) or not pages or len(pages) > MAX_REQUESTS:
        raise ContractError()
    seen = set()
    context = None
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            raise ContractError()
        tier, version, pagination = page.get('tier'), page.get('intelligence_index_version'), page.get('pagination')
        if (tier not in ('free', 'pro', 'commercial') or type(version) not in (int, float)
                or not math.isfinite(version) or version <= 0 or not isinstance(pagination, dict)):
            raise ContractError()
        if any(type(pagination.get(key)) is not int or pagination[key] < 1
               for key in ('page', 'page_size', 'total_pages')):
            raise ContractError()
        total, size = pagination['total_pages'], pagination['page_size']
        if total > MAX_REQUESTS:
            raise ContractError('request_budget_exceeded')
        if (pagination['page'] != number or number > total or size > 1000
                or type(pagination.get('has_more')) is not bool
                or pagination['has_more'] != (number < total)):
            raise ContractError()
        current = (tier, version, total, size)
        if context is not None and current != context:
            raise ContractError()
        context = current
        rows = page.get('data')
        if (not isinstance(rows, list) or not rows or len(rows) > size
                or (number < total and len(rows) != size)):
            raise ContractError()
        for row in rows:
            if not isinstance(row, dict):
                raise ContractError()
            identity = row.get('id')
            if not isinstance(identity, str) or not identity.strip() or identity in seen:
                raise ContractError()
            seen.add(identity)
    if complete and len(pages) != context[2]:
        raise ContractError()


def merge_pages(pages: list[dict]) -> dict:
    validate_pages(pages, complete=True)
    return {'api_contract': CONTRACT, 'tier': pages[0]['tier'],
            'intelligence_index_version': pages[0]['intelligence_index_version'],
            'api_pages': deepcopy(pages),
            'data': deepcopy([row for page in pages for row in page['data']])}


def validate_bundle(payload: dict) -> None:
    pages = payload.get('api_pages')
    expected = merge_pages(pages)
    if payload != expected:
        raise ContractError()


def comparison_context(snapshot: dict | None) -> tuple:
    meta = (snapshot or {}).get('metadata') or {}
    return (meta.get('api_contract', 'legacy'), meta.get('intelligence_index_version'))


def is_transition(before: dict | None, after: dict | None) -> bool:
    return bool(before and after and comparison_context(before) != comparison_context(after)
                and (snapshot_is_free(before) or snapshot_is_free(after)))


def model_content_hash(records: list[dict]) -> str:
    """Semantic comparison only; persisted raw snapshot hashes stay unchanged."""
    content = json.dumps(sorted(records, key=lambda row: row['id']), ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
