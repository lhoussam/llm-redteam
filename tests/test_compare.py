import pytest
from click.testing import CliRunner

from llm_redteam import cli
from llm_redteam.compare import compare, to_markdown
from llm_redteam.core.result import AttackResult, AttackStatus, SessionReport, Severity
from llm_redteam.core.scoring import WEIGHTS
from llm_redteam.redaction import redact_report
from llm_redteam.reporters import json_reporter

S, F, E = AttackStatus.SUCCESS, AttackStatus.FAILURE, AttackStatus.ERROR


def _run(model: str, jailbreak_successes: int, judge: str = "groq / judge", errors: int = 0) -> SessionReport:
    """A full scan: 10 probes per scored category, all blocked except the given jailbreak successes / errors."""
    results = []
    for cat in WEIGHTS:
        for i in range(10):
            status = F
            if cat == "jailbreak" and i < jailbreak_successes:
                status = S
            elif cat == "jailbreak" and i >= 10 - errors:
                status = E
            results.append(AttackResult(attack_name=f"{cat}/probe_{i}", attack_category=cat,
                                        severity=Severity.HIGH, prompt="p", response="r", status=status))
    return SessionReport(target_model=model, target_provider="groq", results=results,
                         metadata={"judge_model": judge})


def test_scores_are_averaged_over_runs_with_their_range():
    runs = [_run("a", 2), _run("a", 4), _run("a", 6)]  # jailbreak penalty 7, 14 and 21 points

    m = compare(runs).models[0]

    assert m.runs == 3
    assert m.scores == pytest.approx([93.0, 86.0, 79.0])
    assert m.mean_score == pytest.approx(86.0)


def test_category_rates_are_pooled_over_runs():
    m = compare([_run("a", 2), _run("a", 4)]).models[0]
    assert (m.successes["jailbreak"], m.evaluated["jailbreak"]) == (6, 20)


def test_incomplete_runs_are_left_out_and_reported():
    comparison = compare([_run("a", 2), _run("a", 2, errors=5)])  # 5/10 jailbreak probes failed: below 90%

    m = comparison.models[0]
    assert m.runs == 2 and len(m.scores) == 1
    assert any("1 of 2 runs are incomplete" in w for w in comparison.warnings)


def test_models_are_ranked_by_mean_score():
    comparison = compare([_run("weak", 8), _run("strong", 0), _run("broken", 0, errors=10)])
    assert [m.model for m in comparison.models] == ["strong", "weak", "broken"]


def test_consistent_reports_raise_no_warning():
    assert compare([_run("a", 1), _run("b", 2)]).warnings == []


def test_different_judges_are_flagged():
    warnings = compare([_run("a", 1, judge="groq / j1"), _run("b", 1, judge="groq / j2")]).warnings
    assert any("different judges" in w for w in warnings)


def test_different_probe_sets_are_flagged():
    other = _run("b", 1)
    other.results = other.results[:-1]
    assert any("same probes" in w for w in compare([_run("a", 1), other]).warnings)


def test_markdown_table():
    md = to_markdown(compare([_run("a", 2), _run("a", 4), _run("b", 0, errors=10)]))
    assert "| `groq / a` | 2/2 | **90**/100 (86–93) | 30% (6/20) |" in md
    assert "| `groq / b` | 0/1 | N/A | — |" in md


def test_compare_command(tmp_path):
    raw = tmp_path / "groq_a_1.json"
    session = _run("a", 2)
    json_reporter.generate(session, raw)
    json_reporter.generate(redact_report(session), tmp_path / "groq_a_1.redacted.json")  # same run, redacted
    json_reporter.generate(_run("a", 4), tmp_path / "groq_a_2.json")
    table = tmp_path / "table.md"

    result = CliRunner().invoke(cli.main, ["compare", str(tmp_path / "*.json"), "--output", str(table)])

    assert result.exit_code == 0, result.output
    assert "2 runs from 3 files" in result.output  # the redacted copy is not counted twice
    assert "| `groq / a` | 2/2 |" in table.read_text(encoding="utf-8")


def test_compare_command_accepts_a_folder(tmp_path):
    json_reporter.generate(_run("a", 2), tmp_path / "groq_a_1.json")
    result = CliRunner().invoke(cli.main, ["compare", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "1 run from 1 file" in result.output


def test_compare_command_without_reports(tmp_path):
    result = CliRunner().invoke(cli.main, ["compare", str(tmp_path / "*.json")])
    assert result.exit_code != 0
    assert "No JSON report found" in result.output
