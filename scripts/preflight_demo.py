#!/usr/bin/env python3
"""Pre-flight for a recorded demo. Run it before the camera, not after.

    python3 scripts/preflight_demo.py [base_url]

This is the opposite of `negative_control.py`. That one is offline and proves a
property of the verifier. This one talks to the live deployment and proves the
beats a demo depends on are actually working right now. It exits non-zero if any
of them is not, so a failed pre-flight stops a recording session instead of
being discovered in the edit.

Six checks, in the order the demo needs them:

1. The service answers.
2. Retrieval works — this also warms the ClickHouse MCP subprocess, which
   cold-starts slowly enough after a deploy to look broken on camera.
3. A freshly recorded decision carries a non-empty `model_rationale`. This is
   the assertion that matters most: `rationale.explain_decision` was once built
   and never called, leaving every record without prose and `C3_BOUNDARY`
   unreachable. If that regresses, the replay beat is wrong.
4. That decision replays at `C3_BOUNDARY`.
5. A compound request makes the agent call more than one tool. It used to reply
   "which would you like first?" and call none, which is the multi-step
   behaviour the demo is about.
6. The drift memo describes the drift and is marked unsent.

Check 3 records a real decision in the ledger. That is the product's normal
function and a deliberate act, not a side effect — but it is the reason this
script is not run casually.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://sdl-ntvbh3dlvq-uc.a.run.app"
TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"
EFFECTIVE_AT = "2026-07-30T00:00:00Z"
DRIFTED_DECISION = "D-1846"
COMPOUND_REQUEST = (
    "What changed after D-1846, is the release now at risk, and prepare the "
    "reviewer handoff."
)


def call(base: str, path: str, payload: dict | None = None, method: str = "GET",
         timeout: int = 300) -> dict:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8")[:300]
        raise RuntimeError(f"{error.code} from {path}: {detail}") from error


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE).rstrip("/")
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            ok, note = fn()
        except Exception as error:  # a failed check is a result, not a crash
            ok, note = False, str(error)[:160]
        results.append((name, ok, note))

    def service_answers():
        body = call(base, "/api/health")
        return body.get("status") == "ok", str(body)

    def retrieval_works():
        body = call(
            base,
            "/api/evidence",
            {"title_id": TITLE, "territory_code": TERRITORY, "effective_at": EFFECTIVE_AT},
            "POST",
        )
        return body.get("outcome") in {"AVAILABLE", "HOLD", "ESCALATE"}, (
            f"{body.get('outcome')} {body.get('rule_hits')}"
        )

    recorded: dict = {}

    def fresh_decision_has_prose():
        recorded.update(
            call(
                base,
                "/api/decisions",
                {"title_id": TITLE, "territory_code": TERRITORY, "effective_at": EFFECTIVE_AT},
                "POST",
            )
        )
        prose = (recorded.get("model_rationale") or "").strip()
        return bool(prose), (
            f"{recorded.get('decision_id')} · {len(prose)} chars"
            if prose
            else f"{recorded.get('decision_id')} · EMPTY — the rationale layer is not wired"
        )

    def it_replays_at_the_boundary():
        decision_id = recorded.get("decision_id")
        if not decision_id:
            return False, "no decision was recorded"
        body = call(base, f"/api/decisions/{decision_id}/verify", None, "POST")
        actual = body.get("capability_class")
        return actual == "C3_BOUNDARY", f"{actual}"

    def compound_request_chains_tools():
        body = call(base, "/api/agent/ask", {"question": COMPOUND_REQUEST}, "POST")
        calls = [e["name"] for e in body.get("events", []) if e.get("kind") == "tool_call"]
        return len(calls) >= 2, ", ".join(calls) or "no tools called"

    def memo_describes_the_drift():
        body = call(base, f"/api/decisions/{DRIFTED_DECISION}/memo", None, "POST")
        ok = body.get("drifted") is True and body.get("sent") is False
        return ok, f"drifted={body.get('drifted')} sent={body.get('sent')}"

    check("Service answers", service_answers)
    check("Retrieval works (warms MCP)", retrieval_works)
    check("Fresh decision carries prose", fresh_decision_has_prose)
    check("It replays at C3_BOUNDARY", it_replays_at_the_boundary)
    check("Compound request chains tools", compound_request_chains_tools)
    check("Drift memo, marked unsent", memo_describes_the_drift)

    width = max(len(name) for name, _ok, _n in results)
    print(f"\nPre-flight against {base}\n")
    for name, ok, note in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name.ljust(width)}  {note}")

    failed = [name for name, ok, _n in results if not ok]
    if failed:
        print(f"\nDo not record. {len(failed)} check(s) failed: {', '.join(failed)}\n")
        return 1
    print("\nAll checks passed. Safe to record.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
