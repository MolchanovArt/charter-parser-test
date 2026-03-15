from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from charter_parser.assembly import assemble_draft_clauses, compare_clause_sets, worst_mismatches
from charter_parser.candidate_generation import generate_candidate_blocks
from charter_parser.config import Settings
from charter_parser.ir import page_ir_with_lines
from charter_parser.layout_profile import infer_layout_profile
from charter_parser.models import Clause, DraftClause, LayoutProfile, PageIR, RunReport
from charter_parser.pdf_backend import PyMuPDFBackend
from charter_parser.reporting import (
    HISTORY_DIR,
    LATEST_DIR,
    assert_report_matches_artifact,
    ensure_fresh_output,
    fingerprint,
    new_run_id,
    publish_run_report,
    repo_rel,
)
from charter_parser.schema_tools import assert_json_data_valid
from charter_parser.utils import atomic_write_text, normalize_ws, read_json, read_jsonl, utc_now_iso, write_json, write_jsonl


REPO_ROOT = Path(__file__).resolve().parents[2]


def _runs_dir() -> Path:
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return LATEST_DIR


def _history_dir(run_id: str) -> Path:
    path = HISTORY_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _publish_json_artifact(src: Path, dest: Path) -> list[dict] | dict:
    data = read_json(src)
    write_json(dest, data)
    return data


def _write_json_dual(latest_path: Path, archived_path: Path, data) -> None:
    write_json(latest_path, data)
    write_json(archived_path, data)


def _write_jsonl_dual(latest_path: Path, archived_path: Path, rows: list[dict]) -> None:
    write_jsonl(latest_path, rows)
    write_jsonl(archived_path, rows)


def _write_md_dual(latest_path: Path, archived_path: Path, text: str) -> None:
    atomic_write_text(latest_path, text)
    atomic_write_text(archived_path, text)


def _baseline_command(pdf_path: str | Path) -> str:
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


def _unified_command(pdf_path: str | Path) -> str:
    return (
        "python -m charter_parser.cli unified "
        f"--pdf {pdf_path} "
        "--out artifacts/runs/latest/clauses_unified.json "
        "--config configs/default.yaml"
    )


def run_legacy_baseline(pdf_path: str | Path, out_path: str | Path, settings: Settings) -> list[Clause]:
    runs_dir = _runs_dir()
    run_id = new_run_id("baseline")
    run_dir = _history_dir(run_id)
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
        command=_baseline_command(pdf_path),
        started_at=started_at,
        finished_at=finished_at,
        pdf_path=str(pdf_path),
        artifacts={
            "part2": repo_rel(latest_part2),
            "riders": repo_rel(latest_riders),
            "clauses": repo_rel(latest_clauses),
        },
        inputs={"pdf": fingerprint(pdf_path, role="input")},
        artifact_provenance={
            "part2": fingerprint(latest_part2, role="generated"),
            "riders": fingerprint(latest_riders, role="generated"),
            "clauses": fingerprint(latest_clauses, role="generated"),
        },
        metrics={"clause_count": len(clauses)},
        freshness={"status": "fresh", "checks": freshness_checks},
        notes=["Legacy baseline executed via frozen scripts."],
    )
    publish_run_report("baseline", report)
    return clauses


def _extract_probe_inputs(pdf_path: str | Path, settings: Settings) -> tuple[list[PageIR], LayoutProfile]:
    backend = PyMuPDFBackend(pdf_path)
    pages: list[PageIR] = []
    for page_index in range(backend.page_count()):
        page_ir = backend.extract_page_ir(page_index)
        page_ir = page_ir_with_lines(page_ir, y_tol=settings.parsing.line_group_y_tol)
        pages.append(page_ir)
    profile = infer_layout_profile(pages)
    return pages, profile


def probe_document(pdf_path: str | Path, settings: Settings) -> tuple[list[PageIR], dict, RunReport]:
    runs_dir = _runs_dir()
    run_id = new_run_id("probe")
    started_at = utc_now_iso()
    pages, profile = _extract_probe_inputs(pdf_path, settings)

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
        inputs={"pdf": fingerprint(pdf_path, role="input")},
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
        freshness={"status": "fresh", "checks": freshness_checks},
        notes=["Automatic geometric reconnaissance scaffold only; not used for clause extraction yet."],
    )
    publish_run_report("probe", report)
    return pages, profile_payload, report


def _load_fresh_probe_inputs(pdf_path: str | Path, settings: Settings) -> tuple[list[PageIR], LayoutProfile, dict]:
    runs_dir = _runs_dir()
    probe_report = assert_report_matches_artifact(
        mode="probe",
        artifact_key="page_ir",
        artifact_path=runs_dir / "page_ir.jsonl",
        input_keys=["pdf"],
    )
    assert_report_matches_artifact(
        mode="probe",
        artifact_key="layout_profile",
        artifact_path=runs_dir / "layout_profile.json",
        input_keys=["pdf"],
    )
    pages = [PageIR(**row) for row in read_jsonl(runs_dir / "page_ir.jsonl")]
    profile = LayoutProfile(**read_json(runs_dir / "layout_profile.json"))
    page_from = settings.legacy.part2_page_from
    page_to = settings.legacy.riders_page_to
    pages = [page for page in pages if page_from <= page.page_index <= page_to]
    profile = LayoutProfile(page_count=len(pages), pages=[page for page in profile.pages if page_from <= page.page_index <= page_to])
    return pages, profile, probe_report


def _load_baseline_anchor(pdf_path: str | Path) -> tuple[list[Clause], dict]:
    runs_dir = _runs_dir()
    baseline_report = assert_report_matches_artifact(
        mode="baseline",
        artifact_key="clauses",
        artifact_path=runs_dir / "clauses.json",
        input_keys=["pdf"],
    )
    clauses = [Clause(**row) for row in read_json(runs_dir / "clauses.json")]
    return clauses, baseline_report


def _candidate_markdown(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# Candidate report",
        "",
        "## Metrics",
        "",
        f"- candidate_clause_start_count: {metrics['candidate_clause_start_count']}",
        f"- candidate_continuation_count: {metrics['candidate_continuation_count']}",
        f"- noise_block_count: {metrics['noise_block_count']}",
        f"- title_line_precision_proxy: {metrics['title_line_precision_proxy']}",
        f"- body_line_precision_proxy: {metrics['body_line_precision_proxy']}",
        f"- right_noise_suppression_rate: {metrics['right_noise_suppression_rate']}",
        f"- header_footer_suppression_rate: {metrics['header_footer_suppression_rate']}",
        f"- candidate_clause_start_recall_proxy_vs_legacy: {metrics['candidate_clause_start_recall_proxy_vs_legacy']}",
        f"- candidate_clause_start_recall_proxy_vs_reference: {metrics['candidate_clause_start_recall_proxy_vs_reference']}",
        "",
        "## Suspicious pages",
        "",
    ]
    for page in report["pages"][:10]:
        if page["start_blocks"] == 0 or page["suppressed_header_footer_lines"] or page["suppressed_right_noise_words"]:
            lines.append(
                f"- page {page['page']} ({page['page_type']}): starts={page['start_blocks']}, "
                f"continuations={page['continuation_blocks']}, right-noise={page['suppressed_right_noise_words']}, "
                f"header/footer={page['suppressed_header_footer_lines']}"
            )
    lines.extend(["", "## Examples", ""])
    for example in report.get("examples", [])[:8]:
        lines.append(
            f"- page {example['page']} {example['block_type']} {example.get('candidate_clause_id') or example['block_id']}: "
            f"title=`{example['title_text']}` body=`{example['body_text_head']}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def _assembly_markdown(report: dict) -> str:
    metrics = report["metrics"]
    vs_legacy = report["comparisons"]["vs_legacy"]
    vs_reference = report["comparisons"]["vs_reference"]
    lines = [
        "# Assembly report",
        "",
        "## Metrics",
        "",
        f"- unified_clause_count: {metrics['unified_clause_count']}",
        f"- legacy_clause_count: {metrics['legacy_clause_count']}",
        f"- reference_clause_count: {metrics['reference_clause_count']}",
        f"- boundary_alignment_proxy_vs_reference: {metrics['boundary_alignment_proxy_vs_reference']}",
        f"- split_merge_error_proxy: {metrics['split_merge_error_proxy']}",
        f"- body_text_overlap_proxy_vs_reference: {metrics['body_text_overlap_proxy_vs_reference']}",
        "",
        "## Legacy comparison",
        "",
        f"- count_delta: {vs_legacy['count_delta']}",
        f"- missing_ids: {', '.join(vs_legacy['missing_ids']) or '-'}",
        f"- extra_ids: {', '.join(vs_legacy['extra_ids']) or '-'}",
        f"- title_similarity_mean: {vs_legacy['title_similarity_mean']}",
        f"- text_similarity_mean: {vs_legacy['text_similarity_mean']}",
        "",
        "## Reference comparison",
        "",
        f"- count_delta: {vs_reference['count_delta']}",
        f"- missing_ids: {', '.join(vs_reference['missing_ids']) or '-'}",
        f"- extra_ids: {', '.join(vs_reference['extra_ids']) or '-'}",
        f"- title_similarity_mean: {vs_reference['title_similarity_mean']}",
        f"- text_similarity_mean: {vs_reference['text_similarity_mean']}",
        "",
        "## Worst mismatches",
        "",
    ]
    for failure in report.get("failures", [])[:10]:
        if "id" in failure:
            lines.append(
                f"- {failure['id']} page {failure.get('page_start', '?')}: "
                f"title_similarity={failure.get('title_similarity')} text_similarity={failure.get('text_similarity')}"
            )
        else:
            lines.append(f"- {failure['type']} on page {failure.get('page')}: {failure.get('block_id', '')}")
    return "\n".join(lines).rstrip() + "\n"


def _build_candidate_report(run_id: str, diagnostics: dict, blocks: list, legacy_count: int, reference_count: int) -> dict:
    metrics = dict(diagnostics["metrics"])
    metrics["candidate_clause_start_recall_proxy_vs_legacy"] = round(
        metrics["candidate_clause_start_count"] / max(1, legacy_count),
        4,
    )
    metrics["candidate_clause_start_recall_proxy_vs_reference"] = round(
        metrics["candidate_clause_start_count"] / max(1, reference_count),
        4,
    )
    examples = [
        {
            "block_id": block.block_id,
            "page": block.page,
            "block_type": block.block_type,
            "candidate_clause_id": block.candidate_clause_id,
            "title_text": block.title_text[:120],
            "body_text_head": normalize_ws(block.body_text)[:180],
            "reasons": block.reasons,
        }
        for block in blocks
        if block.block_type != "noise_block" and (not block.title_text or not block.body_text or block.support_score < 0.75)
    ][:10]
    report = {
        "run_id": run_id,
        "mode": "unified_candidate_report",
        "metrics": metrics,
        "pages": diagnostics["page_summaries"],
        "examples": examples,
        "notes": [
            "title_line_precision_proxy and body_line_precision_proxy are heuristic proxies, not gold-labeled precision.",
            "candidate_clause_start_recall_proxy compares detected starts to legacy/reference clause counts.",
        ],
    }
    assert_json_data_valid(report, "candidate_report.schema.json", label="candidate_report.json")
    return report


def _build_assembly_report(run_id: str, clauses: list[DraftClause], assembly_diag: dict, legacy: list[Clause], reference: list[Clause], blocks: list) -> dict:
    vs_legacy = compare_clause_sets(clauses, legacy)
    vs_reference = compare_clause_sets(clauses, reference)
    metrics = dict(assembly_diag["metrics"])
    metrics.update({
        "legacy_clause_count": len(legacy),
        "reference_clause_count": len(reference),
        "source_candidate_block_count": len(blocks),
        "boundary_alignment_proxy_vs_reference": vs_reference["id_sequence_match_ratio"],
        "split_merge_error_proxy": len(vs_reference["missing_ids"]) + len(vs_reference["extra_ids"]),
        "body_text_overlap_proxy_vs_reference": vs_reference["text_similarity_mean"],
        "normalized_title_similarity_vs_reference": vs_reference["title_similarity_mean"],
    })
    failures = list(assembly_diag["failures"]) + worst_mismatches(clauses, reference)
    report = {
        "run_id": run_id,
        "mode": "unified_assembly_report",
        "metrics": metrics,
        "comparisons": {
            "vs_legacy": vs_legacy,
            "vs_reference": vs_reference,
        },
        "failures": failures,
        "notes": [
            "boundary_alignment_proxy_vs_reference and split_merge_error_proxy are proxies until line-level gold boundaries exist.",
            "The unified draft is deterministic and assembled only from source lines.",
        ],
    }
    assert_json_data_valid(report, "assembly_report.schema.json", label="assembly_report.json")
    return report


def run_unified_draft(pdf_path: str | Path, out_path: str | Path, settings: Settings) -> list[DraftClause]:
    runs_dir = _runs_dir()
    run_id = new_run_id("unified")
    run_dir = _history_dir(run_id)
    started_at = utc_now_iso()

    pages, profile, probe_report = _load_fresh_probe_inputs(pdf_path, settings)
    legacy_clauses, baseline_report = _load_baseline_anchor(pdf_path)
    reference_clauses = [Clause(**row) for row in read_json(settings.project.golden_path)]

    blocks, diagnostics = generate_candidate_blocks(pages, profile, settings)
    candidate_rows = [block.model_dump() for block in blocks]
    for idx, row in enumerate(candidate_rows):
        assert_json_data_valid(row, "candidate_blocks.schema.json", label=f"candidate_blocks[{idx}]")

    candidate_path = runs_dir / "candidate_blocks.jsonl"
    candidate_archived_path = run_dir / "candidate_blocks.jsonl"
    _write_jsonl_dual(candidate_path, candidate_archived_path, candidate_rows)

    candidate_report = _build_candidate_report(run_id, diagnostics, blocks, len(legacy_clauses), len(reference_clauses))
    candidate_report_path = runs_dir / "candidate_report.json"
    candidate_report_archived_path = run_dir / "candidate_report.json"
    _write_json_dual(candidate_report_path, candidate_report_archived_path, candidate_report)

    candidate_md = _candidate_markdown(candidate_report)
    candidate_md_path = runs_dir / "candidate_report.md"
    candidate_md_archived_path = run_dir / "candidate_report.md"
    _write_md_dual(candidate_md_path, candidate_md_archived_path, candidate_md)

    clauses, assembly_diag = assemble_draft_clauses(blocks)
    clause_rows = [clause.model_dump() for clause in clauses]
    assert_json_data_valid(clause_rows, "clauses_unified.schema.json", label="clauses_unified.json")
    clause_path = Path(out_path)
    clause_archived_path = run_dir / "clauses_unified.json"
    _write_json_dual(clause_path, clause_archived_path, clause_rows)

    assembly_report = _build_assembly_report(run_id, clauses, assembly_diag, legacy_clauses, reference_clauses, blocks)
    assembly_report_path = runs_dir / "assembly_report.json"
    assembly_report_archived_path = run_dir / "assembly_report.json"
    _write_json_dual(assembly_report_path, assembly_report_archived_path, assembly_report)

    assembly_md = _assembly_markdown(assembly_report)
    assembly_md_path = runs_dir / "assembly_report.md"
    assembly_md_archived_path = run_dir / "assembly_report.md"
    _write_md_dual(assembly_md_path, assembly_md_archived_path, assembly_md)

    freshness_checks = [
        ensure_fresh_output(candidate_path, [runs_dir / "page_ir.jsonl", runs_dir / "layout_profile.json"]),
        ensure_fresh_output(clause_path, [candidate_path]),
        ensure_fresh_output(candidate_report_path, [candidate_path]),
        ensure_fresh_output(assembly_report_path, [clause_path]),
    ]
    finished_at = utc_now_iso()
    report = RunReport(
        run_id=run_id,
        mode="unified",
        command=_unified_command(pdf_path),
        started_at=started_at,
        finished_at=finished_at,
        pdf_path=str(pdf_path),
        artifacts={
            "candidate_blocks": repo_rel(candidate_path),
            "candidate_report_json": repo_rel(candidate_report_path),
            "candidate_report_md": repo_rel(candidate_md_path),
            "clauses_unified": repo_rel(clause_path),
            "assembly_report_json": repo_rel(assembly_report_path),
            "assembly_report_md": repo_rel(assembly_md_path),
        },
        inputs={
            "pdf": fingerprint(pdf_path, role="input"),
            "probe_report": {
                "path": probe_report["archived_report_path"],
                "role": "derived",
                "run_id": probe_report["run_id"],
            },
            "baseline_report": {
                "path": baseline_report["archived_report_path"],
                "role": "derived",
                "run_id": baseline_report["run_id"],
            },
            "golden": fingerprint(settings.project.golden_path, role="reference"),
        },
        artifact_provenance={
            "candidate_blocks": fingerprint(candidate_path, role="generated"),
            "candidate_report_json": fingerprint(candidate_report_path, role="generated"),
            "clauses_unified": fingerprint(clause_path, role="generated"),
            "assembly_report_json": fingerprint(assembly_report_path, role="generated"),
        },
        metrics={
            "candidate_clause_start_count": candidate_report["metrics"]["candidate_clause_start_count"],
            "unified_clause_count": assembly_report["metrics"]["unified_clause_count"],
            "reference_count_delta": assembly_report["comparisons"]["vs_reference"]["count_delta"],
            "legacy_count_delta": assembly_report["comparisons"]["vs_legacy"]["count_delta"],
            "duplicate_ids": assembly_report["metrics"]["duplicate_ids"],
            "order_violations": assembly_report["metrics"]["order_violations"],
            "banner_leaks": assembly_report["metrics"]["banner_leaks"],
        },
        freshness={
            "status": "fresh",
            "checks": freshness_checks,
            "consumed_probe_run_id": probe_report["run_id"],
            "consumed_baseline_run_id": baseline_report["run_id"],
        },
        notes=[
            "Unified deterministic draft consumed fresh probe artifacts and baseline/reference anchors.",
            "Metrics include both exact comparisons and explicitly labeled proxies in the candidate and assembly reports.",
        ],
    )
    publish_run_report("unified", report)
    return clauses


def run_pipeline(pdf_path: str | Path, out_path: str | Path, settings: Settings, mode: str = "legacy"):
    if mode == "legacy":
        return run_legacy_baseline(pdf_path, out_path, settings)
    if mode == "unified":
        return run_unified_draft(pdf_path, out_path, settings)
    raise NotImplementedError("Unknown pipeline mode.")
