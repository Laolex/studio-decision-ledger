"""Drafting an escalation memo.

A memo is a draft and stays a draft. The model writes prose; sending it,
approving an exception, and resolving a hold are human actions performed
through the console (SPEC "Architecture"). Nothing in this path writes, and
the draft is not recorded — an unsent draft stored in the ledger would look
like an artifact of something that happened, and nothing happened.

The memo must carry its grounding. A paragraph about a release that does not
name the decision, the snapshot and the policy revision it came from is an
opinion; with them it is a citation a reader can follow back.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from sdl.memo import MEMO_TEMPLATE_REVISION, build_prompt, draft_memo
from sdl.record import DecisionRecord

RECORD = DecisionRecord(
    decision_id="D-1846",
    title_id="NORTHSTAR-S01E06",
    territory_code="NG",
    effective_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    policy_revision="POL-2026.08",
    policy_sha256="a" * 64,
    snapshot_id="RS-2026-07-30-1F2E",
    outcome="HOLD",
    rule_hits=["LIC-002"],
    decided_at=datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
)

BLOCKING = "The territory grant does not cover this release path."


class FakeModel:
    def __init__(self, text="Drafted memo body.", error=None):
        self._text = text
        self._error = error
        self.prompts: list[str] = []

    def explain(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        return self._text


def test_the_draft_is_grounded_in_the_record_it_came_from():
    memo = draft_memo(FakeModel(), RECORD, blocking_condition=BLOCKING)

    assert memo["grounded_in"] == {
        "decision_id": "D-1846",
        "snapshot_id": "RS-2026-07-30-1F2E",
        "policy_revision": "POL-2026.08",
    }
    assert memo["template_revision"] == MEMO_TEMPLATE_REVISION


def test_the_subject_names_the_title_territory_and_gate():
    memo = draft_memo(FakeModel(), RECORD, blocking_condition=BLOCKING)

    assert "NORTHSTAR-S01E06" in memo["subject"]
    assert "NG" in memo["subject"]
    assert "HOLD" in memo["subject"]


def test_the_body_is_the_model_text():
    memo = draft_memo(FakeModel("Escalating for rights review."), RECORD,
                      blocking_condition=BLOCKING)
    assert memo["body"] == "Escalating for rights review."


def test_the_prompt_carries_the_blocking_condition_and_the_bindings():
    model = FakeModel()
    draft_memo(model, RECORD, blocking_condition=BLOCKING)
    prompt = model.prompts[0]

    assert BLOCKING in prompt
    assert "D-1846" in prompt
    assert "RS-2026-07-30-1F2E" in prompt
    assert "POL-2026.08" in prompt


def test_the_prompt_forbids_sending_or_deciding():
    prompt = build_prompt(RECORD, blocking_condition=BLOCKING)
    lowered = prompt.lower()

    assert "draft" in lowered
    assert "do not" in lowered


def test_a_model_failure_is_not_an_empty_memo():
    """Unlike a rationale, the memo *is* the deliverable.

    A decision without an explanation is still a valid decision, so
    rationale swallows model errors. A memo with no body is nothing, so this
    raises and lets the endpoint say the draft could not be produced.
    """
    with pytest.raises(RuntimeError):
        draft_memo(FakeModel(error=RuntimeError("model down")), RECORD,
                   blocking_condition=BLOCKING)


def test_the_memo_module_exposes_no_write_capability():
    import sdl.memo

    source = inspect.getsource(sdl.memo)
    assert "from sdl.ledger" not in source
    assert "import sdl.ledger" not in source
    assert "write_decision" not in source
    assert "writer" not in source
