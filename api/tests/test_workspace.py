import hashlib
import json
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sdl.app import create_app
from sdl.access import COOKIE
from sdl.imports import ImportPreflightBody, preflight_licenses
from sdl.workspace import WorkspaceStore

KEY = "individual-workspace-operator-" + "a" * 32
CSV = "license_id,title_id,territory_code,rights_scope,valid_from,valid_to,status,amendment_note\nL1,T1,NG,SVOD,2026-01-01T00:00:00Z,2027-01-01T00:00:00Z,ACTIVE,\n"


def validated(csv=CSV, source="contract"):
    return preflight_licenses(ImportPreflightBody(table="title_licenses", source_reference=source, csv_text=csv))


class EvidenceService:
    def __init__(self):
        self.events = {}
        self.writes = 0
        self.lose_ack = False

    def execute(self, sql):
        if "workspace_identity" in sql:
            return [{"workspace_id": "test-studio"}]
        if "max(revision)" in sql:
            return [{"revision": max([event["revision"] for event in self.events.values()] or [0])}]
        import_id = sql.split("import_id = '")[1].split("'")[0]
        return [{"payload": self.events[import_id]["payload"]}] if import_id in self.events else []

    def write(self, sql):
        event = json.loads(sql.split("\n", 1)[1])
        self.events[event["import_id"]] = event
        self.writes += 1
        if self.lose_ack:
            raise TimeoutError("acknowledgement lost")


def test_publication_is_durable_and_idempotent(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    review = store.review(validated(), service.execute)
    receipt = store.publish(review, 0, False, "alice", service.execute, service.write)
    assert receipt["actor"] == "alice" and receipt["revision"] == 1
    restarted = WorkspaceStore(str(tmp_path), "test-studio")
    repeated = restarted.publish(review, 0, False, "bob", service.execute, service.write)
    assert repeated == receipt and service.writes == 1
    assert restarted.receipts()[0]["status"] == "published"


def test_lost_ack_blocks_new_work_and_retry_recovers(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    service.lose_ack = True
    with pytest.raises(HTTPException) as error:
        store.publish(validated(), 0, False, "alice", service.execute, service.write)
    assert error.value.status_code == 503
    assert store.head() == 0
    with pytest.raises(HTTPException):
        store.publish(validated(source="other"), 0, False, "bob", service.execute, service.write)
    receipt = store.retry(store.receipts()[0]["import_id"], service.execute, service.write)
    assert receipt["revision"] == 1 and service.writes == 1
    assert store.head() == 1


def test_corrections_require_fresh_review_consent_and_note(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    store.publish(validated(), 0, False, "alice", service.execute, service.write)
    correction = validated(CSV.replace("SVOD", "AVOD"), "amendment")
    review = store.review(correction, service.execute)
    assert review["corrections"] == 1 and review["changes"][0]["before"]["rights_scope"] == "SVOD"
    for head, consent, code in [(0, True, 409), (1, False, 409), (1, True, 422)]:
        with pytest.raises(HTTPException) as error:
            store.publish(correction, head, consent, "alice", service.execute, service.write)
        assert error.value.status_code == code
    correction = validated(CSV.replace("SVOD", "AVOD").replace("ACTIVE,\n", "ACTIVE,Scope corrected\n"), "amendment")
    assert store.publish(correction, 1, True, "bob", service.execute, service.write)["revision"] == 2
    assert len(service.events) == 2


def test_pending_without_remote_write_can_retry(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    def fail(sql):
        raise TimeoutError()
    with pytest.raises(HTTPException):
        store.publish(validated(), 0, False, "alice", service.execute, fail)
    with pytest.raises(HTTPException):
        store.review(validated(source="next"), service.execute)
    assert store.retry(store.receipts()[0]["import_id"], service.execute, service.write)["status"] == "published"


def test_concurrent_writers_cannot_allocate_same_revision(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    other = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    entered, finish = threading.Event(), threading.Event()
    errors = []
    def slow_write(sql):
        entered.set()
        assert finish.wait(5)
        service.write(sql)
    def first():
        try:
            store.publish(validated(), 0, False, "alice", service.execute, slow_write)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(HTTPException) as error:
            other.publish(validated(source="other"), 0, False, "bob", service.execute, service.write)
        assert error.value.status_code == 409
    finally:
        finish.set()
        thread.join(5)
    assert not errors and service.writes == 1


def test_wrong_workspace_and_restore_drift_fail_closed(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    with pytest.raises(ValueError):
        WorkspaceStore(str(tmp_path), "another-studio")
    with pytest.raises(HTTPException):
        store.review(validated(), lambda sql: [{"workspace_id": "other"}])
    service = EvidenceService()
    service.events["unexpected"] = {"revision": 12, "payload": "{}"}
    with pytest.raises(HTTPException):
        store.publish(validated(), 0, False, "alice", service.execute, service.write)
    assert service.writes == 0


def test_completed_receipt_never_claims_missing_data_was_published(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    receipt = store.publish(validated(), 0, False, "alice", service.execute, service.write)
    service.events.clear()
    with pytest.raises(HTTPException):
        store.publish(validated(), 0, False, "alice", service.execute, service.write)
    with pytest.raises(HTTPException):
        store.retry(receipt["import_id"], service.execute, service.write)
    assert service.writes == 1


def test_pending_retry_never_backfills_below_a_divergent_remote_head(tmp_path):
    store = WorkspaceStore(str(tmp_path), "test-studio")
    service = EvidenceService()
    def fail(sql):
        raise TimeoutError()
    with pytest.raises(HTTPException):
        store.publish(validated(), 0, False, "alice", service.execute, fail)
    service.events["divergent"] = {"revision": 20, "payload": "{}"}
    with pytest.raises(HTTPException):
        store.retry(store.receipts()[0]["import_id"], service.execute, service.write)
    assert service.writes == 0


@pytest.fixture
def browser_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_ACCESS_MODE", "private")
    monkeypatch.setenv("SDL_WORKSPACE_ID", "test-studio")
    monkeypatch.setenv("SDL_WORKSPACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SDL_PUBLIC_ORIGIN", "https://studio.example")
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps([{"sha256": hashlib.sha256(KEY.encode()).hexdigest(), "subject": "alice", "role": "operator"}]))
    return tmp_path


def test_browser_login_csrf_logout_and_revocation(browser_env, monkeypatch):
    client = TestClient(create_app(), base_url="https://studio.example")
    assert client.post("/api/workspace/login", json={"credential": KEY}).status_code == 403
    response = client.post("/api/workspace/login", headers={"Origin": "https://studio.example"}, json={"credential": KEY})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert KEY not in cookie and KEY not in response.text
    assert client.get("/api/workspace/session").json()["subject"] == "alice"
    assert client.post("/api/workspace/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/workspace/logout", headers={"Origin": "https://studio.example"}).status_code == 200
    assert client.get("/api/workspace/session").status_code == 401
    client.post("/api/workspace/login", headers={"Origin": "https://studio.example"}, json={"credential": KEY})
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps([{"sha256": "0" * 64, "subject": "alice", "role": "operator"}]))
    restarted = TestClient(create_app(), base_url="https://studio.example")
    restarted.cookies.update(client.cookies)
    assert restarted.get("/api/workspace/session").status_code == 401


def test_expiry_and_login_rate_limit(browser_env):
    store = WorkspaceStore(str(browser_env), "test-studio")
    token = store.session(hashlib.sha256(KEY.encode()).hexdigest())
    with store.db() as db:
        db.execute("UPDATE sessions SET expires = 0")
    client = TestClient(create_app(), base_url="https://studio.example")
    client.cookies.set(COOKIE, token)
    assert client.get("/api/workspace/session").status_code == 401
    for _ in range(10):
        assert client.post("/api/workspace/login", headers={"Origin": "https://studio.example"}, json={"credential": "bad" * 20}).status_code == 401
    assert client.post("/api/workspace/login", headers={"Origin": "https://studio.example"}, json={"credential": KEY}).status_code == 429


def test_registry_file_and_reader_cookie_permissions(browser_env, monkeypatch):
    registry = browser_env / "access.json"
    registry.write_text(json.dumps([{"sha256": hashlib.sha256(KEY.encode()).hexdigest(), "subject": "reader-one", "role": "reader"}]))
    monkeypatch.delenv("SDL_ACCESS_KEYS")
    monkeypatch.setenv("SDL_ACCESS_KEYS_FILE", str(registry))
    client = TestClient(create_app(), base_url="https://studio.example")
    assert client.post("/api/workspace/login", headers={"Origin": "https://studio.example"}, json={"credential": KEY}).status_code == 200
    assert client.get("/api/workspace/session").json()["role"] == "reader"
    for path in ("/api/imports", "/api/imports/preflight", "/api/decisions"):
        assert client.post(path, headers={"Origin": "https://studio.example"}, json={}).status_code == 403


def test_private_writer_requires_distinct_identity(browser_env, monkeypatch):
    from sdl.app import get_writer
    monkeypatch.setenv("CLICKHOUSE_USER", "reader")
    monkeypatch.delenv("CLICKHOUSE_WRITE_USER", raising=False)
    with pytest.raises(HTTPException) as error:
        get_writer()
    assert error.value.status_code == 503
    monkeypatch.setenv("CLICKHOUSE_WRITE_USER", "reader")
    monkeypatch.setenv("CLICKHOUSE_WRITE_PASSWORD", "test-password")
    with pytest.raises(HTTPException):
        get_writer()


def test_incomplete_evidence_never_claims_group_readiness():
    from sdl.evaluator import Facts, Decision
    from sdl.service import evidence_groups
    facts = Facts(licenses=[], clearances=[], ratings=[], deliveries=[], continuity_exceptions=[])
    groups = evidence_groups(facts, Decision(outcome="ESCALATE", rule_hits=["ESC-001"]), "POL-2026.08")
    assert all(group["tone"] == "unknown" for group in groups)
    assert all(group["summary"] == "Readiness not established" for group in groups)
