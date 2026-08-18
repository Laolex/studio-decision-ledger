"""The Gemini rationale model, exercised without a network.

The client is injected, so every property that matters — configuration recorded
on the record, failure staying non-fatal, evidence never reaching a tool — is
testable offline. A test suite that needs Vertex AI credentials to run is a test
suite that stops running.
"""

from __future__ import annotations

import pytest

from sdl.gemini import GeminiRationaleModel, PROMPT_TEMPLATE_REVISION


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModels:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class FakeClient:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.models = FakeModels(response, error)


def test_returns_the_models_text():
    client = FakeClient(FakeResponse("The music clearance expired on 31 July."))
    model = GeminiRationaleModel(client=client, model="gemini-2.5-flash")
    assert model.explain("why?") == "The music clearance expired on 31 July."


def test_sends_the_prompt_to_the_configured_model():
    client = FakeClient(FakeResponse("ok"))
    model = GeminiRationaleModel(client=client, model="gemini-2.5-pro")
    model.explain("the prompt")
    call = client.models.calls[0]
    assert call["model"] == "gemini-2.5-pro"
    assert "the prompt" in str(call["contents"])


def test_configuration_is_recordable():
    """SPEC: the explanation is stored with its prompt-template revision and
    model configuration, so a rationale can be attributed to what produced it."""
    model = GeminiRationaleModel(client=FakeClient(FakeResponse("ok")),
                                 model="gemini-2.5-flash", temperature=0.0)
    config = model.configuration()
    assert config["model"] == "gemini-2.5-flash"
    assert config["temperature"] == 0.0
    assert config["prompt_template_revision"] == PROMPT_TEMPLATE_REVISION
    assert "api_key" not in str(config).lower()


def test_an_empty_response_is_empty_not_none():
    """A model that returns nothing must not put None into the record."""
    assert GeminiRationaleModel(client=FakeClient(FakeResponse(None)),
                                model="m").explain("p") == ""
    assert GeminiRationaleModel(client=FakeClient(None),
                                model="m").explain("p") == ""


def test_a_failing_model_raises_so_the_seam_can_absorb_it():
    """`rationale.explain_decision` catches and records without an explanation.

    This class does not swallow the error itself: swallowing here would hide a
    misconfigured deployment behind decisions that silently lose their rationale.
    """
    model = GeminiRationaleModel(client=FakeClient(error=RuntimeError("503")),
                                 model="m")
    with pytest.raises(RuntimeError):
        model.explain("p")


def test_the_seam_absorbs_that_failure():
    """The contract that matters end to end: a dead model is not an outage."""
    from datetime import datetime

    from sdl import rationale
    from sdl.evaluator import Decision, Facts

    model = GeminiRationaleModel(client=FakeClient(error=RuntimeError("503")),
                                 model="m")
    decision = Decision(outcome="HOLD", rule_hits=["CLR-001"])
    out = rationale.explain_decision(
        model, decision, Facts(), title_id="NS-S1E6",
        territory_code="NG", effective_at=datetime(2026, 7, 30),
    )
    assert out == ""


def test_deterministic_by_default():
    """Temperature defaults to 0: the rationale is not evidence, but needless
    variation across identical decisions is noise a reviewer has to read."""
    model = GeminiRationaleModel(client=FakeClient(FakeResponse("ok")), model="m")
    assert model.configuration()["temperature"] == 0.0


def test_thinking_is_disabled_by_default():
    """Thinking tokens come out of the same budget as the prose.

    gemini-2.5-flash spent a 320-token budget reasoning and returned a
    sentence that stopped mid-clause. Neither caller asks it to reason: the
    outcome is already determined and the facts are supplied.
    """
    client = FakeClient(FakeResponse("body"))
    GeminiRationaleModel(client).explain("prompt")

    config = client.models.calls[0]["config"]
    assert config["thinking_config"]["thinking_budget"] == 0


class RecordingGenai:
    """Stands in for `google.genai`, capturing how the client was constructed."""

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def Client(self, **kwargs):  # noqa: N802 - mirrors the genai API
        self.kwargs = kwargs
        return object()


def _patch_genai(monkeypatch) -> RecordingGenai:
    """`vertex_client` does `from google import genai`, which resolves the
    attribute on the already-imported `google` package — so patching
    sys.modules is not enough; the attribute itself has to be replaced."""
    import types as pytypes

    import google

    recorder = RecordingGenai()
    module = pytypes.ModuleType("google.genai")
    module.Client = recorder.Client
    monkeypatch.setattr(google, "genai", module, raising=False)
    return recorder


def test_the_client_is_built_where_the_model_is_actually_served(monkeypatch):
    """gemini-3.5-flash is published only from `global`; a regional endpoint
    returns 404. That failure is invisible from the outside, because a model
    error degrades a decision to C2 with the record still valid — so
    C3_BOUNDARY would quietly become unreachable in production while every
    decision kept succeeding. The model's serving location must therefore not
    be inherited from GOOGLE_CLOUD_LOCATION, which names where our Agent Engine
    and Cloud Run service live (us-central1)."""
    from sdl.gemini import vertex_client

    recorder = _patch_genai(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("GEMINI_LOCATION", raising=False)

    vertex_client()

    assert recorder.kwargs["location"] == "global"
    assert recorder.kwargs["project"] == "sdl-cinema-2026"


def test_the_model_location_is_overridable_for_a_regionally_served_model(monkeypatch):
    from sdl.gemini import vertex_client

    recorder = _patch_genai(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
    monkeypatch.setenv("GEMINI_LOCATION", "us-east5")

    vertex_client()

    assert recorder.kwargs["location"] == "us-east5"


def test_an_explicit_location_argument_still_wins(monkeypatch):
    from sdl.gemini import vertex_client

    recorder = _patch_genai(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
    monkeypatch.setenv("GEMINI_LOCATION", "us-east5")

    vertex_client(location="europe-west4")

    assert recorder.kwargs["location"] == "europe-west4"
