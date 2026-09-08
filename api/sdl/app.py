"""HTTP API for the console.

Retrieval runs over the ClickHouse MCP server; writes go straight to the
service's own connection. The dependency seam exists so tests can substitute a
direct executor — safe because a parity test holds both paths to identical
evidence hashes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sdl.canonical import canonical_rows
from sdl.access import WorkspaceAccess, COOKIE
from sdl.imports import ImportPreflightBody, preflight_licenses, IMPORT_MODELS
from sdl.evaluator import Decision, evaluate, ReleaseRequest
from sdl.ledger import list_decisions, release_catalogue, read_decision, read_policy, read_snapshot
from sdl.mcp_executor import ClickHouseMCPWorkerPool, MCPQueryError, QueryMeasurement
from sdl.mcp_executor import worker_package_version
from sdl.resolve import canonical_result_hash, resolve_facts
from sdl.agent_proxy import VertexAgentEngine
from sdl.gemini import GeminiRationaleModel, vertex_client
from sdl.memo import draft_memo
from sdl.agent_proxy import ask as agent_ask_engine
from sdl.agent_proxy import resource_name as agent_resource_name
from sdl.service import (
    DecisionConflict,
    DEFAULT_POLICY_REVISION,
    blocking_condition,
    compare_recorded,
    evidence_groups,
    make_decision,
    preview_decision,
    preview_token,
)
from sdl.verifier import verify
from sdl.resolution import UnsupportedRule, build_resolution_plan

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("CLICKHOUSE_")
    }
    if ENV_PATH.exists() and not os.getenv("SDL_WORKSPACE_STATE_DIR"):
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
        f"{'http' if env.get('CLICKHOUSE_SECURE', 'true').lower() == 'false' else 'https'}://{host}:{port}/",
        data=body.encode("utf-8"),
        headers={"Authorization": f"Basic {credentials}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8").strip()
    if not want_rows:
        return []
    return canonical_rows([json.loads(line) for line in text.splitlines() if line])


_mcp_executor: ClickHouseMCPWorkerPool | None = None
_mcp_call = None
_mcp_init_lock = threading.Lock()


def get_executor():
    """Production retrieval path: a bounded pool of ClickHouse MCP workers."""
    global _mcp_executor, _mcp_call
    if _mcp_call is None:
        with _mcp_init_lock:
            if _mcp_call is None:
                size = int(os.getenv("SDL_MCP_WORKER_POOL_SIZE", "2"))
                _mcp_executor = ClickHouseMCPWorkerPool(load_env(), size=size)
                _mcp_call = _mcp_executor.__enter__()
    if os.getenv("SDL_WORKSPACE_STATE_DIR"):
        identity = _mcp_call("SELECT workspace_id FROM sdl.workspace_identity")
        if identity != [{"workspace_id": os.environ["SDL_WORKSPACE_ID"]}]:
            raise HTTPException(503, "Evidence service workspace identity mismatch")
    return _mcp_call


def get_agent_client():
    """The deployed agent, or a clear 503 when the console is running unwired.

    Local development and the test suite run without an Agent Engine
    deployment. Failing here with an explicit message beats letting the
    console show a generic error for a surface that was simply never
    configured.
    """
    resource = agent_resource_name()
    if not resource:
        raise HTTPException(
            status_code=503,
            detail=(
                "AGENT_ENGINE_RESOURCE is not set, so no agent is deployed for "
                "this environment."
            ),
        )
    return VertexAgentEngine(resource)


def get_rationale_model():
    """The rationale model, or None when Vertex is not configured here.

    Constructed defensively on purpose. A decision must be recordable whether
    or not the model is reachable — the outcome is determined before the model
    is asked, so an unavailable model costs an explanation and nothing else.
    Raising here would make a missing environment variable look like a failure
    to decide.
    """
    if os.getenv("SDL_WORKSPACE_STATE_DIR"):
        return None  # Private evidence is not sent to a model by default.
    try:
        return GeminiRationaleModel(
            vertex_client(),
            fallback_client_factory=lambda location: vertex_client(location=location),
        )
    except Exception:
        logger.warning("rationale model unavailable; decisions will record without one",
                       exc_info=True)
        return None


def get_writer():
    """Writes never travel over MCP — SPEC invariant 13."""
    env = load_env()
    if os.getenv("SDL_WORKSPACE_STATE_DIR") and (
        not env.get("CLICKHOUSE_WRITE_USER") or not env.get("CLICKHOUSE_WRITE_PASSWORD")
        or env["CLICKHOUSE_WRITE_USER"] == env.get("CLICKHOUSE_USER")
    ):
        raise HTTPException(503, "Private writes require a separate restricted ClickHouse writer identity")
    if env.get("CLICKHOUSE_WRITE_USER"):
        env = {**env, "CLICKHOUSE_USER": env["CLICKHOUSE_WRITE_USER"], "CLICKHOUSE_PASSWORD": env["CLICKHOUSE_WRITE_PASSWORD"]}

    def write(sql: str) -> None:
        _http_call(env, sql, want_rows=False)

    return write


class DecisionRequestBody(BaseModel):
    title_id: str = Field(min_length=1)
    territory_code: str = Field(min_length=2, max_length=2)
    effective_at: datetime
    policy_revision: str = DEFAULT_POLICY_REVISION


class RecordDecisionBody(DecisionRequestBody):
    supersedes: str = Field(default="", max_length=100)
    expected_preview_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AgentAskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # Threaded back by the console so a follow-up reaches the same session.
    session_id: str | None = None


class WorkspaceLoginBody(BaseModel):
    credential: str = Field(min_length=32, max_length=512)


class ImportCommitBody(ImportPreflightBody):
    expected_head: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    allow_corrections: bool = False


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
        "evidence_bindings": list(snapshot.facts),
        "model_rationale": record.model_rationale,
        "supersedes": record.supersedes,
        "recorded_by": record.recorded_by,
        "evidence_groups": evidence_groups(facts, decision, record.policy_revision),
    }


def _measured_execute(executor, sql: str) -> tuple[list[dict], QueryMeasurement]:
    """Use pool telemetry in production and an honest zero-wait seam in tests."""
    measured = getattr(executor, "execute_measured", None)
    if measured is not None:
        return measured(sql)
    started = time.perf_counter()
    rows = executor(sql)
    return rows, QueryMeasurement(
        pool_wait_ms=0.0,
        query_ms=(time.perf_counter() - started) * 1000,
        worker_index=0,
    )


def _runtime_integrity_probe(executor, record, snapshot) -> dict:
    """Re-read every bound result serially and report measured runtime facts."""
    checks = []
    measurements = []
    for fact in snapshot.facts:
        try:
            rows, measurement = _measured_execute(executor, fact["canonical_query"])
            observed_hash = canonical_result_hash(rows)
            matched = observed_hash == fact["result_hash"]
            error = ""
            measurements.append(measurement)
        except Exception as exc:
            observed_hash = ""
            matched = False
            error = f"{type(exc).__name__}: {exc}"
        checks.append(
            {
                "table_name": fact["table_name"],
                "expected_hash": fact["result_hash"],
                "observed_hash": observed_hash,
                "matched": matched,
                "error": error,
            }
        )

    matched_count = sum(1 for check in checks if check["matched"])
    if any(check["error"] for check in checks):
        status = "SOURCE_ERROR"
    elif matched_count == len(checks):
        status = "VERIFIED"
    else:
        status = "MISMATCH"

    waits = [measurement.pool_wait_ms for measurement in measurements]
    query_times = [measurement.query_ms for measurement in measurements]
    return {
        "decision_id": record.decision_id,
        "service_revision": os.getenv("K_REVISION", "local"),
        "worker": {
            "package": "mcp-clickhouse",
            "version": worker_package_version(),
            "pool_size": int(getattr(executor, "size", 1)),
        },
        "pool_wait": {
            "samples": len(waits),
            "total_ms": round(sum(waits), 3),
            "max_ms": round(max(waits, default=0.0), 3),
        },
        "query_time": {
            "total_ms": round(sum(query_times), 3),
        },
        "serial_canonical_rehash": {
            "status": status,
            "checked": len(checks),
            "matched": matched_count,
            "checks": checks,
        },
    }


def create_app() -> FastAPI:
    access = WorkspaceAccess()
    app = FastAPI(
        title="Studio Decision Ledger", version="0.1.0",
        dependencies=[Depends(access.authorize)],
        docs_url=None if access.mode == "private" else "/docs",
        redoc_url=None if access.mode == "private" else "/redoc",
        openapi_url=None if access.mode == "private" else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if access.mode == "public" else [],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if access.mode == "private":
        @app.middleware("http")
        async def private_response_cache(request: Request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            return response

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/workspace/config")
    def workspace_config():
        return {"mode": access.mode, "browser_login": access.store is not None}

    @app.post("/api/workspace/login")
    def workspace_login(body: WorkspaceLoginBody, request: Request, response: Response):
        if access.store is None:
            raise HTTPException(503, "Browser sign-in is not configured")
        access.require_origin(request)
        access.store.login_attempt(request.client.host if request.client else "unknown")
        digest = access.store.digest(body.credential)
        principal = access.principal_for(digest)
        if principal is None:
            raise HTTPException(401, "Invalid studio access key")
        previous = request.cookies.get(COOKIE)
        if previous:
            access.store.logout(previous)
        token = access.store.session(digest)
        response.set_cookie(COOKIE, token, max_age=28800, secure=True, httponly=True, samesite="strict", path="/")
        return {"workspace_id": access.workspace, "subject": principal.subject, "role": principal.role, "mode": "private"}

    @app.post("/api/workspace/logout")
    def workspace_logout(request: Request, response: Response):
        if access.store:
            access.require_origin(request)
            access.store.logout(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return {"signed_out": True}

    @app.get("/api/workspace/session")
    def workspace_session(request: Request) -> dict:
        principal = getattr(request.state, "principal", None)
        return {
            "mode": access.mode,
            "workspace_id": access.workspace or None,
            "subject": principal.subject if principal else None,
            "role": principal.role if principal else None,
        }

    @app.post("/api/imports/preflight")
    def import_preflight(body: ImportPreflightBody) -> dict:
        if access.mode != "private":
            raise HTTPException(403, "Evidence import preflight requires private workspace mode")
        try:
            result = preflight_licenses(body)
            if access.store:
                return access.store.review(result, get_executor())
            return result
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/imports/templates")
    def import_templates():
        return {"templates": [{"table": table, "columns": list(model.model_fields), "required": [name for name, field in model.model_fields.items() if field.is_required()]} for table, model in IMPORT_MODELS.items()]}

    @app.get("/api/imports")
    def import_history():
        if access.store is None:
            raise HTTPException(503, "Durable workspace storage is not configured")
        return {"imports": [{key: value for key, value in receipt.items() if key != "rows"} | {"row_count": len(receipt["rows"])} for receipt in access.store.receipts()]}

    @app.post("/api/imports", status_code=201)
    def commit_import(body: ImportCommitBody, request: Request):
        if access.store is None:
            raise HTTPException(503, "Durable workspace storage is not configured")
        try:
            reviewed = preflight_licenses(ImportPreflightBody(**body.model_dump(include={"table", "source_reference", "csv_text"})))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if not reviewed["valid"] or reviewed["content_sha256"] != body.content_sha256:
            raise HTTPException(409, "File differs from the validated preview. Review it again.")
        return access.store.publish(reviewed, body.expected_head, body.allow_corrections, request.state.principal.subject, get_executor(), get_writer())

    @app.post("/api/imports/{import_id}/retry")
    def retry_import(import_id: str):
        if access.store is None:
            raise HTTPException(503, "Durable workspace storage is not configured")
        return access.store.retry(import_id, get_executor(), get_writer())

    @app.post("/api/decisions", status_code=201)
    def create_decision(
        body: RecordDecisionBody,
        request: Request,
        executor=Depends(get_executor),
        writer=Depends(get_writer),
        model=Depends(get_rationale_model),
    ) -> dict:
        effective_at = body.effective_at
        if effective_at.tzinfo is None:
            effective_at = effective_at.replace(tzinfo=timezone.utc)
        try:
            recorded = make_decision(
                executor, writer,
                title_id=body.title_id,
                territory_code=body.territory_code,
                effective_at=effective_at,
                policy_revision=body.policy_revision,
                model=model,
                supersedes=body.supersedes,
                expected_preview_token=body.expected_preview_token,
                recorded_by=request.state.principal.subject if access.store else "",
            )
        except DecisionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _decision_payload(
            recorded.record, recorded.snapshot, recorded.decision, recorded.facts
        )

    @app.get("/api/catalogue")
    def get_catalogue(executor=Depends(get_executor)) -> dict:
        return {"releases": release_catalogue(executor)}

    @app.get("/api/decisions")
    def get_decisions(
        title_id: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
        executor=Depends(get_executor),
    ) -> dict:
        return {"decisions": list_decisions(executor, title_id=title_id, limit=limit)}

    @app.post("/api/agent/ask")
    def agent_ask(body: AgentAskBody, client=Depends(get_agent_client)) -> dict:
        """Put a question to the agent deployed on Vertex AI Agent Engine.

        Returns the transcript, not just the answer: the tool call and the gate
        it returned travel with the model's text so the console can show where
        the determination came from.
        """
        try:
            return agent_ask_engine(
                client, body.question, session_id=body.session_id
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            # The agent is an assistive surface. A failure here must not read
            # like a decision failure, so it is reported as its own thing.
            raise HTTPException(
                status_code=502, detail=f"The agent could not be reached: {error}"
            ) from error

    @app.post("/api/evidence")
    def preview_evidence(
        body: DecisionRequestBody,
        executor=Depends(get_executor),
    ) -> dict:
        """Answer a release question without recording a decision.

        This is what the operator-facing agent reaches. It takes no writer, so
        an agent cannot leave a receipt in the ledger as a side effect of an
        operator thinking out loud. Recording is a deliberate act performed by
        a person through `POST /api/decisions`.
        """
        effective_at = body.effective_at
        if effective_at.tzinfo is None:
            effective_at = effective_at.replace(tzinfo=timezone.utc)
        previewed = preview_decision(
            executor,
            title_id=body.title_id,
            territory_code=body.territory_code,
            effective_at=effective_at,
            policy_revision=body.policy_revision,
        )
        return {
            "title_id": body.title_id,
            "territory_code": body.territory_code,
            "effective_at": effective_at.isoformat(),
            "outcome": previewed.decision.outcome,
            "rule_hits": list(previewed.decision.rule_hits),
            "blocking_condition": blocking_condition(previewed.decision),
            "policy_revision": previewed.policy_revision,
            "max_revision": previewed.max_revision,
            "retrieval_count": len(previewed.evidence),
            "recorded": False,
            "preview_token": preview_token(previewed, title_id=body.title_id, territory_code=body.territory_code, effective_at=effective_at),
            "evidence_groups": evidence_groups(
                previewed.facts, previewed.decision, previewed.policy_revision
            ),
        }

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

    @app.post("/api/decisions/{decision_id}/integrity-probe")
    def probe_decision_integrity(
        decision_id: str, executor=Depends(get_executor)
    ) -> dict:
        """Measure the current serving path without rewriting the record."""
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        if snapshot is None:
            raise HTTPException(
                status_code=409,
                detail=f"decision {decision_id} names a snapshot that is unavailable",
            )
        return _runtime_integrity_probe(executor, record, snapshot)

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

    @app.post("/api/decisions/{decision_id}/memo")
    def draft_memo_for(decision_id: str, executor=Depends(get_executor)) -> dict:
        """Draft an escalation memo for a recorded decision.

        Drafting only. Nothing is written and nothing is sent — this takes no
        writer, and sending stays a human action in the console.
        """
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")

        decision = Decision(outcome=record.outcome, rule_hits=list(record.rule_hits))

        # Drift is looked up unconditionally rather than behind a flag. A memo
        # about a decision that has since drifted, written as though it had
        # not, is the handoff failing at the moment it matters: the reviewer is
        # told what was true in July and not what is true now.
        snapshot = read_snapshot(executor, record.snapshot_id)
        drift = (
            compare_recorded(executor, record, snapshot)
            if snapshot is not None
            else None
        )

        try:
            model = GeminiRationaleModel(vertex_client(), max_output_tokens=900)
            return draft_memo(
                model,
                record,
                blocking_condition=blocking_condition(decision),
                drift=drift,
            )
        except Exception as error:
            # A memo with no body is nothing, so this is reported rather than
            # returned empty — the opposite of the rationale path, where a
            # decision without an explanation is still a valid decision.
            raise HTTPException(
                status_code=502, detail=f"The memo could not be drafted: {error}"
            ) from error

    @app.get("/api/decisions/{decision_id}/compare")
    def compare_decision(decision_id: str, executor=Depends(get_executor)) -> dict:
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=409, detail="snapshot unavailable")
        return compare_recorded(executor, record, snapshot)

    def resolution_plan_for(decision_id: str, executor) -> dict:
        record = read_decision(executor, decision_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no decision {decision_id}")
        snapshot = read_snapshot(executor, record.snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=409, detail="snapshot unavailable")
        try:
            comparison = compare_recorded(executor, record, snapshot)
            current_rule_hits: list[str] | None = list(
                comparison["current"]["rule_hits"]
            )
        except (MCPQueryError, FileNotFoundError, TimeoutError):
            # Source unavailability is an evidence state, not permission to
            # infer that a blocker remains open or has been resolved.
            current_rule_hits = None
        try:
            items = build_resolution_plan(
                record,
                snapshot,
                current_rule_hits=current_rule_hits,
            )
        except UnsupportedRule as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        all_complete = bool(items) and all(item.status == "COMPLETE" for item in items)
        if not items:
            next_action = "no blocking conditions to resolve"
        elif all_complete:
            next_action = "record a new release decision"
        else:
            next_action = "resolve the next open item"
        return {
            "decision_id": record.decision_id,
            "snapshot_id": record.snapshot_id,
            "policy_revision": record.policy_revision,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "items": [item.to_dict() for item in items],
            "all_complete": all_complete,
            "record_unchanged": True,
            "next_action": next_action,
        }

    @app.get("/api/decisions/{decision_id}/resolution-plan")
    def get_resolution_plan(decision_id: str, executor=Depends(get_executor)) -> dict:
        return resolution_plan_for(decision_id, executor)

    @app.post("/api/decisions/{decision_id}/resolution-plan/recheck")
    def recheck_resolution_plan(decision_id: str, executor=Depends(get_executor)) -> dict:
        return resolution_plan_for(decision_id, executor)

    # The built console is served from the same origin as the API, so the
    # client's relative /api paths need no proxy and no CORS in production.
    # Mounted last: API routes are matched first, and this catches the rest.
    # SDL_CONSOLE_DIR wins so the container can say where the built console
    # lives. Walking up three parents only works when the package is run from
    # a source checkout; in an image the package is installed and `dist` sits
    # somewhere unrelated to site-packages.
    console_dist = Path(
        os.environ.get(
            "SDL_CONSOLE_DIR",
            Path(__file__).resolve().parent.parent.parent / "dist",
        )
    )
    if console_dist.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(console_dist), html=True), name="console"
        )

    return app


app = create_app()
