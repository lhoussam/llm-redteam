"""Measure the evaluators against human-labeled responses.

Each labeled response is judged three ways, exactly as a scan would judge it:
- "offline rules": keywords, planted-secret match and correction heuristic only (no LLM judge);
- "<judge> (alone)": the LLM judge on every response, with the judging instructions of its category;
- "<judge> (cascade)": the full evaluation chain used by the scanner (rules first, judge when ambiguous).
"""
import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_redteam.attacks import data_extraction, hallucination, prompt_injection
from llm_redteam.core.result import AttackStatus
from llm_redteam.core.scoring import wilson_ci
from llm_redteam.datasets import load_dataset
from llm_redteam.evaluators.base import BaseEvaluator, EvaluationResult
from llm_redteam.evaluators.strategies import Evaluate, judge_or_skip, keyword_then_judge, secret_match

OFFLINE = "offline rules"
_JUDGE_DELAY = 1.5  # seconds between two judge calls (rate limits)


@dataclass
class ProbeJudging:
    """How the scanner judges one probe: the judge label and instructions, and the evaluation cascade."""

    label: str
    judge_system: str | None
    cascade: Callable[[BaseEvaluator | None], Evaluate]
    uses_judge: bool = True


def probe_judging(attack_name: str) -> ProbeJudging:
    """Rebuild, from a result's attack name, the evaluation the attack module applies to it."""
    module, _, name = attack_name.partition("/")
    injection_judge = prompt_injection._JUDGE_TEMPLATE.format(
        system_prompt=prompt_injection._REF_SYSTEM_PROMPT, goal=load_dataset("injections")["goals"][0]
    )
    if attack_name == "baseline/prompt_injection":
        return ProbeJudging("baseline_injection", injection_judge, lambda j: keyword_then_judge(j, injection_judge))
    if module == "baseline":
        return ProbeJudging("baseline_0", None, lambda j: keyword_then_judge(j))
    if module == "jailbreak":
        return ProbeJudging(re.sub("_g[0-9]+$", "", name), None, lambda j: keyword_then_judge(j))
    if module == "prompt_injection":
        return ProbeJudging(name, injection_judge, lambda j: keyword_then_judge(j, injection_judge))
    if module == "data_extraction" and name.startswith("system_prompt"):
        judge_system = data_extraction._SYSTEM_PROMPT_JUDGE
        return ProbeJudging(name, judge_system, lambda j: secret_match(data_extraction._SECRET_KEY, j, judge_system))
    if module == "data_extraction" and name.startswith("training_data"):
        judge_system = data_extraction._TRAINING_DATA_JUDGE
        return ProbeJudging(name, judge_system, lambda j: judge_or_skip(j, judge_system, "Requires LLM evaluator"))
    if module == "data_extraction":
        return ProbeJudging(name, None, lambda j: secret_match(data_extraction._SECRET_SSN), uses_judge=False)
    if module == "hallucination":
        return ProbeJudging(name, hallucination._HALLUCINATION_JUDGE, hallucination._correction_then_judge)
    raise ValueError(f"Unknown attack name: {attack_name}")


def _status_value(status: AttackStatus | None) -> str | None:
    return status.value if status is not None else None


def _metrics_by(items: list[dict], verdicts: list[AttackStatus | None], key: str) -> dict[str, dict]:
    pairs = [(str(it.get(key)), it["label"], s) for it, s in zip(items, verdicts, strict=True)]
    return {
        group: compute_metrics([(label, s) for g, label, s in pairs if g == group])
        for group in sorted({g for g, _, _ in pairs})
    }


class _CachedJudge(BaseEvaluator):
    """Asks the wrapped judge once per identical request, so the cascade reuses the 'alone' verdicts."""

    def __init__(self, judge: BaseEvaluator) -> None:
        self.judge = judge
        self.cache: dict[tuple, EvaluationResult] = {}

    async def evaluate(
        self, attack_name: str, prompt: str, response: str, judge_system: str | None = None
    ) -> EvaluationResult:
        key = (attack_name, prompt, response, judge_system)
        if key not in self.cache:
            self.cache[key] = await self.judge.evaluate(attack_name, prompt, response, judge_system)
        return self.cache[key]


def compute_metrics(pairs: list[tuple[str, AttackStatus | None]]) -> dict[str, Any]:
    """Agreement with the human labels. pairs = (human label, evaluator status or None if not applicable).

    "success" is the positive class. SKIPPED / ERROR verdicts count as abstentions, outside the rates.
    """
    applicable = [(truth, status) for truth, status in pairs if status is not None and truth != "unsure"]
    verdicts = (AttackStatus.SUCCESS, AttackStatus.FAILURE)
    decided = [(truth, status) for truth, status in applicable if status in verdicts]
    tp = sum(1 for t, s in decided if t == "success" and s == AttackStatus.SUCCESS)
    fp = sum(1 for t, s in decided if t == "failure" and s == AttackStatus.SUCCESS)
    fn = sum(1 for t, s in decided if t == "success" and s == AttackStatus.FAILURE)
    tn = sum(1 for t, s in decided if t == "failure" and s == AttackStatus.FAILURE)
    n = len(decided)
    return {
        "applicable": len(applicable),
        "decided": n,
        "abstained": len(applicable) - n,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / n * 100, 1) if n else None,
        "accuracy_ci_95": wilson_ci(tp + tn, n),
        "precision": round(tp / (tp + fp) * 100, 1) if tp + fp else None,
        "recall": round(tp / (tp + fn) * 100, 1) if tp + fn else None,
    }


@dataclass
class ValidationReport:
    items: list[dict] = field(default_factory=list)
    metrics: dict[str, dict] = field(default_factory=dict)


def load_labeled(path: Path) -> list[dict]:
    """JSONL with: id, category, attack_name, prompt, response, label (success|failure|unsure), optional partial."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def validate(items: list[dict], judges: Mapping[str, BaseEvaluator]) -> ValidationReport:
    verdicts: dict[str, list[AttackStatus | None]] = {OFFLINE: []}
    for item in items:
        pj = probe_judging(item["attack_name"])
        verdict = await pj.cascade(None)(pj.label, item["prompt"], item["response"])
        verdicts[OFFLINE].append(verdict.status)

    for name, judge in judges.items():
        cached = _CachedJudge(judge)
        alone, cascade = f"{name} (alone)", f"{name} (cascade)"
        verdicts[alone], verdicts[cascade] = [], []
        for item in items:
            pj = probe_judging(item["attack_name"])
            if pj.uses_judge:
                await asyncio.sleep(_JUDGE_DELAY)
                result = await cached.evaluate(pj.label, item["prompt"], item["response"], pj.judge_system)
                verdicts[alone].append(result.status)
            else:
                verdicts[alone].append(None)
            result = await pj.cascade(cached)(pj.label, item["prompt"], item["response"])
            verdicts[cascade].append(result.status)

    report = ValidationReport()
    for i, item in enumerate(items):
        report.items.append({
            "id": item.get("id"), "label": item["label"], "partial": item.get("partial", False),
            **{name: _status_value(v[i]) for name, v in verdicts.items()},
        })
    strict_labels = ["failure" if it.get("partial") else it["label"] for it in items]
    for name, v in verdicts.items():
        report.metrics[name] = {
            "overall": compute_metrics(list(zip([it["label"] for it in items], v, strict=True))),
            "partial_as_failure": compute_metrics(list(zip(strict_labels, v, strict=True))),
            "by_category": _metrics_by(items, v, "category"),
            "by_target_model": _metrics_by(items, v, "target_model"),
        }
    return report
