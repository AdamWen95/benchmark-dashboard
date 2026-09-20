"""Historical reads preserve immutable envelopes; fixtures and paths are isolated."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path

import pytest

from benchmark_dashboard import briefing, m2c_run
from tests.test_m2c_run import Client, NOW, ids, run, settings, state


def historical_fixture(state, settings, root):
    generated = run(state, settings, Client(), root)
    assert generated['status'] == 'generated'
    artifact = deepcopy(generated['artifact'])
    pack = artifact['fact_pack']
    pack['rule_version'] = 'm2a-rules-v1'
    pack['scope']['pack_version'] = 'm2c-selected-v1'
    pack['fact_hash'] = sha256(briefing._canonical({key: value for key, value in pack.items()
                                                   if key != 'fact_hash'}).encode()).hexdigest()
    artifact['result']['fact_hash'] = pack['fact_hash']
    artifact['authorization_scope'] = m2c_run._scope(pack, artifact['selected_ids'])
    artifact['versions'] = dict(m2c_run.HISTORICAL_VERSIONS)
    artifact['fact_pack_bytes'] = len(briefing._canonical(pack).encode())
    path = Path(generated['artifact_path'])
    path.write_text(briefing._canonical(artifact), encoding='utf-8')
    return artifact, path


@pytest.mark.parametrize('age', [timedelta(), timedelta(days=2)])
def test_original_known_versions_remain_history_without_relabel_expiry_or_writes(state, settings, tmp_path, monkeypatch, age):
    artifact, path = historical_fixture(state, settings, tmp_path)
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    def forbidden(*args, **kwargs):
        pytest.fail('history must not generate, configure or write')
    monkeypatch.setattr(briefing, 'generate_briefing', forbidden)
    monkeypatch.setattr(m2c_run, 'scoped_settings', forbidden)
    monkeypatch.setattr(m2c_run, '_atomic_write', forbidden)
    view = m2c_run.read_historical_artifact(root=tmp_path, now=NOW + age)
    assert view['status'] == 'historical'
    assert view['result'] == artifact['result']
    assert view['artifact']['versions'] == artifact['versions']
    assert view['generated_at'] == artifact['generation']['generated_at']
    assert view['expires_at'] == artifact['generation']['expires_at']
    assert view['artifact']['generation']['human_review_status'] == 'pending'
    assert view['result_stale'] == bool(age)
    assert m2c_run.get_default_saved_ids(tmp_path) == ids(state)
    current = m2c_run.read_current_artifact(state, ids(state), root=tmp_path, now=NOW + age)
    assert current['status'] == 'mismatch' and current['result'] is None
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before


@pytest.mark.parametrize('change', ['version', 'claim_reference', 'value_hash', 'expiry', 'approval'])
def test_history_still_rejects_corruption_and_unknown_version(state, settings, tmp_path, change):
    artifact, path = historical_fixture(state, settings, tmp_path)
    if change == 'version': artifact['versions']['prompt_version'] = 'unrecognized-version'
    elif change == 'claim_reference': artifact['result']['sections'][0]['claims'][0]['fact_ids'] = ['missing']
    elif change == 'value_hash': artifact['fact_pack']['facts'][0]['text'] += '改动'
    elif change == 'expiry': artifact['generation']['expires_at'] = (NOW + timedelta(days=2)).isoformat()
    else: artifact['generation']['human_review_status'] = 'approved'
    path.write_text(json.dumps(artifact), encoding='utf-8')
    result = m2c_run.read_historical_artifact(root=tmp_path, now=NOW)
    assert result['status'] == 'failed' and result['result'] is None


def test_old_attempt_survives_version_change_without_reopening_request(state, settings, tmp_path):
    artifact, path = historical_fixture(state, settings, tmp_path)
    attempt = path.with_name(m2c_run.SCOPE_ID + '.attempt.json')
    before = attempt.read_bytes()
    client = Client()
    view = run(state, settings, client, tmp_path)
    assert view['status'] == 'already_attempted' and client.calls == 0
    assert attempt.read_bytes() == before
    assert json.loads(path.read_text(encoding='utf-8')) == artifact
