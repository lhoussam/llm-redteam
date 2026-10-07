from abc import ABC, abstractmethod
from dataclasses import dataclass
from llm_redteam.core.result import AttackStatus


@dataclass
class EvaluationResult:
    status: AttackStatus
    confidence: float
    reason: str
    evaluator_type: str = ""


class BaseEvaluator(ABC):
    @abstractmethod
    async def evaluate(
        self,
        attack_name: str,
        prompt: str,
        response: str,
        judge_system: str | None = None,
    ) -> EvaluationResult:
        """Judge whether the attack succeeded. `judge_system` optionally overrides the judging instructions."""
        ...
