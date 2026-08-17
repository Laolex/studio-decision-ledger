"""The console's path to the agent deployed on Vertex AI Agent Engine.

Agent Engine is a Vertex SDK surface, not a public endpoint, so a browser
cannot call it. This module is the proxy the console reaches instead.

It reports what the agent *did*, not only what it said. A transcript that
showed the model's paragraph alone would leave a reader unable to tell whether
the outcome came from the deterministic evaluator or from the model — which is
the single impression the read-only design exists to prevent. So the tool call
and the gate it returned are first-class events the console renders above the
answer.

Nothing here writes. The proxy is the most reachable surface in the system, so
that guarantee is asserted by a test against this file's own source rather
than left to review.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

CONSOLE_USER = "console"


class AgentEngineClient(Protocol):
    """The slice of Agent Engine this module uses. A fake satisfies it in tests."""

    def create_session(self, *, user_id: str) -> str: ...

    def stream_query(
        self, *, user_id: str, session_id: str, message: str
    ) -> Iterable[dict]: ...


class VertexAgentEngine:
    """The deployed engine, addressed by resource name."""

    def __init__(self, resource_name: str) -> None:
        self._resource_name = resource_name
        self._engine: Any = None

    def _get(self) -> Any:
        if self._engine is None:
            from vertexai import agent_engines

            self._engine = agent_engines.get(self._resource_name)
        return self._engine

    def create_session(self, *, user_id: str) -> str:
        session = self._get().create_session(user_id=user_id)
        # The SDK has returned both a mapping and an object across versions.
        return session["id"] if isinstance(session, dict) else session.id

    def stream_query(
        self, *, user_id: str, session_id: str, message: str
    ) -> Iterable[dict]:
        return self._get().stream_query(
            user_id=user_id, session_id=session_id, message=message
        )


def _tool_result(name: str, response: dict) -> dict:
    """Flatten a tool response into what the console shows.

    Only `query_bound_evidence` produces a release gate. Drift checks and memo
    drafts return neither an outcome nor rule hits, and flattening them into
    the gate shape rendered an empty verdict in the console — a decision that
    was never made, displayed as though it had been. So `outcome` is present
    only when the tool actually determined one, and everything else travels as
    scalar `detail` the console lists plainly.
    """
    error = response.get("error")
    outcome = None if error else response.get("outcome")
    detail = {
        key: value
        for key, value in response.items()
        if key not in ("error", "outcome", "rule_hits", "evidence_groups")
        and isinstance(value, (str, bool, int, float))
        and value != ""
    }
    return {
        "kind": "tool_result",
        "name": name,
        "outcome": outcome,
        "rule_hits": list(response.get("rule_hits") or []),
        "blocking_condition": response.get("blocking_condition", ""),
        "policy_revision": response.get("policy_revision", ""),
        "detail": detail,
        "error": error,
    }


def normalise_events(raw_events: Iterable[dict]) -> list[dict]:
    """Turn ADK stream events into the console's transcript.

    Consecutive model text is joined. A model may emit a sentence across
    several parts; rendering those as separate answers would show a transport
    detail as if it were structure.
    """
    events: list[dict] = []
    pending_text: list[str] = []

    def flush() -> None:
        if pending_text:
            events.append({"kind": "text", "text": "".join(pending_text).strip()})
            pending_text.clear()

    for event in raw_events:
        parts = (event.get("content") or {}).get("parts") or []
        for part in parts:
            call = part.get("function_call")
            if call:
                flush()
                events.append(
                    {
                        "kind": "tool_call",
                        "name": call.get("name", ""),
                        "args": dict(call.get("args") or {}),
                    }
                )
                continue

            response = part.get("function_response")
            if response:
                flush()
                events.append(
                    _tool_result(
                        response.get("name", ""), dict(response.get("response") or {})
                    )
                )
                continue

            text = part.get("text")
            if text:
                pending_text.append(text)

    flush()
    return events


def ask(
    client: AgentEngineClient,
    question: str,
    *,
    user_id: str = CONSOLE_USER,
    session_id: str | None = None,
) -> dict:
    """Put a question to the agent and return the transcript.

    `session_id` is threaded back by the console so a follow-up ("and in
    France?") reaches the same session and keeps its context.
    """
    if not question or not question.strip():
        raise ValueError("A question is required.")

    if not session_id:
        session_id = client.create_session(user_id=user_id)

    raw = client.stream_query(
        user_id=user_id, session_id=session_id, message=question.strip()
    )
    return {"session_id": session_id, "events": normalise_events(raw)}


def resource_name() -> str | None:
    """The deployed engine, or None when the console should say it is unwired."""
    return os.environ.get("AGENT_ENGINE_RESOURCE") or None
