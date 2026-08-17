"""API tests — the contract the console consumes.

The executor and writer are injected so tests drive the real ClickHouse service
without paying MCP subprocess startup on every request. Production wires the
MCP executor; one test in test_mcp_executor.py holds the two paths to identical
evidence hashes, which is what makes that substitution safe.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from sdl.app import create_app, get_executor, get_writer

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"


@pytest.fixture
def client(http_executor, writer):
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: http_executor
    app.dependency_overrides[get_writer] = lambda: writer
    return TestClient(app)


def make_decision(client, effective_at: str = "2026-07-30T00:00:00Z"):
    response = client.post(
        "/api/decisions",
        json={
            "title_id": TITLE,
            "territory_code": TERRITORY,
            "effective_at": effective_at,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_recording_a_decision_returns_the_outcome_and_a_binding(client):
    body = make_decision(client)

    assert body["outcome"] in {"AVAILABLE", "HOLD", "ESCALATE"}
    assert body["decision_id"].startswith("D-")
    assert body["snapshot_id"].startswith("RS-")
    assert body["policy_revision"] == "POL-2026.08"


def test_a_decision_made_now_sees_the_backdated_correction(client):
    """Pinned to current data, 30 July no longer clears: revision 3 restated the
    grant as AVOD-only, backdated to commencement."""
    body = make_decision(client, "2026-07-30T00:00:00Z")

    assert body["outcome"] == "HOLD"
    assert body["rule_hits"] == ["LIC-002"]
    assert body["max_revision"] == 3


def test_evidence_is_grouped_for_the_console(client):
    body = make_decision(client)

    labels = [group["label"] for group in body["evidence_groups"]]
    assert labels == ["Rights & clearances", "Delivery & continuity", "Release policy"]
    for group in body["evidence_groups"]:
        assert group["tone"] in {"clear", "hold"}
        assert group["items"], "a group with no items tells an operator nothing"


def test_a_blocking_condition_is_named_in_plain_language(client):
    body = make_decision(client)

    assert body["blocking_condition"]
    assert "LIC-002" not in body["blocking_condition"], "rule ids are not an explanation"


def test_a_recorded_decision_can_be_fetched_again(client):
    created = make_decision(client)

    fetched = client.get(f"/api/decisions/{created['decision_id']}")

    assert fetched.status_code == 200
    assert fetched.json()["outcome"] == created["outcome"]
    assert fetched.json()["snapshot_id"] == created["snapshot_id"]


def test_unknown_decision_returns_404(client):
    assert client.get("/api/decisions/D-NOPE").status_code == 404


def test_replaying_a_freshly_recorded_decision_certifies(client):
    created = make_decision(client)

    result = client.post(f"/api/decisions/{created['decision_id']}/verify")

    assert result.status_code == 200
    assert result.json()["capability_class"] in {"C2", "C3_BOUNDARY"}
    assert result.json()["failed_requirement"] == ""


def test_comparison_separates_the_record_from_current_state(client):
    created = make_decision(client)

    comparison = client.get(f"/api/decisions/{created['decision_id']}/compare").json()

    assert comparison["historical"]["outcome"] == created["outcome"]
    assert comparison["historical"]["max_revision"] == created["max_revision"]
    assert "current" in comparison
    assert comparison["record_unchanged"] is True


def test_the_evidence_endpoint_answers_without_recording(client, http_executor):
    """What the operator-facing agent reaches.

    The gate must match what `POST /api/decisions` would record, and the
    ledger must be untouched — an agent answering a question is not a
    decision anyone has taken.
    """
    before = http_executor("SELECT count() AS n FROM sdl.decision_records")[0]["n"]

    response = client.post(
        "/api/evidence",
        json={
            "title_id": TITLE,
            "territory_code": TERRITORY,
            "effective_at": "2026-07-30T00:00:00Z",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["outcome"] == "HOLD"
    assert body["rule_hits"] == ["LIC-002"]
    assert body["blocking_condition"]
    assert body["recorded"] is False
    assert "decision_id" not in body
    assert "snapshot_id" not in body

    after = http_executor("SELECT count() AS n FROM sdl.decision_records")[0]["n"]
    assert after == before
