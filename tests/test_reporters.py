from datetime import datetime, timezone

import pytest

from llm_redteam.core.result import AttackStatus, SessionReport
from llm_redteam.core.scoring import compute_score
from llm_redteam.reporters import json_reporter, markdown_reporter
from tests.helpers import make_result


@pytest.fixture
def session() -> SessionReport:
    results = [
        make_result("baseline", AttackStatus.FAILURE),
        make_result("jailbreak", AttackStatus.SUCCESS),
        make_result("jailbreak", AttackStatus.FAILURE),
        make_result("prompt_injection", AttackStatus.FAILURE),
    ]
    results[1].response = "Réponse avec accents et emoji 🔓"
    return SessionReport(
        target_model="fake-model",
        target_provider="fake",
        completed_at=datetime.now(timezone.utc),
        results=results,
        total_attacks=len(results),
        successful_attacks=1,
        robustness_score=compute_score(results),
        metadata={"judge_model": "none", "attack_modules": ["jailbreak", "prompt_injection"]},
    )


def test_json_report_round_trips(session, tmp_path):
    path = tmp_path / "report.json"

    json_reporter.generate(session, path)

    assert SessionReport.model_validate_json(path.read_text(encoding="utf-8")) == session


def test_markdown_report_content(session, tmp_path):
    path = tmp_path / "report.md"

    markdown_reporter.generate(session, path)

    md = path.read_text(encoding="utf-8")
    assert md.startswith("# LLM Red Team")
    assert "fake / fake-model" in md
    assert "**82/100**" in md
    for section in ("## Methodology", "## Score Breakdown", "## Control Baselines", "## Jailbreak", "## Prompt Injection"):
        assert section in md
    assert "Réponse avec accents et emoji 🔓" in md
