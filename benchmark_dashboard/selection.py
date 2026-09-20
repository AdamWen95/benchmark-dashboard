"""Local selection and alias mapping; opaque stable IDs are never truncated."""
from hashlib import sha256
import json

from . import metrics
from .fact_pack import EXAMPLE_PATHS


SELECTION_ERROR = '请选择当前快照中 2–4 条不同的有效模型记录。'
SOURCE = 'artificial_analysis'


def _index(state: dict) -> dict:
    if not isinstance(state, dict) or not isinstance(state.get('records'), list):
        raise ValueError(SELECTION_ERROR)
    result = {}
    for record in state['records']:
        model_id = record.get('id') if isinstance(record, dict) else None
        if not isinstance(model_id, str) or not model_id.strip() or model_id in result:
            raise ValueError(SELECTION_ERROR)
        result[model_id] = record
    return result


def validate_selection(state: dict, ids: list[str]) -> list[str]:
    """Return a fresh list in UI/CLI order, requiring 2–4 unique current IDs."""
    indexed = _index(state)
    if (not isinstance(ids, list) or not 2 <= len(ids) <= 4
            or any(not isinstance(model_id, str) or model_id not in indexed for model_id in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError(SELECTION_ERROR)
    return list(ids)


def _different_creators(first: dict, second: dict) -> bool:
    left, right = first.get('model_creator'), second.get('model_creator')
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for key in ('id', 'name'):
        a, b = left.get(key), right.get(key)
        if isinstance(a, str) and a.strip() and isinstance(b, str) and b.strip():
            return a != b
    return False


def choose_acceptance_ids(state: dict) -> list[str]:
    """Pick coverage-rich technical examples, preferring two known creators.

    Values' magnitude never affects the choice. This is neither a best-model
    selection nor a market ranking. Missing creator metadata proves no diversity.
    """
    indexed = _index(state)
    if len(indexed) < 2:
        raise ValueError(SELECTION_ERROR)
    keys = [path for path in EXAMPLE_PATHS if path in metrics.DISPLAY_METRICS]
    ordered = sorted(indexed, key=lambda model_id: (
        -sum(metrics.numeric_value(metrics.value_at(indexed[model_id], path)) is not None for path in keys),
        model_id))
    first = ordered[0]
    different = [model_id for model_id in ordered[1:]
                 if _different_creators(indexed[first], indexed[model_id])]
    return [first, different[0] if different else ordered[1]]


def selection_hash(state: dict, ids: list[str]) -> str:
    chosen = validate_selection(state, ids)
    source = (state.get('snapshot') or {}).get('source')
    if source != SOURCE:
        raise ValueError(SELECTION_ERROR)
    content = json.dumps({'source': source, 'ids': chosen}, ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
    return sha256(content.encode('utf-8')).hexdigest()


def local_model_mapping(state: dict, ids: list[str]) -> list[dict]:
    """Local-only identity map; callers must not put it in a network fact pack."""
    chosen = validate_selection(state, ids)
    indexed = _index(state)
    source = (state.get('snapshot') or {}).get('source')
    if source != SOURCE:
        raise ValueError(SELECTION_ERROR)
    result = []
    for index, model_id in enumerate(chosen, start=1):
        record = indexed[model_id]
        creator = record.get('model_creator')
        creator = creator if isinstance(creator, dict) else {}
        result.append({'alias': f'R{index}', 'id': model_id, 'model_id': model_id, 'source': source,
                       'name': record.get('name') or '源站未提供',
                       'creator': creator.get('name') or '源站未提供'})
    return result
