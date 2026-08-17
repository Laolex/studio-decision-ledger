"""The console's path to the deployed agent.

The browser cannot reach Agent Engine — it is a Vertex SDK surface, not a
public endpoint — so the API proxies. What matters here is that the proxy
reports what the agent actually did rather than only what it said: the tool
call and the gate it returned are the evidence that the determination came
from the evaluator and not from the model.
"""

from __future__ import annotations

import inspect

import pytest

from sdl.agent_proxy import ask, normalise_events

TOOL_RESPONSE = {
    "outcome": "HOLD",
    "rule_hits": ["LIC-002"],
    "blocking_condition": "The territory grant does not cover this release path.",
    "policy_revision": "POL-2026.07",
    "evidence_groups": [],
}

RAW_EVENTS = [
    {
        "content": {
            "parts": [
                {
                    "function_call": {
                        "name": "query_bound_evidence",
                        "args": {
                            "title_id": "NORTHSTAR-S01E06",
                            "territory_code": "NG",
                            "effective_at": "2026-07-30",
                        },
                    }
                }
            ]
        }
    },
    {
        "content": {
            "parts": [
                {
                    "function_response": {
                        "name": "query_bound_evidence",
                        "response": TOOL_RESPONSE,
                    }
                }
            ]
        }
    },
    {"content": {"parts": [{"text": "The outcome is HOLD. "}, {"text": "The territory grant does not cover this path."}]}},
]


class FakeEngine:
    def __init__(self, events=None, error=None):
        self._events = events if events is not None else RAW_EVENTS
        self._error = error
        self.sessions_created = 0
        self.queries: list[dict] = []

    def create_session(self, *, user_id: str) -> str:
        self.sessions_created += 1
        return f"session-{self.sessions_created}"

    def stream_query(self, *, user_id: str, session_id: str, message: str):
        self.queries.append(
            {"user_id": user_id, "session_id": session_id, "message": message}
        )
        if self._error is not None:
            raise self._error
        return iter(self._events)


def test_a_tool_call_is_reported_with_its_arguments():
    events = normalise_events(RAW_EVENTS)
    call = next(e for e in events if e["kind"] == "tool_call")

    assert call["name"] == "query_bound_evidence"
    assert call["args"]["title_id"] == "NORTHSTAR-S01E06"


def test_the_tool_result_carries_the_gate_the_evaluator_produced():
    events = normalise_events(RAW_EVENTS)
    result = next(e for e in events if e["kind"] == "tool_result")

    assert result["outcome"] == "HOLD"
    assert result["rule_hits"] == ["LIC-002"]
    assert result["blocking_condition"].startswith("The territory grant")


def test_model_text_is_joined_into_one_event():
    """Consecutive text parts are one answer, not several.

    The console renders each event; splitting a sentence across two bubbles
    because the model emitted two parts would be an artifact of transport.
    """
    events = normalise_events(RAW_EVENTS)
    texts = [e for e in events if e["kind"] == "text"]

    assert len(texts) == 1
    assert texts[0]["text"] == (
        "The outcome is HOLD. The territory grant does not cover this path."
    )


def test_events_keep_the_order_the_agent_produced_them():
    kinds = [e["kind"] for e in normalise_events(RAW_EVENTS)]
    assert kinds == ["tool_call", "tool_result", "text"]


def test_a_tool_error_is_reported_rather_than_shown_as_a_gate():
    raw = [
        {
            "content": {
                "parts": [
                    {
                        "function_response": {
                            "name": "query_bound_evidence",
                            "response": {"error": "Could not retrieve evidence"},
                        }
                    }
                ]
            }
        }
    ]
    result = normalise_events(raw)[0]

    assert result["kind"] == "tool_result"
    assert result["error"] == "Could not retrieve evidence"
    assert result["outcome"] is None


def test_asking_creates_a_session_when_none_is_given():
    engine = FakeEngine()
    answer = ask(engine, "Can it ship?", user_id="console")

    assert engine.sessions_created == 1
    assert answer["session_id"] == "session-1"
    assert answer["events"][0]["kind"] == "tool_call"


def test_a_supplied_session_is_reused_so_follow_ups_have_context():
    engine = FakeEngine()
    ask(engine, "And in France?", user_id="console", session_id="session-9")

    assert engine.sessions_created == 0
    assert engine.queries[0]["session_id"] == "session-9"


def test_a_blank_question_is_refused_before_the_agent_is_called():
    engine = FakeEngine()
    with pytest.raises(ValueError):
        ask(engine, "   ", user_id="console")

    assert engine.queries == []


def test_the_proxy_exposes_no_write_capability():
    """Same structural guarantee the agent module carries.

    The proxy is reachable from a browser, so a reviewer should be able to
    establish that it cannot write without reading the implementation.
    """
    import sdl.agent_proxy

    source = inspect.getsource(sdl.agent_proxy)
    assert "from sdl.ledger" not in source
    assert "import sdl.ledger" not in source
    assert "write_decision" not in source


def test_the_endpoint_returns_the_transcript(monkeypatch):
    from fastapi.testclient import TestClient

    from sdl.app import create_app, get_agent_client

    app = create_app()
    app.dependency_overrides[get_agent_client] = lambda: FakeEngine()
    client = TestClient(app)

    response = client.post("/api/agent/ask", json={"question": "Can it ship in NG?"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["session_id"] == "session-1"
    assert [e["kind"] for e in body["events"]] == ["tool_call", "tool_result", "text"]


def test_the_endpoint_says_so_when_no_agent_is_deployed(monkeypatch):
    """An unwired environment is not an error the operator caused."""
    from fastapi.testclient import TestClient

    from sdl.app import create_app

    monkeypatch.delenv("AGENT_ENGINE_RESOURCE", raising=False)
    client = TestClient(create_app())

    response = client.post("/api/agent/ask", json={"question": "Can it ship?"})
    assert response.status_code == 503
    assert "AGENT_ENGINE_RESOURCE" in response.json()["detail"]


def test_an_unreachable_agent_is_not_reported_as_a_decision_failure():
    from fastapi.testclient import TestClient

    from sdl.app import create_app, get_agent_client

    app = create_app()
    app.dependency_overrides[get_agent_client] = lambda: FakeEngine(
        error=RuntimeError("engine down")
    )
    client = TestClient(app)

    response = client.post("/api/agent/ask", json={"question": "Can it ship?"})
    assert response.status_code == 502
    assert "could not be reached" in response.json()["detail"]
