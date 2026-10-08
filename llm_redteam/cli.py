import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import click
import litellm
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from llm_redteam.attacks import ATTACK_REGISTRY
from llm_redteam.core.result import AttackResult, AttackStatus, SessionReport
from llm_redteam.core.scoring import MIN_COVERAGE, compute_score, compute_summary, score_label, score_status
from llm_redteam.targets import PROVIDERS, LiteLLMTarget

# Keep the terminal output readable: silence LiteLLM's debug banners and info logs.
litellm.suppress_debug_info = True
litellm.verbose = False
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)

load_dotenv()
console = Console()


def _build_target(target_name: str, model: str) -> tuple[LiteLLMTarget, str | None]:
    target = LiteLLMTarget(provider=target_name, model=model)
    return target, target.api_base


def _parse_attacks(attacks_str: str) -> list[str]:
    if attacks_str == "all":
        return list(ATTACK_REGISTRY.keys())
    keys = [k.strip() for k in attacks_str.split(",")]
    for k in keys:
        if k not in ATTACK_REGISTRY:
            raise click.BadParameter(f"Unknown attack module '{k}'. Available: {', '.join(ATTACK_REGISTRY)}")
    return keys


def _safe_filename(text: str) -> str:
    """Replace characters that are invalid in file names on Windows or Linux (e.g. ':' in 'llama3.2:3b')."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.") or "model"


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


@click.group()
def main() -> None:
    pass


@main.command()
@click.option("--target", required=True, type=click.Choice(list(PROVIDERS)), help="Model provider.")
@click.option("--model", required=True, help="Model name (e.g. openai/gpt-oss-20b on groq, llama3.2 on ollama)")
@click.option("--attacks", default="all", show_default=True,
              help="Comma-separated modules or 'all': jailbreak, prompt_injection, data_extraction, hallucination")
@click.option("--judge-model", default=None, show_default=True,
              help="Separate model for evaluation, on the same provider (e.g. openai/gpt-oss-120b).")
@click.option("--report", type=click.Choice(["markdown", "json", "both"]), default="both", show_default=True)
@click.option("--output-dir", default="reports", show_default=True)
def scan(target: str, model: str, attacks: str, judge_model: str | None, report: str, output_dir: str) -> None:
    """Run adversarial attacks against a target LLM."""
    try:
        attack_keys = _parse_attacks(attacks)
    except click.BadParameter as e:
        raise click.ClickException(str(e)) from e
    asyncio.run(_scan(target, model, attack_keys, judge_model, report, output_dir))


async def _scan(
    target_name: str, model: str, attack_keys: list[str], judge_model: str | None, report_fmt: str, output_dir: str
) -> None:
    from llm_redteam.evaluators.llm_evaluator import LLMEvaluator

    target, api_base = _build_target(target_name, model)

    evaluator = None
    if judge_model:
        judge_litellm = PROVIDERS[target_name]["prefix"] + judge_model
        evaluator = LLMEvaluator(model=judge_litellm, api_base=api_base)
        judge_label = judge_model
    else:
        judge_label = "none (keyword/heuristic only)"

    console.print(f"Target  : {target_name} / {model}")
    console.print(f"Attacks : {', '.join(attack_keys)}")
    console.print(f"Judge   : {judge_label}")
    console.print()

    session = SessionReport(
        target_model=model,
        target_provider=target_name,
        metadata={
            "judge_model": judge_label,
            "attack_modules": attack_keys,
            "temperature": 0,
            "dataset": "fixed (no dynamic generation)",
        },
    )

    all_results: list[AttackResult] = []
    t0 = time.perf_counter()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                  TaskProgressColumn(), console=console, transient=True) as progress:
        for key in attack_keys:
            attack_cls = ATTACK_REGISTRY[key]
            attack = attack_cls()
            task = progress.add_task(f"  Running {key}...", total=None)
            results = await attack.run(target=target, evaluator=evaluator)
            all_results.extend(results)
            progress.update(task, completed=len(results), total=len(results))

    duration = time.perf_counter() - t0

    score = compute_score(all_results)
    summary = compute_summary(all_results)
    status, issues = score_status(summary)
    successes = sum(1 for r in all_results if r.status == AttackStatus.SUCCESS)
    errors = sum(1 for r in all_results if r.status == AttackStatus.ERROR)

    session.results = all_results
    session.total_attacks = len(all_results)
    session.successful_attacks = successes
    session.robustness_score = score
    session.score_status = status
    session.score_issues = issues
    session.completed_at = datetime.now(UTC)
    session.summary = summary

    sep = "━" * 60
    score_text = "N/A" if score is None else f"{score:.0f}/100"
    console.print(f"[bold]{sep}[/bold]")
    console.print(f"  ROBUSTNESS SCORE  [bold]{score_text}[/bold]  {score_label(score)}")
    console.print(f"  Duration          {_fmt_duration(duration)}   "
                  f"({len(all_results)} probes, {successes} succeeded, {errors} errors)")
    if status == "valid":
        console.print(f"  [green]✓ Valid: {MIN_COVERAGE:.0f}%+ of the probes evaluated in every category[/green]")
    elif status == "no_data":
        console.print("\n  [yellow]⚠ No probe could be evaluated, so there is no score:[/yellow]")
    else:
        console.print(f"\n  [yellow]⚠ INCOMPLETE: evaluation coverage is below {MIN_COVERAGE:.0f}% for:[/yellow]")
    for issue in issues:
        console.print(f"  [yellow]  • {issue}[/yellow]")
    console.print()
    console.print(f"  {'Category':<18} {'Success':>7} {'Rate':>5} {'95% CI':>11} {'Impact':>9} {'Evaluated':>9}")
    console.print(f"  {'-' * 18} {'-' * 7} {'-' * 5} {'-' * 11} {'-' * 9} {'-' * 9}")
    for cat, stats in summary.items():
        evaluated = f"{stats['evaluated']}/{stats['total']}"
        if stats["evaluated"]:
            success = f"{stats['successful']}/{stats['evaluated']}"
            rate = f"{stats['success_rate']:.0f}%"
            ci = f"[{stats['ci_95_low']:.0f}–{stats['ci_95_high']:.0f}%]"
            impact = f"-{stats['score_impact']:.1f} pts" if stats["score_impact"] else "0 pts"
        else:
            success = rate = ci = impact = "—"
        console.print(f"  {cat:<18} {success:>7} {rate:>5} {ci:>11} {impact:>9} {evaluated:>9}")
    console.print(f"[bold]{sep}[/bold]")

    _save_reports(session, report_fmt, output_dir, target_name, model)


def _save_reports(session: SessionReport, fmt: str, output_dir: str, target_name: str, model: str) -> None:
    from llm_redteam.reporters import json_reporter, markdown_reporter
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = f"{target_name}_{_safe_filename(model)}_{ts}"
    saved = []
    if fmt in ("json", "both"):
        p = out / f"{slug}.json"
        json_reporter.generate(session, p)
        saved.append(p)
    if fmt in ("markdown", "both"):
        p = out / f"{slug}.md"
        markdown_reporter.generate(session, p)
        saved.append(p)
    console.print()
    for p in saved:
        console.print(f"  Report saved → {p}")
