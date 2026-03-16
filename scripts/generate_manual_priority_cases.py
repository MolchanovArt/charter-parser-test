from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from charter_parser.models import DraftClause, PageIR
from charter_parser.pipeline import (
    _line_lookup_from_pages,
    _line_strike_snapshot,
    _raw_text_from_line_ids,
    _reconstruct_clause_residual,
    _residual_recommendation,
    _word_lookup_from_pages,
)
from charter_parser.utils import normalize_ws


CASE_IDS = [
    "shell:21",
    "shell:32",
    "shell:33",
    "shell:37",
    "shell:41",
    "shell:34",
    "shell:36",
    "shell:39",
    "part2:16",
    "part2:17",
    "part2:18",
    "part2:19",
    "part2:20",
    "part2:21",
    "essar:6",
    "part2:2",
    "part2:14",
    "part2:36",
]

CASE_VERDICTS = {
    "shell:21": "duplicate/id collision",
    "shell:32": "orphaned residual kept",
    "shell:33": "orphaned residual kept",
    "shell:37": "structural boundary failure",
    "shell:41": "structural boundary failure",
    "shell:34": "true strike failure",
    "shell:36": "true strike failure",
    "shell:39": "true strike failure",
    "part2:16": "missing-start failure",
    "part2:17": "missing-start failure",
    "part2:18": "missing-start failure",
    "part2:19": "missing-start failure",
    "part2:20": "missing-start failure",
    "part2:21": "missing-start failure",
    "essar:6": "reference quirk / uncertain",
    "part2:2": "valid residual correctly kept",
    "part2:14": "valid residual correctly kept",
    "part2:36": "valid residual correctly kept",
}

MANUAL_PRIORITY_NOTES = {
    "shell:32": "Manually confirmed fully struck; expected final state is suppressed.",
    "shell:33": "Manually confirmed fully struck; expected final state is suppressed.",
    "shell:37": "Manually confirmed fully struck; expected final state is suppressed.",
    "shell:41": "Manually confirmed fully struck; expected final state is suppressed.",
    "part2:19": "Manually confirmed live; expected final state is recovered and kept.",
    "part2:20": "Manually confirmed live rewritten residual; expected final state is recovered in cleaned form.",
    "part2:21": "Manually confirmed acceptable to suppress when only the heading survives.",
}


def load_json(path: Path):
    return json.loads(path.read_text())


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def preview(text: str | None, limit: int = 360) -> str:
    compact = normalize_ws(text or "")
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def maybe_full(text: str | None, limit: int = 1200) -> str | None:
    compact = normalize_ws(text or "")
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def infer_local_num(case_id: str) -> int | None:
    try:
        return int(case_id.split(":")[1])
    except Exception:
        return None


def find_reference(reference_rows: list[dict], case_id: str) -> dict | None:
    return next((row for row in reference_rows if row["id"] == case_id), None)


def find_clauses(rows: list[dict], case_id: str) -> list[dict]:
    return [row for row in rows if row["id"] == case_id]


def block_summary(block: dict) -> dict:
    changed_lines = []
    for line in block.get("line_decisions", []):
        if normalize_ws(line.get("raw_text", "")) != normalize_ws(line.get("extracted_text", "")):
            changed_lines.append(
                {
                    "line_id": line["line_id"],
                    "raw_text": line.get("raw_text", ""),
                    "extracted_text": line.get("extracted_text", ""),
                    "clean_text": line.get("clean_text", ""),
                    "reasons": line.get("reasons", []),
                }
            )
    local_source = next((reason for reason in block.get("reasons", []) if reason.startswith("start_regex:")), None)
    return {
        "block_id": block["block_id"],
        "page": block["page"],
        "block_type": block["block_type"],
        "inferred_section": block.get("section_hint"),
        "candidate_clause_id": block.get("candidate_clause_id"),
        "candidate_local_num": block.get("candidate_local_num"),
        "local_number_source": local_source or "n/a",
        "routing_mode": block.get("routing_mode"),
        "title_line_ids": block.get("title_line_ids", []),
        "body_line_ids": block.get("body_line_ids", []),
        "title_text": block.get("title_text", ""),
        "body_text_preview": preview(block.get("body_text", ""), 260),
        "selection_reasons": block.get("reasons", []),
        "right_margin_numeric_suppression": {
            "changed": bool(changed_lines),
            "changed_lines": changed_lines[:6],
        },
    }


def relevant_blocks(
    *,
    case_id: str,
    local_num: int | None,
    m2_blocks: list[dict],
    adjudicated_blocks: list[dict],
    ambiguity_cases: list[dict],
    reference_case: dict | None,
) -> dict:
    exact_m2 = [block for block in m2_blocks if block.get("candidate_clause_id") == case_id]
    exact_adj = [block for block in adjudicated_blocks if block.get("candidate_clause_id") == case_id]
    ambiguity_hits = [case for case in ambiguity_cases if case.get("candidate_clause_id") == case_id]
    ambiguity_block_ids = {case["block_id"] for case in ambiguity_hits}
    linked_m2 = [block for block in m2_blocks if block["block_id"] in ambiguity_block_ids and block not in exact_m2]
    linked_adj = [block for block in adjudicated_blocks if block["block_id"] in ambiguity_block_ids and block not in exact_adj]
    pages = set()
    if reference_case:
        pages.update(
            p
            for p in [reference_case.get("page_start"), reference_case.get("page_end")]
            if isinstance(p, int)
        )
    for block in exact_m2 + exact_adj + linked_m2 + linked_adj:
        pages.add(block["page"])
    nearby_m2 = []
    if not exact_m2 and local_num is not None and pages:
        for block in m2_blocks:
            if block["page"] in pages and block.get("candidate_local_num") in {local_num - 1, local_num, local_num + 1}:
                nearby_m2.append(block)
    return {
        "m2_exact": [block_summary(block) for block in exact_m2],
        "adjudicated_exact": [block_summary(block) for block in exact_adj],
        "m2_linked_ambiguity_blocks": [block_summary(block) for block in linked_m2],
        "adjudicated_linked_ambiguity_blocks": [block_summary(block) for block in linked_adj],
        "nearby_m2_blocks": [block_summary(block) for block in nearby_m2[:6]],
        "exact_start_created": bool(exact_m2 or exact_adj),
    }


def page_band_names(page_profile: dict, x0: float, x1: float) -> list[str]:
    cx = (x0 + x1) / 2.0
    names = []
    for band in page_profile.get("bands", []):
        if band["x0"] <= cx <= band["x1"]:
            names.append(band["name"])
    return names


def page_context(
    *,
    case_id: str,
    pages: list[PageIR],
    layout_profile: dict,
    m2_blocks: list[dict],
    adjudicated_blocks: list[dict],
    deterministic_clauses: list[dict],
    adjudicated_clauses: list[dict],
    ambiguity_cases: list[dict],
    reference_case: dict | None,
) -> dict:
    local_num = infer_local_num(case_id)
    selected_pages: set[int] = set()
    anchor_line_ids: set[str] = set()
    for clause in find_clauses(deterministic_clauses, case_id) + find_clauses(adjudicated_clauses, case_id):
        selected_pages.update([clause["page_start"], clause["page_end"]])
        anchor_line_ids.update(clause.get("title_line_ids", []))
        anchor_line_ids.update(clause.get("body_line_ids", []))
    for block in m2_blocks + adjudicated_blocks:
        if block.get("candidate_clause_id") == case_id:
            selected_pages.add(block["page"])
            anchor_line_ids.update(block.get("title_line_ids", []))
            anchor_line_ids.update(block.get("body_line_ids", []))
    for amb in ambiguity_cases:
        if amb.get("candidate_clause_id") == case_id:
            selected_pages.add(amb["page"])
            anchor_line_ids.update(amb.get("candidate_line_ids", []))
    if reference_case:
        for p in [reference_case.get("page_start"), reference_case.get("page_end")]:
            if isinstance(p, int):
                selected_pages.add(p)
    if not selected_pages and case_id.startswith("part2:") and local_num in {19, 20, 21}:
        selected_pages.update({9, 10})
    line_windows = []
    profile_by_page = {page["page_index"]: page for page in layout_profile["pages"]}
    for page in pages:
        if page.page_index not in selected_pages:
            continue
        page_profile = profile_by_page[page.page_index]
        line_index_by_id = {line.line_id: idx for idx, line in enumerate(page.lines)}
        relevant_indexes = sorted(
            {
                idx
                for line_id, idx in line_index_by_id.items()
                if line_id in anchor_line_ids
                or (
                    local_num is not None
                    and re.search(rf"(^|\\s){local_num}[\\.)]", page.lines[idx].text)
                )
            }
        )
        if not relevant_indexes:
            relevant_indexes = list(range(min(10, len(page.lines))))
        window_indexes: set[int] = set()
        for idx in relevant_indexes:
            for probe in range(max(0, idx - 2), min(len(page.lines), idx + 3)):
                window_indexes.add(probe)
        rows = []
        for idx in sorted(window_indexes):
            line = page.lines[idx]
            x0, y0, x1, y1 = line.bbox
            rows.append(
                {
                    "line_id": line.line_id,
                    "text": line.text,
                    "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                    "band_hits": page_band_names(page_profile, x0, x1),
                }
            )
        line_windows.append(
            {
                "page": page.page_index,
                "routing_mode": page_profile["page_type"],
                "geometry_summary": {
                    "page_type": page_profile["page_type"],
                    "bands": page_profile.get("bands", []),
                    "notes": page_profile.get("notes", []),
                },
                "nearby_lines": rows,
                "screenshot_reference": None,
            }
        )
    return {"pages": line_windows}


def pseudo_clause_from_block(block: dict) -> DraftClause:
    return DraftClause(
        order=1,
        section=block.get("section_hint") or "unknown",
        local_num=block.get("candidate_local_num") or 1,
        id=block.get("candidate_clause_id") or f"pseudo:{block['block_id']}",
        title=block.get("title_text") or "",
        text=block.get("body_text") or "",
        page_start=block["page"],
        page_end=block["page"],
        candidate_block_ids=[block["block_id"]],
        title_line_ids=list(block.get("title_line_ids", [])),
        body_line_ids=list(block.get("body_line_ids", [])),
        support_score=block.get("support_score") or 0.0,
    )


def strike_stage(
    *,
    case_id: str,
    pages: list[PageIR],
    deterministic_clauses: list[dict],
    adjudicated_clauses: list[dict],
    m2_blocks: list[dict],
    adjudicated_blocks: list[dict],
) -> dict:
    line_by_id = _line_lookup_from_pages(pages)
    word_by_id = _word_lookup_from_pages(pages)
    clause_row = next((row for row in adjudicated_clauses if row["id"] == case_id), None)
    source = "adjudicated_clause"
    if clause_row is None:
        clause_row = next((row for row in deterministic_clauses if row["id"] == case_id), None)
        source = "deterministic_clause"
    if clause_row is None:
        block = next((row for row in adjudicated_blocks if row.get("candidate_clause_id") == case_id), None)
        source = "adjudicated_block"
        if block is None:
            block = next((row for row in m2_blocks if row.get("candidate_clause_id") == case_id), None)
            source = "m2_block"
        if block is None:
            return {"source": None}
        clause = pseudo_clause_from_block(block)
    else:
        clause = DraftClause(**clause_row)
    raw_title = _raw_text_from_line_ids(clause.title_line_ids, line_by_id)
    raw_body = _raw_text_from_line_ids(clause.body_line_ids, line_by_id)
    residual_title, residual_body, line_rows = _reconstruct_clause_residual(clause, line_by_id=line_by_id, word_by_id=word_by_id)
    residual = _residual_recommendation(raw_title, raw_body, residual_title, residual_body)
    source_counts: Counter = Counter()
    evidence_modes = []
    title_marked = []
    body_marked = []
    for row in line_rows:
        source_counts.update(row.get("strike_source_counts", {}))
        if row.get("struck_word_count", 0):
            evidence_modes.append(row.get("strike_evidence_mode", "none"))
        if row.get("line_id") in clause.title_line_ids:
            title_marked.append(row.get("marked_text", row.get("raw_text", "")))
        else:
            body_marked.append(row.get("marked_text", row.get("raw_text", "")))
    evidence_summary = {
        "drawing_rect": source_counts.get("drawing_rect", 0),
        "path_rect": source_counts.get("path_rect", 0),
        "line_crossing": source_counts.get("line_crossing", 0),
        "off_center_crossing": sum(1 for row in line_rows if row["strike_evidence_mode"] == "off_center_crossing"),
        "direct_no_evidence_lines": sum(1 for row in line_rows if row.get("struck_word_count", 0) == 0),
    }
    return {
        "source": source,
        "original": {"title": raw_title, "body": raw_body},
        "struck_marked": {
            "title": normalize_ws(" ".join(part for part in title_marked if part)),
            "body": normalize_ws(" ".join(part for part in body_marked if part)),
        },
        "strike_evidence_summary": evidence_summary,
        "evidence_modes": sorted(set(evidence_modes)),
        "cleaned_residual": {"title": residual_title, "body": residual_body},
        "metrics": {
            "title_struck_ratio": residual["title_struck_ratio"],
            "body_struck_ratio": residual["body_struck_ratio"],
            "residual_alphabetic_char_count": residual["residual_alphabetic_char_count"],
            "residual_word_count": residual["residual_word_count"],
            "longest_meaningful_residual_segment": residual["longest_meaningful_residual_segment"],
        },
        "decision": {
            "keep_or_suppress": residual["recommendation"],
            "reason": residual["reason"],
        },
    }


def adjudication_stage(case_id: str, ambiguity_cases: list[dict], adjudication_results: list[dict]) -> dict | None:
    case = next((row for row in ambiguity_cases if row.get("candidate_clause_id") == case_id), None)
    if case is None:
        return None
    result = next((row for row in adjudication_results if row["case_id"] == case["case_id"]), None)
    deterministic_decision = {
        "candidate_start": True,
        "attach_to_previous": False,
        "section_hint": case.get("section_hint"),
        "candidate_line_ids": case.get("candidate_line_ids", []),
    }
    return {
        "ambiguity_case_id": case["case_id"],
        "bucket": case["bucket"],
        "deterministic_decision": deterministic_decision,
        "model_decision": None if result is None else result.get("decision"),
        "status": None if result is None else result.get("status"),
        "effect": None if result is None else result.get("effect"),
    }


def output_summary(rows: list[dict]) -> list[dict]:
    payload = []
    for row in rows:
        payload.append(
            {
                "clause_id": row["id"],
                "page_span": f"{row['page_start']}-{row['page_end']}",
                "title": row.get("title", ""),
                "body_preview": preview(row.get("text", ""), 280),
            }
        )
    return payload


def diagnosis_for_case(case_id: str, reference_case: dict | None, deterministic_rows: list[dict], adjudicated_rows: list[dict]) -> list[str]:
    labels = []
    if reference_case is None and adjudicated_rows:
        labels.append("extra")
    if reference_case is not None and not adjudicated_rows:
        labels.append("missing")
    if len(adjudicated_rows) > 1 or len(deterministic_rows) > 1:
        labels.append("duplicate")
    if case_id in {"shell:21"}:
        labels.append("wrong boundary")
    if case_id in {"shell:32", "shell:33"}:
        labels.append("orphaned residual")
        labels.append("likely struck clause kept")
    if case_id in {"part2:16", "part2:17", "part2:18", "part2:19", "part2:20", "part2:21"}:
        labels.append("likely page-9 start missed")
    if case_id in {"part2:2", "part2:14", "part2:36"}:
        labels.append("likely valid residual preserved")
    return labels


def what_to_look_at(case_id: str) -> str:
    notes = {
        "shell:21": "Compare the page-19 nested item against the real page-25 clause and confirm the collision is resolved by attachment, not renumbering.",
        "shell:32": "Check whether the surviving text is just a legally invalid residual from a heavily struck Shell tail clause.",
        "shell:33": "Check whether the remaining Rotterdam text is a real live clause or a residual fragment from a struck block.",
        "shell:37": "Inspect whether this extra Shell clause is a true boundary mistake rather than a strike mistake.",
        "shell:41": "Inspect whether this clause is structurally extra and entered the sequence without any ambiguity adjudication.",
        "shell:34": "Confirm the strike evidence is strong enough that suppression is justified.",
        "shell:36": "Confirm suppression removed the whole struck clause rather than a valid residual.",
        "shell:39": "Verify the enumerated subitem suppression removed the last orphaned residual fragment.",
        "part2:16": "Check the embedded left-label start recovery from raw line to candidate block to final clean clause.",
        "part2:17": "Same page-9 embedded start pattern as clause 16; verify title/body separation stayed deterministic.",
        "part2:18": "Same page-9 embedded start pattern as clauses 16 and 17.",
        "part2:19": "Check whether a true start was never created after clause 18 and where the sequence jumps.",
        "part2:20": "Check whether clause 20 was created or swallowed into neighboring page-9/page-10 structure.",
        "part2:21": "Check whether clause 21 is absent because candidate generation missed it or assembly dropped it.",
        "essar:6": "Inspect whether this is truly extra or just a golden/reference mismatch.",
        "part2:2": "Inspect a clause with meaningful surviving text after strike cleanup.",
        "part2:14": "Inspect whether the residual is valid text or a suspiciously preserved fragment.",
        "part2:36": "Inspect whether this is a correct residual keep from a mostly struck clause.",
    }
    return notes[case_id]


def build_case_bundle(case_id: str, artifacts: dict) -> dict:
    local_num = infer_local_num(case_id)
    reference_case = find_reference(artifacts["reference"], case_id)
    deterministic_rows = find_clauses(artifacts["deterministic_clauses"], case_id)
    adjudicated_rows = find_clauses(artifacts["adjudicated_clauses"], case_id)
    candidate_info = relevant_blocks(
        case_id=case_id,
        local_num=local_num,
        m2_blocks=artifacts["m2_blocks"],
        adjudicated_blocks=artifacts["adjudicated_blocks"],
        ambiguity_cases=artifacts["ambiguity_cases"],
        reference_case=reference_case,
    )
    return {
        "case_id": case_id,
        "what_to_look_at": what_to_look_at(case_id),
        "manual_priority_note": MANUAL_PRIORITY_NOTES.get(case_id),
        "reference": None if reference_case is None else {
            "reference_clause_id": reference_case["id"],
            "page_span": None if reference_case.get("page_start") is None else f"{reference_case.get('page_start')}-{reference_case.get('page_end')}",
            "title": reference_case.get("title", ""),
            "body_preview": preview(reference_case.get("text", ""), 320),
            "full_text": maybe_full(reference_case.get("text", "")),
        },
        "raw_page_context": page_context(
            case_id=case_id,
            pages=artifacts["pages"],
            layout_profile=artifacts["layout_profile"],
            m2_blocks=artifacts["m2_blocks"],
            adjudicated_blocks=artifacts["adjudicated_blocks"],
            deterministic_clauses=artifacts["deterministic_clauses"],
            adjudicated_clauses=artifacts["adjudicated_clauses"],
            ambiguity_cases=artifacts["ambiguity_cases"],
            reference_case=reference_case,
        ),
        "candidate_generation": candidate_info,
        "strike_residual_processing": strike_stage(
            case_id=case_id,
            pages=artifacts["pages"],
            deterministic_clauses=artifacts["deterministic_clauses"],
            adjudicated_clauses=artifacts["adjudicated_clauses"],
            m2_blocks=artifacts["m2_blocks"],
            adjudicated_blocks=artifacts["adjudicated_blocks"],
        ),
        "adjudication": adjudication_stage(case_id, artifacts["ambiguity_cases"], artifacts["adjudication_results"]),
        "final_output_comparison": {
            "deterministic_unified_output": output_summary(deterministic_rows),
            "adjudicated_final_output": output_summary(adjudicated_rows),
            "reference_golden": None if reference_case is None else {
                "clause_id": reference_case["id"],
                "page_span": None if reference_case.get("page_start") is None else f"{reference_case.get('page_start')}-{reference_case.get('page_end')}",
                "title": reference_case.get("title", ""),
                "body_preview": preview(reference_case.get("text", ""), 320),
            },
            "diagnosis": diagnosis_for_case(case_id, reference_case, deterministic_rows, adjudicated_rows),
        },
        "case_verdict": CASE_VERDICTS[case_id],
    }


def md_block(text: str | None) -> str:
    return "```text\n" + ((text or "-").strip() or "-") + "\n```"


def write_markdown(bundle: dict) -> str:
    lines = ["# Manual Priority Cases", ""]
    for case in bundle["cases"]:
        lines.append(f"## {case['case_id']}")
        lines.append("")
        if case.get("manual_priority_note"):
            lines.append("**Manual Priority Note**")
            lines.append(case["manual_priority_note"])
            lines.append("")
        lines.append(f"**What To Look At**")
        lines.append(case["what_to_look_at"])
        lines.append("")

        lines.append("**A. Reference / Golden**")
        ref = case["reference"]
        if ref is None:
            lines.append("- No reference clause for this id in the golden set.")
        else:
            lines.append(f"- Reference id: `{ref['reference_clause_id']}`")
            lines.append(f"- Page span: `{ref['page_span']}`")
            lines.append(f"- Title: `{ref['title'] or '-'}`")
            lines.append(f"- Body preview: `{ref['body_preview'] or '-'}`")
            lines.append(md_block(ref["full_text"]))
        lines.append("")

        lines.append("**B. Raw Page Context**")
        for page in case["raw_page_context"]["pages"]:
            lines.append(f"- Page `{page['page']}` routing mode: `{page['routing_mode']}`")
            lines.append(f"- Geometry: `{json.dumps(page['geometry_summary'], ensure_ascii=False)}`")
            lines.append("- Screenshot reference: `none in existing artifacts`")
            lines.append("```text")
            for row in page["nearby_lines"]:
                lines.append(
                    f"{row['line_id']} bbox={row['bbox']} bands={row['band_hits']} text={row['text']}"
                )
            lines.append("```")
        if not case["raw_page_context"]["pages"]:
            lines.append("- No page context found.")
        lines.append("")

        lines.append("**C. Candidate Generation**")
        cg = case["candidate_generation"]
        lines.append(f"- Exact start created: `{cg['exact_start_created']}`")
        for label in ["m2_exact", "adjudicated_exact", "m2_linked_ambiguity_blocks", "adjudicated_linked_ambiguity_blocks", "nearby_m2_blocks"]:
            rows = cg[label]
            if not rows:
                continue
            lines.append(f"- {label}:")
            lines.append("```json")
            lines.append(json.dumps(rows, indent=2, ensure_ascii=False))
            lines.append("```")
        lines.append("")

        lines.append("**D. Strike / Residual Processing**")
        strike = case["strike_residual_processing"]
        if not strike.get("source"):
            lines.append("- No clause/block source available for residual reconstruction.")
        else:
            lines.append(f"- Source: `{strike['source']}`")
            lines.append(f"- Evidence summary: `{json.dumps(strike['strike_evidence_summary'], ensure_ascii=False)}`")
            lines.append(f"- Evidence modes: `{strike['evidence_modes']}`")
            lines.append(f"- Decision: `{strike['decision']['keep_or_suppress']}` because `{strike['decision']['reason']}`")
            lines.append(f"- Metrics: `{json.dumps(strike['metrics'], ensure_ascii=False)}`")
            lines.append(md_block("ORIGINAL TITLE\n" + (strike["original"]["title"] or "-")))
            lines.append(md_block("ORIGINAL BODY\n" + (strike["original"]["body"] or "-")))
            lines.append(md_block("STRUCK-MARKED TITLE\n" + (strike["struck_marked"]["title"] or "-")))
            lines.append(md_block("STRUCK-MARKED BODY\n" + (strike["struck_marked"]["body"] or "-")))
            lines.append(md_block("CLEANED RESIDUAL TITLE\n" + (strike["cleaned_residual"]["title"] or "-")))
            lines.append(md_block("CLEANED RESIDUAL BODY\n" + (strike["cleaned_residual"]["body"] or "-")))
        lines.append("")

        lines.append("**E. Adjudication**")
        adjud = case["adjudication"]
        if adjud is None:
            lines.append("- No ambiguity/adjudication record for this case.")
        else:
            lines.append("```json")
            lines.append(json.dumps(adjud, indent=2, ensure_ascii=False))
            lines.append("```")
        lines.append("")

        lines.append("**F. Final Output Comparison**")
        final = case["final_output_comparison"]
        lines.append("- Deterministic unified:")
        lines.append("```json")
        lines.append(json.dumps(final["deterministic_unified_output"], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("- Adjudicated final:")
        lines.append("```json")
        lines.append(json.dumps(final["adjudicated_final_output"], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("- Reference/golden:")
        lines.append("```json")
        lines.append(json.dumps(final["reference_golden"], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append(f"- Diagnosis: `{final['diagnosis']}`")
        lines.append("")

        lines.append("**G. Case Verdict**")
        lines.append(f"`{case['case_verdict']}`")
        lines.append("")

    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Case | Verdict | Deterministic Count | Adjudicated Count | Reference Present |")
    lines.append("| --- | --- | ---: | ---: | --- |")
    for case in bundle["cases"]:
        final = case["final_output_comparison"]
        lines.append(
            f"| `{case['case_id']}` | `{case['case_verdict']}` | {len(final['deterministic_unified_output'])} | {len(final['adjudicated_final_output'])} | {'yes' if final['reference_golden'] else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    latest = repo / "artifacts/runs/latest"
    artifacts = {
        "reference": load_json(repo / "artifacts/golden/clauses_merged.json"),
        "pages": [PageIR(**row) for row in load_jsonl(latest / "page_ir.jsonl")],
        "layout_profile": load_json(latest / "layout_profile.json"),
        "m2_blocks": load_jsonl(latest / "candidate_blocks_m2.jsonl"),
        "adjudicated_blocks": load_jsonl(latest / "candidate_blocks_adjudicated.jsonl"),
        "ambiguity_cases": load_jsonl(latest / "ambiguity_cases.jsonl"),
        "adjudication_results": load_jsonl(latest / "adjudication_results.jsonl"),
        "deterministic_clauses": load_json(latest / "clauses_unified.json"),
        "adjudicated_clauses": load_json(latest / "clauses_unified_adjudicated.json"),
    }
    bundle = {"cases": [build_case_bundle(case_id, artifacts) for case_id in CASE_IDS]}
    json_path = latest / "manual_priority_cases.json"
    md_path = latest / "manual_priority_cases.md"
    json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
    md_path.write_text(write_markdown(bundle) + "\n")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
