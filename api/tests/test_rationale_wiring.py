"""The rationale layer, actually connected to the write path.

`rationale.py` and `gemini.py` were fully built and tested, and
`explain_decision` was never called from anywhere. So `model_rationale` was
empty on every recorded decision, `C3_BOUNDARY` was unreachable in production,
and the README's claim that Gemini's explanation is stored was false.

Both paths are covered here because they end in different classes, and the
difference is the honest ceiling the product is about:

    a stored rationale   → C3_BOUNDARY   evidence and outcome reproduced, the
                                         model's reasoning explicitly not
    no rationale         → C2            evidence and outcome reproduced

A model failure must not cost a decision. The decision is determined before the
model is asked, so an unavailable model costs an explanation and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sdl.ledger import read_policy
from sdl.service import make_decision
from sdl.verifier import verify

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"
EFFECTIVE = datetime(2026, 7, 30, tzinfo=timezone.utc)

RATIONALE = "The territory grant covers AVOD only, so this release path is not licensed."


class WorkingModel:
    def __init__(self, text: str = RATIONALE):
        self._text = text
        self.prompts: list[str] = []

    def explain(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._text

    def configuration(self) -> dict:
        return {
            "provider": "test",
            "model": "fake-1",
            "temperature": 0.0,
            "prompt_template_revision": "rationale-test",
        }


class FailingModel:
    def explain(self, prompt: str) -> str:
        raise RuntimeError("vertex unavailable")

    def configuration(self) -> dict:
        return {"provider": "test", "model": "fake-1"}


def _verify(executor, recorded):
    policy, _sha = read_policy(executor, recorded.record.policy_revision)
    return verify(recorded.record, recorded.snapshot, policy, executor)


def test_a_stored_rationale_replays_at_the_boundary(http_executor, writer):
    model = WorkingModel()
    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE,
        model=model,
    )

    assert recorded.record.model_rationale == RATIONALE
    assert "rationale-test" == recorded.record.prompt_template_revision
    assert "fake-1" in recorded.record.model_config
    assert _verify(http_executor, recorded).capability_class == "C3_BOUNDARY"


def test_the_model_is_asked_about_the_decision_that_was_reached(http_executor, writer):
    """The prompt carries the settled outcome, not a question about it."""
    model = WorkingModel()
    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE,
        model=model,
    )

    prompt = model.prompts[0]
    assert recorded.record.outcome in prompt
    assert TITLE in prompt


def test_a_failing_model_still_records_a_verifiable_decision(http_executor, writer):
    """An unavailable model costs an explanation, never a decision."""
    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE,
        model=FailingModel(),
    )

    assert recorded.record.model_rationale == ""
    assert recorded.record.outcome in {"AVAILABLE", "HOLD", "ESCALATE"}
    assert _verify(http_executor, recorded).capability_class == "C2"


def test_no_model_at_all_is_still_a_valid_decision(http_executor, writer):
    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE,
    )

    assert recorded.record.model_rationale == ""
    assert _verify(http_executor, recorded).capability_class == "C2"


@pytest.mark.parametrize("empty", ["", "   "])
def test_blank_model_text_is_recorded_as_no_rationale(http_executor, writer, empty):
    """Whitespace is not an explanation.

    Stored as-is it would push the record to C3_BOUNDARY, claiming a rationale
    boundary exists when there is nothing there.
    """
    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE,
        model=WorkingModel(empty),
    )

    assert recorded.record.model_rationale == ""
    assert _verify(http_executor, recorded).capability_class == "C2"
