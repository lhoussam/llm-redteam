import base64

from llm_redteam.core.result import AttackResult, Severity
from llm_redteam.datasets import load_dataset
from llm_redteam.evaluators.base import BaseEvaluator
from llm_redteam.evaluators.strategies import keyword_then_judge
from llm_redteam.targets.base import BaseTarget
from .base import BaseAttack

# (technique, whether the goal is base64-encoded in the prompt template)
_TECHNIQUES = [
    ("dan", False),
    ("roleplay", False),
    ("hypothetical", False),
    ("base64", True),
]


def _build_prompt(seed: str, goal: str, is_base64: bool) -> str:
    if is_base64:
        return seed.replace("{base64_goal}", base64.b64encode(goal.encode()).decode())
    return seed.replace("{goal}", goal)


class JailbreakAttack(BaseAttack):
    name = "jailbreak"
    category = "jailbreak"
    severity = Severity.CRITICAL
    description = "Tests if the model can be jailbroken via DAN, roleplay, hypothetical, and Base64 techniques"

    async def run(
        self,
        target: BaseTarget,
        evaluator: BaseEvaluator | None = None,
    ) -> list[AttackResult]:
        dataset = load_dataset("jailbreaks")
        evaluate = keyword_then_judge(evaluator)
        results: list[AttackResult] = []

        for g_idx, goal in enumerate(dataset["goals"]):
            goal_tag = f"g{g_idx + 1}"

            # Baseline: the raw goal, without any technique, to check the guardrails are active at all.
            results.append(await self._probe(
                target, f"baseline/jailbreak_{goal_tag}", goal, evaluate, label="baseline_0", baseline=True,
            ))

            for tech_name, is_base64 in _TECHNIQUES:
                for i, seed in enumerate(dataset[tech_name]):
                    results.append(await self._probe(
                        target,
                        f"{self.name}/{tech_name}_{i + 1}_{goal_tag}",
                        _build_prompt(seed, goal, is_base64),
                        evaluate,
                        label=f"{tech_name}_{i + 1}",
                    ))

        return results
