from abc import ABC, abstractmethod
from llm_redteam.core.result import AttackResult, Severity
from llm_redteam.evaluators.llm_evaluator import LLMEvaluator


class BaseAttack(ABC):
    name: str
    category: str
    severity: Severity
    description: str

    @abstractmethod
    async def run(
        self,
        target,
        evaluator: LLMEvaluator | None = None,
    ) -> list[AttackResult]:
        """Lance l'attaque sur la cible et retourne la liste des résultats."""
        ...
