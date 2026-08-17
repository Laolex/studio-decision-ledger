"""The operator-facing agent and the read-only tool it is permitted to hold.

Two properties are load-bearing here and both are asserted rather than
documented. The agent's tool set must stay within the permitted list, and the
tool must have no path that writes — the deployed agent is reachable by an
operator typing free text, so "the prompt says not to" is not a control.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sdl.agent import PERMITTED_TOOLS, build_evidence_tool, tool_names

EFFECTIVE = datetime(2026, 9, 1, tzinfo=timezone.utc)

AVAILABLE_PAYLOAD = {
    "title_id": "TITLE-001",
    "territory_code": "GB",
    "effective_at": "2026-09-01T00:00:00+00:00",
    "outcome": "AVAILABLE",
    "rule_hits": [],
    "blocking_condition": "",
    "policy_revision": "POL-2026.07",
    "max_revision": 7,
    "retrieval_count": 5,
    "evidence_groups": [
        {
            "label": "Rights & clearances",
            "tone": "clear",
            "summary": "Ready to release",
            "items": [{"name": "Licence LIC-9", "value": "SVOD · through 01 Jan 2027"}],
        }
    ],
}

ESCALATE_PAYLOAD = {
    **AVAILABLE_PAYLOAD,
    "outcome": "ESCALATE",
    "rule_hits": ["ESC-001"],
    "blocking_condition": "The facts on file are incomplete or contradictory, so no safe determination is possible.",
    "evidence_groups": [
        {"label": "Rights & clearances", "tone": "hold", "summary": "1 blocking condition", "items": []}
    ],
}


class FakeClient:
    """Stands in for the Cloud Run API. Records what it was asked."""

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error
        self.calls: list[dict] = []

    def evidence(self, *, title_id, territory_code, effective_at):
        self.calls.append(
            {
                "title_id": title_id,
                "territory_code": territory_code,
                "effective_at": effective_at,
            }
        )
        if self._error is not None:
            raise self._error
        return self._payload


def test_tool_returns_the_gate_and_the_bound_evidence():
    tool = build_evidence_tool(FakeClient(AVAILABLE_PAYLOAD))
    result = tool("TITLE-001", "GB", "2026-09-01")

    assert result["outcome"] == "AVAILABLE"
    assert result["policy_revision"] == "POL-2026.07"
    assert result["evidence_groups"][0]["label"] == "Rights & clearances"


def test_tool_passes_the_request_through_unaltered():
    client = FakeClient(AVAILABLE_PAYLOAD)
    build_evidence_tool(client)("TITLE-001", "GB", "2026-09-01")

    assert client.calls == [
        {"title_id": "TITLE-001", "territory_code": "GB", "effective_at": "2026-09-01"}
    ]


def test_escalate_is_reported_as_absence_not_resolved_either_way():
    """Invariant 20: absence is reported as absence.

    The tool must hand the model the blocking condition verbatim. If it
    summarised or softened ESCALATE into a HOLD or an AVAILABLE the model
    would present a determination the evaluator refused to make.
    """
    tool = build_evidence_tool(FakeClient(ESCALATE_PAYLOAD))
    result = tool("TITLE-001", "GB", "2026-09-01")

    assert result["outcome"] == "ESCALATE"
    assert "incomplete or contradictory" in result["blocking_condition"]


def test_a_failing_api_becomes_a_reported_error_not_an_exception():
    """A tool raising inside Agent Engine surfaces as an opaque agent failure.

    Returning the failure as data lets the agent tell the operator that it
    could not retrieve evidence, which is a truthful answer. Inventing one
    would not be.
    """
    tool = build_evidence_tool(FakeClient(error=RuntimeError("clickhouse unreachable")))
    result = tool("TITLE-001", "GB", "2026-09-01")

    assert result["error"]
    assert "outcome" not in result


def test_the_agent_holds_exactly_the_permitted_tools():
    """Equality, not a subset.

    A subset assertion passes when a tool is silently missing, which is how a
    deployed agent ended up holding one tool while the spec named three.
    """
    from sdl.agent import build_agent

    agent = build_agent(FakeClient(AVAILABLE_PAYLOAD))
    assert set(tool_names(agent)) == PERMITTED_TOOLS


def test_the_tool_module_exposes_no_write_capability():
    """The guarantee is structural: nothing in the agent path can write.

    `sdl.ledger` holds every write in the system. If the agent module ever
    imports it, this fails — which is the point. A reviewer should not have
    to re-read the module to know the model cannot reach the ledger.
    """
    import inspect

    import sdl.agent

    source = inspect.getsource(sdl.agent)
    assert "from sdl.ledger" not in source
    assert "import sdl.ledger" not in source
    assert "write_decision" not in source


@pytest.mark.parametrize("bad", ["", "   "])
def test_a_blank_title_is_refused_before_a_call_is_made(bad):
    client = FakeClient(AVAILABLE_PAYLOAD)
    result = build_evidence_tool(client)(bad, "GB", "2026-09-01")

    assert result["error"]
    assert client.calls == []


DRIFT_PAYLOAD = {
    "historical": {"outcome": "AVAILABLE", "max_revision": 1, "snapshot_id": "RS-1"},
    "current": {
        "outcome": "HOLD",
        "max_revision": 3,
        "blocking_condition": "The territory grant does not cover this release path.",
    },
    "differences": ["Current data would produce HOLD for the same date."],
    "record_unchanged": True,
}

MEMO_PAYLOAD = {
    "subject": "NORTHSTAR-S01E06 — release gate HOLD for NG",
    "body": "Escalating for rights review.",
    "grounded_in": {
        "decision_id": "D-1846",
        "snapshot_id": "RS-1",
        "policy_revision": "POL-2026.08",
    },
    "sent": False,
}


class FakeFullClient(FakeClient):
    def __init__(self, drift_payload=DRIFT_PAYLOAD, memo_payload=MEMO_PAYLOAD, **kw):
        super().__init__(**kw)
        self._drift = drift_payload
        self._memo = memo_payload
        self.drift_calls: list[str] = []

    def drift(self, *, decision_id):
        self.drift_calls.append(decision_id)
        if isinstance(self._drift, Exception):
            raise self._drift
        return self._drift

    def memo(self, *, decision_id):
        if isinstance(self._memo, Exception):
            raise self._memo
        return self._memo


def test_drift_reports_both_outcomes_and_that_the_record_stands():
    from sdl.agent import build_drift_tool

    result = build_drift_tool(FakeFullClient())("D-1846")

    assert result["recorded_outcome"] == "AVAILABLE"
    assert result["current_outcome"] == "HOLD"
    assert result["drifted"] is True
    assert result["record_unchanged"] is True


def test_no_differences_means_no_drift():
    from sdl.agent import build_drift_tool

    payload = {**DRIFT_PAYLOAD, "differences": []}
    result = build_drift_tool(FakeFullClient(drift_payload=payload))("D-1846")

    assert result["drifted"] is False


def test_a_drift_failure_is_returned_as_data():
    from sdl.agent import build_drift_tool

    client = FakeFullClient(drift_payload=RuntimeError("compare unavailable"))
    result = build_drift_tool(client)("D-1846")

    assert result["error"]
    assert "recorded_outcome" not in result


def test_the_memo_tool_returns_a_draft_marked_unsent():
    from sdl.agent import build_memo_tool

    result = build_memo_tool(FakeFullClient())("D-1846")

    assert result["sent"] is False
    assert result["grounded_in"]["decision_id"] == "D-1846"


def test_the_memo_tool_refuses_a_blank_decision_id():
    from sdl.agent import build_memo_tool

    assert build_memo_tool(FakeFullClient())("  ")["error"]


