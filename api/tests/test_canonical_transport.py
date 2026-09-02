"""Transport contract: canonical rows must be identical across MCP and HTTP.

Without this, the same facts read over two paths hash differently and a sound
decision fails to certify for reasons unconnected to the facts (SPEC invariant 14).
"""

from sdl.canonical import canonical_rows, canonical_value


def test_canonical_value_normalises_millisecond_forms():
    assert canonical_value("2026-07-31 00:00:00") == "2026-07-31 00:00:00.000"
    assert canonical_value("2026-07-31 00:00:00.0") == "2026-07-31 00:00:00.000"
    assert canonical_value("2026-07-31 00:00:00.00") == "2026-07-31 00:00:00.000"
    assert canonical_value("2026-07-31 00:00:00.123") == "2026-07-31 00:00:00.123"
    assert canonical_value("2026-07-31 00:00:00.1234") == "2026-07-31 00:00:00.123"


def test_canonical_rows_are_idempotent():
    rows = [{"approved_at": "2026-07-31 00:00:00", "status": "ACTIVE"}]
    once = canonical_rows(rows)
    twice = canonical_rows(once)
    assert once == twice


def test_canonical_rows_preserve_non_timestamps():
    rows = [{"license_id": "LIC-001", "revision": 1, "status": "ACTIVE"}]
    assert canonical_rows(rows) == rows


def test_seed_is_deterministic():
    """Regenerating seed.py must not change the committed hash (db/seed.sha256).

    If this fires, the generated SQL changed without updating the golden file —
    which would silently break every verifier expectation in the suite.
    """
    import hashlib
    from pathlib import Path

    seed_sql = Path(__file__).resolve().parents[2] / "db" / "seed.sql"
    digest = hashlib.sha256(seed_sql.read_bytes()).hexdigest()
    golden = Path(__file__).resolve().parents[2] / "db" / "seed.sha256"
    first_line = golden.read_text().splitlines()[0].strip().split()[0]
    assert digest == first_line, f"seed.sql hash {digest} != golden {first_line}; run python3 db/seed.py and update db/seed.sha256"


def test_policy_hash_is_pinned():
    import hashlib
    import json
    import importlib.util
    from pathlib import Path

    golden = Path(__file__).resolve().parents[2] / "db" / "seed.sha256"
    lines = golden.read_text().splitlines()
    assert len(lines) >= 2
    expected = lines[1].split()[0]
    seed_path = Path(__file__).resolve().parents[2] / "db" / "seed.py"
    spec = importlib.util.spec_from_file_location("seed_mod", seed_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore
    payload = json.dumps(mod.POLICY_RULES, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    assert digest == expected
