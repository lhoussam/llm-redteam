from pathlib import Path

from llm_redteam.core.result import SessionReport


def generate(report: SessionReport, output_path: Path) -> None:
    output_path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
