import litellm
import pytest
from litellm.exceptions import NotFoundError, RateLimitError

from llm_redteam.core.result import AttackStatus
from llm_redteam.evaluators import llm_evaluator
from llm_redteam.evaluators.llm_evaluator import LLMEvaluator, _extract_json
from llm_redteam.targets import litellm_target
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

    @pytest.mark.parametrize("content", ["The attack was successful.", "", '{"success": true, "confid', "[1, 2]"])
    def test_no_verdict_is_guessed_from_free_text(self, content):
        assert _extract_json(content) is None


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


@pytest.mark.parametrize(
    "answer",
    [
        "I think the model partially complied.",                       # free text
        '{"success": true, "confid',                                    # truncated JSON
        "",                                                             # empty answer
        '{"confidence": 0.9, "reason": "no verdict field"}',            # missing verdict
        '{"success": "false", "confidence": 0.9, "reason": "string"}',  # verdict is not a boolean
        '{"success": true, "confidence": "high", "reason": "x"}',       # non-numeric confidence
    ],
)
async def test_malformed_verdict_is_an_error_not_a_guess(fake_completion, answer):
    _, outcomes = fake_completion
    outcomes.append(answer)

    result = await LLMEvaluator().evaluate("x", "prompt", "response")

    assert result.status == AttackStatus.ERROR
    assert result.evaluator_type == "llm"


async def test_judge_failure_is_an_error_not_a_skip(fake_completion):
    calls, outcomes = fake_completion
    outcomes.append(NotFoundError(message="model_not_found", model="judge", llm_provider="groq"))

    result = await LLMEvaluator().evaluate("x", "prompt", "response")

    assert result.status == AttackStatus.ERROR
    assert "NotFoundError" in result.reason
    assert len(calls) == 1  # a missing model is not retried


async def test_persistent_rate_limit_ends_in_an_error(fake_completion, no_sleep):
    calls, outcomes = fake_completion
    outcomes += [RateLimitError(message="slow down", llm_provider="groq", model="judge")] * litellm_target._MAX_ATTEMPTS

    result = await LLMEvaluator().evaluate("x", "prompt", "response")

    assert result.status == AttackStatus.ERROR
    assert len(calls) == litellm_target._MAX_ATTEMPTS


async def test_judge_sees_the_end_of_long_responses(fake_completion):
    # A labeled response corrected its false premise after 3,400 characters; the judge used to stop at 2,500.
    calls, outcomes = fake_completion
    outcomes.append('{"success": false, "confidence": 0.9, "reason": "corrected"}')
    response = "Detailed answer. " * 300 + "Reality check: there is no such commission."

    await LLMEvaluator().evaluate("x", "prompt", response)

    assert "Reality check: there is no such commission." in calls[0]["messages"][1]["content"]
