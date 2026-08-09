"""HTTP API for the console.

Retrieval runs over the ClickHouse MCP server; writes go straight to the
service's own connection. The dependency seam exists so tests can substitute a
direct executor — safe because a parity test holds both paths to identical
evidence hashes.
"""

from __future__ import annotations

import json
import os
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sdl.canonical import canonical_rows
from sdl.evaluator import Decision, evaluate, ReleaseRequest
from sdl.ledger import read_decision, read_policy, read_snapshot
from sdl.mcp_executor import ClickHouseMCPExecutor
from sdl.resolve import resolve_facts
from sdl.service import blocking_condition, evidence_groups, make_decision
from sdl.verifier import verify

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("CLICKHOUSE_")
    }
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env.setdefault(key.strip(), value.strip())
    return env


def _http_call(env: dict[str, str], sql: str, want_rows: bool) -> list[dict]:
    host = env["CLICKHOUSE_HOST"]
    port = env.get("CLICKHOUSE_PORT", "8443")
    credentials = b64encode(
        f"{env['CLICKHOUSE_USER']}:{env['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()
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


_mcp_executor: ClickHouseMCPExecutor | None = None
_mcp_call = None


def get_executor():
    """Production retrieval path: the ClickHouse MCP server."""
    global _mcp_executor, _mcp_call
    if _mcp_call is None:
        _mcp_executor = ClickHouseMCPExecutor(load_env())
        _mcp_call = _mcp_executor.__enter__()
    return _mcp_call


def get_writer():
    """Writes never travel over MCP — SPEC invariant 13."""
    env = load_env()

    def write(sql: str) -> None:
        _http_call(env, sql, want_rows=False)

    return write


class DecisionRequestBody(BaseModel):
    title_id: str = Field(min_length=1)
    territory_code: str = Field(min_length=2, max_length=2)
    effective_at: datetime
    policy_revision: str = "POL-2026.07"


def _decision_payload(record, snapshot, decision: Decision, facts) -> dict:
    return {
        "decision_id": record.decision_id,
        "title_id": record.title_id,
        "territory_code": record.territory_code,
        "effective_at": record.effective_at.isoformat(),
        "decided_at": record.decided_at.isoformat(),
        "outcome": record.outcome,
        "rule_hits": list(record.rule_hits),
        "blocking_condition": blocking_condition(decision),
        "policy_revision": record.policy_revision,
        "snapshot_id": record.snapshot_id,
        "source_manifest_hash": snapshot.source_manifest_hash,
        "max_revision": snapshot.max_revision,
        "retrieval_count": len(snapshot.facts),
        "model_rationale": record.model_rationale,
        "evidence_groups": evidence_groups(facts, decision, record.policy_revision),
    }


def create_app() -> FastAPI:
    app = FastAPI(title="Studio Decision Ledger", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/decisions", status_code=201)
    def create_decision(
        body: DecisionRequestBody,
        executor=Depends(get_executor),
        writer=Depends(get_writer),
    ) -> dict:
        effective_at = body.effective_at
        if effective_at.tzinfo is None:
            effective_at = effective_at.replace(tzinfo=timezone.utc)
        recorded = make_decision(
            executor,
            writer,
            title_id=body.title_id,
            territory_code=body.territory_code,
            effective_at=effective_at,
            policy_revision=body.policy_revision,
        )
        return _decision_payload(
            recorded.record, recorded.snapshot, recorded.decision, recorded.facts
        )

    @app.get("/api/decisions/{decision_id}")
    def get_decision(decision_id: str, executor=Depends(get_executor)) -> dict:
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        if snapshot is None:
            raise HTTPException(
                status_code=409,
                detail=f"decision {decision_id} names a snapshot that is unavailable",
            )
        facts, _evidence = resolve_facts(
            executor, record.title_id, record.territory_code, snapshot.max_revision
        )
        decision = Decision(outcome=record.outcome, rule_hits=list(record.rule_hits))
        return _decision_payload(record, snapshot, decision, facts)

    @app.post("/api/decisions/{decision_id}/verify")
    def verify_decision(decision_id: str, executor=Depends(get_executor)) -> dict:
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        try:
            policy, _sha = read_policy(executor, record.policy_revision)
        except KeyError:
            policy = None
        result = verify(record, snapshot, policy, executor)
        return {
            "decision_id": decision_id,
            "capability_class": result.capability_class,
            "failed_requirement": result.failed_requirement,
            "detail": result.detail,
        }

    @app.post("/api/decisions/{decision_id}/ablate")
    def ablate_decision(decision_id: str, executor=Depends(get_executor)) -> dict:
        """Show what this decision is worth without its evidence binding.

        Runs the same verifier twice: once with the snapshot, once with it
        withheld. Read-only — an ablation that mutated the record to make its
        point would be the exact failure it exists to warn about.
        """
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        try:
            policy, _sha = read_policy(executor, record.policy_revision)
        except KeyError:
            policy = None

        bound = verify(record, snapshot, policy, executor)
        unbound = verify(record, None, policy, executor)

        return {
            "decision_id": decision_id,
            "withheld": "snapshot binding",
            "with_binding": {
                "capability_class": bound.capability_class,
                "failed_requirement": bound.failed_requirement,
                "detail": bound.detail,
            },
            "without_binding": {
                "capability_class": unbound.capability_class,
                "failed_requirement": unbound.failed_requirement,
                "detail": unbound.detail,
            },
            "explanation": (
                "The outcome, the reasoning and the timestamp are all still present. "
                "Only the binding to the evidence is gone — and that is enough for the "
                "record to stop being evidence of anything."
            ),
        }

    @app.get("/api/decisions/{decision_id}/compare")
    def compare_decision(decision_id: str, executor=Depends(get_executor)) -> dict:
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=409, detail="snapshot unavailable")

        from sdl.ledger import current_max_revision

        latest = current_max_revision(executor, record.title_id)
        policy, _sha = read_policy(executor, record.policy_revision)
        current_facts, _evidence = resolve_facts(
            executor, record.title_id, record.territory_code, latest
        )
        current = evaluate(
            ReleaseRequest(
                title_id=record.title_id,
                territory_code=record.territory_code,
                effective_at=record.effective_at,
            ),
            current_facts,
            policy,
        )

        differences = []
        if current.outcome != record.outcome:
            differences.append(
                f"Current data would produce {current.outcome} for the same date; "
                f"the record stands at {record.outcome}."
            )
        if latest != snapshot.max_revision:
            differences.append(
                f"Evidence has moved from revision {snapshot.max_revision} to {latest} "
                "since this decision was recorded."
            )

        return {
            "historical": {
                "outcome": record.outcome,
                "rule_hits": list(record.rule_hits),
                "max_revision": snapshot.max_revision,
                "snapshot_id": snapshot.snapshot_id,
                "decided_at": record.decided_at.isoformat(),
            },
            "current": {
                "outcome": current.outcome,
                "rule_hits": list(current.rule_hits),
                "max_revision": latest,
                "blocking_condition": blocking_condition(current),
            },
            "differences": differences,
            # The comparison surface never writes. Saying so in the payload
            # keeps the console honest about what it is showing.
            "record_unchanged": True,
        }

    # The built console is served from the same origin as the API, so the
    # client's relative /api paths need no proxy and no CORS in production.
    # Mounted last: API routes are matched first, and this catches the rest.
    console_dist = Path(__file__).resolve().parent.parent.parent / "dist"
    if console_dist.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(console_dist), html=True), name="console"
        )

    return app


app = create_app()
