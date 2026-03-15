from __future__ import annotations

import re
from collections import Counter

from charter_parser.config import Settings
from charter_parser.models import CandidateBlock, CandidateLineDecision, LayoutProfile, PageIR
from charter_parser.utils import normalize_ws


CLAUSE_START_RE = re.compile(r"^\s*(\d{1,3})\s*\.\s*(.*)$")
SECTION_PATTERNS = {
    "part2": re.compile(r"\bPART\s+II\b", re.I),
    "shell": re.compile(r"\bSHELL\s+ADDITIONAL\s+CLAUSES\b", re.I),
    "essar": re.compile(r"\bEssar\s+Rider\s+Clauses\b", re.I),
}
HEADER_HINTS = ("issued july", "shellvoy")


def _band_map(page_profile) -> dict[str, tuple[float, float, float]]:
    return {band.name: (band.x0, band.x1, band.confidence) for band in page_profile.bands}


def _word_lookup(page: PageIR) -> dict[str, dict]:
    return {word.word_id: word.model_dump() for word in page.words}


def _join_words(words: list[dict]) -> str:
    return normalize_ws(" ".join(word["text"] for word in sorted(words, key=lambda item: item["x0"])))


def _upper_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch.isupper()) / len(letters)


def _looks_inline_title(text: str, settings: Settings) -> bool:
    text = normalize_ws(text)
    if not text:
        return False
    if len(text) > settings.candidate.inline_title_max_chars:
        return False
    if text.count(",") > settings.candidate.inline_title_max_commas:
        return False
    if text.endswith(":"):
        return False
    if _upper_ratio(text) >= settings.candidate.inline_title_upper_ratio:
        return True
    if "clause" in text.lower():
        return True
    words = text.split()
    if len(words) <= 8 and all(word[:1].isupper() or word.isupper() for word in words if word):
        return True
    return False


def _strip_clause_prefix(text: str) -> str:
    return normalize_ws(re.sub(r"^\s*\d{1,3}\s*\.\s*", "", text or ""))


def _is_header_footer_noise(raw_text: str, line, page: PageIR, settings: Settings) -> tuple[bool, str]:
    lower = raw_text.lower()
    _, y0, _, _ = line.bbox
    if any(token in lower for token in HEADER_HINTS):
        return True, "repeated_header_text"
    if y0 / max(page.height, 1.0) >= settings.candidate.footer_min_y_ratio and raw_text.strip().isdigit():
        return True, "bottom_band_numeric_text"
    return False, ""


def _detect_section_banner(text: str) -> str | None:
    for section, pattern in SECTION_PATTERNS.items():
        if pattern.search(text):
            return section
    return None


def _split_line(line, page: PageIR, page_profile, settings: Settings, words_by_id: dict[str, dict]) -> dict:
    bands = _band_map(page_profile)
    body_band = bands.get("body_band_hint", (line.bbox[0], page.width, 0.0))
    body_x0 = body_band[0]
    right_noise_x = max(
        page.width * settings.candidate.right_noise_min_x_ratio,
        body_band[1] - settings.candidate.title_body_overlap_gap,
    )
    tol = settings.candidate.body_band_left_tolerance
    words = [words_by_id[word_id] for word_id in line.word_ids if word_id in words_by_id]

    left_words: list[dict] = []
    body_words: list[dict] = []
    noise_words: list[dict] = []
    for word in sorted(words, key=lambda item: item["x0"]):
        token = str(word["text"]).strip()
        if token.isdigit() and len(token) <= settings.candidate.right_noise_max_chars and word["x0"] >= right_noise_x:
            noise_words.append(word)
            continue
        if re.fullmatch(r"\d{1,3}\.", token):
            body_words.append(word)
            continue
        if word["x0"] < body_x0 - tol:
            left_words.append(word)
            continue
        body_words.append(word)

    clean_words = [word for word in sorted(words, key=lambda item: item["x0"]) if word not in noise_words]
    return {
        "raw_text": line.text,
        "left_text": _join_words(left_words),
        "body_text": _join_words(body_words),
        "clean_text": _join_words(clean_words),
        "noise_text": _join_words(noise_words),
        "noise_word_count": len(noise_words),
    }


def _start_match(text: str) -> tuple[int, str] | None:
    match = CLAUSE_START_RE.match(text)
    if not match:
        return None
    return int(match.group(1)), normalize_ws(match.group(2))


def _new_block(
    *,
    block_id: str,
    page_index: int,
    page_type: str,
    section_hint: str,
    block_type: str,
    routing_mode: str,
    reasons: list[str],
) -> dict:
    return {
        "block_id": block_id,
        "page": page_index,
        "page_type": page_type,
        "routing_mode": routing_mode,
        "section_hint": section_hint,
        "block_type": block_type,
        "line_ids": [],
        "title_line_ids": [],
        "body_line_ids": [],
        "noise_line_ids": [],
        "line_decisions": [],
        "candidate_clause_id": None,
        "candidate_local_num": None,
        "title_parts": [],
        "body_parts": [],
        "support_score": 0.0,
        "reasons": list(reasons),
    }


def _append_line(block: dict, line, extracted_text: str, labels: list[str], reasons: list[str]) -> None:
    if line.line_id not in block["line_ids"]:
        block["line_ids"].append(line.line_id)
    if "title_line" in labels and line.line_id not in block["title_line_ids"]:
        block["title_line_ids"].append(line.line_id)
    if "body_line" in labels and line.line_id not in block["body_line_ids"]:
        block["body_line_ids"].append(line.line_id)
    if "noise_line" in labels and line.line_id not in block["noise_line_ids"]:
        block["noise_line_ids"].append(line.line_id)
    block["line_decisions"].append(
        CandidateLineDecision(
            line_id=line.line_id,
            raw_text=line.text,
            extracted_text=extracted_text,
            labels=labels,
            reasons=reasons,
        ).model_dump()
    )


def _finalize_block(block: dict) -> CandidateBlock:
    block["title_text"] = normalize_ws(" ".join(block.pop("title_parts")))
    block["body_text"] = "\n".join(part for part in block.pop("body_parts") if part).strip()
    if (
        block["block_type"] == "candidate_clause_start"
        and not block["title_text"]
        and block["body_text"]
        and len(block["body_line_ids"]) <= 1
    ):
        block["title_text"] = _strip_clause_prefix(block["body_text"])
        block["body_text"] = ""
        block["title_line_ids"] = list(block["body_line_ids"])
        block["body_line_ids"] = []
        block["reasons"].append("reclassified_single_short_body_as_title")
    label_bonus = 0.15 if block["title_line_ids"] else 0.0
    body_bonus = 0.1 if block["body_line_ids"] else 0.0
    base = {
        "candidate_clause_start": 0.65,
        "candidate_continuation": 0.55,
        "noise_block": 0.85,
        "section_banner": 0.9,
    }[block["block_type"]]
    block["support_score"] = min(1.0, round(base + label_bonus + body_bonus, 3))
    return CandidateBlock(**block)


def generate_candidate_blocks(pages: list[PageIR], profile: LayoutProfile, settings: Settings) -> tuple[list[CandidateBlock], dict]:
    profile_by_page = {page.page_index: page for page in profile.pages}
    blocks: list[CandidateBlock] = []
    current_block: dict | None = None
    current_section = "part2"
    part2_started = not any(
        SECTION_PATTERNS["part2"].search(line.text)
        for page in pages
        for line in page.lines
    )
    diagnostics = {
        "page_summaries": [],
        "suppressed_right_noise_words": 0,
        "observed_right_noise_words": 0,
        "suppressed_header_footer_lines": 0,
        "observed_header_footer_lines": 0,
        "suspicious_title_lines": 0,
        "title_line_count": 0,
        "suspicious_body_lines": 0,
        "body_line_count": 0,
        "fallback_pages": 0,
    }

    for page in pages:
        page_profile = profile_by_page[page.page_index]
        words_by_id = _word_lookup(page)
        page_summary = {
            "page": page.page_index,
            "page_type": page_profile.page_type,
            "routing_mode": page_profile.page_type,
            "start_blocks": 0,
            "continuation_blocks": 0,
            "noise_blocks": 0,
            "suppressed_right_noise_words": 0,
            "suppressed_header_footer_lines": 0,
        }

        for line in page.lines:
            split = _split_line(line, page, page_profile, settings, words_by_id)
            raw_text = split["raw_text"]
            clean_text = split["clean_text"]
            left_text = split["left_text"]
            body_text = split["body_text"]

            diagnostics["observed_right_noise_words"] += split["noise_word_count"]
            page_summary["suppressed_right_noise_words"] += split["noise_word_count"]
            if split["noise_word_count"]:
                diagnostics["suppressed_right_noise_words"] += split["noise_word_count"]

            if not clean_text:
                if split["noise_word_count"]:
                    noise_block = _new_block(
                        block_id=f"p{page.page_index}_noise_{len(blocks):04d}",
                        page_index=page.page_index,
                        page_type=page_profile.page_type,
                        section_hint=current_section,
                        block_type="noise_block",
                        routing_mode=page_profile.page_type,
                        reasons=["right_noise_only_line"],
                    )
                    _append_line(noise_block, line, "", ["noise_line"], ["right_noise_only_line"])
                    blocks.append(_finalize_block(noise_block))
                    page_summary["noise_blocks"] += 1
                continue

            header_footer_noise, noise_reason = _is_header_footer_noise(raw_text, line, page, settings)
            section_banner = _detect_section_banner(clean_text)

            if not part2_started:
                if section_banner == "part2":
                    part2_started = True
                else:
                    continue

            if section_banner:
                if current_block is not None:
                    blocks.append(_finalize_block(current_block))
                    current_block = None
                current_section = section_banner
                banner_block = _new_block(
                    block_id=f"p{page.page_index}_banner_{len(blocks):04d}",
                    page_index=page.page_index,
                    page_type=page_profile.page_type,
                    section_hint=current_section,
                    block_type="section_banner",
                    routing_mode=page_profile.page_type,
                    reasons=[f"section_banner:{section_banner}"],
                )
                _append_line(
                    banner_block,
                    line,
                    "",
                    ["noise_line", "section_banner"],
                    [f"section_banner:{section_banner}"],
                )
                blocks.append(_finalize_block(banner_block))
                page_summary["noise_blocks"] += 1
                continue

            if header_footer_noise:
                diagnostics["observed_header_footer_lines"] += 1
                diagnostics["suppressed_header_footer_lines"] += 1
                page_summary["suppressed_header_footer_lines"] += 1
                noise_block = _new_block(
                    block_id=f"p{page.page_index}_noise_{len(blocks):04d}",
                    page_index=page.page_index,
                    page_type=page_profile.page_type,
                    section_hint=current_section,
                    block_type="noise_block",
                    routing_mode=page_profile.page_type,
                    reasons=[noise_reason],
                )
                _append_line(noise_block, line, "", ["noise_line"], [noise_reason])
                blocks.append(_finalize_block(noise_block))
                page_summary["noise_blocks"] += 1
                continue

            body_start = _start_match(body_text)
            clean_start = _start_match(clean_text)
            start = body_start or clean_start
            if start:
                if current_block is not None:
                    blocks.append(_finalize_block(current_block))
                    current_block = None
                local_num, remainder = start
                current_block = _new_block(
                    block_id=f"p{page.page_index}_start_{len(blocks):04d}",
                    page_index=page.page_index,
                    page_type=page_profile.page_type,
                    section_hint=current_section,
                    block_type="candidate_clause_start",
                    routing_mode=page_profile.page_type,
                    reasons=[f"start_regex:{local_num}"],
                )
                current_block["candidate_local_num"] = local_num
                current_block["candidate_clause_id"] = f"{current_section}:{local_num}"
                labels = ["candidate_clause_start"]
                reasons = [f"start_regex:{local_num}"]
                body_payload = ""
                if left_text:
                    title_payload = _strip_clause_prefix(left_text)
                    if title_payload:
                        current_block["title_parts"].append(title_payload)
                        labels.append("title_line")
                        reasons.append("left_title_band_text")
                    if body_start:
                        body_payload = remainder
                elif remainder and _looks_inline_title(remainder, settings):
                    current_block["title_parts"].append(remainder)
                    labels.append("title_line")
                    reasons.append("inline_title_like_start")
                else:
                    body_payload = remainder or clean_text
                if body_payload:
                    current_block["body_parts"].append(body_payload)
                    labels.append("body_line")
                    reasons.append("body_payload_from_start")
                _append_line(current_block, line, normalize_ws(remainder or clean_text), labels, reasons)
                page_summary["start_blocks"] += 1
                if "title_line" in labels:
                    diagnostics["title_line_count"] += 1
                    if len(normalize_ws(" ".join(current_block["title_parts"]))) > settings.candidate.title_suspicious_max_chars:
                        diagnostics["suspicious_title_lines"] += 1
                if "body_line" in labels:
                    diagnostics["body_line_count"] += 1
                    body_len = len(current_block["body_parts"][-1]) if current_block["body_parts"] else 0
                    if 0 < body_len <= settings.candidate.body_suspicious_short_chars:
                        diagnostics["suspicious_body_lines"] += 1
                continue

            if current_block is None:
                current_block = _new_block(
                    block_id=f"p{page.page_index}_cont_{len(blocks):04d}",
                    page_index=page.page_index,
                    page_type=page_profile.page_type,
                    section_hint=current_section,
                    block_type="candidate_continuation",
                    routing_mode=page_profile.page_type,
                    reasons=["page_started_with_continuation"],
                )
                page_summary["continuation_blocks"] += 1

            labels = ["candidate_continuation"]
            reasons = ["continuation_body"]
            if left_text and page_profile.page_type == "margin_title_like":
                current_block["title_parts"].append(left_text)
                labels.append("title_line")
                reasons.append("margin_title_continuation")
            body_payload = body_text or clean_text
            if body_payload:
                current_block["body_parts"].append(body_payload)
                labels.append("body_line")
            _append_line(current_block, line, body_payload, labels, reasons)
            if "title_line" in labels:
                diagnostics["title_line_count"] += 1
                if len(left_text) > settings.candidate.title_suspicious_max_chars:
                    diagnostics["suspicious_title_lines"] += 1
            if "body_line" in labels:
                diagnostics["body_line_count"] += 1
                if 0 < len(body_payload) <= settings.candidate.body_suspicious_short_chars:
                    diagnostics["suspicious_body_lines"] += 1

        diagnostics["page_summaries"].append(page_summary)

    if current_block is not None:
        blocks.append(_finalize_block(current_block))

    seen_pages_with_starts = {block.page for block in blocks if block.block_type == "candidate_clause_start"}
    for summary in diagnostics["page_summaries"]:
        if summary["page"] not in seen_pages_with_starts and summary["continuation_blocks"]:
            diagnostics["fallback_pages"] += 1

    metrics = {
        "pages_by_routing_mode": dict(Counter(summary["routing_mode"] for summary in diagnostics["page_summaries"])),
        "candidate_clause_start_count": sum(1 for block in blocks if block.block_type == "candidate_clause_start"),
        "candidate_continuation_count": sum(1 for block in blocks if block.block_type == "candidate_continuation"),
        "noise_block_count": sum(1 for block in blocks if block.block_type in {"noise_block", "section_banner"}),
        "title_line_precision_proxy": round(
            1.0 - (diagnostics["suspicious_title_lines"] / max(1, diagnostics["title_line_count"])),
            4,
        ),
        "body_line_precision_proxy": round(
            1.0 - (diagnostics["suspicious_body_lines"] / max(1, diagnostics["body_line_count"])),
            4,
        ),
        "right_noise_suppression_rate": round(
            diagnostics["suppressed_right_noise_words"] / max(1, diagnostics["observed_right_noise_words"]),
            4,
        ),
        "header_footer_suppression_rate": round(
            diagnostics["suppressed_header_footer_lines"] / max(1, diagnostics["observed_header_footer_lines"]),
            4,
        ),
        "fallback_page_count": diagnostics["fallback_pages"],
    }
    diagnostics["metrics"] = metrics
    return blocks, diagnostics
