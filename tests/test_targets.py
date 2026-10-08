import litellm
import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    Timeout,
)

from llm_redteam.targets import PROVIDERS, LiteLLMTarget
from tests.helpers import make_response


@pytest.fixture
def completion(monkeypatch):
    """Replace litellm.acompletion with scripted outcomes ("pong" by default); records the call kwargs."""
    calls: list[dict] = []
    outcomes: list = []

    async def _acompletion(**kwargs):
        calls.append(kwargs)
        outcome = outcomes.pop(0) if outcomes else "pong"
        if isinstance(outcome, Exception):
            raise outcome
        return make_response(outcome, model=kwargs["model"])

    monkeypatch.setattr(litellm, "acompletion", _acompletion)
    return calls, outcomes


@pytest.mark.parametrize(
    ("provider", "model", "expected_model"),
    [
        ("openai", "gpt-4o-mini", "gpt-4o-mini"),
        ("anthropic", "claude-model", "anthropic/claude-model"),
        ("mistral", "mistral-small-latest", "mistral/mistral-small-latest"),
        ("groq", "openai/gpt-oss-20b", "groq/openai/gpt-oss-20b"),
        ("ollama", "llama3.2", "ollama/llama3.2"),
    ],
)
async def test_send_builds_a_deterministic_request(completion, provider, model, expected_model):
    calls, _ = completion

    response = await LiteLLMTarget(provider, model).send("ping", system_prompt="be brief")

    call = calls[0]
    assert call["model"] == expected_model
    assert call["temperature"] == 0
    assert call["timeout"] == PROVIDERS[provider]["timeout"]
    assert call["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "ping"},
    ]
    assert response.content == "pong"
    assert response.tokens_used == 15
    assert response.latency_ms >= 0


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown provider"):
        LiteLLMTarget("teleport-ai", "model")


async def test_system_prompt_is_optional(completion):
    calls, _ = completion
    await LiteLLMTarget("groq", "m").send("ping")
    assert calls[0]["messages"] == [{"role": "user", "content": "ping"}]


async def test_only_ollama_sets_a_base_url(completion, monkeypatch):
    calls, _ = completion
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:1234")

    await LiteLLMTarget("ollama", "llama3.2").send("ping")
    await LiteLLMTarget("groq", "m").send("ping")

    assert calls[0]["api_base"] == "http://ollama.local:1234"
    assert "api_base" not in calls[1]


@pytest.mark.parametrize(
    "transient",
    [
        RateLimitError(message="slow down", llm_provider="groq", model="m"),
        Timeout(message="timed out", model="m", llm_provider="groq"),
        APIConnectionError(message="connection reset", llm_provider="groq", model="m"),
        InternalServerError(message="500", llm_provider="groq", model="m"),
    ],
)
async def test_transient_errors_are_retried(completion, no_sleep, transient):
    calls, outcomes = completion
    outcomes.append(transient)

    response = await LiteLLMTarget("groq", "m").send("ping")

    assert response.content == "pong"
    assert len(calls) == 2


async def test_transient_errors_give_up_after_the_last_retry(completion, no_sleep):
    calls, outcomes = completion
    outcomes += [RateLimitError(message="slow down", llm_provider="groq", model="m")] * 3

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await LiteLLMTarget("groq", "m").send("ping")
    assert len(calls) == 3


@pytest.mark.parametrize(
    "permanent",
    [
        # What Groq actually returns for an expired key and for a retired model.
        BadRequestError(message="Invalid API Key (expired_api_key)", model="m", llm_provider="groq"),
        NotFoundError(message="The model does not exist (model_not_found)", model="m", llm_provider="groq"),
    ],
)
async def test_permanent_errors_fail_immediately(completion, no_sleep, permanent):
    calls, outcomes = completion
    outcomes.append(permanent)

    with pytest.raises(RuntimeError, match="request failed"):
        await LiteLLMTarget("groq", "m").send("ping")
    assert len(calls) == 1


async def test_authentication_error_is_not_retried(completion, no_sleep):
    calls, outcomes = completion
    outcomes.append(AuthenticationError(message="bad key", llm_provider="groq", model="m"))

    with pytest.raises(RuntimeError, match="authentication failed"):
        await LiteLLMTarget("groq", "m").send("ping")
    assert len(calls) == 1


async def test_health_check_succeeds_when_target_answers(completion):
    assert await LiteLLMTarget("groq", "m").health_check() is True
