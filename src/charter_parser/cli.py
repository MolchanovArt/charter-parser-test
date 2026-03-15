from __future__ import annotations

import json
from pathlib import Path

import typer

from charter_parser.config import load_settings
from charter_parser.pipeline import probe_document, run_pipeline
from charter_parser.models import RunReport
from charter_parser.reporting import assert_report_matches_artifact, new_run_id, publish_run_report
from charter_parser.utils import utc_now_iso
from charter_parser.validators import validate_clause_file


app = typer.Typer(no_args_is_help=True)


@app.command()
def baseline(
    pdf: str = typer.Option("data/raw/voyage-charter-example.pdf", help="Path to source PDF"),
    out: str = typer.Option("artifacts/runs/latest/clauses.json", help="Path to merged output JSON"),
    config: str = typer.Option("configs/default.yaml", help="Path to YAML config"),
) -> None:
    settings = load_settings(config)
    clauses = run_pipeline(pdf, out, settings, mode="legacy")
    typer.echo(f"Wrote {len(clauses)} clauses to {out}")


@app.command()
def unified(
    pdf: str = typer.Option("data/raw/voyage-charter-example.pdf", help="Path to source PDF"),
    out: str = typer.Option("artifacts/runs/latest/clauses_unified.json", help="Path to unified draft JSON"),
    config: str = typer.Option("configs/default.yaml", help="Path to YAML config"),
) -> None:
    settings = load_settings(config)
    clauses = run_pipeline(pdf, out, settings, mode="unified")
    typer.echo(f"Wrote {len(clauses)} unified draft clauses to {out}")


@app.command()
def probe(
    pdf: str = typer.Option("data/raw/voyage-charter-example.pdf", help="Path to source PDF"),
    config: str = typer.Option("configs/default.yaml", help="Path to YAML config"),
) -> None:
    settings = load_settings(config)
    pages, profile, _report = probe_document(pdf, settings)
    typer.echo(f"Wrote PageIR for {len(pages)} pages and layout_profile.json")


@app.command()
def validate(
    json_path: str = typer.Option("artifacts/runs/latest/clauses.json", "--json", help="Path to clauses JSON"),
) -> None:
    started_at = utc_now_iso()
    baseline_report = assert_report_matches_artifact(
        mode="baseline",
        artifact_key="clauses",
        artifact_path=json_path,
        input_keys=["pdf"],
    )
    report = validate_clause_file(json_path)
    validate_report = RunReport(
        run_id=new_run_id("validate"),
        mode="validate",
        command=f"python -m charter_parser.cli validate --json {json_path}",
        started_at=started_at,
        finished_at=utc_now_iso(),
        pdf_path=baseline_report["pdf_path"],
        artifacts={"clauses": json_path},
        inputs={
            "baseline_report": {
                "path": baseline_report["archived_report_path"],
                "role": "derived",
                "run_id": baseline_report["run_id"],
            }
        },
        artifact_provenance={
            "clauses": baseline_report["artifact_provenance"]["clauses"],
        },
        metrics=report,
        freshness={
            "status": "fresh",
            "consumed_mode": "baseline",
            "consumed_run_id": baseline_report["run_id"],
        },
        notes=["Validation consumed a baseline artifact that matched the latest baseline provenance record."],
    )
    publish_run_report("validate", validate_report)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
