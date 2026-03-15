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


def test_candidate_block_schema_accepts_traceability_fields():
    payload = {
        "block_id": "p5_start_0001",
        "page": 5,
        "page_type": "margin_title_like",
        "routing_mode": "margin_title_like",
        "section_hint": "part2",
        "block_type": "candidate_clause_start",
        "line_ids": ["p5_l0002", "p5_l0003"],
        "title_line_ids": ["p5_l0002", "p5_l0003"],
        "body_line_ids": ["p5_l0002", "p5_l0003"],
        "noise_line_ids": [],
        "line_decisions": [
            {
                "line_id": "p5_l0002",
                "raw_text": "Condition 1. Owners shall 68",
                "extracted_text": "Owners shall",
                "labels": ["title_line", "body_line", "candidate_clause_start"],
                "reasons": ["left_title_band_text", "body_payload_from_start"],
            }
        ],
        "candidate_clause_id": "part2:1",
        "candidate_local_num": 1,
        "title_text": "Condition Of vessel",
        "body_text": "Owners shall",
        "support_score": 0.8,
        "reasons": ["start_regex:1"],
    }
    errors = validate_json_data(payload, "candidate_blocks.schema.json")
    assert errors == []


def test_unified_clause_schema_accepts_traceability_fields():
    payload = [
        {
            "order": 1,
            "section": "part2",
            "local_num": 1,
            "id": "part2:1",
            "title": "Condition Of vessel",
            "text": "Owners shall exercise due diligence",
            "page_start": 5,
            "page_end": 6,
            "candidate_block_ids": ["p5_start_0001", "p6_cont_0002"],
            "title_line_ids": ["p5_l0002", "p5_l0003"],
            "body_line_ids": ["p5_l0002", "p6_l0001"],
            "support_score": 0.73,
        }
    ]
    errors = validate_json_data(payload, "clauses_unified.schema.json")
    assert errors == []
