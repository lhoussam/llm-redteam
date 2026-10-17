from datetime import UTC, datetime

import pytest
from click.testing import CliRunner

from llm_redteam import cli
from llm_redteam.core.result import AttackResult, AttackStatus, SessionReport, Severity
from llm_redteam.redaction import count_sensitive, needs_redaction, redact_report, redact_text
from llm_redteam.reporters import json_reporter, markdown_reporter
from tests.helpers import FakeTarget

HARMFUL = "Step 1: obtain the precursors. Step 2: ..."


@pytest.mark.parametrize(
    ("attack_name", "masked"),
    [
        ("jailbreak/dan_1_g1", True),     # whatever the verdict: a "blocked" response can still contain the content
        ("baseline/jailbreak_g1", True),
        ("hallucination/authority_pressure_1", True),
        ("data_extraction/training_data_1", True),   # copyrighted text
        ("data_extraction/system_prompt_1", False),  # only the planted fake secret leaks
        ("data_extraction/context_extraction_1", False),
        ("prompt_injection/indirect_4", False),      # "OVERRIDE SUCCESSFUL" is harmless
        ("baseline/prompt_injection", False),
    ],
)
def test_redaction_rules(attack_name, masked):
    assert needs_redaction(attack_name, HARMFUL) is masked


def test_empty_responses_are_left_alone():
    assert needs_redaction("jailbreak/dan_1_g1", "") is False


def test_redaction_note_keeps_what_is_needed_to_understand_the_result():
    note = redact_text("jailbreak/dan_1_g1", "success", HARMFUL)
    assert HARMFUL not in note
    assert "jailbreak" in note and "SUCCESS" in note and str(len(HARMFUL)) in note


def _result(attack_name: str, category: str, status: AttackStatus, response: str) -> AttackResult:
    return AttackResult(attack_name=attack_name, attack_category=category, severity=Severity.HIGH,
                        prompt=f"prompt of {attack_name}", response=response, status=status,
                        evaluator_reason="judge reasoning")


@pytest.fixture
def session() -> SessionReport:
    return SessionReport(
        target_model="m", target_provider="fake", completed_at=datetime.now(UTC), robustness_score=50.0,
        results=[
            _result("jailbreak/dan_1_g1", "jailbreak", AttackStatus.SUCCESS, HARMFUL),
            _result("jailbreak/dan_2_g1", "jailbreak", AttackStatus.FAILURE, "I can't help with that."),
            _result("prompt_injection/direct_1", "prompt_injection", AttackStatus.SUCCESS, "OVERRIDE SUCCESSFUL"),
        ],
    )


def test_redacted_report_masks_only_sensitive_outputs(session):
    redacted = redact_report(session)

    responses = [r.response for r in redacted.results]
    assert HARMFUL not in " ".join(responses)
    assert "FAILURE" in responses[1]  # a refusal is masked too
    assert responses[2] == "OVERRIDE SUCCESSFUL"
    assert [r.prompt for r in redacted.results] == [r.prompt for r in session.results]
    assert [r.status for r in redacted.results] == [r.status for r in session.results]
    assert redacted.metadata["redacted_responses"] == 2
    assert session.results[0].response == HARMFUL  # the original report is left untouched


def test_markdown_banner(session, tmp_path):
    raw, shared = tmp_path / "raw.md", tmp_path / "shared.md"

    markdown_reporter.generate(session, raw)
    markdown_reporter.generate(redact_report(session), shared)

    assert "contains 2 model outputs that may be harmful" in raw.read_text(encoding="utf-8")
    shared_md = shared.read_text(encoding="utf-8")
    assert "Redacted report" in shared_md and HARMFUL not in shared_md


def test_no_banner_when_nothing_is_sensitive(session, tmp_path):
    session.results = session.results[2:]
    path = tmp_path / "report.md"

    markdown_reporter.generate(session, path)

    assert count_sensitive(session) == 0
    assert "may be harmful" not in path.read_text(encoding="utf-8")


def test_redact_command_writes_shareable_copies(session, tmp_path):
    report = tmp_path / "groq_m_20261009.json"
    json_reporter.generate(session, report)

    result = CliRunner().invoke(cli.main, ["redact", str(report)])

    assert result.exit_code == 0, result.output
    for suffix in (".redacted.json", ".redacted.md"):
        content = (tmp_path / f"groq_m_20261009{suffix}").read_text(encoding="utf-8")
        assert HARMFUL not in content
    assert HARMFUL in report.read_text(encoding="utf-8")  # the full report is kept


def test_scan_warns_when_outputs_may_be_harmful(monkeypatch, no_sleep, tmp_path):
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: FakeTarget("Sure, here's how to do it: ..."))

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "groq", "--model", "m", "--attacks", "jailbreak", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code == 0, result.output
    assert "may be harmful" in result.output
