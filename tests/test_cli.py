import json

import click
import pytest
from click.testing import CliRunner

from llm_redteam import cli
from llm_redteam.attacks import ATTACK_REGISTRY
from tests.helpers import FakeTarget


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o-mini", "gpt-4o-mini"),
        ("llama3.2:3b", "llama3.2-3b"),
        ("meta-llama/llama-4-scout", "meta-llama-llama-4-scout"),
        ('a<b>c|d?e*f"g\\h', "a-b-c-d-e-f-g-h"),
        (":::", "model"),
    ],
)
def test_safe_filename(model, expected):
    assert cli._safe_filename(model) == expected


class TestParseAttacks:
    def test_all(self):
        assert cli._parse_attacks("all") == list(ATTACK_REGISTRY)

    def test_comma_separated_list(self):
        assert cli._parse_attacks("jailbreak, hallucination") == ["jailbreak", "hallucination"]

    def test_unknown_module_is_rejected(self):
        with pytest.raises(click.BadParameter, match="Unknown attack module"):
            cli._parse_attacks("jailbreak,teleportation")


def test_unknown_module_fails_the_command():
    result = CliRunner().invoke(cli.main, ["scan", "--target", "groq", "--model", "m", "--attacks", "teleportation"])
    assert result.exit_code != 0
    assert "Unknown attack module" in result.output


def test_scan_end_to_end_offline(monkeypatch, no_sleep, tmp_path):
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: (FakeTarget(), None))

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "ollama", "--model", "llama3.2:3b",
        "--attacks", "prompt_injection", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code == 0, result.output
    assert "ROBUSTNESS SCORE" in result.output
    reports = sorted(p.name for p in tmp_path.iterdir())
    assert len(reports) == 2
    assert all(name.startswith("ollama_llama3.2-3b_") for name in reports)
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert data["target_model"] == "llama3.2:3b"
    assert data["robustness_score"] == 100.0
    assert data["score_status"] == "incomplete"  # only one of the four scored categories was tested
    assert "jailbreak: not tested" in data["score_issues"]


def _unreachable(prompt: str, system_prompt: str | None) -> str:
    raise RuntimeError("groq request failed: model_not_found")


def test_scan_where_every_probe_fails_has_no_score(monkeypatch, no_sleep, tmp_path):
    # Reproduces a real incident: a retired model returned 18 errors and used to score 100/100 "Robust".
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: (FakeTarget(_unreachable), None))

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "groq", "--model", "retired-model",
        "--attacks", "prompt_injection", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code == 0, result.output
    assert "N/A" in result.output
    assert "100/100" not in result.output
    data = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert data["robustness_score"] is None
    assert data["score_status"] == "no_data"
