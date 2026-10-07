import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import litellm
from dotenv import load_dotenv

litellm.suppress_debug_info = True
litellm.verbose = False
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from llm_redteam.attacks import ATTACK_REGISTRY
from llm_redteam.core.result import AttackResult, AttackStatus, SessionReport
from llm_redteam.core.scoring import compute_score, compute_summary, score_label

load_dotenv()
console = Console()

_PROVIDER_PREFIX = {"openai": None, "ollama": "ollama", "groq": "groq"}


def _build_target(target_name: str, model: str):
    if target_name == "openai":
        from llm_redteam.targets.openai_target import OpenAITarget
        return OpenAITarget(model=model), None
    if target_name == "ollama":
        from llm_redteam.targets.ollama_target import OllamaTarget
        api_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return OllamaTarget(model=model), api_base
    if target_name == "groq":
        from llm_redteam.targets.groq_target import GroqTarget
        return GroqTarget(model=model), None
    raise click.BadParameter(f"Unknown target: {target_name}")


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
@click.option("--target", required=True, type=click.Choice(["openai", "ollama", "groq"]))
@click.option("--model", required=True, help="Model name (e.g. llama-3.1-8b-instant)")
@click.option("--attacks", default="all", show_default=True,
              help="Comma-separated modules or 'all': jailbreak, prompt_injection, data_extraction, hallucination")
@click.option("--judge-model", default=None, show_default=True,
              help="Separate model for evaluation (e.g. llama-3.3-70b-versatile).")
@click.option("--report", type=click.Choice(["markdown", "json", "both"]), default="both", show_default=True)
@click.option("--output-dir", default="reports", show_default=True)
def scan(target, model, attacks, judge_model, report, output_dir):
    """Run adversarial attacks against a target LLM."""
    try:
        attack_keys = _parse_attacks(attacks)
    except click.BadParameter as e:
        raise click.ClickException(str(e))
    asyncio.run(_scan(target, model, attack_keys, judge_model, report, output_dir))


async def _scan(target_name, model, attack_keys, judge_model, report_fmt, output_dir):
    from llm_redteam.evaluators.llm_evaluator import LLMEvaluator

    target, api_base = _build_target(target_name, model)

    evaluator = None
    if judge_model:
        prefix = _PROVIDER_PREFIX.get(target_name)
        judge_litellm = f"{prefix}/{judge_model}" if prefix else judge_model
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
    successes = sum(1 for r in all_results if r.status == AttackStatus.SUCCESS)
    errors = sum(1 for r in all_results if r.status == AttackStatus.ERROR)

    session.results = all_results
    session.total_attacks = len(all_results)
    session.successful_attacks = successes
    session.robustness_score = score
    session.completed_at = datetime.now(timezone.utc)
    session.summary = {cat: stats for cat, stats in summary.items()}

    sep = "━" * 52
    console.print(f"[bold]{sep}[/bold]")
    console.print(f"  ROBUSTNESS SCORE  [bold]{score:.0f}/100[/bold]  {score_label(score)}")
    console.print(f"  Duration          {_fmt_duration(duration)}   "
                  f"({len(all_results)} probes, {successes} succeeded, {errors} errors)")
    console.print()
    console.print(f"  {'Category':<22} {'Succeeded':>10}  {'Rate':>8}  {'95% CI':>14}  {'Impact':>8}")
    console.print(f"  {'-'*22}  {'-'*9}  {'-'*7}  {'-'*13}  {'-'*7}")
    for cat, stats in summary.items():
        impact = f"-{stats['score_impact']:.1f}" if stats['score_impact'] > 0 else "   0.0"
        ci = f"[{stats['ci_95_low']:.0f}%–{stats['ci_95_high']:.0f}%]"
        skipped = f" ({stats['skipped']} skipped)" if stats['skipped'] else ""
        console.print(
            f"  {cat:<22} {stats['successful']:>4}/{stats['evaluated']:<5}  "
            f"{stats['success_rate']:>6.0f}%  {ci:>14}  {impact:>7} pts{skipped}"
        )
    if errors:
        console.print(f"\n  [yellow]⚠ {errors} probes returned errors — results may be incomplete.[/yellow]")
    console.print(f"[bold]{sep}[/bold]")

    _save_reports(session, report_fmt, output_dir, target_name, model)


def _save_reports(session, fmt, output_dir, target_name, model):
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
