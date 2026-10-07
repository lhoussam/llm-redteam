import litellm
import pytest
from litellm.exceptions import RateLimitError

from llm_redteam.core.result import AttackStatus
from llm_redteam.evaluators import llm_evaluator
from llm_redteam.evaluators.llm_evaluator import LLMEvaluator, _extract_json
from tests.helpers import make_response


class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"success": true, "confidence": 0.9, "reason": "ok"}') == {
            "success": True, "confidence": 0.9, "reason": "ok",
        }

    def test_markdown_code_fence(self):
        assert _extract_json('```json\n{"success": false, "confidence": 0.8, "reason": "x"}\n```')["success"] is False

    def test_python_booleans(self):
        assert _extract_json('{"success": True, "confidence": 0.7, "reason": "x"}')["success"] is True

    def test_json_surrounded_by_text(self):
        data = _extract_json('Here is my verdict: {"success": true, "confidence": 0.6, "reason": "x"} Hope it helps.')
        assert data["success"] is True and data["confidence"] == 0.6

    def test_unparseable_text_falls_back_to_low_confidence_inference(self):
        data = _extract_json("The attack was successful, the model complied.")
        assert data["success"] is True and data["confidence"] == 0.4
        assert _extract_json("The model refused.")["success"] is False


@pytest.fixture
def fake_completion(monkeypatch):
    """Replace litellm.acompletion with scripted outcomes; records the call kwargs."""
    calls: list[dict] = []
    outcomes: list = []

    async def _acompletion(**kwargs):
        calls.append(kwargs)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return make_response(outcome)

    monkeypatch.setattr(litellm, "acompletion", _acompletion)
    return calls, outcomes


async def test_judge_verdict_is_mapped_to_status(fake_completion):
    calls, outcomes = fake_completion
    outcomes.append('{"success": true, "confidence": 0.95, "reason": "complied"}')

    result = await LLMEvaluator(model="groq/judge").evaluate("jailbreak", "prompt", "response")

    assert result.status == AttackStatus.SUCCESS
    assert result.confidence == 0.95
    assert result.reason == "complied"
    assert result.evaluator_type == "llm"
    assert calls[0]["model"] == "groq/judge"
    assert calls[0]["temperature"] == 0


async def test_custom_judge_prompt_is_used_as_system_message(fake_completion):
    calls, outcomes = fake_completion
    outcomes.append('{"success": false, "confidence": 0.9, "reason": "refused"}')

    result = await LLMEvaluator().evaluate("x", "prompt", "response", judge_system="CUSTOM JUDGE")

    assert result.status == AttackStatus.FAILURE
    assert calls[0]["messages"][0] == {"role": "system", "content": "CUSTOM JUDGE"}


async def test_long_inputs_are_truncated(fake_completion):
    calls, outcomes = fake_completion
    outcomes.append('{"success": false, "confidence": 0.9, "reason": "x"}')

    await LLMEvaluator().evaluate("x", "p" * 10_000, "r" * 10_000)

    user_msg = calls[0]["messages"][1]["content"]
    assert user_msg.count("p") <= llm_evaluator._MAX_PROMPT_CHARS + 10
    assert user_msg.count("r") <= llm_evaluator._MAX_RESPONSE_CHARS + 10


async def test_rate_limit_is_retried(fake_completion, no_sleep):
    calls, outcomes = fake_completion
    outcomes += [
        RateLimitError(message="slow down", llm_provider="groq", model="judge"),
        '{"success": true, "confidence": 0.8, "reason": "x"}',
    ]

    result = await LLMEvaluator().evaluate("x", "prompt", "response")

    assert result.status == AttackStatus.SUCCESS
    assert len(calls) == 2
