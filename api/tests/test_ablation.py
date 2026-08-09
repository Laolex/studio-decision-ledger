"""The ablation surface: what this decision is worth without its binding.

Every system claims its audit trail is meaningful. This one can demonstrate the
counterfactual — withhold the pinned evidence and watch the same verifier that
just certified the decision refuse to certify it.

The demonstration must not alter anything. An ablation that mutated the record
to prove a point would be the exact failure it exists to warn about.
"""

import pytest
from fastapi.testclient import TestClient

from sdl.app import create_app, get_executor, get_writer
from sdl.ledger import read_decision

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"


@pytest.fixture
def client(http_executor, writer):
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: http_executor
    app.dependency_overrides[get_writer] = lambda: writer
    return TestClient(app)


@pytest.fixture
def decision_id(client):
    response = client.post(
        "/api/decisions",
        json={
            "title_id": TITLE,
            "territory_code": TERRITORY,
            "effective_at": "2026-07-30T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["decision_id"]


def test_ablation_shows_certified_and_unbound_side_by_side(client, decision_id):
    body = client.post(f"/api/decisions/{decision_id}/ablate").json()

    assert body["with_binding"]["capability_class"] in {"C2", "C3_BOUNDARY"}
    assert body["without_binding"]["capability_class"] == "NOT_CERTIFIED"


def test_ablation_names_what_was_withheld(client, decision_id):
    body = client.post(f"/api/decisions/{decision_id}/ablate").json()

    assert "snapshot" in body["without_binding"]["failed_requirement"].lower()
    assert body["withheld"] == "snapshot binding"


def test_ablation_leaves_the_record_untouched(client, decision_id, http_executor):
    before = read_decision(http_executor, decision_id)

    client.post(f"/api/decisions/{decision_id}/ablate")

    assert read_decision(http_executor, decision_id) == before


def test_ablating_an_unknown_decision_returns_404(client):
    assert client.post("/api/decisions/D-NOPE/ablate").status_code == 404
