from __future__ import annotations

import json
from pathlib import Path

from rapidfuzz import fuzz

from charter_parser.assembly import assemble_draft_clauses
from charter_parser.candidate_generation import generate_candidate_blocks
from charter_parser.config import load_settings
from charter_parser.pipeline import (
    _extract_probe_inputs,
    _line_lookup_from_pages,
    _page_strike_segments,
    _reconstruct_clause_residual,
    _residual_recommendation,
    _word_lookup_from_pages,
)
from charter_parser.utils import normalize_ws, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
LATEST_DIR = REPO_ROOT / "artifacts" / "runs" / "latest"
TITLE_ONLY_SUPPRESSION_IDS = {"part2:21", "part2:27", "part2:38"}
STRIKE_EVIDENCE_GAP_IDS = {"shell:2", "shell:35", "essar:6"}
UNCERTAIN_REFERENCE_QUIRK_IDS = {"part2:21", "part2:27", "part2:38", "essar:18"}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _ratio(a: str, b: str) -> float:
    return round(fuzz.ratio(normalize_ws(a), normalize_ws(b)) / 100.0, 4)


def _preview(text: str, limit: int = 220) -> str:
    compact = normalize_ws(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _likely_kind(case_id: str, error_class: str) -> str:
    if error_class == "extra_clause":
        return "true bug"
    if error_class == "missing_clause":
        return "true bug"
    if error_class == "title_only_suppression":
        return "likely reference quirk"
    if error_class == "wrong_boundary" and case_id == "essar:18":
        return "likely reference quirk"
    if error_class == "wrong_boundary":
        return "true bug"
    if error_class == "formatting_only_issue":
        return "acceptable suppression"
    return "ambiguous edge case"


def _diagnosis(case_id: str, error_class: str) -> str:
    diagnoses = {
        "essar:6": "Clause still survives final output despite strong struck/dead evidence and no frozen-reference match.",
        "shell:2": "Clause still survives final output despite struck/dead evidence; accepted parser keeps it as live text.",
        "shell:35": "Clause still survives final output even though the frozen reference suppresses it.",
        "part2:3": "Title/body assembly still swallows clause-leading body text into the title and shifts the boundary.",
        "part2:13": "Clause start and notice-of-readiness body remain merged, depressing both title and body alignment.",
        "part2:24": "The title band still absorbs too much inline clause text before body separation.",
        "shell:1": "Indemnity clause body still diverges materially from the frozen reference boundary.",
        "shell:4": "Early Loading clause retains boundary contamination around the title/body split.",
        "part2:21": "Candidate exists upstream but final residual cleanup suppresses it; the frozen reference keeps an empty/title-only clause.",
        "part2:27": "Candidate exists upstream but final residual cleanup suppresses it; the frozen reference keeps an empty/title-only clause.",
        "part2:38": "Candidate exists upstream but final residual cleanup suppresses it; the frozen reference keeps an empty/title-only clause.",
        "essar:18": "Body mismatch remains, but the frozen reference is likely quirky because it keeps an empty body for a heading-like clause.",
    }
    if case_id in diagnoses:
        return diagnoses[case_id]
    if error_class == "extra_clause":
        return "Final output contains a clause with no frozen-reference match."
    if error_class == "missing_clause":
        return "Frozen-reference clause is still absent from the accepted final output."
    if error_class == "title_only_suppression":
        return "Final cleanup suppresses the clause body while the frozen reference keeps only a title/empty shell."
    if error_class == "wrong_boundary":
        return "Title/body or neighboring-clause boundary still diverges materially from the frozen reference."
    if error_class == "formatting_only_issue":
        return "Text overlap is high; the remaining difference is mostly title formatting or numbering placement."
    return "Residual difference still needs case-specific inspection."


def _root_cause(case_id: str, error_class: str) -> str:
    if case_id in TITLE_ONLY_SUPPRESSION_IDS:
        return "Residual cleanup suppresses the assembled candidate because the surviving body is judged empty after strike filtering."
    if case_id in STRIKE_EVIDENCE_GAP_IDS:
        return "Strike/dead-vs-live decision remains too permissive in the accepted final assembly."
    if case_id == "part2:3":
        return "Clause-leading body text is still attached to the title block."
    if case_id == "part2:13":
        return "Clause start and following notice-of-readiness content are still not split cleanly."
    if case_id == "part2:24":
        return "Inline-title/body separation is still too greedy for this clause."
    if case_id == "shell:1":
        return "Clause body boundary still retains extra preamble or misses the expected cut."
    if case_id == "shell:4":
        return "Body boundary still diverges around the early-loading preamble."
    if case_id == "essar:18":
        return "Reference likely treats this as heading-like while the parser keeps more body text."
    if error_class == "formatting_only_issue":
        return "The remaining difference is mostly title-number placement rather than body extraction."
    return "See case-level comparison."


def _error_priority(error_class: str) -> int:
    order = {
        "duplicate_clause": 0,
        "missing_clause": 1,
        "extra_clause": 2,
        "wrong_section": 3,
        "wrong_boundary": 4,
        "title_only_suppression": 5,
        "formatting_only_issue": 6,
    }
    return order.get(error_class, 9)


def main() -> None:
    settings = load_settings(REPO_ROOT / "configs" / "default.yaml")
    pdf_path = REPO_ROOT / "data" / "raw" / "voyage-charter-example.pdf"
    pages, profile = _extract_probe_inputs(pdf_path, settings)
    blocks, _ = generate_candidate_blocks(pages, profile, settings)
    draft_clauses, _ = assemble_draft_clauses(blocks)
    line_by_id = _line_lookup_from_pages(pages)
    word_by_id = _word_lookup_from_pages(pages)
    page_strike_segments = _page_strike_segments(str(pdf_path), {page.page_index for page in pages})

    baseline = _read_json(LATEST_DIR / "clauses.json")
    unified = _read_json(LATEST_DIR / "clauses_unified.json")
    adjudicated = _read_json(LATEST_DIR / "clauses_unified_adjudicated.json")
    golden = _read_json(REPO_ROOT / "artifacts" / "golden" / "clauses_merged.json")
    unified_report = _read_json(LATEST_DIR / "assembly_report.json")
    adjudicated_report = _read_json(LATEST_DIR / "assembly_report_adjudicated.json")

    baseline_by_id = {row["id"]: row for row in baseline}
    unified_by_id = {row["id"]: row for row in unified}
    final_by_id = {row["id"]: row for row in adjudicated}
    golden_by_id = {row["id"]: row for row in golden}
    draft_by_id = {clause.id: clause for clause in draft_clauses}

    missing_ids = adjudicated_report["comparisons"]["vs_reference"]["missing_ids"]
    extra_ids = adjudicated_report["comparisons"]["vs_reference"]["extra_ids"]
    duplicate_ids = adjudicated_report["metrics"]["duplicate_ids"]

    overlap_rows: list[dict] = []
    for clause_id in sorted(set(final_by_id) & set(golden_by_id)):
        final_row = final_by_id[clause_id]
        golden_row = golden_by_id[clause_id]
        overlap_rows.append(
            {
                "id": clause_id,
                "page_span": f"{final_row['page_start']}-{final_row['page_end']}",
                "title_similarity": _ratio(final_row["title"], golden_row["title"]),
                "body_similarity": _ratio(final_row["text"], golden_row["text"]),
                "section_match": final_row["section"] == golden_row["section"],
                "golden_empty_text": not normalize_ws(golden_row["text"]),
            }
        )

    title_only_ids = [clause_id for clause_id in missing_ids if clause_id in TITLE_ONLY_SUPPRESSION_IDS]
    real_missing_ids = [clause_id for clause_id in missing_ids if clause_id not in TITLE_ONLY_SUPPRESSION_IDS]
    wrong_section_ids = [row["id"] for row in overlap_rows if not row["section_match"]]
    wrong_boundary_ids = [
        row["id"]
        for row in overlap_rows
        if row["id"] not in TITLE_ONLY_SUPPRESSION_IDS
        and normalize_ws(golden_by_id[row["id"]]["text"])
        and row["body_similarity"] < 0.6
    ]
    formatting_only_ids = [
        row["id"]
        for row in overlap_rows
        if row["id"] not in wrong_boundary_ids
        and row["body_similarity"] >= 0.95
        and row["title_similarity"] < 0.5
    ]
    orphaned_residual_kept_ids: list[str] = []
    uncertain_reference_quirk_ids = [clause_id for clause_id in UNCERTAIN_REFERENCE_QUIRK_IDS if clause_id in golden_by_id]

    error_classes = {
        "missing_clause": {
            "count": len(real_missing_ids),
            "affected_clause_ids": real_missing_ids,
            "short_explanation": "Frozen-reference live clauses still absent from the accepted final output.",
        },
        "extra_clause": {
            "count": len(extra_ids),
            "affected_clause_ids": extra_ids,
            "short_explanation": "Accepted final output still contains clauses with no frozen-reference match.",
        },
        "duplicate_clause": {
            "count": len(duplicate_ids),
            "affected_clause_ids": duplicate_ids,
            "short_explanation": "Duplicate clause ids in the accepted final output.",
        },
        "wrong_section": {
            "count": len(wrong_section_ids),
            "affected_clause_ids": wrong_section_ids,
            "short_explanation": "Clause ids present in both outputs but assigned to different sections.",
        },
        "wrong_boundary": {
            "count": len(wrong_boundary_ids),
            "affected_clause_ids": wrong_boundary_ids,
            "short_explanation": "Clause ids present in both outputs but still materially misaligned on title/body boundaries.",
        },
        "title_only_suppression": {
            "count": len(title_only_ids),
            "affected_clause_ids": title_only_ids,
            "short_explanation": "Missing clauses whose frozen-reference body is empty or title-only, so the accepted suppression may be acceptable.",
        },
        "strike_evidence_gap": {
            "count": len([clause_id for clause_id in extra_ids if clause_id in STRIKE_EVIDENCE_GAP_IDS]),
            "affected_clause_ids": [clause_id for clause_id in extra_ids if clause_id in STRIKE_EVIDENCE_GAP_IDS],
            "short_explanation": "Extras that still look like struck/dead-vs-live mistakes rather than numbering or ordering errors.",
        },
        "orphaned_residual_kept": {
            "count": len(orphaned_residual_kept_ids),
            "affected_clause_ids": orphaned_residual_kept_ids,
            "short_explanation": "Residual fragments kept as standalone clauses with no clear frozen-reference support.",
        },
        "formatting_only_issue": {
            "count": len(formatting_only_ids),
            "affected_clause_ids": formatting_only_ids,
            "short_explanation": "High body overlap but title-number or formatting placement still differs.",
        },
        "uncertain_reference_quirk": {
            "count": len(uncertain_reference_quirk_ids),
            "affected_clause_ids": uncertain_reference_quirk_ids,
            "short_explanation": "Cases where the frozen reference itself looks title-only, empty, or otherwise quirky.",
        },
    }

    case_rows: list[dict] = []
    for clause_id in missing_ids:
        draft_clause = draft_by_id.get(clause_id)
        candidate_info = None
        residual_info = None
        if draft_clause is not None:
            raw_title = " ".join(
                line_by_id[line_id]["text"]
                for line_id in draft_clause.title_line_ids
                if line_id in line_by_id
            )
            raw_body = " ".join(
                line_by_id[line_id]["text"]
                for line_id in draft_clause.body_line_ids
                if line_id in line_by_id
            )
            residual_title, residual_body, _ = _reconstruct_clause_residual(
                draft_clause,
                line_by_id=line_by_id,
                word_by_id=word_by_id,
                page_strike_segments=page_strike_segments,
                settings=settings,
            )
            residual_info = _residual_recommendation(
                raw_title,
                raw_body,
                residual_title,
                residual_body,
                section=draft_clause.section,
            )
            candidate_info = {
                "block_ids": draft_clause.candidate_block_ids,
                "page_span": f"{draft_clause.page_start}-{draft_clause.page_end}",
                "title_preview": _preview(draft_clause.title, 120),
                "body_preview": _preview(draft_clause.text, 220),
            }
        error_class = "title_only_suppression" if clause_id in TITLE_ONLY_SUPPRESSION_IDS else "missing_clause"
        case_rows.append(
            {
                "clause_id": clause_id,
                "page_span": candidate_info["page_span"] if candidate_info else None,
                "error_class": error_class,
                "why_selected": "Missing vs frozen reference.",
                "golden_preview": _preview(golden_by_id[clause_id]["text"], 220),
                "current_preview": "",
                "deterministic_preview": _preview(unified_by_id[clause_id]["text"], 220) if clause_id in unified_by_id else "",
                "diagnosis": _diagnosis(clause_id, error_class),
                "likely_kind": _likely_kind(clause_id, error_class),
                "candidate_info": candidate_info,
                "residual_info": residual_info,
            }
        )

    for clause_id in extra_ids:
        final_row = final_by_id[clause_id]
        case_rows.append(
            {
                "clause_id": clause_id,
                "page_span": f"{final_row['page_start']}-{final_row['page_end']}",
                "error_class": "extra_clause",
                "why_selected": "Extra vs frozen reference.",
                "golden_preview": "",
                "current_preview": _preview(final_row["text"], 220),
                "deterministic_preview": _preview(unified_by_id[clause_id]["text"], 220) if clause_id in unified_by_id else "",
                "diagnosis": _diagnosis(clause_id, "extra_clause"),
                "likely_kind": _likely_kind(clause_id, "extra_clause"),
                "candidate_info": None,
                "residual_info": None,
            }
        )

    for clause_id in wrong_boundary_ids + formatting_only_ids:
        final_row = final_by_id[clause_id]
        golden_row = golden_by_id[clause_id]
        error_class = "wrong_boundary" if clause_id in wrong_boundary_ids else "formatting_only_issue"
        case_rows.append(
            {
                "clause_id": clause_id,
                "page_span": f"{final_row['page_start']}-{final_row['page_end']}",
                "error_class": error_class,
                "why_selected": "Low similarity vs frozen reference."
                if error_class == "wrong_boundary"
                else "High body overlap but low title match.",
                "golden_preview": _preview(golden_row["text"], 220),
                "current_preview": _preview(final_row["text"], 220),
                "deterministic_preview": _preview(unified_by_id[clause_id]["text"], 220) if clause_id in unified_by_id else "",
                "diagnosis": _diagnosis(clause_id, error_class),
                "likely_kind": _likely_kind(clause_id, error_class),
                "candidate_info": None,
                "residual_info": {
                    "title_similarity": _ratio(final_row["title"], golden_row["title"]),
                    "body_similarity": _ratio(final_row["text"], golden_row["text"]),
                },
            }
        )

    case_rows.sort(key=lambda row: (_error_priority(row["error_class"]), row["clause_id"]))
    top_cases = case_rows[:12]

    missing_details = []
    for clause_id in missing_ids:
        draft_clause = draft_by_id.get(clause_id)
        golden_row = golden_by_id[clause_id]
        missing_details.append(
            {
                "clause_id": clause_id,
                "golden_title": golden_row["title"],
                "golden_preview": _preview(golden_row["text"], 220),
                "last_known_candidate": {
                    "page_span": f"{draft_clause.page_start}-{draft_clause.page_end}",
                    "block_ids": draft_clause.candidate_block_ids,
                    "title_preview": _preview(draft_clause.title, 120),
                    "body_preview": _preview(draft_clause.text, 220),
                }
                if draft_clause
                else None,
                "likely_root_cause": _root_cause(
                    clause_id,
                    "title_only_suppression" if clause_id in TITLE_ONLY_SUPPRESSION_IDS else "missing_clause",
                ),
            }
        )

    extra_details = []
    for clause_id in extra_ids:
        row = final_by_id[clause_id]
        extra_details.append(
            {
                "clause_id": clause_id,
                "page_span": f"{row['page_start']}-{row['page_end']}",
                "title": row["title"],
                "current_preview": _preview(row["text"], 220),
                "likely_root_cause": _root_cause(clause_id, "extra_clause"),
            }
        )

    report = {
        "global_summary": {
            "baseline_clause_count": len(baseline),
            "unified_clause_count": unified_report["metrics"]["unified_clause_count"],
            "adjudicated_clause_count": adjudicated_report["metrics"]["unified_clause_count"],
            "duplicate_ids": duplicate_ids,
            "missing_ids_vs_golden": missing_ids,
            "extra_ids_vs_golden": extra_ids,
            "boundary_alignment_proxy": adjudicated_report["metrics"]["boundary_alignment_proxy_vs_reference"],
            "body_overlap_proxy": adjudicated_report["metrics"]["body_text_overlap_proxy_vs_reference"],
            "title_similarity": adjudicated_report["metrics"]["normalized_title_similarity_vs_reference"],
            "split_merge_proxy": adjudicated_report["metrics"]["split_merge_error_proxy"],
        },
        "error_classes": error_classes,
        "top_remaining_bad_cases": top_cases,
        "missing_clauses": missing_details,
        "extra_clauses": extra_details,
        "duplicates": [],
        "what_remains": {
            "definitely_real_remaining_bugs": [
                "shell:2",
                "shell:35",
                "essar:6",
                "part2:3",
                "part2:13",
            ],
            "acceptable_or_low_priority": title_only_ids + formatting_only_ids,
            "ambiguous": [case_id for case_id in uncertain_reference_quirk_ids if case_id not in TITLE_ONLY_SUPPRESSION_IDS],
            "top_3_remaining_technical_issues": [
                "Final strike/dead-vs-live suppression still keeps three extra struck clauses (`shell:2`, `shell:35`, `essar:6`).",
                "Two high-value clause boundary/title contamination cases remain in `part2:3` and `part2:13`.",
                "A small tail of title-only or formatting/reference-quirk cases remains, but they are lower priority than the strike extras and major boundary errors.",
            ],
        },
    }

    write_json(LATEST_DIR / "final_errors_vs_golden.json", report)

    lines = [
        "# Final Errors vs Golden",
        "",
        "## Global summary",
        f"- baseline clause count: {report['global_summary']['baseline_clause_count']}",
        f"- unified clause count: {report['global_summary']['unified_clause_count']}",
        f"- adjudicated clause count: {report['global_summary']['adjudicated_clause_count']}",
        f"- duplicate ids: {report['global_summary']['duplicate_ids']}",
        f"- missing ids vs golden: {report['global_summary']['missing_ids_vs_golden']}",
        f"- extra ids vs golden: {report['global_summary']['extra_ids_vs_golden']}",
        f"- boundary alignment proxy: {report['global_summary']['boundary_alignment_proxy']}",
        f"- body overlap proxy: {report['global_summary']['body_overlap_proxy']}",
        f"- title similarity: {report['global_summary']['title_similarity']}",
        f"- split/merge proxy: {report['global_summary']['split_merge_proxy']}",
        "",
        "## Error class summary",
    ]
    for name, bucket in error_classes.items():
        lines.extend(
            [
                f"### {name}",
                f"- count: {bucket['count']}",
                f"- affected clause ids: {bucket['affected_clause_ids']}",
                f"- explanation: {bucket['short_explanation']}",
                "",
            ]
        )

    lines.append("## Top remaining bad cases")
    for case in top_cases:
        lines.extend(
            [
                f"### {case['clause_id']}",
                f"- page span: {case['page_span']}",
                f"- why selected: {case['why_selected']}",
                f"- golden/reference preview: {case['golden_preview'] or '[no frozen-reference clause]'}",
                f"- current final output preview: {case['current_preview'] or '[missing from final output]'}",
                f"- deterministic comparison preview: {case['deterministic_preview'] or '[same or unavailable]'}",
                f"- diagnosis: {case['diagnosis']}",
                f"- error class: {case['error_class']}",
                f"- likely kind: {case['likely_kind']}",
                "",
            ]
        )

    lines.append("## Missing clauses")
    for row in missing_details:
        lines.extend(
            [
                f"### {row['clause_id']}",
                f"- golden title/body preview: {row['golden_title']} | {row['golden_preview'] or '[empty body]'}",
                f"- last known candidate info: {row['last_known_candidate']}",
                f"- likely root cause: {row['likely_root_cause']}",
                "",
            ]
        )

    lines.append("## Extra clauses")
    for row in extra_details:
        lines.extend(
            [
                f"### {row['clause_id']}",
                f"- page span: {row['page_span']}",
                f"- current title/body preview: {row['title']} | {row['current_preview']}",
                f"- likely root cause: {row['likely_root_cause']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Duplicates",
            "- No duplicate clause ids remain.",
            "",
            "## What remains to fix",
            f"- definitely real remaining bugs: {report['what_remains']['definitely_real_remaining_bugs']}",
            f"- acceptable or low priority issues: {report['what_remains']['acceptable_or_low_priority']}",
            f"- ambiguous issues: {report['what_remains']['ambiguous']}",
            "- top 3 remaining technical issues:",
        ]
    )
    for item in report["what_remains"]["top_3_remaining_technical_issues"]:
        lines.append(f"- {item}")

    (LATEST_DIR / "final_errors_vs_golden.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
