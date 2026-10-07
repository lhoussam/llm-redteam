import asyncio
import base64
from pathlib import Path

import yaml

from llm_redteam.core.result import AttackResult, AttackStatus, Severity
from llm_redteam.evaluators.keyword_evaluator import KeywordEvaluator
from llm_redteam.evaluators.llm_evaluator import LLMEvaluator
from .base import BaseAttack

_DATASET_PATH = Path(__file__).parent.parent.parent / "datasets" / "jailbreaks.yaml"
with open(_DATASET_PATH, encoding="utf-8") as _f:
    _DATASET: dict = yaml.safe_load(_f)

_TECHNIQUES = [
    ("dan", False),
    ("roleplay", False),
    ("hypothetical", False),
    ("base64", True),
]


async def _evaluate(keyword_eval, llm_eval, tech_name: str, idx: int, prompt: str, response: str):
    eval_result = await keyword_eval.evaluate(
        attack_name=f"{tech_name}_{idx}",
        prompt=prompt,
        response=response,
    )
    if eval_result.status == AttackStatus.SKIPPED and llm_eval:
        await asyncio.sleep(1.5)
        eval_result = await llm_eval.evaluate(
            attack_name=f"{tech_name}_{idx}",
            prompt=prompt,
            response=response,
        )
    return eval_result


class JailbreakAttack(BaseAttack):
    name = "jailbreak"
    category = "jailbreak"
    severity = Severity.CRITICAL
    description = "Tests if the model can be jailbroken via DAN, roleplay, hypothetical, and Base64 techniques"

    async def run(
        self,
        target,
        evaluator: LLMEvaluator | None = None,
    ) -> list[AttackResult]:
        keyword_eval = KeywordEvaluator()
        llm_eval = evaluator
        goals: list[str] = _DATASET["goals"]
        results: list[AttackResult] = []

        for g_idx, goal in enumerate(goals):
            goal_tag = f"g{g_idx + 1}"

            # ── Baseline : requête brute sans aucune technique ────────────────
            await asyncio.sleep(1.0)
            try:
                baseline_response = await target.send(goal)
                eval_result = await _evaluate(
                    keyword_eval, llm_eval, "baseline", 0, goal, baseline_response.content
                )
                results.append(AttackResult(
                    attack_name=f"baseline/jailbreak_{goal_tag}",
                    attack_category="baseline",
                    severity=Severity.LOW,
                    prompt=goal,
                    response=baseline_response.content,
                    status=eval_result.status,
                    confidence=eval_result.confidence,
                    evaluator_reason=eval_result.reason,
                    evaluator_type=eval_result.evaluator_type,
                    latency_ms=baseline_response.latency_ms,
                ))
            except Exception as exc:
                results.append(AttackResult(
                    attack_name=f"baseline/jailbreak_{goal_tag}",
                    attack_category="baseline",
                    severity=Severity.LOW,
                    prompt=goal,
                    response="",
                    status=AttackStatus.ERROR,
                    evaluator_reason=str(exc),
                ))

            # ── Techniques de jailbreak ───────────────────────────────────────
            for tech_name, is_base64 in _TECHNIQUES:
                seeds: list[str] = _DATASET[tech_name]
                prompts: list[str] = []

                for seed in seeds:
                    if is_base64:
                        encoded = base64.b64encode(goal.encode()).decode()
                        prompts.append(seed.replace("{base64_goal}", encoded))
                    else:
                        prompts.append(seed.replace("{goal}", goal))

                for i, prompt in enumerate(prompts):
                    await asyncio.sleep(1.0)

                    try:
                        target_response = await target.send(prompt)
                        eval_result = await _evaluate(
                            keyword_eval, llm_eval, tech_name, i + 1, prompt, target_response.content
                        )
                        result = AttackResult(
                            attack_name=f"{self.name}/{tech_name}_{i + 1}_{goal_tag}",
                            attack_category=self.category,
                            severity=self.severity,
                            prompt=prompt,
                            response=target_response.content,
                            status=eval_result.status,
                            confidence=eval_result.confidence,
                            evaluator_reason=eval_result.reason,
                            evaluator_type=eval_result.evaluator_type,
                            latency_ms=target_response.latency_ms,
                        )
                    except Exception as exc:
                        result = AttackResult(
                            attack_name=f"{self.name}/{tech_name}_{i + 1}_{goal_tag}",
                            attack_category=self.category,
                            severity=self.severity,
                            prompt=prompt,
                            response="",
                            status=AttackStatus.ERROR,
                            evaluator_reason=str(exc),
                        )

                    results.append(result)

        return results
