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


def test_the_prompt_forbids_the_model_writing_its_own_subject_line():
    """The subject is generated; a model-written one is discarded.

    Left unsaid, the model opens with `Subject: ...` and spends its budget
    on a header nobody reads.
    """
    prompt = build_prompt(RECORD, blocking_condition=BLOCKING)
    assert "subject line" in prompt.lower()


def test_a_decision_with_nothing_blocking_is_not_forced_into_an_escalation():
    """An AVAILABLE decision has no problem to escalate.

    Without this the model invents one, because the prompt asked it to
    explain a block that does not exist.
    """
    clear = DecisionRecord(**{**RECORD.__dict__, "outcome": "AVAILABLE", "rule_hits": []})
    prompt = build_prompt(clear, blocking_condition="")

    assert "none recorded" in prompt
    assert "status note" in prompt.lower()


DRIFT = {
    "historical": {"outcome": "AVAILABLE", "max_revision": 1},
    "current": {
        "outcome": "HOLD",
        "max_revision": 3,
        "blocking_condition": "The territory grant does not cover this release path.",
    },
    "differences": ["Current data would produce HOLD for the same date."],
    "record_unchanged": True,
}


def test_a_drifted_memo_binds_both_truths():
    """The reviewer needs what was true then and what is true now.

    A memo describing only the historical position tells them what was true
    in July, which is the handoff failing at the moment it matters.
    """
    prompt = build_prompt(RECORD, blocking_condition=BLOCKING, drift=DRIFT)

    assert "Drift since this decision was recorded" in prompt
    assert "Recorded: AVAILABLE at evidence revision 1" in prompt
    assert "would produce: HOLD at revision 3" in prompt
    assert "unchanged and remains as taken" in prompt


def test_the_drifted_subject_names_both_outcomes():
    memo = draft_memo(FakeModel(), RECORD, blocking_condition=BLOCKING, drift=DRIFT)

    assert memo["drifted"] is True
    assert "recorded HOLD" in memo["subject"]
    assert "would produce HOLD" in memo["subject"]


def test_an_undrifted_record_gets_no_drift_section():
    """No empty section for the model to narrate into significance."""
    steady = {**DRIFT, "differences": []}
    prompt = build_prompt(RECORD, blocking_condition=BLOCKING, drift=steady)

    assert "Drift since" not in prompt
    memo = draft_memo(FakeModel(), RECORD, blocking_condition=BLOCKING, drift=steady)
    assert memo["drifted"] is False


def test_drift_is_optional_so_the_plain_memo_still_works():
    memo = draft_memo(FakeModel(), RECORD, blocking_condition=BLOCKING)
    assert memo["drifted"] is False
