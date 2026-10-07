from collections.abc import Callable

import litellm

from llm_redteam.core.result import AttackResult, AttackStatus, Severity
from llm_redteam.evaluators.base import EvaluationResult
from llm_redteam.targets.base import BaseTarget, TargetResponse

REFUSAL = "I'm sorry, but I can't help with that."


class FakeTarget(BaseTarget):
    """Offline target returning scripted responses and recording every call."""

    name = "fake"

    def __init__(self, reply: str | Callable[[str, str | None], str] = REFUSAL) -> None:
        self.model = "fake-model"
        self._reply = reply
        self.calls: list[tuple[str, str | None]] = []

    async def send(self, prompt: str, system_prompt: str | None = None) -> TargetResponse:
        self.calls.append((prompt, system_prompt))
        content = self._reply(prompt, system_prompt) if callable(self._reply) else self._reply
        return TargetResponse(content=content, model=self.model, latency_ms=1.0)

    async def health_check(self) -> bool:
        return True


class FakeJudge:
    """Offline stand-in for LLMEvaluator returning a fixed verdict."""

    def __init__(self, status: AttackStatus = AttackStatus.SUCCESS) -> None:
        self.status = status
        self.calls: list[dict] = []

    async def evaluate(
        self, attack_name: str, prompt: str, response: str, judge_system: str | None = None
    ) -> EvaluationResult:
        self.calls.append(
            {"attack_name": attack_name, "prompt": prompt, "response": response, "judge_system": judge_system}
        )
        return EvaluationResult(status=self.status, confidence=0.75, reason="fake verdict", evaluator_type="llm")


def make_response(content: str, model: str = "test-model") -> litellm.ModelResponse:
    return litellm.ModelResponse(
        choices=[{"message": {"content": content, "role": "assistant"}}],
        model=model,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


def make_result(category: str, status: AttackStatus, confidence: float = 0.9) -> AttackResult:
    return AttackResult(
        attack_name=f"{category}/probe",
        attack_category=category,
        severity=Severity.HIGH,
        prompt="prompt",
        response="response",
        status=status,
        confidence=confidence,
    )
