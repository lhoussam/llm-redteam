import asyncio
import glob
import json
import logging
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import click
import litellm
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from llm_redteam.attacks import ATTACK_REGISTRY
from llm_redteam.compare import compare, to_markdown
from llm_redteam.core.result import AttackResult, AttackStatus, SessionReport
from llm_redteam.core.scoring import MIN_COVERAGE, compute_score, compute_summary, score_label, score_status
from llm_redteam.evaluators.llm_evaluator import LLMEvaluator
from llm_redteam.redaction import count_sensitive, outputs, redact_report
from llm_redteam.reporters import json_reporter, markdown_reporter
from llm_redteam.targets import PROVIDERS, BaseTarget, LiteLLMTarget
from llm_redteam.validation import OFFLINE, load_labeled
from llm_redteam.validation import validate as run_validation

# Keep the terminal output readable: silence LiteLLM's debug banners and info logs.
litellm.suppress_debug_info = True
litellm.verbose = False
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)

load_dotenv()
console = Console()


def _build_target(target_name: str, model: str) -> BaseTarget:
    return LiteLLMTarget(provider=target_name, model=model)


async def _preflight(target: BaseTarget, judge: LLMEvaluator | None) -> None:
    """Fail fast, before sending any attack, if the target or the judge cannot be used."""
    try:
        await target.send("ping")
    except Exception as e:
        raise click.ClickException(f"The target cannot be used, scan aborted.\n  {e}") from e
    if judge:
        # A test verdict also catches judges that answer but cannot produce the expected JSON.
        verdict = await judge.evaluate("preflight", "ping", "pong")
        if verdict.status == AttackStatus.ERROR:
            raise click.ClickException(f"The judge cannot be used, scan aborted.\n  {verdict.reason}")


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
@click.option("--judge-model", default=None,
              help="LLM judge for ambiguous responses (e.g. qwen/qwen3.8-27b, validated against human labels).")
@click.option("--judge-provider", type=click.Choice(list(PROVIDERS)), default=None,
              help="Provider of the judge model. Defaults to the target's provider.")
@click.option("--report", type=click.Choice(["markdown", "json", "both"]), default="both", show_default=True)
@click.option("--output-dir", default="reports", show_default=True)
def scan(
    target: str, model: str, attacks: str, judge_model: str | None, judge_provider: str | None,
    report: str, output_dir: str,
) -> None:
    """Run adversarial attacks against a target LLM."""
    try:
        attack_keys = _parse_attacks(attacks)
    except click.BadParameter as e:
        raise click.ClickException(str(e)) from e
    asyncio.run(_scan(target, model, attack_keys, judge_model, judge_provider, report, output_dir))


async def _scan(
    target_name: str, model: str, attack_keys: list[str], judge_model: str | None, judge_provider: str | None,
    report_fmt: str, output_dir: str,
) -> None:
    target = _build_target(target_name, model)

    evaluator = None
    judge_label = "none (keyword/heuristic only)"
    if judge_model:
        judge_provider = judge_provider or target_name
        judge = LiteLLMTarget(provider=judge_provider, model=judge_model)  # resolves the LiteLLM name and API base
        evaluator = LLMEvaluator(model=judge.litellm_model, api_base=judge.api_base)
        judge_label = f"{judge_provider} / {judge_model}"

    console.print(f"Target  : {target_name} / {model}")
    console.print(f"Attacks : {', '.join(attack_keys)}")
    console.print(f"Judge   : {judge_label}")
    await _preflight(target, evaluator)
    console.print("[green]✓ Target and judge reachable[/green]" if evaluator else "[green]✓ Target reachable[/green]")
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
    sensitive = count_sensitive(session)
    if sensitive:
        console.print(f"  [yellow]⚠ {outputs(sensitive)} may be harmful or copyrighted: "
                      "share only a redacted copy (llm-redteam redact <report.json>).[/yellow]")


def _expand_reports(patterns: tuple[str, ...]) -> list[Path]:
    """Expand files, folders and wildcards ourselves: PowerShell does not expand them for external commands."""
    files: list[Path] = []
    for pattern in patterns:
        path = Path(pattern)
        if path.is_dir():
            files += sorted(path.glob("*.json"))
        else:
            files += [Path(p) for p in sorted(glob.glob(pattern))]
    return files


@main.command(name="compare")
@click.argument("patterns", nargs=-1, required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Also write the table to this Markdown file.")
def compare_command(patterns: tuple[str, ...], output: Path | None) -> None:
    """Aggregate JSON scan reports into one comparison table (files, folders or wildcards like reports/*.json)."""
    files = _expand_reports(patterns)
    if not files:
        raise click.ClickException(f"No JSON report found for: {' '.join(patterns)}")
    reports: dict[str, SessionReport] = {}
    for f in files:
        report = SessionReport.model_validate_json(f.read_text(encoding="utf-8"))
        reports.setdefault(report.session_id, report)  # a report and its redacted copy are the same run
    table = to_markdown(compare(list(reports.values())))
    runs, n_files = len(reports), len(files)
    console.print(f"{runs} run{'s' * (runs != 1)} from {n_files} file{'s' * (n_files != 1)}" + chr(10))
    console.print(table, markup=False, highlight=False)
    if output:
        output.write_text(table, encoding="utf-8")
        console.print(f"  Table saved → {output}")


@main.command()
@click.argument("report_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def redact(report_file: Path) -> None:
    """Write a shareable copy of a JSON scan report, with harmful or copyrighted model outputs masked."""
    session = SessionReport.model_validate_json(report_file.read_text(encoding="utf-8"))
    redacted = redact_report(session)
    stem = report_file.with_suffix("")
    json_path = stem.with_name(f"{stem.name}.redacted.json")
    md_path = stem.with_name(f"{stem.name}.redacted.md")
    json_reporter.generate(redacted, json_path)
    markdown_reporter.generate(redacted, md_path)
    console.print(f"  {outputs(redacted.metadata['redacted_responses'])} masked.")
    for p in (json_path, md_path):
        console.print(f"  Redacted report saved → {p}")


def _parse_judge(spec: str) -> tuple[str, LLMEvaluator]:
    provider, sep, model = spec.partition(":")
    if not sep or provider not in PROVIDERS or not model:
        raise click.BadParameter(f"Judge '{spec}' must be provider:model, with provider in: {', '.join(PROVIDERS)}")
    judge = LiteLLMTarget(provider=provider, model=model)  # resolves the LiteLLM name and API base
    return model.rsplit("/", 1)[-1], LLMEvaluator(model=judge.litellm_model, api_base=judge.api_base)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}%"


@main.command()
@click.argument("labeled_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--judge", "judge_specs", multiple=True,
              help="LLM judge to measure, as provider:model (e.g. groq:openai/gpt-oss-120b). Repeatable.")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Save the per-response verdicts and every metric as JSON.")
def validate(labeled_file: Path, judge_specs: tuple[str, ...], output: Path | None) -> None:
    """Measure the evaluators against human-labeled responses (JSONL)."""
    try:
        judges = dict(_parse_judge(spec) for spec in judge_specs)
    except click.BadParameter as e:
        raise click.ClickException(str(e)) from e
    asyncio.run(_validate(labeled_file, judges, output))


async def _validate(labeled_file: Path, judges: dict[str, LLMEvaluator], output: Path | None) -> None:
    items, redacted = load_labeled(labeled_file)
    labels = Counter(item["label"] for item in items)
    console.print(f"Labeled responses : {len(items)} ({labels['success']} success, {labels['failure']} failure, "
                  f"{labels['unsure']} unsure)")
    if redacted:
        console.print(f"                    {redacted} redacted responses left out (their text is masked)")
    console.print(f"Judges            : {', '.join(judges) or 'none (offline rules only)'}")
    for name, judge in judges.items():
        verdict = await judge.evaluate("preflight", "ping", "pong")
        if verdict.status == AttackStatus.ERROR:
            raise click.ClickException(f"The judge {name} cannot be used: {verdict.reason}")
    with console.status("Judging the labeled responses..."):
        report = await run_validation(items, judges)

    sep = "━" * 79
    console.print(f"[bold]{sep}[/bold]")
    header = f"{'Evaluator':<31} {'Decided':>7} {'Accuracy [95% CI]':>17} {'Prec.':>5} {'Recall':>6} {'Strict':>6}"
    console.print(f"  {header}")
    for name, m in report.metrics.items():
        o, strict = m["overall"], m["partial_as_failure"]
        low, high = o["accuracy_ci_95"]
        accuracy = f"{_pct(o['accuracy'])} [{low:.0f}–{high:.0f}%]" if o["decided"] else "—"
        decided = f"{o['decided']}/{o['applicable']}"
        console.print(f"  {name:<31} {decided:>7} {accuracy:>17} {_pct(o['precision']):>5} "
                      f"{_pct(o['recall']):>6} {_pct(strict['recall']):>6}")
    console.print()
    categories = list(report.metrics[OFFLINE]["by_category"])
    console.print(f"  {'Accuracy by category':<31} " + " ".join(f"{c[:12]:>12}" for c in categories))
    for name, m in report.metrics.items():
        cells = []
        for c in categories:
            cm = m["by_category"][c]
            cells.append(f"{_pct(cm['accuracy'])} ({cm['decided']})" if cm["decided"] else "—")
        console.print(f"  {name:<31} " + " ".join(f"{cell:>12}" for cell in cells))
    console.print(f"[bold]{sep}[/bold]")
    console.print("  Decided: responses that received a verdict (others: rule undecided without judge, or error).")
    console.print("  Strict: recall when partial successes are counted as failures.")
    if output:
        output.write_text(json.dumps({"metrics": report.metrics, "items": report.items}, indent=1, ensure_ascii=False),
                          encoding="utf-8")
        console.print(f"  Details saved → {output}")
