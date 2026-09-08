"""Opt-in writes ONLY to the dedicated localhost test service on port 18123.

Provision it with scripts/provision_private.py --workspace test-studio first.
Run once against a fresh service; never redirects to the production .env.
"""
import csv
import io
import os
from datetime import datetime, timezone

import pytest

from sdl.app import _http_call
from sdl.imports import ImportPreflightBody, preflight_licenses
from sdl.ledger import read_decision, read_snapshot, read_policy
from sdl.mcp_executor import ClickHouseMCPExecutor
from sdl.service import make_decision, preview_decision
from sdl.verifier import verify
from sdl.workspace import WorkspaceStore

pytestmark = pytest.mark.skipif(os.getenv("SDL_LOCAL_INTEGRATION") != "1", reason="dedicated localhost integration disabled")

ENV = {"CLICKHOUSE_HOST": "127.0.0.1", "CLICKHOUSE_PORT": "18123", "CLICKHOUSE_USER": "sdl_test", "CLICKHOUSE_PASSWORD": "sdl-isolated-test-only-20260908", "CLICKHOUSE_SECURE": "false", "CLICKHOUSE_DATABASE": "sdl"}
START, END = "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"


def csv_for(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_full_private_import_and_historical_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_WORKSPACE_STATE_DIR", str(tmp_path))
    store = WorkspaceStore(str(tmp_path), "test-studio")
    datasets = {
        "title_licenses": [{"license_id": "L1", "title_id": "PRIVATE-FILM", "territory_code": "NG", "rights_scope": "SVOD", "valid_from": START, "valid_to": END, "status": "ACTIVE"}],
        "clearances": [{"clearance_id": f"C-{kind}", "title_id": "PRIVATE-FILM", "territory_code": "NG", "asset_ref": kind, "clearance_kind": kind, "valid_from": START, "valid_to": END, "status": "ACTIVE"} for kind in ["MUSIC_SYNC", "MUSIC_MASTER", "STOCK_FOOTAGE", "TALENT"]],
        "ratings": [{"rating_id": "R1", "title_id": "PRIVATE-FILM", "territory_code": "NG", "rating_code": "15", "issued_at": START, "expires_at": END, "status": "VALID"}],
        "deliveries": [{"delivery_id": "D1", "title_id": "PRIVATE-FILM", "master_version": "v1", "approved_at": START, "captions_state": "APPROVED", "audio_description_state": "APPROVED"}],
        "continuity_exceptions": [{"exception_id": "E1", "title_id": "PRIVATE-FILM", "scene_ref": "Scene1", "severity": "ADVISORY", "state": "RESOLVED", "resolution_ref": "review1"}],
        "synthetic_content": [{"record_id": "S1", "title_id": "PRIVATE-FILM", "asset_ref": "Scene1", "generation_kind": "NONE", "tool_ref": "", "disclosure_obligation_ref": ""}],
        "performer_consents": [{"consent_id": "P1", "title_id": "PRIVATE-FILM", "performer_ref": "performer1", "consent_scope": "both", "territory_code": "NG", "valid_from": START, "valid_to": END, "status": "ACTIVE"}],
    }
    def write(sql):
        _http_call(ENV, sql, False)
    with ClickHouseMCPExecutor(ENV) as execute:
        for table, rows in datasets.items():
            result = preflight_licenses(ImportPreflightBody(table=table, source_reference=f"synthetic-integration-{table}", csv_text=csv_for(rows)))
            assert result["valid"], result
            review = store.review(result, execute)
            receipt = store.publish(review, review["expected_head"], False, "integration-operator", execute, write)
            assert receipt["status"] == "published"
        moment = datetime(2026, 9, 8, tzinfo=timezone.utc)
        original = make_decision(execute, write, title_id="PRIVATE-FILM", territory_code="NG", effective_at=moment, recorded_by="integration-operator", model=None)
        assert original.record.outcome == "AVAILABLE"
        loaded = read_decision(execute, original.record.decision_id)
        assert loaded.recorded_by == "integration-operator"
        correction = {**datasets["title_licenses"][0], "rights_scope": "AVOD", "amendment_note": "Synthetic test correction"}
        result = preflight_licenses(ImportPreflightBody(table="title_licenses", source_reference="synthetic-amendment", csv_text=csv_for([correction])))
        review = store.review(result, execute)
        assert review["corrections"] == 1
        changed = store.publish(review, review["expected_head"], True, "second-operator", execute, write)
        assert changed["revision"] == 8
        assert preview_decision(execute, title_id="PRIVATE-FILM", territory_code="NG", effective_at=moment).decision.outcome == "HOLD"
        snapshot = read_snapshot(execute, loaded.snapshot_id)
        policy, _ = read_policy(execute, loaded.policy_revision)
        assert verify(loaded, snapshot, policy, execute).capability_class == "C2"
        # Simulate a duplicate network delivery after the next revision exists.
        first = store.receipts()[-1]
        first.pop("status")
        from sdl.record import canonical_json
        write("INSERT INTO sdl.workspace_imports FORMAT JSONEachRow\n" + canonical_json({"import_id": first["import_id"], "revision": first["revision"], "payload": canonical_json(first)}))
        assert verify(loaded, snapshot, policy, execute).capability_class == "C2"
