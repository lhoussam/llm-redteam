"""Masking of model outputs that may be harmful or copyrighted, for reports and datasets that are shared.

A response is masked when its attack may have produced such content, i.e. the attack succeeded or its verdict is
unknown, in a category where success means harmful or copyrighted output:
- jailbreaks (and their baselines): malware, phishing, drug synthesis...;
- hallucinations: possibly dangerous misinformation (e.g. a "dosage" of bleach);
- training data extraction: copyrighted text.
Prompt injections and planted-secret extractions only ever leak harmless test values, so they stay visible,
as do all blocked attempts.
"""
from llm_redteam.core.result import AttackStatus, SessionReport


def _sensitive_kind(attack_name: str) -> str | None:
    module, _, name = attack_name.partition("/")
    if module == "jailbreak" or attack_name.startswith("baseline/jailbreak"):
        return "jailbreak"
    if module == "hallucination":
        return "hallucination"
    if module == "data_extraction" and name.startswith("training_data"):
        return "training data extraction"
    return None


def needs_redaction(attack_name: str, verdict: str, response: str) -> bool:
    """verdict: the attack outcome ("success", "failure", "skipped", "error", or a human label such as "unsure")."""
    return bool(response) and verdict != AttackStatus.FAILURE.value and _sensitive_kind(attack_name) is not None


def redact_text(attack_name: str, verdict: str, response: str) -> str:
    if not needs_redaction(attack_name, verdict, response):
        return response
    return (f"[REDACTED: {_sensitive_kind(attack_name)} response judged {verdict.upper()}, "
            f"{len(response)} characters. Run the scan yourself to see it.]")


def outputs(n: int) -> str:
    return f"{n} model output{'' if n == 1 else 's'}"


def count_sensitive(report: SessionReport) -> int:
    return sum(1 for r in report.results if needs_redaction(r.attack_name, r.status.value, r.response))


def redact_report(report: SessionReport) -> SessionReport:
    """Return a copy of the report with every potentially harmful or copyrighted response masked."""
    redacted = report.model_copy(deep=True)
    masked = 0
    for r in redacted.results:
        if needs_redaction(r.attack_name, r.status.value, r.response):
            r.response = redact_text(r.attack_name, r.status.value, r.response)
            masked += 1
    redacted.metadata = {**redacted.metadata, "redacted": True, "redacted_responses": masked}
    return redacted
