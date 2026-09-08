"""Provision an EMPTY, dedicated ClickHouse service; never seeds title evidence.

Run from the repository with the API venv and PYTHONPATH=api. Credentials come
only from the process environment. A separate admin connection is needed here;
runtime uses read-only MCP credentials plus a restricted write identity.
"""
import argparse
import hashlib
import importlib.util
import os
import re
from pathlib import Path

from sdl.app import _http_call
from sdl.private_schema import statements
from sdl.record import canonical_json, _quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--confirm-empty-service", action="store_true", required=True)
    parser.add_argument("--accept-baseline-policy", action="store_true", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.workspace):
        parser.error("Invalid workspace identifier")
    env = {key: value for key, value in os.environ.items() if key.startswith("CLICKHOUSE_")}
    if not all(env.get(key) for key in ["CLICKHOUSE_HOST", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"]):
        parser.error("Provide dedicated ClickHouse credentials in the process environment")
    existing = _http_call(env, "SELECT name FROM system.tables WHERE database = 'sdl'", True)
    if existing:
        parser.error("Refusing a nonempty sdl database; use a fresh dedicated service")
    root = Path(__file__).resolve().parents[1]
    for sql in statements((root / "db/schema.sql").read_text()):
        _http_call(env, sql, False)
    spec = importlib.util.spec_from_file_location("baseline_policy", root / "db/seed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Definitions only; never call the seed writer.
    payload = canonical_json(module.POLICY_RULES_V2)
    policy = {"policy_revision": module.POLICY_REVISION_V2, "rules_payload": payload,
              "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
              "effective_at": module.POLICY_EFFECTIVE_AT_V2, "recorded_at": module.POLICY_RECORDED_AT_V2}
    _http_call(env, "INSERT INTO sdl.policy_revisions FORMAT JSONEachRow\n" + canonical_json(policy), False)
    # Marker last: partially provisioned services fail the runtime identity check.
    _http_call(env, "INSERT INTO sdl.workspace_identity VALUES (" + _quote(args.workspace) + ")", False)
    print(f"Provisioned {args.workspace}: seven empty evidence views and the explicitly accepted baseline policy. No studio evidence seeded.")


if __name__ == "__main__":
    main()
