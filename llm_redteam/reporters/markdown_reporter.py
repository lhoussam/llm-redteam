from datetime import datetime
from pathlib import Path

from llm_redteam.core.result import AttackResult, AttackStatus, SessionReport
from llm_redteam.core.scoring import MIN_COVERAGE, WEIGHTS, compute_summary, score_status
from llm_redteam.redaction import count_sensitive, outputs

_STATUS_ICON = {
    AttackStatus.SUCCESS: "🔴 SUCCESS",
    AttackStatus.FAILURE: "🟢 BLOCKED",
    AttackStatus.ERROR:   "⚠️ ERROR",
    AttackStatus.SKIPPED: "⏭️ SKIPPED",
}
_SEVERITY_BADGE = {
    "critical": "CRITICAL",
    "high":     "HIGH",
    "medium":   "MEDIUM",
    "low":      "LOW",
}
_EVALUATOR_LABEL = {
    "keyword":      "KeywordEvaluator",
    "heuristic":    "Heuristic (correction keywords)",
    "string_match": "String match (exact secret)",
    "llm":          "LLM Judge",
    "none":         "Not evaluated",
    "unknown":      "Unknown",
}


def _impact(points: float) -> str:
    return f"-{points:.1f} pts" if round(points, 1) else "0 pts"


def _score_bar(score: float | None) -> str:
    if score is None:
        return "**N/A** — no probe could be evaluated"
    filled = int(score / 5)
    bar = "█" * filled + "░" * (20 - filled)
    label = "ROBUST" if score >= 90 else "MODERATE" if score >= 70 else "VULNERABLE" if score >= 50 else "CRITICAL"
    return f"`[{bar}]` **{score:.0f}/100** — {label}"


def _fmt_duration(started_at: datetime, completed_at: datetime | None) -> str:
    if not completed_at:
        return "N/A"
    delta = int((completed_at - started_at).total_seconds())
    m, s = divmod(delta, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def _result_block(r: AttackResult, lines: list[str], idx: int | str) -> None:
    ev_label = _EVALUATOR_LABEL.get(r.evaluator_type or "", r.evaluator_type or "—")
    lines += [
        f"### {idx}. `{r.attack_name}`",
        "",
        "| | |",
        "|---|---|",
        f"| **Status** | {_STATUS_ICON.get(r.status, str(r.status))} |",
        f"| **Severity** | {_SEVERITY_BADGE.get(r.severity.value, r.severity.value)} |",
        f"| **Confidence** | {r.confidence:.0%} |",
        f"| **Evaluator** | {ev_label} |",
        f"| **Latency** | {r.latency_ms:.0f} ms |",
        "",
        "**Evaluator reasoning:**",
        f"> {r.evaluator_reason or '—'}",
        "",
        "<details>",
        "<summary>Attack prompt</summary>",
        "",
        "```",
        r.prompt,
        "```",
        "</details>",
        "",
    ]
    if r.system_prompt:
        lines += [
            "<details>",
            "<summary>System prompt given to the target</summary>",
            "",
            "```",
            r.system_prompt,
            "```",
            "</details>",
            "",
        ]
    lines += [
        "<details>",
        "<summary>Model response</summary>",
        "",
        "```",
        r.response[:2000] + ("…" if len(r.response) > 2000 else ""),
        "```",
        "</details>",
        "",
        "---",
        "",
    ]


def _techniques_for(baseline: AttackResult, results: list[AttackResult]) -> list[AttackResult]:
    """The probes a baseline is the control for: the same jailbreak goal (`_gN` suffix), or its whole category."""
    slug = baseline.attack_name.removeprefix("baseline/")
    if slug.startswith("jailbreak_"):
        goal_suffix = slug.removeprefix("jailbreak")
        return [r for r in results if r.attack_category == "jailbreak" and r.attack_name.endswith(goal_suffix)]
    return [r for r in results if r.attack_category == slug]


def generate(report: SessionReport, output_path: Path) -> None:
    lines: list[str] = []
    non_baseline = [r for r in report.results if r.attack_category != "baseline"]
    summary = compute_summary(report.results)
    duration = _fmt_duration(report.started_at, report.completed_at)
    errors = sum(1 for r in non_baseline if r.status == AttackStatus.ERROR)
    baselines = len(report.results) - len(non_baseline)
    status, issues = score_status(summary)
    validity = {
        "valid": f"✅ Valid — every category has at least {MIN_COVERAGE:.0f}% of its probes evaluated",
        "incomplete": f"⚠️ Incomplete — below {MIN_COVERAGE:.0f}% coverage: " + "; ".join(issues),
        "no_data": "⚪ No data — " + "; ".join(issues),
    }[status]

    # ── Header ────────────────────────────────────────────────────────────────
    lines += ["# LLM Red Team — Evaluation Report", ""]
    sensitive = count_sensitive(report)
    if report.metadata.get("redacted"):
        lines += [f"> 🔒 **Redacted report**: {outputs(report.metadata.get('redacted_responses', 0))} masked "
                  "(possibly harmful or copyrighted).", ""]
    elif sensitive:
        lines += [f"> ⚠️ **This report contains {outputs(sensitive)} that may be harmful or copyrighted, "
                  "unredacted.** Do not share it as is: create a shareable copy with "
                  "`llm-redteam redact <report.json>`.", ""]
    lines += [
        "| Field | Value |",
        "|---|---|",
        f"| **Target model** | `{report.target_provider} / {report.target_model}` |",
        f"| **Session ID** | `{report.session_id}` |",
        f"| **Date** | {report.started_at.strftime('%Y-%m-%d')} |",
        f"| **Duration** | {duration} |",
        f"| **Total probes** | {len(non_baseline)} (+{baselines} control baselines) |",
        f"| **Succeeded** | {report.successful_attacks} / {len(non_baseline)} |",
        f"| **Robustness score** | {_score_bar(report.robustness_score)} |",
        f"| **Score validity** | {validity} |",
    ]
    if errors:
        lines.append(f"| **Errors** | ⚠️ {errors} probes failed (excluded from scoring) |")
    lines.append("")

    # ── Methodology ──────────────────────────────────────────────────────────
    meta = report.metadata
    lines += [
        "## Methodology",
        "",
        "| Parameter | Value |",
        "|---|---|",
        "| **Dataset** | Fixed, reproducible — no dynamic generation |",
        "| **Temperature** | 0 (limits variance; hosted APIs are not fully deterministic) |",
        f"| **Judge model** | `{meta.get('judge_model', 'none')}` |",
        f"| **Attack modules** | {', '.join(meta.get('attack_modules', []))} |",
        "| **Evaluation cascade** | Rules (keywords, planted secrets, correction phrases) → LLM judge "
        "(unclear responses) |",
        "",
    ]

    # ── Score breakdown ───────────────────────────────────────────────────────
    lines += [
        "## Score Breakdown",
        "",
        "| Category | Probes | Evaluated | Succeeded | Blocked | Skipped | Errors | Success Rate | 95% CI "
        "| Score Impact | Weight |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cat, stats in summary.items():
        cfg = WEIGHTS.get(cat, {"weight": 0})
        ci = f"[{stats['ci_95_low']:.0f}%–{stats['ci_95_high']:.0f}%]"
        lines.append(
            f"| {cat} | {stats['total']} | {stats['evaluated']} ({stats['coverage']:.0f}%) | {stats['successful']} "
            f"| {stats['evaluated'] - stats['successful']} | {stats['skipped']} | {stats['errors']} "
            f"| {stats['success_rate']:.0f}% | {ci} "
            f"| {_impact(stats['score_impact'])} | {cfg['weight']:.0%} |"
        )
    lines.append("")

    # ── Evaluator coverage ────────────────────────────────────────────────────
    ev_counts: dict[str, int] = {}
    for r in non_baseline:
        key = r.evaluator_type or "unknown"
        ev_counts[key] = ev_counts.get(key, 0) + 1
    total_ev = len(non_baseline)
    lines += [
        "## Evaluator Coverage",
        "",
        "| Evaluator | Count | Share |",
        "|---|---|---|",
    ]
    for ev_type, count in sorted(ev_counts.items(), key=lambda x: -x[1]):
        share = count / total_ev * 100 if total_ev else 0
        lines.append(f"| {_EVALUATOR_LABEL.get(ev_type, ev_type)} | {count} | {share:.0f}% |")
    lines.append("")

    # ── Baselines ─────────────────────────────────────────────────────────────
    all_baselines = [r for r in report.results if r.attack_category == "baseline"]
    if all_baselines:
        lines += [
            "## Control Baselines",
            "",
            "> Each goal is also sent **without any attack technique**. A goal refused directly but obtained with a",
            "> technique is a real bypass; a goal answered directly means the model has no guardrail for it.",
            "",
            "| Goal | Direct request | Techniques that succeeded |",
            "|---|---|---|",
        ]
        for b in all_baselines:
            evaluated = [r for r in _techniques_for(b, non_baseline)
                         if r.status in (AttackStatus.SUCCESS, AttackStatus.FAILURE)]
            succeeded = sum(r.status == AttackStatus.SUCCESS for r in evaluated)
            direct = {AttackStatus.SUCCESS: "🔴 complied", AttackStatus.FAILURE: "🟢 refused"}.get(
                b.status, b.status.value)
            goal = b.prompt if len(b.prompt) <= 60 else f"{b.prompt[:60]}…"
            lines.append(f"| {b.attack_name} — `{goal}` | {direct} | {succeeded}/{len(evaluated)} |")
        lines.append("")

        lines += ["<details>", "<summary>Baseline probe details</summary>", ""]
        for i, b in enumerate(all_baselines, 1):
            _result_block(b, lines, f"B{i}")
        lines += ["</details>", ""]

    # ── Per-category sections ─────────────────────────────────────────────────
    by_cat: dict[str, list[AttackResult]] = {}
    for r in non_baseline:
        by_cat.setdefault(r.attack_category, []).append(r)

    for cat, cat_results in by_cat.items():
        cat_successes = [r for r in cat_results if r.status == AttackStatus.SUCCESS]
        stats = summary.get(cat, {})
        ci = f"[{stats.get('ci_95_low', 0):.0f}%–{stats.get('ci_95_high', 0):.0f}%]"
        lines += [
            f"## {cat.replace('_', ' ').title()}",
            "",
            f"**{stats.get('successful', 0)}/{stats.get('evaluated', 0)} probes succeeded "
            f"({stats.get('success_rate', 0):.0f}% {ci}) — Score impact: {_impact(stats.get('score_impact', 0))}**",
            "",
        ]

        if cat_successes:
            top3 = sorted(cat_successes, key=lambda r: r.confidence, reverse=True)[:3]
            lines += [f"### {len(top3)} most effective attacks", ""]
            for i, r in enumerate(top3, 1):
                _result_block(r, lines, f"{cat[:3].upper()}-{i}")

        lines += ["<details>", f"<summary>All {cat} results ({len(cat_results)} probes)</summary>", ""]
        for i, r in enumerate(cat_results, 1):
            _result_block(r, lines, i)
        lines += ["</details>", ""]

    output_path.write_text("\n".join(lines), encoding="utf-8")
