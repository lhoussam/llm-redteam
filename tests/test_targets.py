import litellm
import pytest

from llm_redteam.targets.groq_target import GroqTarget
from llm_redteam.targets.ollama_target import OllamaTarget
from llm_redteam.targets.openai_target import OpenAITarget
from tests.helpers import make_response


@pytest.fixture
def completion_calls(monkeypatch):
    calls: list[dict] = []

    async def _acompletion(**kwargs):
        calls.append(kwargs)
        return make_response("pong", model=kwargs["model"])

    monkeypatch.setattr(litellm, "acompletion", _acompletion)
    return calls


@pytest.mark.parametrize(
    ("target", "expected_model"),
    [
        (OpenAITarget(model="gpt-4o-mini"), "gpt-4o-mini"),
        (GroqTarget(model="openai/gpt-oss-20b"), "groq/openai/gpt-oss-20b"),
        (OllamaTarget(model="llama3.2"), "ollama/llama3.2"),
    ],
)
async def test_send_builds_a_deterministic_request(completion_calls, target, expected_model):
    response = await target.send("ping", system_prompt="be brief")

    call = completion_calls[0]
    assert call["model"] == expected_model
    assert call["temperature"] == 0
    assert call["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "ping"},
    ]
    assert response.content == "pong"
    assert response.tokens_used == 15
    assert response.latency_ms >= 0


async def test_system_prompt_is_optional(completion_calls):
    await GroqTarget(model="m").send("ping")
    assert completion_calls[0]["messages"] == [{"role": "user", "content": "ping"}]


async def test_ollama_uses_configured_base_url(completion_calls, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:1234")
    await OllamaTarget(model="llama3.2").send("ping")
    assert completion_calls[0]["api_base"] == "http://ollama.local:1234"


async def test_health_check_succeeds_when_target_answers(completion_calls):
    assert await GroqTarget(model="m").health_check() is True
