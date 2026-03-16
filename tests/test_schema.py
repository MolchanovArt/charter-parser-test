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
                "clean_text": "Condition 1. Owners shall",
                "title_text": "Condition",
                "body_text": "Owners shall",
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


def test_ambiguity_case_schema_accepts_window_payload():
    payload = {
        "case_id": "p32_start_0100:nested_numbering",
        "bucket": "nested_numbering",
        "page": 32,
        "block_id": "p32_start_0100",
        "line_id": "p32_l0028",
        "candidate_clause_id": "part2:1",
        "candidate_local_num": 1,
        "section_hint": "part2",
        "previous_section": "part2",
        "previous_clause_id": "part2:42",
        "next_clause_id": None,
        "candidate_line_ids": ["p32_l0028", "p32_l0029"],
        "candidate_lines": [
            {"line_id": "p32_l0028", "page": 32, "text": "1. Drawing"},
            {"line_id": "p32_l0029", "page": 32, "text": "Owners shall supply Charterers with copies"},
        ],
        "line_window": [
            {"line_id": "p32_l0027", "page": 32, "text": "42. Japan Clause"},
            {"line_id": "p32_l0028", "page": 32, "text": "1. Drawing"},
        ],
        "evidence": ["low_number_restart_after_high_clause", "numbering_backtrack_in_section"],
    }
    errors = validate_json_data(payload, "ambiguity_case.schema.json")
    assert errors == []


def test_adjudication_result_schema_accepts_structured_decision():
    payload = {
        "case_id": "p22_banner_0052:false_banner_section",
        "bucket": "false_banner_section",
        "page": 22,
        "block_id": "p22_banner_0052",
        "status": "accepted",
        "applied": True,
        "effect": "attach_to_previous",
        "decision": {
            "candidate_start": False,
            "attach_to_previous": True,
            "section_hint": "shell",
            "title_line_ids": [],
            "body_line_ids": ["p22_l0016"],
            "confidence": 0.95,
            "reason_short": "inline reference stays in current clause",
        },
        "error": None,
    }
    errors = validate_json_data(payload, "adjudication_result.schema.json")
    assert errors == []


def test_adjudication_report_schema_accepts_metrics_and_examples():
    payload = {
        "run_id": "20260316T000000000000Z-unified_adjudicated",
        "mode": "structured_adjudication_report",
        "metrics": {
            "ambiguity_case_count": 4,
            "applied_case_count": 2,
            "clause_count_before": 109,
            "clause_count_after": 91,
        },
        "examples": [
            {
                "case_id": "p22_banner_0052:false_banner_section",
                "status": "accepted",
                "effect": "attach_to_previous",
                "before": "part2",
                "after": "shell",
            }
        ],
        "notes": [],
    }
    errors = validate_json_data(payload, "adjudication_report.schema.json")
    assert errors == []
