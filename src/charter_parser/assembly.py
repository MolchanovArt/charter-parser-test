from __future__ import annotations

from rapidfuzz import fuzz

from charter_parser.models import CandidateBlock, DraftClause
from charter_parser.utils import normalize_ws
from charter_parser.validators import banner_leaks, duplicate_ids, empty_text_ids, order_violations


def _ratio(a: str, b: str) -> float:
    return round(fuzz.ratio(normalize_ws(a), normalize_ws(b)) / 100.0, 4)


def _title_similarity_mean(candidate, reference_by_id) -> float:
    scores = [_ratio(clause.title, reference_by_id[clause.id].title) for clause in candidate if clause.id in reference_by_id]
    return round(sum(scores) / max(1, len(scores)), 4)


def _text_similarity_mean(candidate, reference_by_id) -> float:
    scores = [_ratio(clause.text, reference_by_id[clause.id].text) for clause in candidate if clause.id in reference_by_id]
    return round(sum(scores) / max(1, len(scores)), 4)


def assemble_draft_clauses(blocks: list[CandidateBlock]) -> tuple[list[DraftClause], dict]:
    clauses: list[DraftClause] = []
    current: dict | None = None
    failures: list[dict] = []

    for block in blocks:
        if block.block_type in {"noise_block", "section_banner"}:
            continue

        if block.block_type == "candidate_clause_start":
            if current is not None:
                clauses.append(DraftClause(**current))
            if block.candidate_local_num is None or block.candidate_clause_id is None:
                failures.append({"type": "start_without_id", "block_id": block.block_id, "page": block.page})
                current = None
                continue
            current = {
                "order": len(clauses) + 1,
                "section": block.section_hint,
                "local_num": block.candidate_local_num,
                "id": block.candidate_clause_id,
                "title": normalize_ws(block.title_text),
                "text": block.body_text.strip(),
                "page_start": block.page,
                "page_end": block.page,
                "candidate_block_ids": [block.block_id],
                "title_line_ids": list(block.title_line_ids),
                "body_line_ids": list(block.body_line_ids),
                "support_score": block.support_score,
            }
            continue

        if current is None:
            failures.append({"type": "orphan_continuation", "block_id": block.block_id, "page": block.page})
            continue

        current["page_end"] = block.page
        current["candidate_block_ids"].append(block.block_id)
        current["title_line_ids"].extend(line_id for line_id in block.title_line_ids if line_id not in current["title_line_ids"])
        current["body_line_ids"].extend(line_id for line_id in block.body_line_ids if line_id not in current["body_line_ids"])
        if block.title_text:
            current["title"] = normalize_ws(f"{current['title']} {block.title_text}")
        if block.body_text:
            current["text"] = "\n".join(part for part in [current["text"], block.body_text] if part).strip()
        current["support_score"] = round((current["support_score"] + block.support_score) / 2.0, 3)

    if current is not None:
        clauses.append(DraftClause(**current))

    metrics = {
        "unified_clause_count": len(clauses),
        "duplicate_ids": duplicate_ids(clauses),
        "order_violations": order_violations(clauses),
        "banner_leaks": banner_leaks(clauses),
        "empty_text_ids": empty_text_ids(clauses),
        "near_empty_clause_ids": [clause.id for clause in clauses if 0 < len(normalize_ws(clause.text)) <= 24],
        "orphan_continuation_count": sum(1 for failure in failures if failure["type"] == "orphan_continuation"),
    }
    return clauses, {"metrics": metrics, "failures": failures}


def compare_clause_sets(candidate: list, reference: list) -> dict:
    reference_ids = [item.id for item in reference]
    candidate_ids = [item.id for item in candidate]
    reference_by_id = {item.id: item for item in reference}
    candidate_by_id = {item.id: item for item in candidate}
    overlap = [cid for cid in candidate_ids if cid in reference_by_id]
    title_exact = sum(1 for cid in overlap if normalize_ws(candidate_by_id[cid].title) == normalize_ws(reference_by_id[cid].title))
    text_exact = sum(1 for cid in overlap if normalize_ws(candidate_by_id[cid].text) == normalize_ws(reference_by_id[cid].text))
    sequence_matches = sum(1 for ref_id, cand_id in zip(reference_ids, candidate_ids) if ref_id == cand_id)
    return {
        "count": len(candidate),
        "count_delta": len(candidate) - len(reference),
        "missing_ids": [item_id for item_id in reference_ids if item_id not in candidate_by_id],
        "extra_ids": [item_id for item_id in candidate_ids if item_id not in reference_by_id],
        "id_sequence_match_ratio": round(sequence_matches / max(1, min(len(reference_ids), len(candidate_ids))), 4),
        "title_exact_match_ratio": round(title_exact / max(1, len(overlap)), 4),
        "text_exact_match_ratio": round(text_exact / max(1, len(overlap)), 4),
        "title_similarity_mean": _title_similarity_mean(candidate, reference_by_id),
        "text_similarity_mean": _text_similarity_mean(candidate, reference_by_id),
    }


def worst_mismatches(candidate: list, reference: list, limit: int = 8) -> list[dict]:
    reference_by_id = {item.id: item for item in reference}
    rows: list[dict] = []
    for clause in candidate:
        if clause.id not in reference_by_id:
            rows.append(
                {
                    "id": clause.id,
                    "page_start": clause.page_start,
                    "title_similarity": 0.0,
                    "text_similarity": 0.0,
                    "reason": "extra_id",
                }
            )
            continue
        ref = reference_by_id[clause.id]
        rows.append(
            {
                "id": clause.id,
                "page_start": clause.page_start,
                "title_similarity": _ratio(clause.title, ref.title),
                "text_similarity": _ratio(clause.text, ref.text),
                "reason": "overlap",
                "unified_title": clause.title[:120],
                "reference_title": ref.title[:120],
                "unified_text_head": normalize_ws(clause.text)[:180],
                "reference_text_head": normalize_ws(ref.text)[:180],
            }
        )
    rows.sort(key=lambda item: (item["text_similarity"], item["title_similarity"]))
    return rows[:limit]
