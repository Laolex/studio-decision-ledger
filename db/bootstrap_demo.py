#!/usr/bin/env python3
"""Record the two demo decisions through the real pipeline.

Deliberately not seeded SQL. A reviewer should watch the pipeline produce these
records — retrieve over MCP, evaluate deterministically, pin a snapshot, write
the ledger — rather than take our word that it would have.

    D-1846  taken 30 July, pinned at revision 1, before any correction landed
    D-1847  taken 8 August, at current evidence, after both corrections

Both ask the SAME question about the SAME date. D-1846 answers AVAILABLE and
must keep answering AVAILABLE forever, because that is what was knowable when
it was taken. D-1847 answers HOLD, because the grant has since been restated as
AVOD-only with retroactive effect. Neither answer is wrong, and the distinction
between them is the entire product.

Idempotent: existing decisions are left alone rather than duplicated.

Usage:
    python3 db/bootstrap_demo.py            # write the demo decisions
    python3 db/bootstrap_demo.py --verify   # write, then replay each one
"""

from __future__ import annotations

import json
import sys
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from sdl.canonical import canonical_rows  # noqa: E402
from sdl.ledger import read_decision, read_policy, read_snapshot  # noqa: E402
from sdl.service import make_decision  # noqa: E402
from sdl.verifier import verify  # noqa: E402

ENV_PATH = ROOT / "api" / ".env"

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"
EFFECTIVE_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)

DEMO_DECISIONS = [
    {
        "decision_id": "D-1846",
        "snapshot_id": "RS-2026-07-30-0001",
        "max_revision": 1,
        "decided_at": datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
        "note": "taken before any correction landed",
    },
    {
        "decision_id": "D-1847",
        "snapshot_id": "RS-2026-08-08-0142",
        "max_revision": None,  # current
        "decided_at": datetime(2026, 8, 8, 14, 32, tzinfo=timezone.utc),
        "note": "taken after the backdated restatement",
    },
]


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        sys.exit(f"missing {ENV_PATH} — copy api/.env.example and fill it in")
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def make_clients(env: dict[str, str]):
    host = env["CLICKHOUSE_HOST"]
    port = env.get("CLICKHOUSE_PORT", "8443")
    credentials = b64encode(
        f"{env['CLICKHOUSE_USER']}:{env['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()

    def call(sql: str, want_rows: bool):
        body = f"{sql} FORMAT JSONEachRow" if want_rows else sql
        request = urllib.request.Request(
            f"https://{host}:{port}/",
            data=body.encode("utf-8"),
            headers={"Authorization": f"Basic {credentials}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8").strip()
        if not want_rows:
            return []
        return canonical_rows([json.loads(line) for line in text.splitlines() if line])

    return (lambda sql: call(sql, True)), (lambda sql: call(sql, False))


def main() -> None:
    env = load_env()
    executor, writer = make_clients(env)
    should_verify = "--verify" in sys.argv

    for spec in DEMO_DECISIONS:
        decision_id = spec["decision_id"]
        existing = read_decision(executor, decision_id)
        if existing is not None:
            print(f"{decision_id}: already recorded ({existing.outcome}) — leaving alone")
        else:
            recorded = make_decision(
                executor,
                writer,
                title_id=TITLE,
                territory_code=TERRITORY,
                effective_at=EFFECTIVE_AT,
                max_revision=spec["max_revision"],
                decision_id=decision_id,
                snapshot_id=spec["snapshot_id"],
                now=spec["decided_at"],
            )
            print(
                f"{decision_id}: {recorded.record.outcome} "
                f"{recorded.record.rule_hits} at revision "
                f"{recorded.snapshot.max_revision} — {spec['note']}"
            )

        if should_verify:
            record = read_decision(executor, decision_id)
            snapshot = read_snapshot(executor, record.snapshot_id)
            policy, _sha = read_policy(executor, record.policy_revision)
            result = verify(record, snapshot, policy, executor)
            print(f"{decision_id}: replay -> {result.capability_class}. {result.detail}")


if __name__ == "__main__":
    main()
