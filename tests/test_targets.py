import litellm
import pytest
from litellm.exceptions import AuthenticationError, RateLimitError

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


async def test_rate_limit_is_retried(completion, no_sleep):
    calls, outcomes = completion
    outcomes.append(RateLimitError(message="slow down", llm_provider="groq", model="m"))

    response = await LiteLLMTarget("groq", "m").send("ping")

    assert response.content == "pong"
    assert len(calls) == 2


async def test_authentication_error_is_not_retried(completion, no_sleep):
    calls, outcomes = completion
    outcomes.append(AuthenticationError(message="bad key", llm_provider="groq", model="m"))

    with pytest.raises(RuntimeError, match="authentication failed"):
        await LiteLLMTarget("groq", "m").send("ping")
    assert len(calls) == 1


async def test_health_check_succeeds_when_target_answers(completion):
    assert await LiteLLMTarget("groq", "m").health_check() is True
