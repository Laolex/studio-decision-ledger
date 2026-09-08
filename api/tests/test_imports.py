import csv
import io

import pytest
from fastapi.testclient import TestClient

from sdl.app import create_app, get_executor, get_writer
from sdl.imports import ImportPreflightBody, preflight_licenses

HEADER = ["license_id", "title_id", "territory_code", "rights_scope", "valid_from", "valid_to", "status"]
ROW = ["LIC-1", "TITLE-1", "NG", "SVOD", "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z", "ACTIVE"]


def document(rows=None, header=None):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADER if header is None else header)
    writer.writerows([ROW] if rows is None else rows)
    return output.getvalue()


def run(text=None, source="contract-1"):
    return preflight_licenses(ImportPreflightBody(table="title_licenses", source_reference=source, csv_text=text or document()))


def test_valid_preflight_does_not_assign_revision():
    result = run()
    assert result["valid"] and not result["recorded"]
    assert result["row_count"] == 1 and len(result["content_sha256"]) == 64
    assert "revision" not in result["rows"][0] and "recorded_at" not in result["rows"][0]
    assert result["rows"][0]["valid_from"] == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize("column,value", [
    (0, ""), (0, " trailing "), (1, "bad\x00title"), (2, "ng"), (2, "NGA"),
    (3, "ALL"), (6, "APPROVED"), (4, "2026-01-01"),
    (4, "2026-01-01T00:00:00"), (4, "2026-01-01T00:00:00.1230001Z"),
    (4, "1970-01-01T00:00:00Z"), (4, "2300-01-01T00:00:00Z"),
    (4, "2026-02-30T00:00:00Z"), (5, "2026-01-01T00:00:00Z"),
])
def test_invalid_rows_have_no_partial_batch(column, value):
    row = ROW.copy()
    row[column] = value
    result = run(document([ROW, row]))
    assert not result["valid"] and result["rows"] == []
    assert result["content_sha256"] is None
    assert result["issues"][0]["row"] == 2


def test_timezone_and_order_normalization():
    other = ROW.copy()
    other[0] = "LIC-2"
    changed = ROW.copy()
    changed[4] = "2026-01-01T01:00:00+01:00"
    first = run(document([ROW, other]))
    second = run(document([other, changed]))
    assert first["rows"] == second["rows"]
    assert first["content_sha256"] == second["content_sha256"]
    assert first["input_sha256"] != second["input_sha256"]
    assert first["content_sha256"] != run(document([ROW, other]), source="different")["content_sha256"]


def test_duplicate_key_and_width():
    assert not run(document([ROW, ROW]))["valid"]
    assert not run(document([ROW[:-1]]))["valid"]
    assert not run(document([ROW + ["extra"]]))["valid"]


@pytest.mark.parametrize("header", [HEADER[:-1], HEADER + ["revision"], HEADER + ["license_id"]])
def test_reject_invalid_headers(header):
    with pytest.raises(ValueError, match="headers"):
        run(document(header=header))


def test_bounds_and_empty():
    with pytest.raises(ValueError, match="no data"):
        run(document([]))
    with pytest.raises(ValueError, match="500"):
        run(document([ROW] * 501))
    with pytest.raises(ValueError, match="256 KiB"):
        run("é" * 140000)
    with pytest.raises(ValueError, match="Malformed"):
        run(document() + '"unclosed')


def test_bom_and_quoted_note():
    result = run("\ufeff" + document([ROW + ['Clause "A", amendment']], HEADER + ["amendment_note"]))
    assert result["valid"]
    assert result["rows"][0]["amendment_note"] == 'Clause "A", amendment'


def test_private_operator_only_and_no_backend_calls(monkeypatch):
    import hashlib
    import json
    monkeypatch.setenv("SDL_ACCESS_MODE", "private")
    monkeypatch.setenv("SDL_WORKSPACE_ID", "test-studio")
    monkeypatch.setenv("SDL_ACCESS_KEYS", json.dumps([
        {"sha256": hashlib.sha256((role * 16).encode()).hexdigest(), "subject": role, "role": role}
        for role in ["reader", "operator"]
    ]))
    app = create_app()
    def forbidden():
        pytest.fail("Import preflight attempted backend access")
    for dependency in [get_executor, get_writer]:
        app.dependency_overrides[dependency] = forbidden
    client = TestClient(app)
    body = {"table": "title_licenses", "source_reference": "contract", "csv_text": document()}
    assert client.post("/api/imports/preflight", json=body).status_code == 401
    assert client.post("/api/imports/preflight", json=body, headers={"Authorization": "Bearer " + "reader" * 16}).status_code == 403
    response = client.post("/api/imports/preflight", json=body, headers={"Authorization": "Bearer " + "operator" * 16})
    assert response.status_code == 200 and response.json()["valid"]
    assert response.headers["cache-control"] == "no-store"
    body["csv_text"] = document([])
    assert client.post("/api/imports/preflight", json=body, headers={"Authorization": "Bearer " + "operator" * 16}).status_code == 422


def test_public_imports_disabled(monkeypatch):
    for key in ["SDL_ACCESS_MODE", "SDL_WORKSPACE_ID", "SDL_ACCESS_KEYS"]:
        monkeypatch.delenv(key, raising=False)
    response = TestClient(create_app()).post("/api/imports/preflight", json={
        "table": "title_licenses", "source_reference": "contract", "csv_text": document(),
    })
    assert response.status_code == 403
