"""Aggregate several scan reports into one comparison table (one row per target model).

Only valid runs (every category evaluated enough, see scoring.score_status) count towards a model's results;
incomplete runs are reported but left out. Category rates are pooled over the valid runs.
"""
from dataclasses import dataclass, field

from llm_redteam.core.result import SessionReport
from llm_redteam.core.scoring import WEIGHTS, compute_score, compute_summary, score_status


@dataclass
class ModelResults:
    provider: str
    model: str
    runs: int = 0
    scores: list[float] = field(default_factory=list)  # valid runs only
    successes: dict[str, int] = field(default_factory=dict)
    evaluated: dict[str, int] = field(default_factory=dict)

    @property
    def mean_score(self) -> float | None:
        return sum(self.scores) / len(self.scores) if self.scores else None


@dataclass
class Comparison:
    models: list[ModelResults]
    warnings: list[str]


def compare(reports: list[SessionReport]) -> Comparison:
    by_model: dict[tuple[str, str], ModelResults] = {}
    for report in reports:
        key = (report.target_provider, report.target_model)
        entry = by_model.setdefault(key, ModelResults(*key))
        entry.runs += 1
        summary = compute_summary(report.results)
        score = compute_score(report.results)
        if score_status(summary)[0] != "valid" or score is None:
            continue
        entry.scores.append(score)
        for cat, stats in summary.items():
            entry.successes[cat] = entry.successes.get(cat, 0) + stats["successful"]
            entry.evaluated[cat] = entry.evaluated.get(cat, 0) + stats["evaluated"]

    warnings = []
    judges = {str(r.metadata.get("judge_model")) for r in reports}
    if len(judges) > 1:
        warnings.append(f"Reports use different judges ({', '.join(sorted(judges))}): results are not comparable.")
    probe_sets = {frozenset(r.attack_name for r in report.results) for report in reports}
    if len(probe_sets) > 1:
        warnings.append("Reports do not run the same probes (different datasets?): results are not comparable.")
    for entry in by_model.values():
        if len(entry.scores) < entry.runs:
            warnings.append(f"{entry.provider} / {entry.model}: {entry.runs - len(entry.scores)} of {entry.runs} "
                            "runs are incomplete and left out.")

    models = sorted(by_model.values(), key=lambda m: (m.mean_score is None, -(m.mean_score or 0)))
    return Comparison(models, warnings)


def to_markdown(comparison: Comparison) -> str:
    categories = list(WEIGHTS)
    header = ["Model", "Valid runs", "Score (mean, min–max)"] + [c.replace("_", " ") for c in categories]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for m in comparison.models:
        score = f"**{m.mean_score:.0f}**/100 ({min(m.scores):.0f}–{max(m.scores):.0f})" if m.scores else "N/A"
        cells = [f"`{m.provider} / {m.model}`", f"{len(m.scores)}/{m.runs}", score]
        for cat in categories:
            n = m.evaluated.get(cat, 0)
            cells.append(f"{m.successes.get(cat, 0) / n * 100:.0f}% ({m.successes.get(cat, 0)}/{n})" if n else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Category columns: share of probes where the attack succeeded, pooled over the valid runs.")
    for w in comparison.warnings:
        lines.append(f"> ⚠️ {w}")
    return "\n".join(lines) + "\n"
