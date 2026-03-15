from charter_parser.schema_tools import validate_json_data
from charter_parser.utils import read_json


def test_golden_matches_clause_schema():
    data = read_json("artifacts/golden/clauses_merged.json")
    errors = validate_json_data(data, "clauses.schema.json")
    assert errors == []


def test_run_report_schema_accepts_provenance_fields():
    payload = {
        "run_id": "20260316T000000000000Z-baseline",
        "mode": "baseline",
        "command": "python -m charter_parser.cli baseline",
        "started_at": "2026-03-16T00:00:00Z",
        "finished_at": "2026-03-16T00:00:05Z",
        "pdf_path": "data/raw/voyage-charter-example.pdf",
        "archived_report_path": "artifacts/runs/history/20260316T000000000000Z-baseline/baseline_report.json",
        "artifacts": {"clauses": "artifacts/runs/latest/clauses.json"},
        "inputs": {"pdf": {"path": "data/raw/voyage-charter-example.pdf"}},
        "artifact_provenance": {"clauses": {"path": "artifacts/runs/latest/clauses.json"}},
        "metrics": {"clause_count": 91},
        "freshness": {"status": "fresh"},
        "notes": [],
    }
    errors = validate_json_data(payload, "run_report.schema.json")
    assert errors == []


def test_eval_report_schema_accepts_provenance_fields():
    payload = {
        "run_id": "20260316T000000000000Z-eval",
        "mode": "eval",
        "command": "python scripts/eval_against_reference.py",
        "started_at": "2026-03-16T00:00:00Z",
        "finished_at": "2026-03-16T00:00:02Z",
        "golden_path": "artifacts/golden/clauses_merged.json",
        "candidate_path": "artifacts/runs/latest/clauses.json",
        "archived_report_path": "artifacts/runs/history/20260316T000000000000Z-eval/eval_report.json",
        "inputs": {"golden": {"path": "artifacts/golden/clauses_merged.json"}},
        "artifact_provenance": {"candidate": {"path": "artifacts/runs/latest/clauses.json"}},
        "metrics": {"golden_count": 91},
        "freshness": {"status": "fresh"},
        "notes": [],
    }
    errors = validate_json_data(payload, "eval_report.schema.json")
    assert errors == []
