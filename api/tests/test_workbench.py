"""Workbench tests read real evidence but capture every write in memory."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from sdl.app import create_app, get_executor, get_writer, get_rationale_model
from sdl.ledger import list_decisions
from sdl.service import preview_decision, preview_token

REQUEST = {
    "title_id": "NORTHSTAR-S01E06", "territory_code": "NG",
    "effective_at": "2026-07-30T00:00:00Z",
}


@pytest.fixture
def workbench(http_executor):
    writes = []
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: http_executor
    app.dependency_overrides[get_writer] = lambda: writes.append
    app.dependency_overrides[get_rationale_model] = lambda: None
    return TestClient(app), writes


def test_catalogue_and_history_are_read_only(workbench):
    client, writes = workbench
    catalogue = client.get('/api/catalogue')
    assert catalogue.status_code == 200
    assert any(row['title_id'] == REQUEST['title_id'] for row in catalogue.json()['releases'])
    history = client.get('/api/decisions', params={'title_id': REQUEST['title_id'], 'limit': 3})
    assert history.status_code == 200
    assert 0 < len(history.json()['decisions']) <= 3
    assert all(row['title_id'] == REQUEST['title_id'] for row in history.json()['decisions'])
    assert writes == []


def test_preview_then_record_persists_a_link_without_changing_the_prior(workbench, http_executor):
    client, writes = workbench
    prior = client.get('/api/decisions/D-1846').json()
    preview = client.post('/api/evidence', json=REQUEST)
    assert preview.status_code == 200
    assert preview.json()['recorded'] is False
    assert writes == []
    response = client.post('/api/decisions', json={
        **REQUEST, 'supersedes': 'D-1846', 'expected_preview_token': preview.json()['preview_token'],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['supersedes'] == 'D-1846'
    assert body['outcome'] == preview.json()['outcome']
    assert body['decision_id'] != 'D-1846'
    assert len(writes) == 2
    assert all(sql.startswith('INSERT INTO sdl.decision_') for sql in writes)
    assert "'D-1846'" in writes[1]
    assert client.get('/api/decisions/D-1846').json() == prior


def test_stale_preview_is_rejected_before_any_write(workbench):
    client, writes = workbench
    response = client.post('/api/decisions', json={**REQUEST, 'expected_preview_token': '0' * 64})
    assert response.status_code == 409
    assert 'Preview again' in response.json()['detail']
    assert writes == []


@pytest.mark.parametrize('changes', [
    {'title_id': 'OTHER'}, {'territory_code': 'US'},
    {'effective_at': '2026-07-31T00:00:00Z'}, {'supersedes': 'D-NONEXISTENT'},
])
def test_follow_up_cannot_link_an_unrelated_or_missing_receipt(workbench, changes):
    client, writes = workbench
    response = client.post('/api/decisions', json={**REQUEST, 'supersedes': 'D-1846', **changes})
    assert response.status_code == 409
    assert writes == []


def test_preview_fingerprint_binds_request_policy_and_evidence(http_executor):
    request = {**REQUEST, 'effective_at': datetime(2026, 7, 30, tzinfo=timezone.utc)}
    preview = preview_decision(http_executor, **request)
    original = preview_token(preview, **request)
    assert preview_token(preview, **request) == original
    assert preview_token(preview, **{**request, 'effective_at': datetime(2026, 7, 31, tzinfo=timezone.utc)}) != original
    from dataclasses import replace
    assert preview_token(replace(preview, policy_sha256='different'), **request) != original
    changed = deepcopy(preview)
    changed.evidence[0] = replace(changed.evidence[0], result_hash='f' * 64)
    assert preview_token(changed, **request) != original


def test_history_bounds_and_sql_escaping():
    calls = []
    list_decisions(lambda sql: calls.append(sql) or [], title_id="title' OR 1=1 --", limit=2)
    assert "title\\' OR 1=1 --" in calls[0]
    assert calls[0].endswith('LIMIT 2')
    for limit in (0, 101):
        with pytest.raises(ValueError):
            list_decisions(lambda sql: pytest.fail('must not query'), limit=limit)


def test_invalid_history_limit_is_rejected(workbench):
    client, writes = workbench
    assert client.get('/api/decisions?limit=101').status_code == 422
    assert writes == []
