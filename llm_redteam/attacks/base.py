import asyncio
from abc import ABC, abstractmethod

from llm_redteam.core.result import AttackResult, AttackStatus, Severity
from llm_redteam.evaluators.base import BaseEvaluator
from llm_redteam.evaluators.strategies import Evaluate
from llm_redteam.targets.base import BaseTarget

_PROBE_DELAY = 1.0  # seconds before each target call (rate limits)


class BaseAttack(ABC):
    name: str
    category: str
    severity: Severity
    description: str

    @abstractmethod
    async def run(
        self,
        target: BaseTarget,
        evaluator: BaseEvaluator | None = None,
    ) -> list[AttackResult]:
        """Run the attack against the target and return one result per probe."""
        ...

    async def _probe(
        self,
        target: BaseTarget,
        attack_name: str,
        prompt: str,
        evaluate: Evaluate,
        *,
        label: str,
        system_prompt: str | None = None,
        baseline: bool = False,
    ) -> AttackResult:
        """Send one prompt, evaluate the response and record the outcome.

        Any exception (target or judge) is recorded as an ERROR result instead of aborting the run.
        Baseline probes (the goal sent without any technique) are recorded as low-severity controls.
        """
        category, severity = ("baseline", Severity.LOW) if baseline else (self.category, self.severity)
        await asyncio.sleep(_PROBE_DELAY)
        try:
            response = await target.send(prompt, system_prompt=system_prompt)
            verdict = await evaluate(label, prompt, response.content)
        except Exception as exc:
            return AttackResult(
                attack_name=attack_name,
                attack_category=category,
                severity=severity,
                prompt=prompt,
                system_prompt=system_prompt,
                response="",
                status=AttackStatus.ERROR,
                evaluator_reason=str(exc),
            )
        return AttackResult(
            attack_name=attack_name,
            attack_category=category,
            severity=severity,
            prompt=prompt,
            system_prompt=system_prompt,
            response=response.content,
            status=verdict.status,
            confidence=verdict.confidence,
            evaluator_reason=verdict.reason,
            evaluator_type=verdict.evaluator_type,
            latency_ms=response.latency_ms,
        )
