from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from charter_parser.config import Settings
from charter_parser.llm import OpenAIResponsesClient
from charter_parser.models import (
    AdjudicationResult,
    AmbiguityCase,
    AmbiguityContextLine,
    CandidateBlock,
    PageIR,
    StructuredAdjudicationDecision,
)
from charter_parser.utils import normalize_ws


DECIMAL_START_RE = re.compile(r"^\s*\d{1,3}\.\d")
TOP_LEVEL_START_RE = re.compile(r"^\s*(\d{1,3})\s*\.\s*(.*)$")
NESTED_ITEM_START_RE = re.compile(r"^\s*(\d{1,3})\s*\)\s*(.*)$")
INLINE_BANNER_REF_RE = re.compile(r"\b(?:clause|part\s+ii|shellvoy|shell|essar)\b", re.I)
KNOWN_SECTIONS = ("part2", "shell", "essar")


def _upper_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch.isupper()) / len(letters)


def _value(row: Any, key: str):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key)


def _build_line_lookup(pages: list[PageIR]) -> tuple[dict[str, Any], dict[str, tuple[int, int]]]:
    lines_by_id: dict[str, Any] = {}
    positions: dict[str, tuple[int, int]] = {}
    for page in pages:
        for idx, line in enumerate(page.lines):
            lines_by_id[line.line_id] = line
            positions[line.line_id] = (page.page_index, idx)
    return lines_by_id, positions


def _context_window(
    pages_by_index: dict[int, PageIR],
    positions: dict[str, tuple[int, int]],
    line_id: str,
    settings: Settings,
) -> list[AmbiguityContextLine]:
    if line_id not in positions:
        return []
    page_index, line_index = positions[line_id]
    page = pages_by_index[page_index]
    start = max(0, line_index - settings.adjudication.context_lines_before)
    end = min(len(page.lines), line_index + settings.adjudication.context_lines_after + 1)
    return [
        AmbiguityContextLine(line_id=row.line_id, page=row.page, text=row.text)
        for row in page.lines[start:end]
    ]


def _candidate_lines(block: CandidateBlock, lines_by_id: dict[str, Any]) -> list[AmbiguityContextLine]:
    rows: list[AmbiguityContextLine] = []
    for line_id in block.line_ids:
        line = lines_by_id.get(line_id)
        if line is None:
            continue
        rows.append(AmbiguityContextLine(line_id=line.line_id, page=line.page, text=line.text))
    return rows


def _first_line(block: CandidateBlock, lines_by_id: dict[str, Any]):
    if not block.line_ids:
        return None
    return lines_by_id.get(block.line_ids[0])


def _next_clause_id(blocks: list[CandidateBlock], start_index: int) -> str | None:
    for block in blocks[start_index + 1 :]:
        if block.block_type == "candidate_clause_start" and block.candidate_clause_id:
            return block.candidate_clause_id
    return None


def _nested_numbering_evidence(block: CandidateBlock, previous_local_num: int | None, settings: Settings) -> list[str]:
    if block.block_type != "candidate_clause_start" or not block.line_decisions:
        return []

    evidence: list[str] = []
    raw_text = _value(block.line_decisions[0], "raw_text")
    local_num = block.candidate_local_num

    if DECIMAL_START_RE.match(raw_text):
        evidence.append("decimal_like_start_prefix")
    if (
        previous_local_num is not None
        and local_num is not None
        and previous_local_num >= settings.adjudication.nested_restart_prev_min_local_num
        and local_num <= settings.adjudication.nested_restart_candidate_max_local_num
    ):
        evidence.append("low_number_restart_after_high_clause")
    if (
        previous_local_num is not None
        and local_num is not None
        and local_num <= settings.adjudication.nested_restart_candidate_max_local_num
        and local_num < previous_local_num
    ):
        evidence.append("numbering_backtrack_in_section")
    return evidence


def _false_banner_evidence(block: CandidateBlock, line, page: PageIR, settings: Settings) -> list[str]:
    text = normalize_ws(line.text)
    evidence: list[str] = []
    y_ratio = line.bbox[1] / max(page.height, 1.0)
    if y_ratio >= settings.adjudication.banner_midpage_min_y_ratio:
        evidence.append("midpage_banner_hit")
    if (
        len(text) >= settings.adjudication.banner_sentence_min_chars
        and _upper_ratio(text) <= settings.adjudication.banner_sentence_max_upper_ratio
        and any(ch.islower() for ch in text)
    ):
        evidence.append("sentence_like_banner_hit")
    if INLINE_BANNER_REF_RE.search(text) and any(ch.islower() for ch in text):
        evidence.append("inline_reference_terms")
    return evidence


def extract_ambiguity_cases(pages: list[PageIR], blocks: list[CandidateBlock], settings: Settings) -> list[AmbiguityCase]:
    pages_by_index = {page.page_index: page for page in pages}
    lines_by_id, positions = _build_line_lookup(pages)
    cases: list[AmbiguityCase] = []
    previous_local_by_section: dict[str, int] = {}
    previous_clause_id: str | None = None
    current_section = "part2"

    for idx, block in enumerate(blocks):
        first_line = _first_line(block, lines_by_id)
        if first_line is None:
            continue

        if block.block_type == "section_banner":
            page = pages_by_index[first_line.page]
            evidence = _false_banner_evidence(block, first_line, page, settings)
            if {"midpage_banner_hit", "inline_reference_terms"} <= set(evidence) or {
                "midpage_banner_hit",
                "sentence_like_banner_hit",
            } <= set(evidence):
                cases.append(
                    AmbiguityCase(
                        case_id=f"{block.block_id}:false_banner_section",
                        bucket="false_banner_section",
                        page=block.page,
                        block_id=block.block_id,
                        line_id=first_line.line_id,
                        section_hint=block.section_hint,
                        previous_section=current_section,
                        previous_clause_id=previous_clause_id,
                        next_clause_id=_next_clause_id(blocks, idx),
                        candidate_line_ids=list(block.line_ids),
                        candidate_lines=_candidate_lines(block, lines_by_id),
                        line_window=_context_window(pages_by_index, positions, first_line.line_id, settings),
                        evidence=evidence,
                    )
                )
            current_section = block.section_hint
            continue

        if block.block_type == "candidate_clause_start":
            previous_local_num = previous_local_by_section.get(block.section_hint)
            evidence = _nested_numbering_evidence(block, previous_local_num, settings)
            if evidence:
                cases.append(
                    AmbiguityCase(
                        case_id=f"{block.block_id}:nested_numbering",
                        bucket="nested_numbering",
                        page=block.page,
                        block_id=block.block_id,
                        line_id=first_line.line_id,
                        candidate_clause_id=block.candidate_clause_id,
                        candidate_local_num=block.candidate_local_num,
                        section_hint=block.section_hint,
                        previous_section=current_section,
                        previous_clause_id=previous_clause_id,
                        next_clause_id=_next_clause_id(blocks, idx),
                        candidate_line_ids=list(block.line_ids),
                        candidate_lines=_candidate_lines(block, lines_by_id),
                        line_window=_context_window(pages_by_index, positions, first_line.line_id, settings),
                        evidence=evidence,
                    )
                )
            if block.candidate_local_num is not None:
                previous_local_by_section[block.section_hint] = block.candidate_local_num
            if block.candidate_clause_id:
                previous_clause_id = block.candidate_clause_id
            current_section = block.section_hint

    return cases[: settings.adjudication.max_cases_per_run]


def build_case_payload(case: AmbiguityCase) -> dict[str, Any]:
    return {
        "bucket": case.bucket,
        "page": case.page,
        "block_id": case.block_id,
        "candidate_clause_id": case.candidate_clause_id,
        "candidate_local_num": case.candidate_local_num,
        "section_hint": case.section_hint,
        "previous_section": case.previous_section,
        "previous_clause_id": case.previous_clause_id,
        "next_clause_id": case.next_clause_id,
        "evidence": case.evidence,
        "candidate_lines": [row.model_dump() for row in case.candidate_lines],
        "line_window": [row.model_dump() for row in case.line_window],
        "allowed_section_hints": list(KNOWN_SECTIONS),
        "allowed_line_ids": list(case.candidate_line_ids),
    }


def _decision_effect(decision: StructuredAdjudicationDecision | None) -> str:
    if decision is None:
        return "kept_deterministic"
    if decision.attach_to_previous:
        return "attach_to_previous"
    if decision.candidate_start:
        return "candidate_start"
    return "section_switch"


def _line_ids_valid(case: AmbiguityCase, decision: StructuredAdjudicationDecision) -> bool:
    allowed = set(case.candidate_line_ids)
    chosen = set(decision.title_line_ids) | set(decision.body_line_ids)
    return chosen.issubset(allowed)


def _structured_decision_schema() -> dict[str, Any]:
    schema = StructuredAdjudicationDecision.model_json_schema()
    properties = schema.get("properties", {})
    schema["required"] = list(properties.keys())
    schema["additionalProperties"] = False
    return schema


def run_structured_adjudication(
    cases: list[AmbiguityCase],
    settings: Settings,
    *,
    client: OpenAIResponsesClient | None = None,
) -> list[AdjudicationResult]:
    if not cases:
        return []

    if not settings.adjudication.enabled or not settings.llm.enabled:
        return [
            AdjudicationResult(
                case_id=case.case_id,
                bucket=case.bucket,
                page=case.page,
                block_id=case.block_id,
                status="skipped_disabled",
            )
            for case in cases
        ]

    if not os.getenv("OPENAI_API_KEY"):
        return [
            AdjudicationResult(
                case_id=case.case_id,
                bucket=case.bucket,
                page=case.page,
                block_id=case.block_id,
                status="skipped_missing_api_key",
            )
            for case in cases
        ]

    llm_client = client or OpenAIResponsesClient(model=settings.llm.model_primary, store=settings.llm.store)
    schema = _structured_decision_schema()
    system_prompt = (
        "You resolve only local parser ambiguity in a geometry-first charter parser. "
        "Never rewrite clause text. Use only the provided line_ids and section hints. "
        "Set candidate_start=true only when the candidate block is a genuine clause start. "
        "Set attach_to_previous=true only when the candidate lines should stay with the previous clause. "
        "For attach_to_previous decisions, put any carried text lines in body_line_ids and usually leave title_line_ids empty. "
        "For false banner cases, keep candidate_start=false. "
        "Return concise reason_short."
    )

    results: list[AdjudicationResult] = []
    for case in cases:
        payload = build_case_payload(case)
        try:
            raw = llm_client.json_response(
                system_prompt=system_prompt,
                user_payload=payload,
                schema_name="structured_adjudication_decision",
                schema=schema,
                reasoning_effort=settings.llm.reasoning_effort_primary,
            )
            decision = StructuredAdjudicationDecision(**raw)
            if not _line_ids_valid(case, decision):
                results.append(
                    AdjudicationResult(
                        case_id=case.case_id,
                        bucket=case.bucket,
                        page=case.page,
                        block_id=case.block_id,
                        status="rejected_invalid_line_ids",
                        decision=decision,
                        effect=_decision_effect(decision),
                    )
                )
                continue
            applied = decision.confidence >= settings.llm.adjudicate_confidence
            results.append(
                AdjudicationResult(
                    case_id=case.case_id,
                    bucket=case.bucket,
                    page=case.page,
                    block_id=case.block_id,
                    status="accepted" if applied else "rejected_low_confidence",
                    applied=applied,
                    decision=decision,
                    effect=_decision_effect(decision),
                )
            )
        except Exception as exc:
            results.append(
                AdjudicationResult(
                    case_id=case.case_id,
                    bucket=case.bucket,
                    page=case.page,
                    block_id=case.block_id,
                    status="error",
                    error=str(exc),
                )
            )
    return results


def _ordered_subset(source: list[str], selected: list[str]) -> list[str]:
    chosen = set(selected)
    return [line_id for line_id in source if line_id in chosen]


def _first_selected_line(block: dict[str, Any]) -> dict[str, Any] | None:
    selected = set(block.get("title_line_ids", [])) | set(block.get("body_line_ids", []))
    for row in block.get("line_decisions", []):
        if row["line_id"] in selected:
            return row
    return None


def _is_nested_item_candidate_start(block: dict[str, Any]) -> bool:
    row = _first_selected_line(block)
    if row is None:
        return False
    text = row.get("clean_text") or row.get("raw_text") or ""
    return bool(NESTED_ITEM_START_RE.match(text)) and not bool(TOP_LEVEL_START_RE.match(text))


def _convert_start_block_to_continuation(payload: dict[str, Any], *, note: str, confidence: float) -> None:
    payload["block_type"] = "candidate_continuation"
    payload["candidate_clause_id"] = None
    payload["candidate_local_num"] = None
    payload["title_line_ids"] = []
    payload["body_line_ids"] = payload["line_ids"]
    _rewrite_line_decisions(
        payload,
        block_type="candidate_continuation",
        title_line_ids=payload["title_line_ids"],
        body_line_ids=payload["body_line_ids"],
        note=note,
    )
    _rebuild_block_text(payload, body_from_clean=True)
    payload["reasons"] = list(dict.fromkeys(payload["reasons"] + [note]))
    payload["support_score"] = round((payload["support_score"] + confidence) / 2.0, 3)


def _rewrite_line_decisions(
    block: dict[str, Any],
    *,
    block_type: str,
    title_line_ids: list[str],
    body_line_ids: list[str],
    note: str,
) -> None:
    title_set = set(title_line_ids)
    body_set = set(body_line_ids)
    for row in block["line_decisions"]:
        labels: list[str] = []
        if block_type == "candidate_clause_start":
            labels.append("candidate_clause_start")
        elif block_type == "candidate_continuation":
            labels.append("candidate_continuation")
        elif block_type == "section_banner":
            labels.extend(["noise_line", "section_banner"])
        if row["line_id"] in title_set:
            labels.append("title_line")
        if row["line_id"] in body_set:
            labels.append("body_line")
        if block_type in {"noise_block", "section_banner"} and row["line_id"] not in body_set:
            labels.append("noise_line")
        row["labels"] = list(dict.fromkeys(labels))
        if note not in row["reasons"]:
            row["reasons"].append(note)


def _rebuild_block_text(block: dict[str, Any], *, body_from_clean: bool) -> None:
    decisions = {row["line_id"]: row for row in block["line_decisions"]}
    title_parts: list[str] = []
    body_parts: list[str] = []

    for line_id in block["line_ids"]:
        row = decisions.get(line_id)
        if row is None:
            continue
        if line_id in set(block["title_line_ids"]):
            title_text = normalize_ws(row.get("title_text") or row.get("clean_text") or row.get("raw_text") or "")
            if title_text:
                title_parts.append(title_text)
        if line_id in set(block["body_line_ids"]):
            if body_from_clean:
                source_text = row.get("clean_text") or row.get("raw_text") or ""
            else:
                source_text = row.get("body_text") or row.get("clean_text") or row.get("raw_text") or ""
            payload = normalize_ws(source_text)
            if payload:
                body_parts.append(payload)

    block["title_text"] = normalize_ws(" ".join(title_parts))
    block["body_text"] = "\n".join(body_parts).strip()


def apply_adjudication_to_blocks(
    blocks: list[CandidateBlock],
    results: list[AdjudicationResult],
) -> tuple[list[CandidateBlock], dict[str, Any]]:
    results_by_block = {row.block_id: row for row in results if row.applied and row.decision is not None}
    adjusted: list[CandidateBlock] = []
    current_section = "part2"
    effects = Counter()

    for block in blocks:
        payload = block.model_dump()
        result = results_by_block.get(block.block_id)
        note = None if result is None or result.decision is None else f"adjudicated:{result.decision.reason_short}"

        if not adjusted and payload["block_type"] != "section_banner":
            current_section = payload["section_hint"]

        if payload["block_type"] == "section_banner":
            if result is not None and result.decision is not None and result.decision.attach_to_previous:
                payload["block_type"] = "candidate_continuation"
                payload["section_hint"] = current_section
                payload["candidate_clause_id"] = None
                payload["candidate_local_num"] = None
                payload["title_line_ids"] = []
                payload["noise_line_ids"] = []
                body_line_ids = result.decision.body_line_ids or payload["line_ids"]
                payload["body_line_ids"] = _ordered_subset(payload["line_ids"], body_line_ids)
                _rewrite_line_decisions(
                    payload,
                    block_type="candidate_continuation",
                    title_line_ids=payload["title_line_ids"],
                    body_line_ids=payload["body_line_ids"],
                    note=note or "adjudicated_attach_to_previous",
                )
                _rebuild_block_text(payload, body_from_clean=True)
                payload["support_score"] = round((payload["support_score"] + result.decision.confidence) / 2.0, 3)
                payload["reasons"] = list(dict.fromkeys(payload["reasons"] + [note or "adjudicated_attach_to_previous"]))
                effects["banner_attach_to_previous"] += 1
            else:
                if result is not None and result.decision is not None and result.decision.section_hint:
                    payload["section_hint"] = result.decision.section_hint
                current_section = payload["section_hint"]
            adjusted.append(CandidateBlock(**payload))
            continue

        payload["section_hint"] = current_section
        if payload["block_type"] == "candidate_clause_start" and payload["candidate_local_num"] is not None:
            payload["candidate_clause_id"] = f"{payload['section_hint']}:{payload['candidate_local_num']}"

        if result is not None and result.decision is not None and payload["block_type"] == "candidate_clause_start":
            if result.decision.attach_to_previous:
                payload["title_line_ids"] = []
                body_line_ids = result.decision.body_line_ids or payload["line_ids"]
                payload["body_line_ids"] = _ordered_subset(payload["line_ids"], body_line_ids)
                _convert_start_block_to_continuation(
                    payload,
                    note=note or "adjudicated_attach_to_previous",
                    confidence=result.decision.confidence,
                )
                effects["start_attach_to_previous"] += 1
            elif result.decision.candidate_start:
                if result.decision.section_hint:
                    payload["section_hint"] = result.decision.section_hint
                payload["candidate_clause_id"] = f"{payload['section_hint']}:{payload['candidate_local_num']}"
                if result.decision.title_line_ids:
                    payload["title_line_ids"] = _ordered_subset(payload["line_ids"], result.decision.title_line_ids)
                if result.decision.body_line_ids:
                    payload["body_line_ids"] = _ordered_subset(payload["line_ids"], result.decision.body_line_ids)
                _rewrite_line_decisions(
                    payload,
                    block_type="candidate_clause_start",
                    title_line_ids=payload["title_line_ids"],
                    body_line_ids=payload["body_line_ids"],
                    note=note or "adjudicated_candidate_start",
                )
                _rebuild_block_text(payload, body_from_clean=False)
                if _is_nested_item_candidate_start(payload):
                    _convert_start_block_to_continuation(
                        payload,
                        note=note or "adjudicated_nested_item_attach",
                        confidence=result.decision.confidence,
                    )
                    effects["start_attach_to_previous"] += 1
                else:
                    payload["reasons"] = list(dict.fromkeys(payload["reasons"] + [note or "adjudicated_candidate_start"]))
                    payload["support_score"] = round((payload["support_score"] + result.decision.confidence) / 2.0, 3)
                    effects["candidate_start_kept"] += 1

        if payload["block_type"] == "candidate_clause_start":
            current_section = payload["section_hint"]
        adjusted.append(CandidateBlock(**payload))

    metrics = {
        "applied_block_count": sum(effects.values()),
        "effects": dict(effects),
    }
    return adjusted, metrics


def adjudication_metrics(results: list[AdjudicationResult]) -> dict[str, Any]:
    status_counts = Counter(row.status for row in results)
    effect_counts = Counter(row.effect for row in results if row.applied)
    return {
        "ambiguity_case_count": len(results),
        "accepted_case_count": status_counts["accepted"],
        "applied_case_count": sum(1 for row in results if row.applied),
        "cases_by_bucket": dict(Counter(row.bucket for row in results)),
        "status_counts": dict(status_counts),
        "applied_effect_counts": dict(effect_counts),
    }
