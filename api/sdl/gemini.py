"""Gemini on Vertex AI as the rationale model.

This implements `rationale.RationaleModel` and nothing more. The outcome is
already settled by the deterministic evaluator before this is reached, so the
model's only job is to put a decided fact into language an operator can act on.
It holds no write path and reaches no tool from here.

Two deliberate choices:

Temperature defaults to 0. The rationale is an artifact, never evidence, and the
verifier already refuses to claim a new invocation reproduces earlier text. But
identical decisions producing needlessly different paragraphs is noise a reviewer
has to read, and there is nothing to gain from sampling here.

Thinking is disabled. gemini-3.5-flash is a reasoning model and its thinking
tokens are drawn from the same `max_output_tokens` budget as its prose, so a
short budget produces a sentence that stops mid-clause rather than a short
answer. Neither the rationale nor the memo asks the model to reason — the
outcome is already determined and the facts are supplied — so the budget is
better spent entirely on the words.

Errors propagate. `rationale.explain_decision` catches them and records the
decision without an explanation, which is the correct end-to-end behaviour. If
this class swallowed the error instead, a misconfigured deployment would look
exactly like a working one — every decision quietly losing its rationale with
nothing in the logs to say why.
"""

from __future__ import annotations

import os
from typing import Any

# Bump when SYSTEM_FRAMING or build_prompt changes shape. The revision is stored
# beside every rationale so an explanation can be attributed to what produced it.
PROMPT_TEMPLATE_REVISION = "rationale-2026-08-10"

DEFAULT_MODEL = "gemini-3.5-flash"

# Where the MODEL is served, which is not where our resources live.
#
# A Vertex publisher model is a per-region resource, and a new model is enabled
# region by region. Where it is not enabled the resource genuinely does not
# exist, so 404 is the literally correct answer rather than a capacity error.
# gemini-3.5-flash is served from some regions and not others — measured against
# this project on 2026-08-18: global 200, europe-west2 200, us-central1 404,
# europe-west4 404. `global` is the safe default because it routes to wherever
# the model actually is, and that set will grow as the rollout continues.
#
# GOOGLE_CLOUD_LOCATION stays us-central1 because the Agent Engine and Cloud Run
# service genuinely are there, so the model's serving location has to be its own
# fact rather than a reuse of the resource region. Overridable for a model that
# is served regionally.
DEFAULT_MODEL_LOCATION = "global"


def vertex_client(project: str | None = None, location: str | None = None) -> Any:
    """Build a Vertex AI client from the ambient Google credentials.

    Kept separate from the model class so that constructing the model in a test
    never reaches for credentials. Raises if the project is not configured,
    rather than falling back to an unauthenticated path that fails later and
    less clearly.
    """
    from google import genai

    project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = location or os.environ.get("GEMINI_LOCATION", DEFAULT_MODEL_LOCATION)
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set. Run `gcloud auth application-default "
            "login` and set the project before starting the service with a live model."
        )
    return genai.Client(vertexai=True, project=project, location=location)


class GeminiRationaleModel:
    """Turns a prompt into an explanation. One implementation of the seam."""

    def __init__(self, client: Any, model: str = DEFAULT_MODEL,
                 temperature: float = 0.0, max_output_tokens: int = 320,
                 thinking_budget: int = 0) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._thinking_budget = thinking_budget

    def explain(self, prompt: str) -> str:
        """Return the model's text, or an empty string if it produced none."""
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config={
                "temperature": self._temperature,
                "max_output_tokens": self._max_output_tokens,
                "thinking_config": {"thinking_budget": self._thinking_budget},
            },
        )
        text = getattr(response, "text", None)
        return text if text else ""

    def configuration(self) -> dict:
        """What is recorded beside the rationale. Never contains a credential."""
        return {
            "provider": "vertex-ai",
            "model": self._model,
            "temperature": self._temperature,
            "max_output_tokens": self._max_output_tokens,
            "thinking_budget": self._thinking_budget,
            "prompt_template_revision": PROMPT_TEMPLATE_REVISION,
        }
