import json

import click
import litellm
import pytest
from click.testing import CliRunner
from litellm.exceptions import NotFoundError

from llm_redteam import cli
from llm_redteam.attacks import ATTACK_REGISTRY
from tests.helpers import FakeTarget, make_response


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
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: FakeTarget())

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


def _fails_after_preflight(prompt: str, system_prompt: str | None) -> str:
    if prompt == "ping":
        return "pong"
    raise RuntimeError("groq request failed: service unavailable")


def test_unreachable_target_aborts_before_any_attack(monkeypatch, no_sleep, tmp_path):
    # Reproduces a real incident: a retired model used to burn 18 probes and score 100/100 "Robust".
    target = FakeTarget(_unreachable)
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: target)

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "groq", "--model", "retired-model", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code != 0
    assert "target cannot be used" in result.output
    assert "model_not_found" in result.output
    assert len(target.calls) == 1  # only the preflight ping
    assert not any(tmp_path.iterdir())


def test_scan_where_every_probe_fails_has_no_score(monkeypatch, no_sleep, tmp_path):
    # The target answers the preflight check, then fails on every attack.
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: FakeTarget(_fails_after_preflight))

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


@pytest.fixture
def judge_calls(monkeypatch):
    """Route the judge's LiteLLM calls to a recorder; `verdict` holds the answer to return (or an exception)."""
    calls: list[dict] = []
    verdict: list = ['{"success": false, "confidence": 0.9, "reason": "refused"}']

    async def _acompletion(**kwargs):
        calls.append(kwargs)
        if isinstance(verdict[0], Exception):
            raise verdict[0]
        return make_response(verdict[0])

    monkeypatch.setattr(litellm, "acompletion", _acompletion)
    monkeypatch.setattr(cli, "_build_target", lambda target_name, model: FakeTarget("Neutral answer."))
    return calls, verdict


def test_unusable_judge_aborts_before_any_attack(judge_calls, no_sleep, tmp_path):
    calls, verdict = judge_calls
    verdict[0] = NotFoundError(message="model_not_found", model="judge", llm_provider="groq")

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "groq", "--model", "m", "--judge-model", "retired-judge", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code != 0
    assert "judge cannot be used" in result.output
    assert len(calls) == 1
    assert not any(tmp_path.iterdir())


def test_judge_can_come_from_another_provider(judge_calls, no_sleep, tmp_path, monkeypatch):
    calls, _ = judge_calls
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:1234")

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "groq", "--model", "m", "--attacks", "prompt_injection",
        "--judge-provider", "ollama", "--judge-model", "llama3.2", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code == 0, result.output
    assert "ollama / llama3.2" in result.output
    assert {c["model"] for c in calls} == {"ollama/llama3.2"}
    assert all(c["api_base"] == "http://ollama.local:1234" for c in calls)


def test_judge_defaults_to_the_target_provider(judge_calls, no_sleep, tmp_path):
    calls, _ = judge_calls

    result = CliRunner().invoke(cli.main, [
        "scan", "--target", "groq", "--model", "m", "--attacks", "prompt_injection",
        "--judge-model", "openai/gpt-oss-120b", "--output-dir", str(tmp_path),
    ])

    assert result.exit_code == 0, result.output
    assert {c["model"] for c in calls} == {"groq/openai/gpt-oss-120b"}
