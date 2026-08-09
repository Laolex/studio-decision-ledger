"""Replay verifier.

Takes a stored decision, reloads everything it was bound to, and tries to
reproduce it. Reports a capability class — never a confidence percentage,
because a percentage would imply a probability this process cannot compute.

Requirements are checked in a fixed order and the FIRST failure is reported.
An operator asking "why can't you certify this?" needs one specific answer, not
a list of everything that is also wrong downstream of the first problem.

    NOT_CERTIFIED   a binding is absent or mismatched; the record is not
                    evidence of anything and the verifier says so
    C2              snapshot and policy are present, every hash matches, and
                    deterministic evaluation reproduces the recorded outcome
    C3_BOUNDARY     everything in C2 holds AND the record carries model text.
                    The evidence-to-outcome path is certified; the model's
                    stated reasoning explicitly is not. This is not a downgrade
                    from C2 — it is C2 plus an honest statement of where the
                    evidence stops.

There is deliberately no class above C3_BOUNDARY. Reproducing a model's text
would not establish that the text describes what actually drove the model, and
no amount of recording changes that.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sdl.evaluator import evaluate, ReleaseRequest
from sdl.record import DecisionRecord, EvidenceSnapshot, canonical_json
from sdl.resolve import Executor, canonical_result_hash, resolve_facts

RATIONALE_BOUNDARY = (
    "The evidence-to-outcome path is reproduced. The model rationale is a "
    "stored artifact and is not certified: reproducing it would not show that "
    "it describes what actually drove the model."
)


@dataclass(frozen=True)
class Verification:
    capability_class: str
    failed_requirement: str
    detail: str


def _refuse(requirement: str, detail: str) -> Verification:
    return Verification(
        capability_class="NOT_CERTIFIED", failed_requirement=requirement, detail=detail
    )


def verify(
    record: DecisionRecord,
    snapshot: EvidenceSnapshot | None,
    policy: dict | None,
    executor: Executor,
) -> Verification:
    if snapshot is None:
        return _refuse(
            "snapshot binding",
            f"Decision {record.decision_id} names snapshot "
            f"{record.snapshot_id or '(none)'}, which is not available. Without the "
            "pinned evidence the decision cannot be reconstructed from its record.",
        )

    if snapshot.snapshot_id != record.snapshot_id:
        return _refuse(
            "snapshot binding",
            f"Decision {record.decision_id} names snapshot {record.snapshot_id} but "
            f"snapshot {snapshot.snapshot_id} was supplied.",
        )

    if policy is None:
        return _refuse(
            "policy revision",
            f"Policy revision {record.policy_revision} is not available, so the rules "
            "applied at the time cannot be re-applied.",
        )

    recomputed_policy_hash = hashlib.sha256(
        canonical_json(policy).encode("utf-8")
    ).hexdigest()
    if recomputed_policy_hash != record.policy_sha256:
        return _refuse(
            "policy hash",
            f"Policy {record.policy_revision} no longer hashes to the value bound at "
            "decision time. The rules have been edited since.",
        )

    # Re-issue each pinned retrieval and compare against the recorded hash.
    for fact in snapshot.facts:
        rows = executor(fact["canonical_query"])
        if canonical_result_hash(rows) != fact["result_hash"]:
            return _refuse(
                "evidence result hash",
                f"Re-running the pinned query against {fact['table_name']} returned "
                "different data from what the decision was made against.",
            )

    facts, _evidence = resolve_facts(
        executor, record.title_id, record.territory_code, snapshot.max_revision
    )
    replayed = evaluate(
        ReleaseRequest(
            title_id=record.title_id,
            territory_code=record.territory_code,
            effective_at=record.effective_at,
        ),
        facts,
        policy,
    )

    if replayed.outcome != record.outcome or list(replayed.rule_hits) != list(record.rule_hits):
        return _refuse(
            "reproduced outcome",
            f"Replay produced {replayed.outcome} {replayed.rule_hits} but the record "
            f"states {record.outcome} {list(record.rule_hits)}.",
        )

    if record.model_rationale:
        return Verification(
            capability_class="C3_BOUNDARY", failed_requirement="", detail=RATIONALE_BOUNDARY
        )

    return Verification(
        capability_class="C2",
        failed_requirement="",
        detail=(
            f"Reproduced {record.outcome} from the pinned evidence at revision "
            f"{snapshot.max_revision} under policy {record.policy_revision}."
        ),
    )
