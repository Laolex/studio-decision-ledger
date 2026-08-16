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


def test_the_agent_holds_only_permitted_tools():
    from sdl.agent import build_agent

    agent = build_agent(FakeClient(AVAILABLE_PAYLOAD))
    assert set(tool_names(agent)) <= PERMITTED_TOOLS


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
