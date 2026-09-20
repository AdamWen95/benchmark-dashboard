import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from benchmark_dashboard import acquisition
from benchmark_dashboard.validation import DataValidationError


def test_snapshot_metadata_and_order_dedup(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(acquisition, 'load_api_key', lambda root: 'synthetic-key')
    monkeypatch.setattr(acquisition, 'fetch_models', lambda key, **kwargs: (payload, 200, 1))
    first = acquisition.acquire(tmp_path)
    second = acquisition.acquire(tmp_path)
    assert first.snapshot_path == second.snapshot_path
    assert len(list((tmp_path / 'data' / 'snapshots').glob('*.json'))) == 1
    envelope = json.loads(first.snapshot_path.read_text(encoding='utf-8'))
    assert envelope['endpoint'] == acquisition.API_URL
    assert envelope['payload'] == payload
    assert envelope['content_hash'] == first.valid.content_hash
    assert 'synthetic-key' not in first.snapshot_path.read_text(encoding='utf-8')


def test_bad_data_preserves_snapshot_and_last_check(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(acquisition, 'load_api_key', lambda root: 'synthetic-key')
    fetch = Mock(return_value=(payload, 200, 1))
    monkeypatch.setattr(acquisition, 'fetch_models', fetch)
    good = acquisition.acquire(tmp_path)
    acquisition.save_last_check(tmp_path, good)
    previous = (tmp_path / 'data' / 'last_check.json').read_bytes()
    fetch.return_value = ({'data': []}, 200, 1)
    with pytest.raises(DataValidationError) as error:
        acquisition.acquire(tmp_path)
    assert error.value.http_status == 200
    assert error.value.attempts == 1
    assert good.snapshot_path.exists()
    assert (tmp_path / 'data' / 'last_check.json').read_bytes() == previous
    assert len(list(good.snapshot_path.parent.glob('*.json'))) == 1


def test_atomic_failure_preserves_target(tmp_path, monkeypatch):
    target = tmp_path / 'valid.json'
    target.write_text('old', encoding='utf-8')
    def refuse(*args):
        raise OSError('synthetic disk error')
    monkeypatch.setattr(acquisition.os, 'replace', refuse)
    with pytest.raises(OSError):
        acquisition.atomic_write(target, 'new')
    assert target.read_text() == 'old'
    assert not list(tmp_path.glob('.pending-*'))
