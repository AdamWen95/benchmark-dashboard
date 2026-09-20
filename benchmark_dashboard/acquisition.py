"""Manual acquisition only. The UI never imports this module."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .client import API_URL, fetch_models
from .config import load_api_key
from .validation import DataValidationError, ValidatedData, canonical_json, validate_payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class Acquisition:
    valid: ValidatedData
    started_at: str
    collected_at: str
    http_status: int
    attempts: int
    snapshot_path: Path


def acquire(root: Path, *, started_at: str | None = None, max_attempts: int = 3,
            api_key: str | None = None) -> Acquisition:
    started_at = started_at or utc_now()
    key = load_api_key(root) if api_key is None else api_key
    payload, http_status, attempts = fetch_models(key, max_attempts=max_attempts)
    del key
    try:
        valid = validate_payload(payload)
    except DataValidationError as error:
        error.http_status = http_status
        error.attempts = attempts
        raise
    collected_at = utc_now()
    path = root / 'data' / 'snapshots' / f'{valid.content_hash}.json'
    if not path.exists():
        envelope = {'source': 'artificial_analysis', 'endpoint': API_URL,
                    'collected_at': collected_at, 'content_hash': valid.content_hash,
                    'hash_definition': 'SHA-256 canonical JSON, data sorted by stable ID',
                    'payload': payload}
        atomic_write(path, json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return Acquisition(valid, started_at, collected_at, http_status, attempts, path)


def save_last_check(root: Path, acquisition: Acquisition) -> None:
    atomic_write(root / 'data' / 'last_check.json', canonical_json({
        'started_at': acquisition.started_at, 'collected_at': acquisition.collected_at,
        'http_status': acquisition.http_status, 'attempts': acquisition.attempts,
        'content_hash': acquisition.valid.content_hash,
        'snapshot_file': acquisition.snapshot_path.name,
    }) + '\n')
