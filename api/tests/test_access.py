"""Offline tests: unauthorized calls must never reach data or model dependencies."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from sdl.app import create_app, get_executor, get_writer, get_rationale_model, get_agent_client

READER = "reader-test-credential-" + "r" * 32
OPERATOR = "operator-test-credential-" + "o" * 32


@pytest.fixture
def private_env(monkeypatch):
    monkeypatch.setenv("SDL_ACCESS_MODE", "private")
    monkeypatch.setenv("SDL_WORKSPACE_ID", "studio-one")
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps([
        {"sha256": hashlib.sha256(token.encode()).hexdigest(), "subject": role, "role": role}
        for token, role in [(READER, "reader"), (OPERATOR, "operator")]
    ]))


@pytest.fixture
def private_app(private_env):
    app = create_app()
    def forbidden():
        pytest.fail("Access denial reached a backend dependency")
    for dependency in (get_executor, get_writer, get_rationale_model, get_agent_client):
        app.dependency_overrides[dependency] = forbidden
    return app


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_every_existing_api_route_rejects_anonymous_before_dependencies(private_app):
    client = TestClient(private_app)
    for route in private_app.routes:
        if not route.path.startswith("/api/") or route.path == "/api/health":
            continue
        for method in route.methods:
            response = client.request(method, route.path.replace("{decision_id}", "D1846"))
            assert response.status_code == 401, (route.path, response.text)
            assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("value", ["", "Basic abc", "Bearer short", "Bearer " + "x" * 40, "Bearer " + "x" * 513])
def test_invalid_credentials(private_app, value):
    response = TestClient(private_app).get("/api/catalogue", headers={"Authorization": value})
    assert response.status_code == 401


def test_duplicate_headers_rejected(private_app):
    response = TestClient(private_app).get("/api/catalogue", headers=[
        ("Authorization", f"Bearer {READER}"), ("Authorization", f"Bearer {OPERATOR}"),
    ])
    assert response.status_code == 401


@pytest.mark.parametrize("token,role", [(READER, "reader"), (OPERATOR, "operator")])
def test_identity(private_app, token, role):
    response = TestClient(private_app).get("/api/workspace/session", headers=auth(token))
    assert response.json() == {"mode": "private", "workspace_id": "studio-one", "subject": role, "role": role}
    assert "sha256" not in response.text and token not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/decisions", "/api/decisions/D1846/memo"])
def test_reader_cannot_operate(private_app, path):
    response = TestClient(private_app).post(path, headers=auth(READER), json={})
    assert response.status_code == 403


@pytest.mark.parametrize("token", [READER, OPERATOR])
def test_private_remote_agent_disabled(private_app, token):
    response = TestClient(private_app).post("/api/agent/ask", headers=auth(token), json={"question": "hi"})
    assert response.status_code == 503


def test_reader_reaches_catalogue(private_app):
    private_app.dependency_overrides[get_executor] = lambda: lambda sql: []
    response = TestClient(private_app).get("/api/catalogue", headers=auth(READER))
    assert response.status_code == 200


def test_operator_reaches_recording(private_app, monkeypatch):
    from sdl.service import DecisionConflict
    def record(*args, **kwargs):
        raise DecisionConflict("captured authorized record")
    monkeypatch.setattr("sdl.app.make_decision", record)
    for dependency in (get_executor, get_writer, get_rationale_model):
        private_app.dependency_overrides[dependency] = lambda: None
    response = TestClient(private_app).post("/api/decisions", headers=auth(OPERATOR), json={
        "title_id": "A", "territory_code": "NG", "effective_at": "2026-09-08T00:00:00Z",
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "captured authorized record"


def test_new_routes_default_to_operator(private_app):
    @private_app.get("/api/future")
    def future():
        return {"ok": True}
    # create_app mounts the static console last; keep this test API before it.
    private_app.router.routes.insert(0, private_app.router.routes.pop())
    client = TestClient(private_app)
    assert client.get("/api/future", headers=auth(READER)).status_code == 403
    assert client.get("/api/future", headers=auth(OPERATOR)).status_code == 200


def test_health_and_no_cross_origin_access(private_app):
    client = TestClient(private_app)
    assert client.get("/api/health").json() == {"status": "ok"}
    response = client.options("/api/catalogue", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in response.headers
    assert not any(route.path in {"/docs", "/redoc", "/openapi.json"} for route in private_app.routes)


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", "[null]", '[{"sha256":"bad"}]'])
def test_invalid_configuration_fails_startup(private_env, monkeypatch, raw):
    monkeypatch.setenv("SDL_ACCESS_KEYS", raw)
    with pytest.raises(ValueError, match="Invalid SDL_ACCESS_KEYS"):
        create_app()


def test_invalid_mode_and_accidental_public_config(private_env, monkeypatch):
    monkeypatch.setenv("SDL_ACCESS_MODE", "privat")
    with pytest.raises(ValueError):
        create_app()
    monkeypatch.setenv("SDL_ACCESS_MODE", "public")
    with pytest.raises(ValueError):
        create_app()


def test_duplicate_digest_and_invalid_role_fail(private_env, monkeypatch):
    import os
    entries = json.loads(os.environ["SDL_ACCESS_KEYS"])
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps([entries[0], entries[0]]))
    with pytest.raises(ValueError):
        create_app()
    entries[0]["role"] = "admin"
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps(entries))
    with pytest.raises(ValueError):
        create_app()


def test_workspace_is_server_controlled(private_app):
    response = TestClient(private_app).get("/api/workspace/session?workspace_id=other", headers={
        **auth(READER), "X-Workspace-ID": "other", "X-Role": "operator",
    })
    assert response.json()["workspace_id"] == "studio-one"
    assert response.json()["role"] == "reader"


def test_revocation_after_restart(private_env, monkeypatch):
    import os
    entries = json.loads(os.environ["SDL_ACCESS_KEYS"])
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps(entries[1:]))
    client = TestClient(create_app())
    assert client.get("/api/workspace/session", headers=auth(READER)).status_code == 401
    assert client.get("/api/workspace/session", headers=auth(OPERATOR)).status_code == 200


def test_public_mode_compatible(monkeypatch):
    for key in ("SDL_ACCESS_MODE", "SDL_WORKSPACE_ID", "SDL_ACCESS_KEYS"):
        monkeypatch.delenv(key, raising=False)
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: lambda sql: []
    client = TestClient(app)
    assert client.get("/api/catalogue").status_code == 200
    assert client.get("/api/workspace/session").json()["mode"] == "public"
