from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from charter_parser.config import Settings
from charter_parser.ir import page_ir_with_lines
from charter_parser.layout_profile import infer_layout_profile
from charter_parser.models import Clause, PageIR, RunReport
from charter_parser.pdf_backend import PyMuPDFBackend
from charter_parser.reporting import (
    HISTORY_DIR,
    LATEST_DIR,
    ensure_fresh_output,
    fingerprint,
    new_run_id,
    publish_run_report,
    repo_rel,
)
from charter_parser.schema_tools import assert_json_data_valid
from charter_parser.utils import read_json, utc_now_iso, write_json, write_jsonl


REPO_ROOT = Path(__file__).resolve().parents[2]


def _runs_dir() -> Path:
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return LATEST_DIR


def _publish_json_artifact(src: Path, dest: Path) -> list[dict] | dict:
    data = read_json(src)
    if isinstance(data, list):
        write_json(dest, data)
    else:
        write_json(dest, data)
    return data


def _baseline_command(settings: Settings, pdf_path: str | Path) -> str:
    return (
        "python -m charter_parser.cli baseline "
        f"--pdf {pdf_path} "
        "--out artifacts/runs/latest/clauses.json "
        "--config configs/default.yaml"
    )


def _probe_command(pdf_path: str | Path) -> str:
    return (
        "python -m charter_parser.cli probe "
        f"--pdf {pdf_path} "
        "--config configs/default.yaml"
    )


def run_legacy_baseline(pdf_path: str | Path, out_path: str | Path, settings: Settings) -> list[Clause]:
    runs_dir = _runs_dir()
    run_id = new_run_id("baseline")
    run_dir = HISTORY_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir = REPO_ROOT / "legacy"
    started_at = utc_now_iso()

    staged_part2 = run_dir / "part2.json"
    staged_riders = run_dir / "riders.json"
    latest_part2 = runs_dir / "part2.json"
    latest_riders = runs_dir / "riders.json"
    latest_clauses = Path(out_path)

    subprocess.run(
        [
            sys.executable,
            str(legacy_dir / "_part1_extractor.py"),
            "--pdf", str(pdf_path),
            "--page-from", str(settings.legacy.part2_page_from),
            "--page-to", str(settings.legacy.part2_page_to),
            "--section", "part2",
            "--out", str(staged_part2),
        ],
        check=True,
    )

    subprocess.run(
        [
            sys.executable,
            str(legacy_dir / "_part2_extractor.py"),
            "--pdf", str(pdf_path),
            "--page-from", str(settings.legacy.riders_page_from),
            "--page-to", str(settings.legacy.riders_page_to),
            "--out", str(staged_riders),
        ],
        check=True,
    )

    part2 = _publish_json_artifact(staged_part2, latest_part2)
    riders = _publish_json_artifact(staged_riders, latest_riders)
    merged = list(part2) + list(riders)
    for i, item in enumerate(merged, start=1):
        item["order"] = i

    clauses = [Clause(**item) for item in merged]
    clause_payload = [c.model_dump() for c in clauses]
    assert_json_data_valid(clause_payload, "clauses.schema.json", label="clauses.json")
    write_json(latest_clauses, clause_payload)

    freshness_checks = [
        ensure_fresh_output(latest_part2, [pdf_path]),
        ensure_fresh_output(latest_riders, [pdf_path]),
        ensure_fresh_output(latest_clauses, [pdf_path]),
    ]
    finished_at = utc_now_iso()
    report = RunReport(
        run_id=run_id,
        mode="baseline",
        command=_baseline_command(settings, pdf_path),
        started_at=started_at,
        finished_at=finished_at,
        pdf_path=str(pdf_path),
        artifacts={
            "part2": repo_rel(latest_part2),
            "riders": repo_rel(latest_riders),
            "clauses": repo_rel(latest_clauses),
        },
        inputs={
            "pdf": fingerprint(pdf_path, role="input"),
        },
        artifact_provenance={
            "part2": fingerprint(latest_part2, role="generated"),
            "riders": fingerprint(latest_riders, role="generated"),
            "clauses": fingerprint(latest_clauses, role="generated"),
        },
        metrics={
            "clause_count": len(clauses),
        },
        freshness={
            "status": "fresh",
            "checks": freshness_checks,
        },
        notes=["Legacy baseline executed via frozen scripts."],
    )
    publish_run_report("baseline", report)
    return clauses


def probe_document(pdf_path: str | Path, settings: Settings) -> tuple[list[PageIR], dict, RunReport]:
    runs_dir = _runs_dir()
    run_id = new_run_id("probe")
    started_at = utc_now_iso()
    backend = PyMuPDFBackend(pdf_path)
    pages: list[PageIR] = []
    for page_index in range(backend.page_count()):
        page_ir = backend.extract_page_ir(page_index)
        page_ir = page_ir_with_lines(page_ir, y_tol=settings.parsing.line_group_y_tol)
        pages.append(page_ir)

    profile = infer_layout_profile(pages)
    page_payload = json.loads(json.dumps([page.model_dump() for page in pages], ensure_ascii=False))
    for i, page in enumerate(page_payload):
        assert_json_data_valid(page, "page_ir.schema.json", label=f"page_ir[{i}]")
    profile_payload = json.loads(json.dumps(profile.model_dump(), ensure_ascii=False))
    assert_json_data_valid(profile_payload, "layout_profile.schema.json", label="layout_profile.json")

    page_ir_path = runs_dir / "page_ir.jsonl"
    layout_profile_path = runs_dir / "layout_profile.json"
    write_jsonl(page_ir_path, page_payload)
    write_json(layout_profile_path, profile_payload)

    avg_lines = sum(len(page.lines) for page in pages) / max(1, len(pages))
    low_conf = [p.page_index for p in profile.pages if p.confidence < settings.parsing.low_confidence_page_threshold]
    freshness_checks = [
        ensure_fresh_output(page_ir_path, [pdf_path]),
        ensure_fresh_output(layout_profile_path, [pdf_path]),
    ]
    finished_at = utc_now_iso()
    report = RunReport(
        run_id=run_id,
        mode="probe",
        command=_probe_command(pdf_path),
        started_at=started_at,
        finished_at=finished_at,
        pdf_path=str(pdf_path),
        artifacts={
            "page_ir": repo_rel(page_ir_path),
            "layout_profile": repo_rel(layout_profile_path),
        },
        inputs={
            "pdf": fingerprint(pdf_path, role="input"),
        },
        artifact_provenance={
            "page_ir": fingerprint(page_ir_path, role="generated"),
            "layout_profile": fingerprint(layout_profile_path, role="generated"),
        },
        metrics={
            "page_count": len(pages),
            "page_ir_pages_written": len(pages),
            "avg_lines_per_page": round(avg_lines, 2),
            "layout_profile_pages_scored": len(profile.pages),
            "low_confidence_pages": low_conf,
        },
        freshness={
            "status": "fresh",
            "checks": freshness_checks,
        },
        notes=["Automatic geometric reconnaissance scaffold only; not used for clause extraction yet."],
    )
    publish_run_report("probe", report)
    return pages, profile_payload, report


def run_pipeline(pdf_path: str | Path, out_path: str | Path, settings: Settings, mode: str = "legacy") -> list[Clause]:
    if mode == "legacy":
        return run_legacy_baseline(pdf_path, out_path, settings)
    raise NotImplementedError("Unified clause extraction is intentionally deferred. Start with probe + deterministic milestones from PLANS.md.")
