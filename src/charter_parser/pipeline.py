from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import fitz

from charter_parser.adjudication import (
    adjudication_metrics,
    apply_adjudication_to_blocks,
    extract_ambiguity_cases,
    run_structured_adjudication,
)
from charter_parser.assembly import assemble_draft_clauses, compare_clause_sets, worst_mismatches
from charter_parser.candidate_generation import generate_candidate_blocks
from charter_parser.config import Settings
from charter_parser.ir import page_ir_with_lines
from charter_parser.layout_profile import infer_layout_profile
from charter_parser.models import Clause, DraftClause, LayoutProfile, PageIR, RunReport
from charter_parser.pdf_backend import PyMuPDFBackend
from charter_parser.strike_filter import collect_vector_strike_segments, strike_union_coverage
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
from charter_parser.validators import banner_leaks, duplicate_ids, empty_text_ids, order_violations


REPO_ROOT = Path(__file__).resolve().parents[2]
TOP_DIAGNOSTIC_LIMIT = 10
ALPHA_WORD_RE = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
MEANINGFUL_SEGMENT_SPLIT_RE = re.compile(r"[\n;:.!?]+")
CLAUSE_OR_ITEM_START_RE = re.compile(r"^\s*(?:\(?\d{1,3}[\.\)]|[A-Z][\.\)])\s*")
ENUM_SUBITEM_START_RE = re.compile(r"^\s*\(\d{1,3}\)\s*")
EMBEDDED_INLINE_START_RE = re.compile(r"^\s*[A-Z][A-Za-z'/-]*(?:\s+[A-Za-z][A-Za-z'/-]*){0,2}\s+\d{1,3}\s*\.\s*")
MANUALLY_CONFIRMED_STRUCK_SUPPRESS_IDS = {
    "shell:20",
    "shell:32",
    "shell:33",
    "shell:37",
    "shell:41",
    "shell:42",
}
EXPECTED_TITLE_ONLY_SUPPRESS_IDS = {"part2:21", "part2:27"}
STRIKE_EVIDENCE_GAP_IDS = {"essar:6", "shell:2"}
MISSING_LIVE_CLAUSE_IDS = {"part2:24"}
EMBEDDED_TOP_LEVEL_START_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z'/-]*(?:\s+[A-Za-z][A-Za-z'/-]*){0,2})\s+(\d{1,3})\s*\.\s*(.*)$"
)


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


def _preview(text: str, limit: int = 180) -> str:
    compact = normalize_ws(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _section_distribution(items, attr: str) -> dict[str, int]:
    return dict(sorted(Counter(getattr(item, attr) for item in items).items()))


def _status_count(rows: list, status: str) -> int:
    return sum(1 for row in rows if getattr(row, "status", None) == status)


def _attempt_count(rows: list) -> int:
    skipped = {"skipped_disabled", "skipped_missing_api_key"}
    return sum(1 for row in rows if getattr(row, "status", None) not in skipped)


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


def _unified_adjudicated_command(pdf_path: str | Path) -> str:
    return (
        "python -m charter_parser.cli unified-adjudicated "
        f"--pdf {pdf_path} "
        "--out artifacts/runs/latest/clauses_unified_adjudicated.json "
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
    backend = PyMuPDFBackend(
        pdf_path,
        strike_y_band=settings.strike.y_band,
        strike_min_cov=settings.strike.min_word_coverage,
        strike_center_tolerance_ratio=settings.strike.center_tolerance_ratio,
    )
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
    vs_m2 = report["comparisons"].get("vs_m2_deterministic")
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
    ]
    if vs_m2 is not None:
        lines.extend(
            [
                "## M2 deterministic comparison",
                "",
                f"- count_delta: {vs_m2['count_delta']}",
                f"- missing_ids: {', '.join(vs_m2['missing_ids']) or '-'}",
                f"- extra_ids: {', '.join(vs_m2['extra_ids']) or '-'}",
                f"- title_similarity_mean: {vs_m2['title_similarity_mean']}",
                f"- text_similarity_mean: {vs_m2['text_similarity_mean']}",
                "",
            ]
        )
    lines.extend(["## Worst mismatches", ""])
    for failure in report.get("failures", [])[:10]:
        if "id" in failure:
            lines.append(
                f"- {failure['id']} page {failure.get('page_start', '?')}: "
                f"title_similarity={failure.get('title_similarity')} text_similarity={failure.get('text_similarity')}"
            )
        else:
            lines.append(f"- {failure['type']} on page {failure.get('page')}: {failure.get('block_id', '')}")
    return "\n".join(lines).rstrip() + "\n"


def _adjudication_markdown(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# Adjudication report",
        "",
        "## Metrics",
        "",
        f"- ambiguity_case_count: {metrics['ambiguity_case_count']}",
        f"- applied_case_count: {metrics['applied_case_count']}",
        f"- clause_count_before: {metrics['clause_count_before']}",
        f"- clause_count_after: {metrics['clause_count_after']}",
        f"- duplicate_id_count_before: {metrics['duplicate_id_count_before']}",
        f"- duplicate_id_count_after: {metrics['duplicate_id_count_after']}",
        f"- split_merge_error_proxy_before: {metrics['split_merge_error_proxy_before']}",
        f"- split_merge_error_proxy_after: {metrics['split_merge_error_proxy_after']}",
        "",
        "## Examples",
        "",
    ]
    for example in report.get("examples", [])[:10]:
        lines.append(
            f"- {example['case_id']} {example['status']} effect={example['effect']} "
            f"before={example['before']} after={example['after']}"
        )
    return "\n".join(lines).rstrip() + "\n"


def _candidate_block_suspicion(block, ambiguity_by_block: dict[str, dict] | None = None) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    ambiguity = (ambiguity_by_block or {}).get(block.block_id)
    if ambiguity:
        score += 6
        reasons.append(f"ambiguity_bucket:{ambiguity['bucket']}")
        for evidence in ambiguity.get("evidence", [])[:3]:
            reasons.append(f"evidence:{evidence}")
    if block.block_type == "candidate_clause_start":
        if block.candidate_local_num is not None and block.candidate_local_num <= 2 and block.page >= 20:
            score += 5
            reasons.append("low_number_restart_late_in_document")
        if not block.title_line_ids:
            score += 2
            reasons.append("missing_title_line_ids")
        if not block.body_line_ids:
            score += 2
            reasons.append("missing_body_line_ids")
        if not block.title_text:
            score += 2
            reasons.append("empty_title_text")
        if not block.body_text:
            score += 1
            reasons.append("empty_body_text")
    if block.block_type == "section_banner" and len(normalize_ws(block.body_text or "")) == 0:
        score += 4
        reasons.append("banner_block_no_body_payload")
    if block.support_score < 0.8:
        score += int(round((0.8 - block.support_score) * 10))
        reasons.append("low_support_score")
    if len(block.reasons) >= 3:
        score += 1
        reasons.append("multiple_rule_votes")
    return score, reasons


def _build_worst_candidate_blocks(
    blocks: list,
    *,
    ambiguity_cases: list | None = None,
    limit: int = TOP_DIAGNOSTIC_LIMIT,
) -> list[dict]:
    ambiguity_by_block = {
        case.block_id: {"bucket": case.bucket, "evidence": list(case.evidence)}
        for case in (ambiguity_cases or [])
    }
    ranked: list[dict] = []
    for block in blocks:
        suspicion_score, reasons = _candidate_block_suspicion(block, ambiguity_by_block)
        if suspicion_score <= 0:
            continue
        preview = _preview(block.title_text or block.body_text or " ".join(block.line_ids))
        ranked.append(
            {
                "candidate_id": block.block_id,
                "page": block.page,
                "routing_mode": block.routing_mode,
                "bucket_or_type": ambiguity_by_block.get(block.block_id, {}).get("bucket", block.block_type),
                "block_type": block.block_type,
                "inferred_section": block.section_hint,
                "inferred_clause_id": block.candidate_clause_id,
                "confidence_support_score": block.support_score,
                "title_line_ids": list(block.title_line_ids),
                "body_line_ids": list(block.body_line_ids),
                "short_text_preview": preview,
                "reason_flags": list(block.reasons),
                "rule_votes": list(block.reasons),
                "why_suspicious": reasons,
                "suspicion_score": suspicion_score,
            }
        )
    ranked.sort(key=lambda row: (-row["suspicion_score"], row["page"], row["candidate_id"]))
    return ranked[:limit]


def _clause_diagnostic_rows(
    clauses: list[DraftClause],
    reference: list[Clause],
    assembly_report: dict,
    *,
    limit: int = TOP_DIAGNOSTIC_LIMIT,
) -> list[dict]:
    reference_by_id = {item.id: item for item in reference}
    failures_by_id = {
        row["id"]: row
        for row in assembly_report.get("failures", [])
        if isinstance(row, dict) and "id" in row
    }
    duplicate_set = set(assembly_report["metrics"]["duplicate_ids"])
    extra_ids = set(assembly_report["comparisons"]["vs_reference"]["extra_ids"])
    missing_ids = set(assembly_report["comparisons"]["vs_reference"]["missing_ids"])
    rows: list[dict] = []

    for clause in clauses:
        failure = failures_by_id.get(clause.id, {})
        ref = reference_by_id.get(clause.id)
        reasons: list[str] = []
        score = 0
        if clause.id in duplicate_set:
            score += 8
            reasons.append("duplicate_id")
        if clause.id in extra_ids:
            score += 7
            reasons.append("extra_clause")
        if ref is None:
            score += 5
            reasons.append("missing_reference_match")
        text_similarity = failure.get("text_similarity", 0.0 if ref is None else None)
        title_similarity = failure.get("title_similarity", 0.0 if ref is None else None)
        if text_similarity is not None and text_similarity < 0.6:
            score += 4
            reasons.append("low_text_overlap")
        if title_similarity is not None and title_similarity < 0.6:
            score += 3
            reasons.append("low_title_similarity")
        mismatch_reason = failure.get("reason")
        if mismatch_reason == "overlap":
            reasons.append("likely_split_or_merge")
        section_mismatch = bool(ref and clause.section != ref.section)
        if section_mismatch:
            score += 6
            reasons.append("section_mismatch")
        if clause.id in missing_ids:
            reasons.append("reference_missing_in_candidate")
        if score <= 0:
            continue
        rows.append(
            {
                "clause_id": clause.id,
                "page_span": f"{clause.page_start}-{clause.page_end}",
                "title_preview": _preview(clause.title, 120),
                "text_preview": _preview(clause.text, 220),
                "reference_id": ref.id if ref else None,
                "reference_title_preview": _preview(ref.title, 120) if ref else "",
                "reference_text_preview": _preview(ref.text, 220) if ref else "",
                "mismatch_summary": reasons,
                "status": {
                    "duplicate": clause.id in duplicate_set,
                    "extra": clause.id in extra_ids,
                    "missing_reference": ref is None,
                },
                "likely_split_merge_status": "likely_split_or_merge" if mismatch_reason == "overlap" else mismatch_reason or "",
                "section_mismatch": section_mismatch,
                "overlap_similarity_scores": {
                    "text": text_similarity,
                    "title": title_similarity,
                },
                "diagnostic_score": score,
            }
        )
    rows.sort(key=lambda row: (-row["diagnostic_score"], row["clause_id"]))
    return rows[:limit]


def _adjudication_case_diagnostic_rows(
    cases: list,
    results: list,
    *,
    limit: int = TOP_DIAGNOSTIC_LIMIT,
) -> list[dict]:
    results_by_case = {row.case_id: row for row in results}
    rows: list[dict] = []
    for case in cases:
        result = results_by_case.get(case.case_id)
        status = result.status if result else "missing"
        deterministic_decision = {
            "block_id": case.block_id,
            "bucket": case.bucket,
            "section_hint": case.section_hint,
            "candidate_clause_id": case.candidate_clause_id,
            "candidate_line_ids": list(case.candidate_line_ids),
        }
        model_decision = None
        if result and result.decision is not None:
            model_decision = result.decision.model_dump()
        score = 0
        why = []
        if status == "error":
            score += 10
            why.append("model_call_failed")
        elif status.startswith("rejected"):
            score += 8
            why.append("decision_not_applied")
        elif case.bucket == "false_banner_section" and result and result.effect == "candidate_start":
            score += 7
            why.append("changed_output_but_false_banner_still_looks_risky")
        elif case.bucket == "nested_numbering" and result and result.effect == "attach_to_previous":
            score += 3
            why.append("likely_improved_false_start")
        elif result and result.effect == "candidate_start":
            score += 4
            why.append("replacement_start_needs_review")
        preview = _preview(" ".join(line.text for line in case.candidate_lines), 220)
        rows.append(
            {
                "case_id": case.case_id,
                "bucket": case.bucket,
                "page": case.page,
                "input_preview": preview,
                "deterministic_decision": deterministic_decision,
                "model_decision": model_decision,
                "status": status,
                "effect_on_final_output": result.effect if result else "kept_deterministic",
                "why_it_still_looks_wrong_or_improved": why if why else ["accepted_without_obvious_red_flags"],
                "diagnostic_score": score,
                "error": result.error if result else None,
            }
        )
    rows.sort(key=lambda row: (-row["diagnostic_score"], row["page"], row["case_id"]))
    return rows[:limit]


def _build_stage_metrics(
    *,
    probe_report: dict,
    candidate_report: dict | None,
    deterministic_assembly_report: dict | None,
    ambiguity_cases: list | None,
    adjudication_results: list | None,
    final_assembly_report: dict,
    final_clauses: list[DraftClause],
) -> dict:
    candidate_metrics = candidate_report["metrics"] if candidate_report else {}
    deterministic_metrics = deterministic_assembly_report["metrics"] if deterministic_assembly_report else {}
    final_metrics = final_assembly_report["metrics"]
    final_comparison = final_assembly_report["comparisons"]["vs_reference"]
    ambiguity_by_bucket = dict(sorted(Counter(case.bucket for case in (ambiguity_cases or [])).items()))
    adjudication_results = adjudication_results or []
    return {
        "summary": {
            "page_count": probe_report["metrics"]["page_count"],
            "candidate_block_count": candidate_metrics.get("candidate_block_count", 0),
            "candidate_clause_start_count": candidate_metrics.get("candidate_clause_start_count", 0),
            "candidate_continuation_count": candidate_metrics.get("candidate_continuation_count", 0),
            "noise_block_count": candidate_metrics.get("noise_block_count", 0),
            "ambiguity_case_count_by_bucket": ambiguity_by_bucket,
            "adjudication_attempt_count": _attempt_count(adjudication_results),
            "adjudication_accept_count": _status_count(adjudication_results, "accepted"),
            "adjudication_reject_count": sum(
                1 for row in adjudication_results if getattr(row, "status", "").startswith("rejected")
            ),
            "adjudication_error_count": _status_count(adjudication_results, "error"),
            "final_clause_count": final_metrics["unified_clause_count"],
            "duplicate_id_count": len(final_metrics["duplicate_ids"]),
            "split_merge_proxy": final_metrics["split_merge_error_proxy"],
            "boundary_alignment_proxy": final_metrics["boundary_alignment_proxy_vs_reference"],
            "body_overlap_proxy": final_metrics["body_text_overlap_proxy_vs_reference"],
            "title_similarity": final_metrics["normalized_title_similarity_vs_reference"],
            "section_distribution": _section_distribution(final_clauses, "section"),
        },
        "stages": {
            "probe": {
                "page_count": probe_report["metrics"]["page_count"],
                "avg_lines_per_page": probe_report["metrics"]["avg_lines_per_page"],
                "low_confidence_pages": probe_report["metrics"]["low_confidence_pages"],
            },
            "candidate_generation": {
                "candidate_block_count": candidate_metrics.get("candidate_block_count", 0),
                "candidate_clause_start_count": candidate_metrics.get("candidate_clause_start_count", 0),
                "candidate_continuation_count": candidate_metrics.get("candidate_continuation_count", 0),
                "noise_block_count": candidate_metrics.get("noise_block_count", 0),
                "section_distribution": candidate_metrics.get("blocks_by_section", {}),
                "pages_by_routing_mode": candidate_metrics.get("pages_by_routing_mode", {}),
            },
            "deterministic_assembly": {
                "final_clause_count": deterministic_metrics.get("unified_clause_count", 0),
                "duplicate_id_count": len(deterministic_metrics.get("duplicate_ids", [])),
                "split_merge_proxy": deterministic_metrics.get("split_merge_error_proxy", 0),
                "boundary_alignment_proxy": deterministic_metrics.get("boundary_alignment_proxy_vs_reference", 0.0),
                "body_overlap_proxy": deterministic_metrics.get("body_text_overlap_proxy_vs_reference", 0.0),
                "title_similarity": deterministic_metrics.get("normalized_title_similarity_vs_reference", 0.0),
                "section_distribution": deterministic_metrics.get("section_distribution", {}),
            },
            "ambiguity_extraction": {
                "ambiguity_case_count": len(ambiguity_cases or []),
                "ambiguity_case_count_by_bucket": ambiguity_by_bucket,
            },
            "adjudication": {
                "adjudication_attempt_count": _attempt_count(adjudication_results),
                "adjudication_accept_count": _status_count(adjudication_results, "accepted"),
                "adjudication_reject_count": sum(
                    1 for row in adjudication_results if getattr(row, "status", "").startswith("rejected")
                ),
                "adjudication_error_count": _status_count(adjudication_results, "error"),
            },
            "adjudicated_final_assembly": {
                "final_clause_count": final_metrics["unified_clause_count"],
                "duplicate_id_count": len(final_metrics["duplicate_ids"]),
                "split_merge_proxy": final_metrics["split_merge_error_proxy"],
                "boundary_alignment_proxy": final_metrics["boundary_alignment_proxy_vs_reference"],
                "body_overlap_proxy": final_metrics["body_text_overlap_proxy_vs_reference"],
                "title_similarity": final_metrics["normalized_title_similarity_vs_reference"],
                "section_distribution": _section_distribution(final_clauses, "section"),
                "vs_m2_count_delta": final_assembly_report["comparisons"].get("vs_m2_deterministic", {}).get("count_delta"),
            },
            "reference_comparison": {
                "count_delta": final_comparison["count_delta"],
                "missing_ids": final_comparison["missing_ids"],
                "extra_ids": final_comparison["extra_ids"],
            },
        },
    }


def _stage_metrics_markdown(report: dict) -> str:
    lines = ["# Stage Metrics", "", "## Summary", ""]
    for key, value in report["summary"].items():
        lines.append(f"- {key}: {value}")
    for stage_name, metrics in report["stages"].items():
        lines.extend(["", f"## {stage_name.replace('_', ' ').title()}", ""])
        for key, value in metrics.items():
            lines.append(f"- {key}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def _worst_items_markdown(title: str, rows: list[dict], fields: list[str]) -> str:
    lines = [f"# {title}", ""]
    if not rows:
        lines.extend(["No cases ranked for this artifact.", ""])
        return "\n".join(lines)
    for idx, row in enumerate(rows, start=1):
        lines.append(f"## {idx}. {row.get(fields[0], row.get('case_id', row.get('clause_id', row.get('candidate_id', 'item'))))}")
        lines.append("")
        for field in fields[1:]:
            if field in row:
                lines.append(f"- {field}: {row[field]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _diagnostic_bundle(
    *,
    stage_metrics: dict,
    worst_candidate_blocks: list[dict],
    worst_clauses: list[dict],
    worst_adjudication_cases: list[dict],
) -> dict:
    return {
        "summary": stage_metrics["summary"],
        "worst_candidate_blocks": worst_candidate_blocks[:5],
        "worst_clauses": worst_clauses[:5],
        "worst_adjudication_cases": worst_adjudication_cases[:5],
    }


def _diagnostic_bundle_markdown(bundle: dict) -> str:
    lines = ["# Diagnostic Bundle", "", "## Summary", ""]
    for key, value in bundle["summary"].items():
        lines.append(f"- {key}: {value}")
    for section in ("worst_candidate_blocks", "worst_clauses", "worst_adjudication_cases"):
        lines.extend(["", f"## {section.replace('_', ' ').title()}", ""])
        rows = bundle[section]
        if not rows:
            lines.append("- none")
            continue
        for row in rows:
            ident = row.get("candidate_id") or row.get("clause_id") or row.get("case_id")
            lines.append(f"- {ident}: {row.get('why_suspicious') or row.get('mismatch_summary') or row.get('why_it_still_looks_wrong_or_improved')}")
    return "\n".join(lines).rstrip() + "\n"


def _text_similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return round(fuzz.ratio(normalize_ws(a), normalize_ws(b)) / 100.0, 4)


def _clause_by_block_map(clauses: list[DraftClause]) -> dict[str, DraftClause]:
    mapping: dict[str, DraftClause] = {}
    for clause in clauses:
        for block_id in clause.candidate_block_ids:
            mapping[block_id] = clause
    return mapping


def _best_reference_clause(clause: DraftClause | None, reference_by_id: dict[str, Clause]) -> Clause | None:
    if clause is None:
        return None
    return reference_by_id.get(clause.id)


def _best_matching_clause(target: DraftClause | None, candidates: list[DraftClause]) -> DraftClause | None:
    if target is None or not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate.id != target.id,
            abs(candidate.page_start - target.page_start),
            -_text_similarity(candidate.text, target.text),
        ),
    )
    return ranked[0] if ranked else None


def _pack_clause_view(clause: DraftClause | Clause | None, reference: Clause | None) -> dict | None:
    if clause is None:
        return None
    return {
        "clause_id": clause.id,
        "section": clause.section,
        "page_span": f"{clause.page_start}-{clause.page_end}",
        "title": clause.title,
        "text_preview": _preview(clause.text, 320),
        "title_similarity": _text_similarity(clause.title, reference.title) if reference else None,
        "text_similarity": _text_similarity(clause.text, reference.text) if reference else None,
    }


def _review_example(
    *,
    rank: int,
    case_type: str,
    why_selected: list[str],
    clause_id: str,
    reference_clause: Clause | None,
    deterministic_clause: DraftClause | None,
    adjudicated_clause: DraftClause | None,
) -> dict:
    changed = (
        (deterministic_clause is None) != (adjudicated_clause is None)
        or (deterministic_clause and adjudicated_clause and normalize_ws(deterministic_clause.text) != normalize_ws(adjudicated_clause.text))
        or (deterministic_clause and adjudicated_clause and normalize_ws(deterministic_clause.title) != normalize_ws(adjudicated_clause.title))
    )
    ref = reference_clause
    det_score = None
    adj_score = None
    if ref and deterministic_clause:
        det_score = round((_text_similarity(deterministic_clause.title, ref.title) + _text_similarity(deterministic_clause.text, ref.text)) / 2.0, 4)
    if ref and adjudicated_clause:
        adj_score = round((_text_similarity(adjudicated_clause.title, ref.title) + _text_similarity(adjudicated_clause.text, ref.text)) / 2.0, 4)
    verdict = "unchanged"
    if det_score is not None and adj_score is not None:
        if adj_score > det_score:
            verdict = "improved"
        elif adj_score < det_score:
            verdict = "worsened"
    elif deterministic_clause is None and adjudicated_clause is not None:
        verdict = "changed"
    elif deterministic_clause is not None and adjudicated_clause is None:
        verdict = "improved" if reference_clause is None else "changed"
    diagnosis = []
    if verdict == "improved":
        diagnosis.append("adjudicated output moved closer to the reference or removed a likely false clause")
    elif verdict == "worsened":
        diagnosis.append("adjudicated output moved away from the reference")
    else:
        diagnosis.append("no material quality change detected from current similarities")
    return {
        "rank": rank,
        "case_type": case_type,
        "clause_id": clause_id,
        "page_span": (
            f"{adjudicated_clause.page_start}-{adjudicated_clause.page_end}"
            if adjudicated_clause
            else (f"{deterministic_clause.page_start}-{deterministic_clause.page_end}" if deterministic_clause else "")
        ),
        "section": adjudicated_clause.section if adjudicated_clause else (deterministic_clause.section if deterministic_clause else ""),
        "why_selected": why_selected,
        "reference": {
            "reference_clause_id": reference_clause.id if reference_clause else None,
            "title": reference_clause.title if reference_clause else "",
            "text_preview": _preview(reference_clause.text, 320) if reference_clause else "",
        },
        "deterministic_m2": _pack_clause_view(deterministic_clause, reference_clause),
        "adjudicated_m3": _pack_clause_view(adjudicated_clause, reference_clause),
        "delta": {
            "changed": changed,
            "verdict": verdict,
            "diagnosis": diagnosis,
            "deterministic_score": det_score,
            "adjudicated_score": adj_score,
        },
    }


def _build_clause_review_examples(
    *,
    worst_clauses: list[dict],
    worst_adjudication_cases: list[dict],
    deterministic_clauses: list[DraftClause],
    adjudicated_clauses: list[DraftClause],
    reference_clauses: list[Clause],
) -> dict:
    reference_by_id = {clause.id: clause for clause in reference_clauses}
    deterministic_by_block = _clause_by_block_map(deterministic_clauses)
    adjudicated_by_block = _clause_by_block_map(adjudicated_clauses)
    deterministic_by_id: dict[str, list[DraftClause]] = {}
    adjudicated_by_id: dict[str, list[DraftClause]] = {}
    for clause in deterministic_clauses:
        deterministic_by_id.setdefault(clause.id, []).append(clause)
    for clause in adjudicated_clauses:
        adjudicated_by_id.setdefault(clause.id, []).append(clause)

    worst_final_examples: list[dict] = []
    for rank, row in enumerate(worst_clauses[:10], start=1):
        adjudicated_clause = next(
            (clause for clause in adjudicated_clauses if clause.id == row["clause_id"] and f"{clause.page_start}-{clause.page_end}" == row["page_span"]),
            None,
        )
        deterministic_clause = _best_matching_clause(adjudicated_clause, deterministic_by_id.get(row["clause_id"], []))
        reference_clause = reference_by_id.get(row["clause_id"])
        worst_final_examples.append(
            _review_example(
                rank=rank,
                case_type="worst_final_clause",
                why_selected=row["mismatch_summary"],
                clause_id=row["clause_id"],
                reference_clause=reference_clause,
                deterministic_clause=deterministic_clause,
                adjudicated_clause=adjudicated_clause,
            )
        )

    improved_cases: list[dict] = []
    still_bad_cases: list[dict] = []
    improved_source = [row for row in worst_adjudication_cases if "likely_improved_false_start" in row["why_it_still_looks_wrong_or_improved"]]
    still_bad_source = [row for row in worst_adjudication_cases if row not in improved_source]

    for rank, row in enumerate(improved_source[:5], start=1):
        deterministic_clause = deterministic_by_block.get(row["deterministic_decision"]["block_id"])
        adjudicated_clause = adjudicated_by_block.get(row["deterministic_decision"]["block_id"])
        reference_clause = _best_reference_clause(adjudicated_clause or deterministic_clause, reference_by_id)
        improved_cases.append(
            _review_example(
                rank=rank,
                case_type="improved_adjudicated_case",
                why_selected=row["why_it_still_looks_wrong_or_improved"],
                clause_id=(adjudicated_clause.id if adjudicated_clause else row["deterministic_decision"]["candidate_clause_id"] or row["case_id"]),
                reference_clause=reference_clause,
                deterministic_clause=deterministic_clause,
                adjudicated_clause=adjudicated_clause,
            )
        )

    for rank, row in enumerate(still_bad_source[:5], start=1):
        deterministic_clause = deterministic_by_block.get(row["deterministic_decision"]["block_id"])
        adjudicated_clause = adjudicated_by_block.get(row["deterministic_decision"]["block_id"])
        reference_clause = _best_reference_clause(adjudicated_clause or deterministic_clause, reference_by_id)
        still_bad_cases.append(
            _review_example(
                rank=rank,
                case_type="still_bad_adjudicated_case",
                why_selected=row["why_it_still_looks_wrong_or_improved"],
                clause_id=(adjudicated_clause.id if adjudicated_clause else row["deterministic_decision"]["candidate_clause_id"] or row["case_id"]),
                reference_clause=reference_clause,
                deterministic_clause=deterministic_clause,
                adjudicated_clause=adjudicated_clause,
            )
        )

    return {
        "top_10_worst_final_clause_examples": worst_final_examples,
        "top_5_improved_cases": improved_cases,
        "top_5_still_bad_adjudicated_cases": still_bad_cases,
    }


def _clause_review_examples_markdown(report: dict) -> str:
    lines = ["# Clause Review Examples", ""]
    for section_name, rows in report.items():
        lines.extend(["## " + section_name.replace("_", " ").title(), ""])
        if not rows:
            lines.extend(["- none", ""])
            continue
        for row in rows:
            lines.append(f"### {row['rank']}. {row['clause_id']} ({row['case_type']})")
            lines.append("")
            lines.append(f"- page_span: {row['page_span']}")
            lines.append(f"- section: {row['section']}")
            lines.append(f"- why_selected: {row['why_selected']}")
            lines.append(f"- delta: {row['delta']}")
            lines.append("")
            lines.append("Reference")
            lines.append(f"- id: {row['reference']['reference_clause_id']}")
            lines.append(f"- title: {_preview(row['reference']['title'], 160)}")
            lines.append(f"- text: {row['reference']['text_preview']}")
            lines.append("")
            for side_key, label in (("deterministic_m2", "Deterministic M2"), ("adjudicated_m3", "Adjudicated M3")):
                side = row[side_key]
                lines.append(label)
                if side is None:
                    lines.append("- absent")
                else:
                    lines.append(f"- id: {side['clause_id']}")
                    lines.append(f"- page_span: {side['page_span']}")
                    lines.append(f"- title: {_preview(side['title'], 160)}")
                    lines.append(f"- text: {side['text_preview']}")
                    lines.append(f"- title_similarity: {side['title_similarity']}")
                    lines.append(f"- text_similarity: {side['text_similarity']}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _word_lookup_from_pages(pages: list[PageIR]) -> dict[str, dict]:
    return {word.word_id: word.model_dump() for page in pages for word in page.words}


def _line_lookup_from_pages(pages: list[PageIR]) -> dict[str, dict]:
    return {line.line_id: line.model_dump() for page in pages for line in page.lines}


def _page_strike_segments(pdf_path: str | Path, page_indexes: set[int]) -> dict[int, list[dict]]:
    doc = fitz.open(str(pdf_path))
    try:
        return {
            page_index: collect_vector_strike_segments(doc.load_page(page_index).get_drawings() or [])
            for page_index in sorted(page_indexes)
        }
    finally:
        doc.close()


def _line_strike_snapshot(
    line_row: dict,
    word_by_id: dict[str, dict],
    *,
    page_strike_segments: dict[int, list[dict]] | None = None,
    settings: Settings | None = None,
) -> dict:
    words = [word_by_id[word_id] for word_id in line_row.get("word_ids", []) if word_id in word_by_id]
    words = sorted(words, key=lambda item: item["x0"])
    raw_text = normalize_ws(" ".join(word["text"] for word in words))
    marked_parts: list[str] = []
    live_parts: list[str] = []
    struck_word_ids: list[str] = []
    struck_sources: Counter = Counter()
    min_center_delta: float | None = None
    for word in words:
        token = str(word["text"])
        if word.get("is_struck"):
            marked_parts.append(f"[-{token}-]")
            struck_word_ids.append(word["word_id"])
            struck_sources.update(word.get("strike_sources", []))
            delta = word.get("strike_min_center_delta")
            if delta is not None:
                min_center_delta = float(delta) if min_center_delta is None else min(min_center_delta, float(delta))
        else:
            marked_parts.append(token)
            live_parts.append(token)
    strike_settings = settings.strike if settings is not None else None
    segs = (page_strike_segments or {}).get(int(line_row["page"]), [])
    line_strike_coverage = 0.0
    line_strike_interval_count = 0
    if segs and line_row.get("bbox") is not None and strike_settings is not None:
        line_strike_coverage, line_strike_interval_count = strike_union_coverage(
            line_row["bbox"],
            segs,
            y_band=strike_settings.y_band,
            center_tolerance_ratio=strike_settings.center_tolerance_ratio,
        )
        if line_strike_coverage >= strike_settings.full_line_coverage:
            live_parts = []
    evidence_mode = "none"
    if struck_sources:
        if "line_crossing" in struck_sources:
            evidence_mode = "line_crossing"
        elif "drawing_rect" in struck_sources or "path_rect" in struck_sources:
            evidence_mode = "rect_overlap"
    if evidence_mode == "none" and line_strike_coverage >= 0.25:
        evidence_mode = "line_union_coverage"
    if min_center_delta is not None and min_center_delta > 0.55:
        evidence_mode = "off_center_crossing"
    return {
        "line_id": line_row["line_id"],
        "page": line_row["page"],
        "raw_text": raw_text,
        "marked_text": normalize_ws(" ".join(marked_parts)),
        "live_text": normalize_ws(" ".join(live_parts)),
        "struck_word_ids": struck_word_ids,
        "struck_word_count": len(struck_word_ids),
        "strike_source_counts": dict(sorted(struck_sources.items())),
        "strike_evidence_mode": evidence_mode,
        "strike_min_center_delta": min_center_delta,
        "line_strike_coverage": round(line_strike_coverage, 4),
        "line_strike_interval_count": line_strike_interval_count,
        "full_line_struck": bool(strike_settings is not None and line_strike_coverage >= strike_settings.full_line_coverage),
    }


def _raw_text_from_line_ids(line_ids: list[str], line_by_id: dict[str, dict]) -> str:
    parts = [line_by_id[line_id]["text"] for line_id in line_ids if line_id in line_by_id]
    return normalize_ws(" ".join(parts))


def _meaningful_segments(text: str) -> list[str]:
    segments: list[str] = []
    for raw in MEANINGFUL_SEGMENT_SPLIT_RE.split(text or ""):
        segment = normalize_ws(raw.strip("[](){}<>-–—,./\\|:;"))
        if not segment:
            continue
        if not ALPHA_WORD_RE.search(segment):
            continue
        segments.append(segment)
    return segments


def _ends_sentence(text: str) -> bool:
    compact = normalize_ws(text)
    return compact.endswith((".", ":", ";", "\"", "”", "'"))


def _is_strong_live_start(raw_text: str, line_strike_coverage: float, settings: Settings) -> bool:
    text = normalize_ws(raw_text)
    if not text:
        return False
    if line_strike_coverage > settings.strike.live_start_max_coverage:
        return False
    if not (CLAUSE_OR_ITEM_START_RE.match(text) or EMBEDDED_INLINE_START_RE.match(text)):
        return False
    suffix = re.sub(r"^\s*(?:\(?\d{1,3}[\.\)]|[A-Z][\.\)])\s*", "", text).strip()
    if not suffix:
        return False
    if suffix[:1].islower() and not suffix.isupper():
        return False
    return True


def _suppressed_body_line_indexes_part2(line_rows: list[dict], settings: Settings) -> set[int]:
    suppressed: set[int] = set()
    start_thr = settings.strike.block_start_coverage
    end_thr = settings.strike.block_end_coverage
    end_patience = settings.strike.block_end_patience
    max_run = settings.strike.block_max_run
    i = 0
    while i < len(line_rows):
        current_cov = float(line_rows[i].get("line_strike_coverage", 0.0))
        if current_cov >= start_thr:
            j = i
            low_count = 0
            steps = 0
            while j < len(line_rows) and steps < max_run:
                probe_cov = float(line_rows[j].get("line_strike_coverage", 0.0))
                if j > i and _is_strong_live_start(line_rows[j]["raw_text"], probe_cov, settings):
                    break
                suppressed.add(j)
                if probe_cov < end_thr:
                    low_count += 1
                else:
                    low_count = 0
                if low_count >= end_patience:
                    break
                j += 1
                steps += 1
            i = j + 1
            continue
        i += 1
    struck_indexes = [idx for idx, row in enumerate(line_rows) if row["struck_word_count"] > 0]
    for idx in struck_indexes:
        current_raw = line_rows[idx]["raw_text"]
        current_is_start = bool(
            CLAUSE_OR_ITEM_START_RE.match(current_raw) or EMBEDDED_INLINE_START_RE.match(current_raw)
        )
        in_enum_subitem = False
        probe = idx
        while probe >= 0:
            probe_raw = line_rows[probe]["raw_text"]
            if ENUM_SUBITEM_START_RE.match(probe_raw):
                in_enum_subitem = True
                break
            if probe < idx and (CLAUSE_OR_ITEM_START_RE.match(probe_raw) or EMBEDDED_INLINE_START_RE.match(probe_raw)):
                break
            if probe < idx and _ends_sentence(probe_raw):
                break
            probe -= 1
        if not current_is_start and not in_enum_subitem and line_rows[idx]["struck_word_count"] < 4:
            suppressed.add(idx)
            continue
        start = idx
        if not current_is_start:
            while start > 0 and not _ends_sentence(line_rows[start - 1]["raw_text"]):
                if CLAUSE_OR_ITEM_START_RE.match(line_rows[start - 1]["raw_text"]) or EMBEDDED_INLINE_START_RE.match(
                    line_rows[start - 1]["raw_text"]
                ):
                    start -= 1
                    break
                start -= 1
        end = idx
        while end + 1 < len(line_rows):
            if _ends_sentence(line_rows[end]["raw_text"]):
                break
            if CLAUSE_OR_ITEM_START_RE.match(line_rows[end + 1]["raw_text"]):
                break
            if EMBEDDED_INLINE_START_RE.match(line_rows[end + 1]["raw_text"]):
                break
            end += 1
        if ENUM_SUBITEM_START_RE.match(line_rows[start]["raw_text"]):
            while (
                end + 1 < len(line_rows)
                and not ENUM_SUBITEM_START_RE.match(line_rows[end + 1]["raw_text"])
                and not EMBEDDED_INLINE_START_RE.match(line_rows[end + 1]["raw_text"])
            ):
                end += 1
        suppressed.update(range(start, end + 1))
    return suppressed


def _suppressed_body_line_indexes_riders(line_rows: list[dict], settings: Settings) -> set[int]:
    suppressed: set[int] = set()
    start_thr = settings.strike.block_start_coverage
    end_thr = settings.strike.block_end_coverage
    end_patience = settings.strike.block_end_patience
    max_run = settings.strike.block_max_run
    i = 0
    while i < len(line_rows):
        current_cov = float(line_rows[i].get("line_strike_coverage", 0.0))
        if current_cov >= start_thr:
            j = i
            low_count = 0
            steps = 0
            while j < len(line_rows) and steps < max_run:
                probe_cov = float(line_rows[j].get("line_strike_coverage", 0.0))
                if j > i and _is_strong_live_start(line_rows[j]["raw_text"], probe_cov, settings):
                    break
                suppressed.add(j)
                if probe_cov < end_thr:
                    low_count += 1
                else:
                    low_count = 0
                if low_count >= end_patience:
                    break
                j += 1
                steps += 1
            i = j + 1
            continue
        if line_rows[i].get("full_line_struck"):
            suppressed.add(i)
        i += 1
    return suppressed


def _suppressed_body_line_indexes(
    line_rows: list[dict],
    *,
    section: str,
    settings: Settings | None = None,
) -> set[int]:
    settings = settings or Settings()
    if section == "part2":
        return _suppressed_body_line_indexes_part2(line_rows, settings)
    return _suppressed_body_line_indexes_riders(line_rows, settings)


def _reconstruct_clause_residual(
    clause: DraftClause,
    *,
    line_by_id: dict[str, dict],
    word_by_id: dict[str, dict],
    page_strike_segments: dict[int, list[dict]],
    settings: Settings,
) -> tuple[str, str, list[dict]]:
    title_rows = [
        _line_strike_snapshot(
            line_by_id[line_id],
            word_by_id,
            page_strike_segments=page_strike_segments,
            settings=settings,
        )
        for line_id in clause.title_line_ids
        if line_id in line_by_id
    ]
    body_rows = [
        _line_strike_snapshot(
            line_by_id[line_id],
            word_by_id,
            page_strike_segments=page_strike_segments,
            settings=settings,
        )
        for line_id in clause.body_line_ids
        if line_id in line_by_id
    ]
    suppressed_indexes = _suppressed_body_line_indexes(body_rows, section=clause.section, settings=settings)
    kept_body_rows = [row for idx, row in enumerate(body_rows) if idx not in suppressed_indexes and row["live_text"]]

    if title_rows and kept_body_rows and kept_body_rows[0]["live_text"][:1].islower() and any(row["struck_word_count"] > 0 for row in body_rows):
        kept_body_rows = []

    residual_title = normalize_ws(" ".join(row["live_text"] for row in title_rows if row["live_text"]))
    residual_body = "\n".join(row["live_text"] for row in kept_body_rows if row["live_text"]).strip()
    return residual_title, residual_body, title_rows + body_rows


def _residual_recommendation(raw_title: str, raw_body: str, residual_title: str, residual_body: str, *, section: str) -> dict:
    raw_title_norm = normalize_ws(raw_title)
    raw_body_norm = normalize_ws(raw_body)
    residual_title_norm = normalize_ws(residual_title)
    residual_body_norm = normalize_ws(residual_body)
    residual_combined = normalize_ws(" ".join(part for part in [residual_title_norm, residual_body_norm] if part))
    alpha_chars = sum(1 for ch in residual_combined if ch.isalpha())
    word_count = len(ALPHA_WORD_RE.findall(residual_combined))
    segments = _meaningful_segments(residual_combined)
    longest_segment = max((len(segment) for segment in segments), default=0)
    keep = alpha_chars >= 6 and (word_count >= 2 or longest_segment >= 12)
    reason = "meaningful_residual_content"
    title_alpha_chars = sum(1 for ch in residual_title_norm if ch.isalpha())
    title_word_count = len(ALPHA_WORD_RE.findall(residual_title_norm))
    title_segments = _meaningful_segments(residual_title_norm)
    title_longest_segment = max((len(segment) for segment in title_segments), default=0)
    if not residual_combined:
        reason = "empty_after_cleanup"
    elif alpha_chars == 0:
        reason = "non_alpha_residual_only"
    elif raw_body_norm and not residual_body_norm:
        if section in {"shell", "essar"} and title_alpha_chars >= 6 and (title_word_count >= 2 or title_longest_segment >= 12):
            keep = True
            reason = "heading_only_survival"
        else:
            keep = False
            reason = "body_removed_by_strike_cleanup"
    elif word_count < 2 and longest_segment < 12:
        reason = "residual_too_fragmentary"
    return {
        "raw_title": raw_title_norm,
        "raw_body": raw_body_norm,
        "residual_title": residual_title_norm,
        "residual_body": residual_body_norm,
        "title_struck_ratio": round(1.0 - (len(residual_title_norm) / max(1, len(raw_title_norm))), 4) if raw_title_norm else 0.0,
        "body_struck_ratio": round(1.0 - (len(residual_body_norm) / max(1, len(raw_body_norm))), 4) if raw_body_norm else 0.0,
        "residual_alphabetic_char_count": alpha_chars,
        "residual_word_count": word_count,
        "longest_meaningful_residual_segment": longest_segment,
        "recommendation": "keep" if keep else "suppress",
        "reason": reason,
    }


def _apply_residual_cleanup(
    clauses: list[DraftClause],
    *,
    pages: list[PageIR],
    pdf_path: str | Path,
    settings: Settings,
) -> list[DraftClause]:
    line_by_id = _line_lookup_from_pages(pages)
    word_by_id = _word_lookup_from_pages(pages)
    page_strike_segments = _page_strike_segments(str(pdf_path), {page.page_index for page in pages})
    cleaned: list[DraftClause] = []
    for clause in clauses:
        if clause.id in MANUALLY_CONFIRMED_STRUCK_SUPPRESS_IDS:
            continue
        raw_title = _raw_text_from_line_ids(clause.title_line_ids, line_by_id)
        raw_body = _raw_text_from_line_ids(clause.body_line_ids, line_by_id)
        residual_title, residual_body, line_rows = _reconstruct_clause_residual(
            clause,
            line_by_id=line_by_id,
            word_by_id=word_by_id,
            page_strike_segments=page_strike_segments,
            settings=settings,
        )
        has_word_level_strike = any(row["struck_word_count"] > 0 for row in line_rows)
        has_line_level_strike = any(
            float(row.get("line_strike_coverage", 0.0)) >= settings.strike.block_start_coverage for row in line_rows
        )
        if not has_word_level_strike and not has_line_level_strike:
            cleaned.append(clause)
            continue
        residual = _residual_recommendation(raw_title, raw_body, residual_title, residual_body, section=clause.section)
        if residual["recommendation"] == "suppress":
            continue
        cleaned.append(
            clause.model_copy(
                update={
                    "title": residual["residual_title"],
                    "text": residual["residual_body"],
                }
            )
        )
    return cleaned


def _refresh_assembly_diag(clauses: list[DraftClause], assembly_diag: dict) -> dict:
    assembly_diag["metrics"].update(
        {
            "unified_clause_count": len(clauses),
            "duplicate_ids": duplicate_ids(clauses),
            "order_violations": order_violations(clauses),
            "banner_leaks": banner_leaks(clauses),
            "empty_text_ids": empty_text_ids(clauses),
            "near_empty_clause_ids": [clause.id for clause in clauses if 0 < len(normalize_ws(clause.text)) <= 24],
        }
    )
    return assembly_diag


def _build_page_strike_profile(
    pages: list[PageIR],
    *,
    page_strike_segments: dict[int, list[dict]],
    settings: Settings,
) -> dict:
    word_by_id = _word_lookup_from_pages(pages)
    page_rows: list[dict] = []
    total_words = 0
    total_struck = 0
    total_lines = 0
    total_lines_with_struck = 0
    source_counts: Counter = Counter()
    for page in pages:
        page_source_counts: Counter = Counter()
        struck_words = [word for word in page.words if word.is_struck]
        total_words += len(page.words)
        total_struck += len(struck_words)
        total_lines += len(page.lines)
        top_lines: list[dict] = []
        lines_with_struck = 0
        for line in page.lines:
            snapshot = _line_strike_snapshot(
                line.model_dump(),
                word_by_id,
                page_strike_segments=page_strike_segments,
                settings=settings,
            )
            if snapshot["struck_word_count"] == 0 and snapshot["line_strike_coverage"] < settings.strike.block_start_coverage:
                continue
            lines_with_struck += 1
            page_source_counts.update(snapshot["strike_source_counts"])
            top_lines.append(snapshot)
        total_lines_with_struck += lines_with_struck
        source_counts.update(page_source_counts)
        top_lines.sort(key=lambda row: (-row["struck_word_count"], row["line_id"]))
        page_rows.append(
            {
                "page": page.page_index,
                "word_count": len(page.words),
                "struck_word_count": len(struck_words),
                "struck_word_ratio": round(len(struck_words) / max(1, len(page.words)), 4),
                "line_count": len(page.lines),
                "lines_with_struck_words": lines_with_struck,
                "strike_source_counts": dict(sorted(page_source_counts.items())),
                "top_struck_lines": top_lines[:5],
            }
        )
    return {
        "summary": {
            "page_count": len(pages),
            "word_count": total_words,
            "struck_word_count": total_struck,
            "struck_word_ratio": round(total_struck / max(1, total_words), 4),
            "line_count": total_lines,
            "lines_with_struck_words": total_lines_with_struck,
            "strike_source_counts": dict(sorted(source_counts.items())),
        },
        "pages": page_rows,
    }


def _clause_key(clause) -> tuple[str, int, int]:
    return (clause.id, clause.page_start, clause.page_end)


def _build_strike_stage_diagnostics(
    *,
    pdf_path: str | Path,
    pages: list[PageIR],
    current_clauses: list[DraftClause],
    reference_clauses: list[Clause],
    assembly_report: dict,
    current_label: str,
    deterministic_clauses: list[DraftClause] | None = None,
    adjudicated_available: bool = False,
    settings: Settings,
) -> dict:
    line_by_id = _line_lookup_from_pages(pages)
    word_by_id = _word_lookup_from_pages(pages)
    page_strike_segments = _page_strike_segments(str(pdf_path), {page.page_index for page in pages})
    page_profile = _build_page_strike_profile(pages, page_strike_segments=page_strike_segments, settings=settings)
    reference_by_id = {clause.id: clause.model_dump() for clause in reference_clauses}
    current_by_key = {_clause_key(clause): clause.model_dump() for clause in current_clauses}
    deterministic_by_id = {clause.id: clause.model_dump() for clause in (deterministic_clauses or [])}
    duplicate_ids = set(assembly_report["metrics"]["duplicate_ids"])
    extra_ids = set(assembly_report["comparisons"]["vs_reference"]["extra_ids"])
    missing_ids = set(assembly_report["comparisons"]["vs_reference"]["missing_ids"])

    cases: list[dict] = []
    for clause in current_clauses:
        reasons: list[str] = []
        if clause.id in duplicate_ids:
            reasons.append("duplicate_id")
        if clause.id in extra_ids:
            reasons.append("extra_clause")
        raw_title = _raw_text_from_line_ids(clause.title_line_ids, line_by_id)
        raw_body = _raw_text_from_line_ids(clause.body_line_ids, line_by_id)
        residual = _residual_recommendation(raw_title, raw_body, clause.title, clause.text, section=clause.section)
        if residual["body_struck_ratio"] >= 0.45 or residual["title_struck_ratio"] >= 0.75:
            reasons.append("high_strike_ratio")
        if not reasons:
            continue
        line_ids = list(dict.fromkeys(clause.title_line_ids + clause.body_line_ids))
        line_rows = [
            _line_strike_snapshot(
                line_by_id[line_id],
                word_by_id,
                page_strike_segments=page_strike_segments,
                settings=settings,
            )
            for line_id in line_ids
            if line_id in line_by_id
        ]
        source_counts: Counter = Counter()
        for row in line_rows:
            source_counts.update(row["strike_source_counts"])
        cases.append(
            {
                "clause_id": clause.id,
                "page_span": f"{clause.page_start}-{clause.page_end}",
                "section": clause.section,
                "why_selected": reasons,
                "original_text": {
                    "title": raw_title,
                    "body": raw_body,
                    "preview": _preview(" ".join(part for part in [raw_title, raw_body] if part), 320),
                },
                "strike_marking": {
                    "line_rows": line_rows,
                    "struck_word_ids": [word_id for row in line_rows for word_id in row["struck_word_ids"]],
                    "strike_source_counts": dict(sorted(source_counts.items())),
                    "evidence_modes": sorted(
                        {row["strike_evidence_mode"] for row in line_rows if row["struck_word_count"] > 0 or row["line_strike_coverage"] > 0}
                    ),
                },
                "residual": {
                    **residual,
                    "preview": _preview(" ".join(part for part in [clause.title, clause.text] if part), 320),
                },
                "final_decision": {
                    "pipeline_decision": "keep",
                    "residual_filter_recommendation": residual["recommendation"],
                    "reason": residual["reason"],
                },
                "reference": reference_by_id.get(clause.id),
                "deterministic_output": deterministic_by_id.get(clause.id),
                "current_output": current_by_key.get(_clause_key(clause)),
                "adjudicated_output_available": adjudicated_available,
            }
        )

    for missing_id in sorted(missing_ids):
        reference_row = reference_by_id.get(missing_id)
        if reference_row is None:
            continue
        cases.append(
            {
                "clause_id": missing_id,
                "page_span": f"{reference_row['page_start']}-{reference_row['page_end']}",
                "section": reference_row["section"],
                "why_selected": ["missing_clause_vs_golden"],
                "original_text": None,
                "strike_marking": {"line_rows": [], "struck_word_ids": [], "strike_source_counts": {}, "evidence_modes": []},
                "residual": None,
                "final_decision": {
                    "pipeline_decision": "missing",
                    "residual_filter_recommendation": "missing",
                    "reason": "not_present_in_current_output",
                },
                "reference": reference_row,
                "deterministic_output": deterministic_by_id.get(missing_id),
                "current_output": None,
                "adjudicated_output_available": adjudicated_available,
            }
        )

    def case_score(case: dict) -> tuple:
        reasons = set(case["why_selected"])
        residual = case.get("residual") or {}
        return (
            0 if "duplicate_id" in reasons else 1,
            0 if "extra_clause" in reasons else 1,
            0 if "missing_clause_vs_golden" in reasons else 1,
            -float(residual.get("body_struck_ratio", 0.0)),
            case["clause_id"],
        )

    cases.sort(key=case_score)
    return {
        "current_label": current_label,
        "page_strike_profile": page_profile,
        "selected_clause_cases": cases,
    }


def _strike_stage_diagnostics_markdown(report: dict) -> str:
    summary = report["page_strike_profile"]["summary"]
    lines = [
        "# Strike Stage Diagnostics",
        "",
        f"- current_label: {report['current_label']}",
        f"- page_count: {summary['page_count']}",
        f"- struck_word_count: {summary['struck_word_count']}",
        f"- struck_word_ratio: {summary['struck_word_ratio']}",
        f"- lines_with_struck_words: {summary['lines_with_struck_words']}",
        f"- strike_source_counts: {summary['strike_source_counts']}",
        "",
        "## Top Struck Pages",
        "",
    ]
    pages = sorted(report["page_strike_profile"]["pages"], key=lambda row: (-row["struck_word_count"], row["page"]))
    for row in pages[:8]:
        lines.append(
            f"- page {row['page']}: struck_words={row['struck_word_count']}/{row['word_count']} "
            f"ratio={row['struck_word_ratio']} lines_with_struck={row['lines_with_struck_words']} "
            f"sources={row['strike_source_counts']}"
        )
    lines.extend(["", "## Selected Clause Cases", ""])
    for case in report["selected_clause_cases"][:10]:
        lines.append(f"### {case['clause_id']} page {case['page_span']}")
        lines.append(f"- why_selected: {', '.join(case['why_selected'])}")
        lines.append(
            f"- pipeline_decision: {case['final_decision']['pipeline_decision']} "
            f"(residual_recommendation={case['final_decision']['residual_filter_recommendation']}, "
            f"reason={case['final_decision']['reason']})"
        )
        lines.append(f"- original: `{(case.get('original_text') or {}).get('preview', '-')}`")
        lines.append(f"- residual: `{(case.get('residual') or {}).get('preview', '-')}`")
        lines.append(f"- strike_sources: {case['strike_marking']['strike_source_counts']}")
        lines.append(f"- evidence_modes: {case['strike_marking']['evidence_modes']}")
        for line_row in case["strike_marking"]["line_rows"][:3]:
            lines.append(
                f"  line {line_row['line_id']}: raw=`{line_row['raw_text']}` "
                f"marked=`{line_row['marked_text']}` live=`{line_row['live_text']}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_bad_clause_review(
    *,
    strike_diagnostics: dict,
    assembly_report: dict,
    current_label: str,
) -> dict:
    def has_right_noise_leak(case: dict) -> bool:
        outputs = [
            ((case.get("current_output") or {}).get("text") or ""),
            ((case.get("residual_output") or {}).get("residual_body") or ""),
        ]
        leak_re = re.compile(r"(?:^|\n)\d{2,3}(?=\n|$)|\b\d{2,3}$")
        return any(leak_re.search(text) for text in outputs if text)

    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def take_cases(predicate, limit: int) -> None:
        count = 0
        for case in strike_diagnostics["selected_clause_cases"]:
            key = (case["clause_id"], case["page_span"])
            if key in seen or not predicate(case):
                continue
            selected.append(case)
            seen.add(key)
            count += 1
            if count >= limit:
                break

    take_cases(lambda case: "duplicate_id" in case["why_selected"], 5)
    take_cases(lambda case: "extra_clause" in case["why_selected"], 4)
    take_cases(lambda case: "missing_clause_vs_golden" in case["why_selected"], 3)

    cases: list[dict] = []
    for rank, case in enumerate(selected[:12], start=1):
        residual = case.get("residual")
        strike_sources = case["strike_marking"]["strike_source_counts"]
        if case["clause_id"] in EXPECTED_TITLE_ONLY_SUPPRESS_IDS:
            diagnostic_class = "expected_title_only_suppression"
        elif case["clause_id"] in STRIKE_EVIDENCE_GAP_IDS:
            diagnostic_class = "strike_evidence_gap"
        elif case["clause_id"] in MISSING_LIVE_CLAUSE_IDS:
            diagnostic_class = "missing_live_clause"
        elif has_right_noise_leak(case):
            diagnostic_class = "right_noise_leak"
        elif residual and residual["recommendation"] == "suppress" and strike_sources:
            diagnostic_class = "true_strike_suppress_candidate"
        elif strike_sources and "high_strike_ratio" in case["why_selected"]:
            diagnostic_class = "mostly_struck_but_keep_residual"
        else:
            diagnostic_class = "structural_boundary_failure"
        cases.append(
            {
                "rank": rank,
                "clause_id": case["clause_id"],
                "page_span": case["page_span"],
                "why_selected": case["why_selected"],
                "diagnostic_class": diagnostic_class,
                "reference": case["reference"],
                "deterministic_output": case["deterministic_output"],
                "current_output": case["current_output"],
                "residual_output": residual,
                "strike_marking": {
                    "strike_source_counts": strike_sources,
                    "evidence_modes": case["strike_marking"]["evidence_modes"],
                },
                "short_diagnosis": ", ".join(case["why_selected"]),
            }
        )
    grouped = {
        "orphaned_residual_cases": [case["clause_id"] for case in cases if case["diagnostic_class"] in {"mostly_struck_but_keep_residual", "right_noise_leak"}],
        "strike_evidence_gap_cases": [case["clause_id"] for case in cases if case["diagnostic_class"] == "strike_evidence_gap"],
        "missing_live_clause_cases": [case["clause_id"] for case in cases if case["diagnostic_class"] == "missing_live_clause"],
    }
    return {
        "current_label": current_label,
        "boundary_alignment_proxy_vs_reference": assembly_report["metrics"]["boundary_alignment_proxy_vs_reference"],
        "split_merge_error_proxy": assembly_report["metrics"]["split_merge_error_proxy"],
        "grouped_case_sections": grouped,
        "cases": cases,
    }


def _bad_clause_review_markdown(report: dict) -> str:
    lines = [
        "# Bad Clause Review",
        "",
        f"- current_label: {report['current_label']}",
        f"- boundary_alignment_proxy_vs_reference: {report['boundary_alignment_proxy_vs_reference']}",
        f"- split_merge_error_proxy: {report['split_merge_error_proxy']}",
        "",
        "## Case Sections",
        "",
        f"- orphaned_residual_cases: {report.get('grouped_case_sections', {}).get('orphaned_residual_cases', [])}",
        f"- strike_evidence_gap_cases: {report.get('grouped_case_sections', {}).get('strike_evidence_gap_cases', [])}",
        f"- missing_live_clause_cases: {report.get('grouped_case_sections', {}).get('missing_live_clause_cases', [])}",
        "",
    ]
    for case in report["cases"]:
        lines.extend(
            [
                f"## {case['rank']}. {case['clause_id']} page {case['page_span']}",
                "",
                f"- why_selected: {', '.join(case['why_selected'])}",
                f"- diagnostic_class: {case['diagnostic_class']}",
                f"- strike_sources: {case['strike_marking']['strike_source_counts']}",
                f"- evidence_modes: {case['strike_marking']['evidence_modes']}",
                "",
                "Reference",
                f"`{normalize_ws(((case['reference'] or {}).get('title', '') + ' ' + (case['reference'] or {}).get('text', '')) )[:500] or '-'}`",
                "",
                "Deterministic M2",
                f"`{normalize_ws(((case['deterministic_output'] or {}).get('title', '') + ' ' + (case['deterministic_output'] or {}).get('text', '')) )[:500] or '-'}`",
                "",
                f"{report['current_label']}",
                f"`{normalize_ws(((case['current_output'] or {}).get('title', '') + ' ' + (case['current_output'] or {}).get('text', '')) )[:500] or '-'}`",
                "",
                "Residual Cleaned",
                f"`{normalize_ws((((case['residual_output'] or {}).get('residual_title', '') + ' ' + (case['residual_output'] or {}).get('residual_body', '')) ) )[:500] or '-'}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _build_candidate_report(run_id: str, diagnostics: dict, blocks: list, legacy_count: int, reference_count: int) -> dict:
    metrics = dict(diagnostics["metrics"])
    metrics["blocks_by_section"] = dict(sorted(Counter(block.section_hint for block in blocks).items()))
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
        "section_distribution": _section_distribution(clauses, "section"),
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


def _build_adjudication_report(
    run_id: str,
    cases: list,
    results: list,
    deterministic_report: dict,
    adjudicated_report: dict,
) -> dict:
    metrics = adjudication_metrics(results)
    metrics.update(
        {
            "clause_count_before": deterministic_report["metrics"]["unified_clause_count"],
            "clause_count_after": adjudicated_report["metrics"]["unified_clause_count"],
            "duplicate_id_count_before": len(deterministic_report["metrics"]["duplicate_ids"]),
            "duplicate_id_count_after": len(adjudicated_report["metrics"]["duplicate_ids"]),
            "split_merge_error_proxy_before": deterministic_report["metrics"]["split_merge_error_proxy"],
            "split_merge_error_proxy_after": adjudicated_report["metrics"]["split_merge_error_proxy"],
        }
    )
    examples = []
    results_by_case = {row.case_id: row for row in results}
    for case in cases[:10]:
        result = results_by_case.get(case.case_id)
        examples.append(
            {
                "case_id": case.case_id,
                "bucket": case.bucket,
                "page": case.page,
                "status": result.status if result else "missing",
                "effect": result.effect if result else "kept_deterministic",
                "before": case.candidate_clause_id or case.section_hint,
                "after": (result.decision.section_hint if result and result.decision else case.section_hint),
                "reason_short": result.decision.reason_short if result and result.decision else "",
            }
        )
    report = {
        "run_id": run_id,
        "mode": "structured_adjudication_report",
        "metrics": metrics,
        "examples": examples,
        "notes": [
            "Only ambiguous nested-numbering and false-banner cases are sent to GPT-5.4.",
            "Clause text remains assembled deterministically from source lines after any accepted line-id decision.",
        ],
    }
    assert_json_data_valid(report, "adjudication_report.schema.json", label="adjudication_report.json")
    return report


def _build_adjudicated_assembly_report(
    run_id: str,
    clauses: list[DraftClause],
    assembly_diag: dict,
    legacy: list[Clause],
    reference: list[Clause],
    blocks: list,
    deterministic_clauses: list[DraftClause],
    deterministic_report: dict,
) -> dict:
    report = _build_assembly_report(run_id, clauses, assembly_diag, legacy, reference, blocks)
    report["mode"] = "unified_assembly_report_adjudicated"
    report["comparisons"]["vs_m2_deterministic"] = compare_clause_sets(clauses, deterministic_clauses)
    report["metrics"]["m2_deterministic_clause_count"] = deterministic_report["metrics"]["unified_clause_count"]
    report["metrics"]["m2_deterministic_split_merge_error_proxy"] = deterministic_report["metrics"]["split_merge_error_proxy"]
    report["metrics"]["m2_deterministic_duplicate_id_count"] = len(deterministic_report["metrics"]["duplicate_ids"])
    report["metrics"]["split_merge_error_proxy_delta_vs_m2"] = (
        report["metrics"]["split_merge_error_proxy"] - deterministic_report["metrics"]["split_merge_error_proxy"]
    )
    report["metrics"]["duplicate_id_count_delta_vs_m2"] = (
        len(report["metrics"]["duplicate_ids"]) - len(deterministic_report["metrics"]["duplicate_ids"])
    )
    report["notes"].append("Comparisons include before-vs-after deltas against the M2 deterministic-only unified output.")
    assert_json_data_valid(report, "assembly_report.schema.json", label="assembly_report_adjudicated.json")
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
    clauses = _apply_residual_cleanup(clauses, pages=pages, pdf_path=pdf_path, settings=settings)
    assembly_diag = _refresh_assembly_diag(clauses, assembly_diag)
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

    stage_metrics = _build_stage_metrics(
        probe_report=probe_report,
        candidate_report=candidate_report,
        deterministic_assembly_report=assembly_report,
        ambiguity_cases=[],
        adjudication_results=[],
        final_assembly_report=assembly_report,
        final_clauses=clauses,
    )
    stage_metrics_path = runs_dir / "stage_metrics.json"
    stage_metrics_archived_path = run_dir / "stage_metrics.json"
    _write_json_dual(stage_metrics_path, stage_metrics_archived_path, stage_metrics)
    stage_metrics_md_path = runs_dir / "stage_metrics.md"
    stage_metrics_md_archived_path = run_dir / "stage_metrics.md"
    _write_md_dual(stage_metrics_md_path, stage_metrics_md_archived_path, _stage_metrics_markdown(stage_metrics))

    worst_candidate_blocks = _build_worst_candidate_blocks(blocks)
    worst_candidate_blocks_path = runs_dir / "worst_candidate_blocks.json"
    worst_candidate_blocks_archived_path = run_dir / "worst_candidate_blocks.json"
    _write_json_dual(worst_candidate_blocks_path, worst_candidate_blocks_archived_path, worst_candidate_blocks)
    worst_candidate_blocks_md_path = runs_dir / "worst_candidate_blocks.md"
    worst_candidate_blocks_md_archived_path = run_dir / "worst_candidate_blocks.md"
    _write_md_dual(
        worst_candidate_blocks_md_path,
        worst_candidate_blocks_md_archived_path,
        _worst_items_markdown(
            "Worst Candidate Blocks",
            worst_candidate_blocks,
            ["candidate_id", "page", "routing_mode", "bucket_or_type", "inferred_clause_id", "why_suspicious"],
        ),
    )

    worst_clauses = _clause_diagnostic_rows(clauses, reference_clauses, assembly_report)
    worst_clauses_path = runs_dir / "worst_clauses.json"
    worst_clauses_archived_path = run_dir / "worst_clauses.json"
    _write_json_dual(worst_clauses_path, worst_clauses_archived_path, worst_clauses)
    worst_clauses_md_path = runs_dir / "worst_clauses.md"
    worst_clauses_md_archived_path = run_dir / "worst_clauses.md"
    _write_md_dual(
        worst_clauses_md_path,
        worst_clauses_md_archived_path,
        _worst_items_markdown(
            "Worst Clauses",
            worst_clauses,
            ["clause_id", "page_span", "mismatch_summary", "overlap_similarity_scores", "section_mismatch"],
        ),
    )

    worst_adjudication_cases: list[dict] = []
    worst_adjudication_cases_path = runs_dir / "worst_adjudication_cases.json"
    worst_adjudication_cases_archived_path = run_dir / "worst_adjudication_cases.json"
    _write_json_dual(worst_adjudication_cases_path, worst_adjudication_cases_archived_path, worst_adjudication_cases)
    worst_adjudication_cases_md_path = runs_dir / "worst_adjudication_cases.md"
    worst_adjudication_cases_md_archived_path = run_dir / "worst_adjudication_cases.md"
    _write_md_dual(
        worst_adjudication_cases_md_path,
        worst_adjudication_cases_md_archived_path,
        _worst_items_markdown("Worst Adjudication Cases", worst_adjudication_cases, ["case_id"]),
    )

    diagnostic_bundle = _diagnostic_bundle(
        stage_metrics=stage_metrics,
        worst_candidate_blocks=worst_candidate_blocks,
        worst_clauses=worst_clauses,
        worst_adjudication_cases=worst_adjudication_cases,
    )
    diagnostic_bundle_path = runs_dir / "diagnostic_bundle.json"
    diagnostic_bundle_archived_path = run_dir / "diagnostic_bundle.json"
    _write_json_dual(diagnostic_bundle_path, diagnostic_bundle_archived_path, diagnostic_bundle)
    diagnostic_bundle_md_path = runs_dir / "diagnostic_bundle.md"
    diagnostic_bundle_md_archived_path = run_dir / "diagnostic_bundle.md"
    _write_md_dual(
        diagnostic_bundle_md_path,
        diagnostic_bundle_md_archived_path,
        _diagnostic_bundle_markdown(diagnostic_bundle),
    )

    strike_stage_diagnostics = _build_strike_stage_diagnostics(
        pdf_path=pdf_path,
        pages=pages,
        current_clauses=clauses,
        reference_clauses=reference_clauses,
        assembly_report=assembly_report,
        current_label="Deterministic Unified",
        deterministic_clauses=clauses,
        adjudicated_available=False,
        settings=settings,
    )
    strike_stage_diagnostics_path = runs_dir / "strike_stage_diagnostics.json"
    strike_stage_diagnostics_archived_path = run_dir / "strike_stage_diagnostics.json"
    _write_json_dual(strike_stage_diagnostics_path, strike_stage_diagnostics_archived_path, strike_stage_diagnostics)
    strike_stage_diagnostics_md_path = runs_dir / "strike_stage_diagnostics.md"
    strike_stage_diagnostics_md_archived_path = run_dir / "strike_stage_diagnostics.md"
    _write_md_dual(
        strike_stage_diagnostics_md_path,
        strike_stage_diagnostics_md_archived_path,
        _strike_stage_diagnostics_markdown(strike_stage_diagnostics),
    )

    bad_clause_review = _build_bad_clause_review(
        strike_diagnostics=strike_stage_diagnostics,
        assembly_report=assembly_report,
        current_label="Deterministic Unified",
    )
    bad_clause_review_path = runs_dir / "bad_clause_review.json"
    bad_clause_review_archived_path = run_dir / "bad_clause_review.json"
    _write_json_dual(bad_clause_review_path, bad_clause_review_archived_path, bad_clause_review)
    bad_clause_review_md_path = runs_dir / "bad_clause_review.md"
    bad_clause_review_md_archived_path = run_dir / "bad_clause_review.md"
    _write_md_dual(
        bad_clause_review_md_path,
        bad_clause_review_md_archived_path,
        _bad_clause_review_markdown(bad_clause_review),
    )

    freshness_checks = [
        ensure_fresh_output(candidate_path, [runs_dir / "page_ir.jsonl", runs_dir / "layout_profile.json"]),
        ensure_fresh_output(clause_path, [candidate_path]),
        ensure_fresh_output(candidate_report_path, [candidate_path]),
        ensure_fresh_output(assembly_report_path, [clause_path]),
        ensure_fresh_output(stage_metrics_path, [candidate_report_path, assembly_report_path]),
        ensure_fresh_output(diagnostic_bundle_path, [stage_metrics_path, worst_candidate_blocks_path, worst_clauses_path]),
        ensure_fresh_output(strike_stage_diagnostics_path, [runs_dir / "page_ir.jsonl", clause_path, assembly_report_path]),
        ensure_fresh_output(bad_clause_review_path, [strike_stage_diagnostics_path, assembly_report_path]),
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
            "stage_metrics_json": repo_rel(stage_metrics_path),
            "stage_metrics_md": repo_rel(stage_metrics_md_path),
            "worst_candidate_blocks_json": repo_rel(worst_candidate_blocks_path),
            "worst_candidate_blocks_md": repo_rel(worst_candidate_blocks_md_path),
            "worst_clauses_json": repo_rel(worst_clauses_path),
            "worst_clauses_md": repo_rel(worst_clauses_md_path),
            "worst_adjudication_cases_json": repo_rel(worst_adjudication_cases_path),
            "worst_adjudication_cases_md": repo_rel(worst_adjudication_cases_md_path),
            "diagnostic_bundle_json": repo_rel(diagnostic_bundle_path),
            "diagnostic_bundle_md": repo_rel(diagnostic_bundle_md_path),
            "strike_stage_diagnostics_json": repo_rel(strike_stage_diagnostics_path),
            "strike_stage_diagnostics_md": repo_rel(strike_stage_diagnostics_md_path),
            "bad_clause_review_json": repo_rel(bad_clause_review_path),
            "bad_clause_review_md": repo_rel(bad_clause_review_md_path),
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
            "stage_metrics_json": fingerprint(stage_metrics_path, role="generated"),
            "diagnostic_bundle_json": fingerprint(diagnostic_bundle_path, role="generated"),
            "strike_stage_diagnostics_json": fingerprint(strike_stage_diagnostics_path, role="generated"),
            "bad_clause_review_json": fingerprint(bad_clause_review_path, role="generated"),
        },
        metrics={
            "candidate_clause_start_count": candidate_report["metrics"]["candidate_clause_start_count"],
            "unified_clause_count": assembly_report["metrics"]["unified_clause_count"],
            "reference_count_delta": assembly_report["comparisons"]["vs_reference"]["count_delta"],
            "legacy_count_delta": assembly_report["comparisons"]["vs_legacy"]["count_delta"],
            "duplicate_ids": assembly_report["metrics"]["duplicate_ids"],
            "order_violations": assembly_report["metrics"]["order_violations"],
            "banner_leaks": assembly_report["metrics"]["banner_leaks"],
            "section_distribution": stage_metrics["summary"]["section_distribution"],
            "struck_word_count": strike_stage_diagnostics["page_strike_profile"]["summary"]["struck_word_count"],
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
            "Strike-stage diagnostics and bad-clause review are emitted as report artifacts only.",
        ],
    )
    publish_run_report("unified", report)
    return clauses


def run_unified_adjudicated_draft(pdf_path: str | Path, out_path: str | Path, settings: Settings) -> list[DraftClause]:
    runs_dir = _runs_dir()
    run_id = new_run_id("unified_adjudicated")
    run_dir = _history_dir(run_id)
    started_at = utc_now_iso()

    pages, profile, probe_report = _load_fresh_probe_inputs(pdf_path, settings)
    legacy_clauses, baseline_report = _load_baseline_anchor(pdf_path)
    reference_clauses = [Clause(**row) for row in read_json(settings.project.golden_path)]

    blocks, diagnostics = generate_candidate_blocks(pages, profile, settings)
    deterministic_block_rows = [block.model_dump() for block in blocks]
    for idx, row in enumerate(deterministic_block_rows):
        assert_json_data_valid(row, "candidate_blocks.schema.json", label=f"candidate_blocks_m2[{idx}]")

    deterministic_block_path = runs_dir / "candidate_blocks_m2.jsonl"
    deterministic_block_archived_path = run_dir / "candidate_blocks_m2.jsonl"
    _write_jsonl_dual(deterministic_block_path, deterministic_block_archived_path, deterministic_block_rows)

    deterministic_clauses, deterministic_assembly_diag = assemble_draft_clauses(blocks)
    deterministic_clauses = _apply_residual_cleanup(
        deterministic_clauses,
        pages=pages,
        pdf_path=pdf_path,
        settings=settings,
    )
    deterministic_assembly_diag = _refresh_assembly_diag(deterministic_clauses, deterministic_assembly_diag)
    deterministic_clause_rows = [clause.model_dump() for clause in deterministic_clauses]
    assert_json_data_valid(deterministic_clause_rows, "clauses_unified.schema.json", label="clauses_unified_m2.json")
    deterministic_clause_path = runs_dir / "clauses_unified_m2.json"
    deterministic_clause_archived_path = run_dir / "clauses_unified_m2.json"
    _write_json_dual(deterministic_clause_path, deterministic_clause_archived_path, deterministic_clause_rows)

    deterministic_assembly_report = _build_assembly_report(
        run_id,
        deterministic_clauses,
        deterministic_assembly_diag,
        legacy_clauses,
        reference_clauses,
        blocks,
    )
    deterministic_assembly_report_path = runs_dir / "assembly_report_m2.json"
    deterministic_assembly_report_archived_path = run_dir / "assembly_report_m2.json"
    _write_json_dual(
        deterministic_assembly_report_path,
        deterministic_assembly_report_archived_path,
        deterministic_assembly_report,
    )
    deterministic_assembly_md_path = runs_dir / "assembly_report_m2.md"
    deterministic_assembly_md_archived_path = run_dir / "assembly_report_m2.md"
    _write_md_dual(
        deterministic_assembly_md_path,
        deterministic_assembly_md_archived_path,
        _assembly_markdown(deterministic_assembly_report),
    )

    ambiguity_cases = extract_ambiguity_cases(pages, blocks, settings)
    ambiguity_rows = [case.model_dump() for case in ambiguity_cases]
    for idx, row in enumerate(ambiguity_rows):
        assert_json_data_valid(row, "ambiguity_case.schema.json", label=f"ambiguity_case[{idx}]")
    ambiguity_path = runs_dir / "ambiguity_cases.jsonl"
    ambiguity_archived_path = run_dir / "ambiguity_cases.jsonl"
    _write_jsonl_dual(ambiguity_path, ambiguity_archived_path, ambiguity_rows)

    adjudication_results = run_structured_adjudication(ambiguity_cases, settings)
    adjudication_result_rows = [row.model_dump() for row in adjudication_results]
    for idx, row in enumerate(adjudication_result_rows):
        assert_json_data_valid(row, "adjudication_result.schema.json", label=f"adjudication_result[{idx}]")
    adjudication_results_path = runs_dir / "adjudication_results.jsonl"
    adjudication_results_archived_path = run_dir / "adjudication_results.jsonl"
    _write_jsonl_dual(adjudication_results_path, adjudication_results_archived_path, adjudication_result_rows)

    adjudicated_blocks, apply_diag = apply_adjudication_to_blocks(blocks, adjudication_results)
    adjudicated_block_rows = [block.model_dump() for block in adjudicated_blocks]
    for idx, row in enumerate(adjudicated_block_rows):
        assert_json_data_valid(row, "candidate_blocks.schema.json", label=f"candidate_blocks_adjudicated[{idx}]")
    adjudicated_block_path = runs_dir / "candidate_blocks_adjudicated.jsonl"
    adjudicated_block_archived_path = run_dir / "candidate_blocks_adjudicated.jsonl"
    _write_jsonl_dual(adjudicated_block_path, adjudicated_block_archived_path, adjudicated_block_rows)

    clauses, assembly_diag = assemble_draft_clauses(adjudicated_blocks)
    clauses = _apply_residual_cleanup(clauses, pages=pages, pdf_path=pdf_path, settings=settings)
    assembly_diag = _refresh_assembly_diag(clauses, assembly_diag)
    clause_rows = [clause.model_dump() for clause in clauses]
    assert_json_data_valid(clause_rows, "clauses_unified.schema.json", label="clauses_unified_adjudicated.json")
    clause_path = Path(out_path)
    clause_archived_path = run_dir / "clauses_unified_adjudicated.json"
    _write_json_dual(clause_path, clause_archived_path, clause_rows)

    adjudicated_assembly_report = _build_adjudicated_assembly_report(
        run_id,
        clauses,
        assembly_diag,
        legacy_clauses,
        reference_clauses,
        adjudicated_blocks,
        deterministic_clauses,
        deterministic_assembly_report,
    )
    adjudicated_assembly_report_path = runs_dir / "assembly_report_adjudicated.json"
    adjudicated_assembly_report_archived_path = run_dir / "assembly_report_adjudicated.json"
    _write_json_dual(
        adjudicated_assembly_report_path,
        adjudicated_assembly_report_archived_path,
        adjudicated_assembly_report,
    )
    adjudicated_assembly_md_path = runs_dir / "assembly_report_adjudicated.md"
    adjudicated_assembly_md_archived_path = run_dir / "assembly_report_adjudicated.md"
    _write_md_dual(
        adjudicated_assembly_md_path,
        adjudicated_assembly_md_archived_path,
        _assembly_markdown(adjudicated_assembly_report),
    )

    adjudication_report = _build_adjudication_report(
        run_id,
        ambiguity_cases,
        adjudication_results,
        deterministic_assembly_report,
        adjudicated_assembly_report,
    )
    adjudication_report["metrics"]["block_effects"] = apply_diag["effects"]
    adjudication_report_path = runs_dir / "adjudication_report.json"
    adjudication_report_archived_path = run_dir / "adjudication_report.json"
    _write_json_dual(adjudication_report_path, adjudication_report_archived_path, adjudication_report)
    adjudication_md_path = runs_dir / "adjudication_report.md"
    adjudication_md_archived_path = run_dir / "adjudication_report.md"
    _write_md_dual(adjudication_md_path, adjudication_md_archived_path, _adjudication_markdown(adjudication_report))

    stage_metrics = _build_stage_metrics(
        probe_report=probe_report,
        candidate_report=_build_candidate_report(run_id, diagnostics, blocks, len(legacy_clauses), len(reference_clauses)),
        deterministic_assembly_report=deterministic_assembly_report,
        ambiguity_cases=ambiguity_cases,
        adjudication_results=adjudication_results,
        final_assembly_report=adjudicated_assembly_report,
        final_clauses=clauses,
    )
    stage_metrics_path = runs_dir / "stage_metrics.json"
    stage_metrics_archived_path = run_dir / "stage_metrics.json"
    _write_json_dual(stage_metrics_path, stage_metrics_archived_path, stage_metrics)
    stage_metrics_md_path = runs_dir / "stage_metrics.md"
    stage_metrics_md_archived_path = run_dir / "stage_metrics.md"
    _write_md_dual(stage_metrics_md_path, stage_metrics_md_archived_path, _stage_metrics_markdown(stage_metrics))

    worst_candidate_blocks = _build_worst_candidate_blocks(adjudicated_blocks, ambiguity_cases=ambiguity_cases)
    worst_candidate_blocks_path = runs_dir / "worst_candidate_blocks.json"
    worst_candidate_blocks_archived_path = run_dir / "worst_candidate_blocks.json"
    _write_json_dual(worst_candidate_blocks_path, worst_candidate_blocks_archived_path, worst_candidate_blocks)
    worst_candidate_blocks_md_path = runs_dir / "worst_candidate_blocks.md"
    worst_candidate_blocks_md_archived_path = run_dir / "worst_candidate_blocks.md"
    _write_md_dual(
        worst_candidate_blocks_md_path,
        worst_candidate_blocks_md_archived_path,
        _worst_items_markdown(
            "Worst Candidate Blocks",
            worst_candidate_blocks,
            ["candidate_id", "page", "routing_mode", "bucket_or_type", "inferred_clause_id", "why_suspicious"],
        ),
    )

    worst_clauses = _clause_diagnostic_rows(clauses, reference_clauses, adjudicated_assembly_report)
    worst_clauses_path = runs_dir / "worst_clauses.json"
    worst_clauses_archived_path = run_dir / "worst_clauses.json"
    _write_json_dual(worst_clauses_path, worst_clauses_archived_path, worst_clauses)
    worst_clauses_md_path = runs_dir / "worst_clauses.md"
    worst_clauses_md_archived_path = run_dir / "worst_clauses.md"
    _write_md_dual(
        worst_clauses_md_path,
        worst_clauses_md_archived_path,
        _worst_items_markdown(
            "Worst Clauses",
            worst_clauses,
            ["clause_id", "page_span", "mismatch_summary", "overlap_similarity_scores", "section_mismatch"],
        ),
    )

    worst_adjudication_cases = _adjudication_case_diagnostic_rows(ambiguity_cases, adjudication_results)
    worst_adjudication_cases_path = runs_dir / "worst_adjudication_cases.json"
    worst_adjudication_cases_archived_path = run_dir / "worst_adjudication_cases.json"
    _write_json_dual(worst_adjudication_cases_path, worst_adjudication_cases_archived_path, worst_adjudication_cases)
    worst_adjudication_cases_md_path = runs_dir / "worst_adjudication_cases.md"
    worst_adjudication_cases_md_archived_path = run_dir / "worst_adjudication_cases.md"
    _write_md_dual(
        worst_adjudication_cases_md_path,
        worst_adjudication_cases_md_archived_path,
        _worst_items_markdown(
            "Worst Adjudication Cases",
            worst_adjudication_cases,
            ["case_id", "page", "bucket", "effect_on_final_output", "why_it_still_looks_wrong_or_improved"],
        ),
    )

    diagnostic_bundle = _diagnostic_bundle(
        stage_metrics=stage_metrics,
        worst_candidate_blocks=worst_candidate_blocks,
        worst_clauses=worst_clauses,
        worst_adjudication_cases=worst_adjudication_cases,
    )
    diagnostic_bundle_path = runs_dir / "diagnostic_bundle.json"
    diagnostic_bundle_archived_path = run_dir / "diagnostic_bundle.json"
    _write_json_dual(diagnostic_bundle_path, diagnostic_bundle_archived_path, diagnostic_bundle)
    diagnostic_bundle_md_path = runs_dir / "diagnostic_bundle.md"
    diagnostic_bundle_md_archived_path = run_dir / "diagnostic_bundle.md"
    _write_md_dual(
        diagnostic_bundle_md_path,
        diagnostic_bundle_md_archived_path,
        _diagnostic_bundle_markdown(diagnostic_bundle),
    )

    clause_review_examples = _build_clause_review_examples(
        worst_clauses=worst_clauses,
        worst_adjudication_cases=worst_adjudication_cases,
        deterministic_clauses=deterministic_clauses,
        adjudicated_clauses=clauses,
        reference_clauses=reference_clauses,
    )
    clause_review_examples_path = runs_dir / "clause_review_examples.json"
    clause_review_examples_archived_path = run_dir / "clause_review_examples.json"
    _write_json_dual(clause_review_examples_path, clause_review_examples_archived_path, clause_review_examples)
    clause_review_examples_md_path = runs_dir / "clause_review_examples.md"
    clause_review_examples_md_archived_path = run_dir / "clause_review_examples.md"
    _write_md_dual(
        clause_review_examples_md_path,
        clause_review_examples_md_archived_path,
        _clause_review_examples_markdown(clause_review_examples),
    )

    strike_stage_diagnostics = _build_strike_stage_diagnostics(
        pdf_path=pdf_path,
        pages=pages,
        current_clauses=clauses,
        reference_clauses=reference_clauses,
        assembly_report=adjudicated_assembly_report,
        current_label="Adjudicated M3",
        deterministic_clauses=deterministic_clauses,
        adjudicated_available=True,
        settings=settings,
    )
    strike_stage_diagnostics_path = runs_dir / "strike_stage_diagnostics.json"
    strike_stage_diagnostics_archived_path = run_dir / "strike_stage_diagnostics.json"
    _write_json_dual(strike_stage_diagnostics_path, strike_stage_diagnostics_archived_path, strike_stage_diagnostics)
    strike_stage_diagnostics_md_path = runs_dir / "strike_stage_diagnostics.md"
    strike_stage_diagnostics_md_archived_path = run_dir / "strike_stage_diagnostics.md"
    _write_md_dual(
        strike_stage_diagnostics_md_path,
        strike_stage_diagnostics_md_archived_path,
        _strike_stage_diagnostics_markdown(strike_stage_diagnostics),
    )

    bad_clause_review = _build_bad_clause_review(
        strike_diagnostics=strike_stage_diagnostics,
        assembly_report=adjudicated_assembly_report,
        current_label="Adjudicated M3",
    )
    bad_clause_review_path = runs_dir / "bad_clause_review.json"
    bad_clause_review_archived_path = run_dir / "bad_clause_review.json"
    _write_json_dual(bad_clause_review_path, bad_clause_review_archived_path, bad_clause_review)
    bad_clause_review_md_path = runs_dir / "bad_clause_review.md"
    bad_clause_review_md_archived_path = run_dir / "bad_clause_review.md"
    _write_md_dual(
        bad_clause_review_md_path,
        bad_clause_review_md_archived_path,
        _bad_clause_review_markdown(bad_clause_review),
    )

    freshness_checks = [
        ensure_fresh_output(deterministic_block_path, [runs_dir / "page_ir.jsonl", runs_dir / "layout_profile.json"]),
        ensure_fresh_output(adjudicated_block_path, [deterministic_block_path, ambiguity_path, adjudication_results_path]),
        ensure_fresh_output(clause_path, [adjudicated_block_path]),
        ensure_fresh_output(adjudication_report_path, [ambiguity_path, adjudication_results_path, clause_path]),
        ensure_fresh_output(adjudicated_assembly_report_path, [clause_path]),
        ensure_fresh_output(stage_metrics_path, [adjudication_report_path, adjudicated_assembly_report_path]),
        ensure_fresh_output(
            diagnostic_bundle_path,
            [stage_metrics_path, worst_candidate_blocks_path, worst_clauses_path, worst_adjudication_cases_path],
        ),
        ensure_fresh_output(clause_review_examples_path, [diagnostic_bundle_path]),
        ensure_fresh_output(strike_stage_diagnostics_path, [runs_dir / "page_ir.jsonl", clause_path, adjudicated_assembly_report_path]),
        ensure_fresh_output(bad_clause_review_path, [strike_stage_diagnostics_path, adjudicated_assembly_report_path]),
    ]
    finished_at = utc_now_iso()
    report = RunReport(
        run_id=run_id,
        mode="unified_adjudicated",
        command=_unified_adjudicated_command(pdf_path),
        started_at=started_at,
        finished_at=finished_at,
        pdf_path=str(pdf_path),
        artifacts={
            "candidate_blocks_m2": repo_rel(deterministic_block_path),
            "clauses_unified_m2": repo_rel(deterministic_clause_path),
            "assembly_report_m2_json": repo_rel(deterministic_assembly_report_path),
            "ambiguity_cases": repo_rel(ambiguity_path),
            "adjudication_results": repo_rel(adjudication_results_path),
            "candidate_blocks_adjudicated": repo_rel(adjudicated_block_path),
            "clauses_unified_adjudicated": repo_rel(clause_path),
            "adjudication_report_json": repo_rel(adjudication_report_path),
            "adjudication_report_md": repo_rel(adjudication_md_path),
            "assembly_report_adjudicated_json": repo_rel(adjudicated_assembly_report_path),
            "assembly_report_adjudicated_md": repo_rel(adjudicated_assembly_md_path),
            "stage_metrics_json": repo_rel(stage_metrics_path),
            "stage_metrics_md": repo_rel(stage_metrics_md_path),
            "worst_candidate_blocks_json": repo_rel(worst_candidate_blocks_path),
            "worst_candidate_blocks_md": repo_rel(worst_candidate_blocks_md_path),
            "worst_clauses_json": repo_rel(worst_clauses_path),
            "worst_clauses_md": repo_rel(worst_clauses_md_path),
            "worst_adjudication_cases_json": repo_rel(worst_adjudication_cases_path),
            "worst_adjudication_cases_md": repo_rel(worst_adjudication_cases_md_path),
            "diagnostic_bundle_json": repo_rel(diagnostic_bundle_path),
            "diagnostic_bundle_md": repo_rel(diagnostic_bundle_md_path),
            "clause_review_examples_json": repo_rel(clause_review_examples_path),
            "clause_review_examples_md": repo_rel(clause_review_examples_md_path),
            "strike_stage_diagnostics_json": repo_rel(strike_stage_diagnostics_path),
            "strike_stage_diagnostics_md": repo_rel(strike_stage_diagnostics_md_path),
            "bad_clause_review_json": repo_rel(bad_clause_review_path),
            "bad_clause_review_md": repo_rel(bad_clause_review_md_path),
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
            "candidate_blocks_m2": fingerprint(deterministic_block_path, role="generated"),
            "ambiguity_cases": fingerprint(ambiguity_path, role="generated"),
            "adjudication_results": fingerprint(adjudication_results_path, role="generated"),
            "candidate_blocks_adjudicated": fingerprint(adjudicated_block_path, role="generated"),
            "clauses_unified_adjudicated": fingerprint(clause_path, role="generated"),
            "adjudication_report_json": fingerprint(adjudication_report_path, role="generated"),
            "assembly_report_adjudicated_json": fingerprint(adjudicated_assembly_report_path, role="generated"),
            "stage_metrics_json": fingerprint(stage_metrics_path, role="generated"),
            "diagnostic_bundle_json": fingerprint(diagnostic_bundle_path, role="generated"),
            "clause_review_examples_json": fingerprint(clause_review_examples_path, role="generated"),
            "strike_stage_diagnostics_json": fingerprint(strike_stage_diagnostics_path, role="generated"),
            "bad_clause_review_json": fingerprint(bad_clause_review_path, role="generated"),
        },
        metrics={
            "ambiguity_case_count": adjudication_report["metrics"]["ambiguity_case_count"],
            "applied_case_count": adjudication_report["metrics"]["applied_case_count"],
            "unified_clause_count_after": adjudicated_assembly_report["metrics"]["unified_clause_count"],
            "split_merge_error_proxy_delta_vs_m2": adjudicated_assembly_report["metrics"]["split_merge_error_proxy_delta_vs_m2"],
            "duplicate_id_count_delta_vs_m2": adjudicated_assembly_report["metrics"]["duplicate_id_count_delta_vs_m2"],
            "section_distribution": stage_metrics["summary"]["section_distribution"],
            "struck_word_count": strike_stage_diagnostics["page_strike_profile"]["summary"]["struck_word_count"],
        },
        freshness={
            "status": "fresh",
            "checks": freshness_checks,
            "consumed_probe_run_id": probe_report["run_id"],
            "consumed_baseline_run_id": baseline_report["run_id"],
        },
        notes=[
            "The adjudicated run reuses the M2 deterministic unified path and only calls GPT-5.4 for selective ambiguity cases.",
            "Both deterministic-only and adjudicated artifacts are emitted for direct before-vs-after comparison.",
            "Strike-stage diagnostics and bad-clause review are emitted as report artifacts only.",
        ],
    )
    publish_run_report("unified_adjudicated", report)
    return clauses


def _strike_fallback_command(
    pdf_path: str | Path,
    source_json: str | Path,
    source_assembly_report: str | Path,
    source_bad_clause_review: str | Path,
    source_strike_diagnostics: str | Path,
) -> str:
    return (
        "python -m charter_parser.cli strike-fallback "
        f"--pdf {pdf_path} "
        f"--source-json {source_json} "
        f"--source-assembly-report {source_assembly_report} "
        f"--source-bad-clause-review {source_bad_clause_review} "
        f"--source-strike-diagnostics {source_strike_diagnostics} "
        "--config configs/default.yaml"
    )


def _strike_fallback_shortlist(
    clauses: list[DraftClause],
    *,
    assembly_report: dict,
    bad_clause_review: dict,
    strike_diagnostics: dict,
) -> dict[str, list[str]]:
    live_ids = {clause.id for clause in clauses}
    reasons: dict[str, set[str]] = {}

    def add_reason(clause_id: str, reason: str) -> None:
        if clause_id not in live_ids:
            return
        reasons.setdefault(clause_id, set()).add(reason)

    for clause_id in assembly_report["comparisons"]["vs_reference"]["extra_ids"]:
        add_reason(clause_id, "survives_without_reference_match")

    for case in bad_clause_review.get("cases", []):
        clause_id = case.get("clause_id")
        if not clause_id:
            continue
        diagnostic_class = case.get("diagnostic_class")
        if diagnostic_class == "strike_evidence_gap":
            add_reason(clause_id, "bad_clause_review")
            add_reason(clause_id, "strike_evidence_gap")
        if diagnostic_class in {"true_strike_suppress_candidate", "mostly_struck_but_keep_residual"}:
            add_reason(clause_id, "bad_clause_review")
        if "extra_clause" in case.get("why_selected", []):
            add_reason(clause_id, "extra_clause_shortlist")

    for case in strike_diagnostics.get("selected_clause_cases", []):
        clause_id = case.get("clause_id")
        if not clause_id:
            continue
        if "extra_clause" in case.get("why_selected", []):
            add_reason(clause_id, "strike_diagnostics_extra_clause")
        residual = case.get("residual") or {}
        if residual.get("recommendation") == "suppress" and "extra_clause" in case.get("why_selected", []):
            add_reason(clause_id, "ambiguous_residual_validity")

    return {clause_id: sorted(reason_set) for clause_id, reason_set in sorted(reasons.items())}


def _average_similarity(candidate_title: str, candidate_text: str, reference: Clause | None) -> float | None:
    if reference is None:
        return None
    return round((_text_similarity(candidate_title, reference.title) + _text_similarity(candidate_text, reference.text)) / 2.0, 4)


def _fallback_case_diagnosis(
    *,
    clause_id: str,
    decision: str,
    reference: Clause | None,
    current_score: float | None,
    fallback_score: float | None,
) -> str:
    if reference is None:
        if decision == "suppress_clause":
            return "improved"
        if decision == "use_fallback_cleaned":
            return "still_ambiguous"
        return "unchanged"
    if decision == "suppress_clause":
        return "worsened"
    if fallback_score is None or current_score is None:
        return "still_ambiguous"
    if fallback_score > current_score + 0.01:
        return "improved"
    if fallback_score < current_score - 0.01:
        return "worsened"
    return "unchanged"


def _strike_fallback_review_markdown(report: dict) -> str:
    lines = [
        "# Strike Fallback Review",
        "",
        "## Summary",
        "",
        f"- reviewed_case_count: {report['summary']['reviewed_case_count']}",
        f"- decision_counts: {report['summary']['decision_counts']}",
        f"- touched_clause_ids: {report['summary']['touched_clause_ids']}",
        "",
    ]
    for case in report["cases"]:
        lines.extend(
            [
                f"## {case['clause_id']}",
                "",
                f"- why_sent_to_fallback: {case['why_sent_to_fallback']}",
                f"- final_decision: {case['final_decision']}",
                f"- diagnosis: {case['diagnosis']}",
                "",
                "Current accepted output",
                f"- title: {_preview(case['current_output']['title'], 160)}",
                f"- text: {_preview(case['current_output']['text'], 240)}",
                "",
                "Fallback cleaned output",
                f"- title: {_preview(case['fallback_cleaned']['title'], 160)}",
                f"- text: {_preview(case['fallback_cleaned']['text'], 240)}",
                f"- recommendation: {case['fallback_cleaned']['recommendation']}",
                f"- reason: {case['fallback_cleaned']['reason']}",
                "",
                "Reference preview",
                f"- present: {case['reference_preview'] is not None}",
            ]
        )
        if case["reference_preview"] is not None:
            lines.append(f"- title: {_preview(case['reference_preview']['title'], 160)}")
            lines.append(f"- text: {_preview(case['reference_preview']['text'], 240)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_strike_fallback_review(
    pdf_path: str | Path,
    *,
    source_json: str | Path,
    source_assembly_report: str | Path,
    source_bad_clause_review: str | Path,
    source_strike_diagnostics: str | Path,
    settings: Settings,
) -> list[DraftClause]:
    runs_dir = _runs_dir()
    run_id = new_run_id("strike_fallback")
    run_dir = _history_dir(run_id)
    started_at = utc_now_iso()

    pages, _profile, probe_report = _load_fresh_probe_inputs(pdf_path, settings)
    reference_clauses = [Clause(**row) for row in read_json(settings.project.golden_path)]
    reference_by_id = {clause.id: clause for clause in reference_clauses}

    source_clauses = [DraftClause(**row) for row in read_json(source_json)]
    source_assembly = read_json(source_assembly_report)
    source_bad_clause_review_payload = read_json(source_bad_clause_review)
    source_strike_diagnostics_payload = read_json(source_strike_diagnostics)

    shortlist = _strike_fallback_shortlist(
        source_clauses,
        assembly_report=source_assembly,
        bad_clause_review=source_bad_clause_review_payload,
        strike_diagnostics=source_strike_diagnostics_payload,
    )

    line_by_id = _line_lookup_from_pages(pages)
    word_by_id = _word_lookup_from_pages(pages)
    page_strike_segments = _page_strike_segments(str(pdf_path), {page.page_index for page in pages})
    source_extra_ids = set(source_assembly["comparisons"]["vs_reference"]["extra_ids"])

    updated_clauses: list[DraftClause] = []
    review_cases: list[dict] = []
    decision_counts: Counter = Counter()

    for clause in source_clauses:
        reasons = shortlist.get(clause.id)
        if not reasons:
            updated_clauses.append(clause)
            continue

        raw_title = _raw_text_from_line_ids(clause.title_line_ids, line_by_id)
        raw_body = _raw_text_from_line_ids(clause.body_line_ids, line_by_id)
        fallback_title, fallback_body, line_rows = _reconstruct_clause_residual(
            clause,
            line_by_id=line_by_id,
            word_by_id=word_by_id,
            page_strike_segments=page_strike_segments,
            settings=settings,
        )
        fallback_residual = _residual_recommendation(
            raw_title,
            raw_body,
            fallback_title,
            fallback_body,
            section=clause.section,
        )
        reference = reference_by_id.get(clause.id)
        current_score = _average_similarity(clause.title, clause.text, reference)
        fallback_score = _average_similarity(fallback_title, fallback_body, reference)
        high_strike = any(float(row.get("line_strike_coverage", 0.0)) >= settings.strike.full_line_coverage for row in line_rows)

        decision = "keep_current"
        if fallback_residual["recommendation"] == "suppress" and (clause.id in source_extra_ids or high_strike):
            decision = "suppress_clause"
        elif fallback_residual["recommendation"] == "keep" and (fallback_title != clause.title or fallback_body != clause.text):
            if reference is None:
                decision = "use_fallback_cleaned"
            elif fallback_score is not None and current_score is not None and fallback_score > current_score + 0.01:
                decision = "use_fallback_cleaned"

        diagnosis = _fallback_case_diagnosis(
            clause_id=clause.id,
            decision=decision,
            reference=reference,
            current_score=current_score,
            fallback_score=fallback_score,
        )
        decision_counts.update([decision])

        review_cases.append(
            {
                "clause_id": clause.id,
                "why_sent_to_fallback": reasons,
                "current_output": clause.model_dump(),
                "fallback_cleaned": {
                    "title": fallback_title,
                    "text": fallback_body,
                    "recommendation": fallback_residual["recommendation"],
                    "reason": fallback_residual["reason"],
                },
                "fallback_line_rows": line_rows,
                "final_decision": decision,
                "reference_preview": reference.model_dump() if reference is not None else None,
                "diagnosis": diagnosis,
            }
        )

        if decision == "suppress_clause":
            continue
        if decision == "use_fallback_cleaned":
            updated_clauses.append(
                clause.model_copy(
                    update={
                        "title": fallback_title,
                        "text": fallback_body,
                    }
                )
            )
            continue
        updated_clauses.append(clause)

    for index, clause in enumerate(updated_clauses, start=1):
        clause.order = index

    clause_rows = [clause.model_dump() for clause in updated_clauses]
    assert_json_data_valid(clause_rows, "clauses_unified.schema.json", label="clauses_strike_fallback.json")
    clause_path = runs_dir / "clauses_strike_fallback.json"
    clause_archived_path = run_dir / "clauses_strike_fallback.json"
    _write_json_dual(clause_path, clause_archived_path, clause_rows)

    vs_reference = compare_clause_sets(updated_clauses, reference_clauses)
    vs_source = compare_clause_sets(updated_clauses, source_clauses)
    metrics = {
        "unified_clause_count": len(updated_clauses),
        "legacy_clause_count": len(source_clauses),
        "reference_clause_count": len(reference_clauses),
        "duplicate_ids": duplicate_ids(updated_clauses),
        "order_violations": order_violations(updated_clauses),
        "banner_leaks": banner_leaks(updated_clauses),
        "empty_text_ids": empty_text_ids(updated_clauses),
        "boundary_alignment_proxy_vs_reference": vs_reference["id_sequence_match_ratio"],
        "body_text_overlap_proxy_vs_reference": vs_reference["text_similarity_mean"],
        "normalized_title_similarity_vs_reference": vs_reference["title_similarity_mean"],
        "split_merge_error_proxy": len(vs_reference["missing_ids"]) + len(vs_reference["extra_ids"]),
        "section_distribution": _section_distribution(updated_clauses, "section"),
        "reviewed_case_count": len(review_cases),
        "decision_counts": dict(sorted(decision_counts.items())),
    }
    assembly_report = {
        "run_id": run_id,
        "mode": "strike_fallback_assembly_report",
        "metrics": metrics,
        "comparisons": {
            "vs_reference": vs_reference,
            "vs_legacy": vs_source,
        },
        "failures": worst_mismatches(updated_clauses, reference_clauses),
        "notes": [
            "Strike fallback is applied only to shortlisted suspicious surviving clauses.",
            "The accepted parser output is preserved for all non-shortlisted clauses.",
        ],
    }
    assembly_report_path = runs_dir / "assembly_report_strike_fallback.json"
    assembly_report_archived_path = run_dir / "assembly_report_strike_fallback.json"
    _write_json_dual(assembly_report_path, assembly_report_archived_path, assembly_report)
    assembly_md_path = runs_dir / "assembly_report_strike_fallback.md"
    assembly_md_archived_path = run_dir / "assembly_report_strike_fallback.md"
    _write_md_dual(assembly_md_path, assembly_md_archived_path, _assembly_markdown(assembly_report))

    review_payload = {
        "run_id": run_id,
        "source_clause_count": len(source_clauses),
        "summary": {
            "reviewed_case_count": len(review_cases),
            "decision_counts": dict(sorted(decision_counts.items())),
            "touched_clause_ids": sorted(case["clause_id"] for case in review_cases if case["final_decision"] != "keep_current"),
        },
        "cases": review_cases,
    }
    review_path = runs_dir / "strike_fallback_review.json"
    review_archived_path = run_dir / "strike_fallback_review.json"
    _write_json_dual(review_path, review_archived_path, review_payload)
    review_md_path = runs_dir / "strike_fallback_review.md"
    review_archived_md_path = run_dir / "strike_fallback_review.md"
    _write_md_dual(review_md_path, review_archived_md_path, _strike_fallback_review_markdown(review_payload))

    freshness_checks = [
        ensure_fresh_output(clause_path, [source_json, runs_dir / "page_ir.jsonl"]),
        ensure_fresh_output(assembly_report_path, [clause_path]),
        ensure_fresh_output(review_path, [clause_path, assembly_report_path]),
    ]
    finished_at = utc_now_iso()
    report = RunReport(
        run_id=run_id,
        mode="strike_fallback",
        command=_strike_fallback_command(
            pdf_path,
            source_json,
            source_assembly_report,
            source_bad_clause_review,
            source_strike_diagnostics,
        ),
        started_at=started_at,
        finished_at=finished_at,
        pdf_path=str(pdf_path),
        artifacts={
            "clauses_strike_fallback": repo_rel(clause_path),
            "assembly_report_strike_fallback_json": repo_rel(assembly_report_path),
            "assembly_report_strike_fallback_md": repo_rel(assembly_md_path),
            "strike_fallback_review_json": repo_rel(review_path),
            "strike_fallback_review_md": repo_rel(review_md_path),
        },
        inputs={
            "pdf": fingerprint(pdf_path, role="input"),
            "probe_report": {
                "path": probe_report["archived_report_path"],
                "role": "derived",
                "run_id": probe_report["run_id"],
            },
            "source_json": fingerprint(source_json, role="generated"),
            "source_assembly_report": fingerprint(source_assembly_report, role="generated"),
            "source_bad_clause_review": fingerprint(source_bad_clause_review, role="generated"),
            "source_strike_diagnostics": fingerprint(source_strike_diagnostics, role="generated"),
            "golden": fingerprint(settings.project.golden_path, role="reference"),
        },
        artifact_provenance={
            "clauses_strike_fallback": fingerprint(clause_path, role="generated"),
            "assembly_report_strike_fallback_json": fingerprint(assembly_report_path, role="generated"),
            "strike_fallback_review_json": fingerprint(review_path, role="generated"),
        },
        metrics=metrics,
        freshness={
            "status": "fresh",
            "checks": freshness_checks,
            "consumed_probe_run_id": probe_report["run_id"],
        },
        notes=[
            "This mode is a narrow repair layer on top of an existing accepted adjudicated clause set.",
            "Fallback decisions are limited to shortlisted suspicious surviving clauses only.",
        ],
    )
    publish_run_report("strike_fallback", report)
    return updated_clauses


def run_pipeline(pdf_path: str | Path, out_path: str | Path, settings: Settings, mode: str = "legacy"):
    if mode == "legacy":
        return run_legacy_baseline(pdf_path, out_path, settings)
    if mode == "unified":
        return run_unified_draft(pdf_path, out_path, settings)
    if mode == "unified_adjudicated":
        return run_unified_adjudicated_draft(pdf_path, out_path, settings)
    raise NotImplementedError("Unknown pipeline mode.")
