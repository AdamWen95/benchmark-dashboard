"""Deterministic change records from two local successful acquisition runs.

This module performs no I/O. Every comparison consumes the same read transaction
as the dashboard. A changed reported value is never called a change in ability.
"""
from __future__ import annotations

from .comparability import assess_metric
from .metrics import METRICS, normalized_value, numeric_delta, value_at


EVENT_TYPES = ('added', 'removed', 'metadata', 'value', 'filled', 'missing', 'context')
EVENT_LABELS = {'added': '新增记录', 'removed': '本次未返回', 'metadata': '元数据更新',
                'value': '已有数值变化', 'filled': '数据补齐', 'missing': '数据转缺失',
                'context': '口径变化/待确认'}


def _event(kind: str, source: str, record: dict, *, path: str = '', before=None,
           after=None, comparability: str = 'unconfirmed', reason: str = '') -> dict:
    return {'type': kind, 'source': source, 'model_id': record['id'],
            'name': record.get('name', record['id']), 'path': path,
            'before': before, 'after': after, 'absolute': None,
            'relative_percent': None, 'delta_unit': None,
            'comparability': comparability, 'reason': reason}


def _index(point: dict) -> dict:
    source = point['snapshot']['source']
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Missing source identity')
    indexed = {}
    for record in point['records']:
        model_id = record.get('id')
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError('Missing stable model identity')
        key = source, model_id
        if key in indexed:
            raise ValueError('Duplicate model identity')
        indexed[key] = record
    return indexed


def compare_runs(comparison: dict) -> dict:
    """Classify source-record changes without collecting or persisting anything."""
    result = {'status': comparison.get('status', 'unavailable'),
              'message': comparison.get('message', '缺少比较依据'), 'events': [],
              'counts': dict.fromkeys(EVENT_TYPES, 0),
              'before': comparison.get('before'), 'after': comparison.get('after')}
    if result['status'] != 'ready':
        return result
    before, after = result['before'], result['after']
    try:
        old, new = _index(before), _index(after)
        old_snapshot, new_snapshot = before['snapshot'], after['snapshot']
        events = []
        for key in sorted(old.keys() - new.keys()):
            events.append(_event('removed', key[0], old[key], before=old[key].get('name'),
                                 reason='本次采集未返回该 ID；不等于模型停服、下架或淘汰。'))
        for key in sorted(new.keys() - old.keys()):
            events.append(_event('added', key[0], new[key], after=new[key].get('name'),
                                 reason='相较上次成功采集新增该 ID；不等于模型今天发布。'))
        paths = sorted(set(METRICS) | set(old_snapshot.get('coverage', {}))
                       | set(new_snapshot.get('coverage', {})))
        metric_roots = {path.split('.')[0] for path in paths}
        for key in sorted(old.keys() & new.keys()):
            previous, current = old[key], new[key]
            # Unknown non-metric record metadata remains an ordinary metadata
            # event. It cannot create a score change or establish a new ID.
            metadata_keys = sorted((set(previous) | set(current)) - metric_roots - {'id'})
            for field in metadata_keys:
                if previous.get(field) != current.get(field):
                    events.append(_event('metadata', key[0], current, path=field,
                                         before=previous.get(field), after=current.get(field),
                                         reason='同一来源与模型 ID 的元数据更新；不算新增模型。'))
            for path in paths:
                raw_before, raw_after = value_at(previous, path), value_at(current, path)
                number_before = normalized_value(raw_before, path)
                number_after = normalized_value(raw_after, path)
                if number_before is None and number_after is None:
                    continue
                assessment = assess_metric(path, [previous, current], [old_snapshot, new_snapshot])
                common = dict(path=path, before=raw_before, after=raw_after,
                              comparability=assessment['status'], reason=assessment['reason'])
                if assessment['status'] == 'changed':
                    events.append(_event('context', key[0], current, **common))
                if number_before is None:
                    common['reason'] += ' 数据补齐，不表示成绩从 0 提升。'
                    events.append(_event('filled', key[0], current, **common))
                elif number_after is None:
                    common['reason'] += ' 数据转缺失，不表示成绩跌为 0。'
                    events.append(_event('missing', key[0], current, **common))
                elif number_before != number_after and assessment['status'] != 'changed':
                    event = _event('value', key[0], current, **common)
                    if assessment['status'] == 'comparable':
                        delta = numeric_delta(raw_before, raw_after, path)
                        for field in ('absolute', 'relative_percent', 'delta_unit'):
                            event[field] = delta[field]
                    events.append(event)
        result['events'] = events
        for event in events:
            result['counts'][event['type']] += 1
        if not events:
            result['message'] = '与上次成功采集相比无数据变化'
        else:
            summary = '；'.join(f'{EVENT_LABELS[key]} {count} 项'
                               for key, count in result['counts'].items() if count)
            result['message'] = '最近两次成功采集的记录比较：' + summary + '。这些变化不等于模型能力变化。'
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        # Bad inputs cannot result in guessed history or partly rendered deltas.
        result.update(status='unavailable', message='缺少比较依据：无法可靠比较成功采集关联的数据',
                      events=[], counts=dict.fromkeys(EVENT_TYPES, 0))
        return result
