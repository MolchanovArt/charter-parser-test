#!/usr/bin/env python3
"""
Extract second-part rider clauses from charterparty PDF.

Target range:
- pages 17..38 (0-based page indices)

Sections:
- shell
- essar

Behavior:
- parse words with coordinates
- build visual lines
- detect strike segments from PDF vector graphics
- remove struck words, drop fully struck lines
- suppress fully struck blocks, but keep strong live clause starts (e.g. 38, 43)
- stream clauses across pages
- switch section on banners
- keep heading-only clauses
- skip dead num-only clauses
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pdfplumber


# -----------------------------
# Logging
# -----------------------------
logger = logging.getLogger("extract_second_part")


# -----------------------------
# Defaults
# -----------------------------
DEFAULT_X_THRESH = 520
DEFAULT_Y_TOL = 3.0
DEFAULT_Y_BAND = 1.1
DEFAULT_MIN_WORD_COV = 0.45
DEFAULT_DROP_LINE_COV = 0.85

DEFAULT_BLOCK_START_THR = 0.60
DEFAULT_BLOCK_END_THR = 0.25
DEFAULT_BLOCK_MAX_RUN = 80
DEFAULT_BLOCK_END_PATIENCE = 3
DEFAULT_KEEP_CLAUSE_COV = 0.10

P2_FROM = 17
P2_TO = 38


# -----------------------------
# Regex
# -----------------------------
RX_SHELL = re.compile(r"\bSHELL\s+ADDITIONAL\s+CLAUSES\b", re.I)
RX_ESSAR = re.compile(r"\bEssar\s+Rider\s+Clauses\b", re.I)

# tolerant to spaces around dot: "22 .BILL ..."
RX_START_INLINE = re.compile(r"^\s*(\d{1,3})\s*\.\s*(.+?)\s*$")
RX_NUM_ONLY = re.compile(r"^\s*(\d{1,3})\s*\.\s*$")

# not clause starts
RX_SUBCLAUSE_PAREN = re.compile(r"^\s*(\d{1,3})\)\s+")
RX_ROMAN_SUB = re.compile(r"^\s*(i{1,4}|v|vi{0,3}|ix|x)\)\s+", re.I)
RX_ALPHA_PAREN = re.compile(r"^\s*\([A-Z]\)\s+", re.I)
RX_ALPHA_DOT = re.compile(r"^\s*[A-Z]\.\s+")

RX_LOWER_START = re.compile(r"^[a-z]")
RX_END_COMMA = re.compile(r",$")

# Rest after "N." must not look like a decimal continuation (e.g. "50 MIO)" from "21.50 MIO)")
RX_DECIMAL_CONTINUATION = re.compile(r"^\d+\.?\d*\b")
KNOWN_STRUCK_LEAK_PATTERNS = [
    re.compile(r"\bANY TIME SAVED TO BE SHARED 50/50\b", re.I),
]


# -----------------------------
# PDF line utilities
# -----------------------------
def words_df(page: pdfplumber.page.Page) -> pd.DataFrame:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    df = pd.DataFrame(words)
    if df.empty:
        return df
    for c in ["x0", "x1", "top", "bottom"]:
        df[c] = df[c].astype(float)
    df["x_center"] = (df["x0"] + df["x1"]) / 2.0
    df["y_center"] = (df["top"] + df["bottom"]) / 2.0
    df["text"] = df["text"].astype(str)
    df["is_digit"] = df["text"].str.fullmatch(r"\d+").fillna(False)
    return df


def group_lines_words(df_words: pd.DataFrame, y_tol: float = DEFAULT_Y_TOL) -> List[pd.DataFrame]:
    if df_words.empty:
        return []
    df2 = df_words.sort_values(["top", "x0"]).copy()

    lines: List[pd.DataFrame] = []
    current: List[pd.Series] = []
    current_top: Optional[float] = None

    for _, r in df2.iterrows():
        t = float(r["top"])
        if current_top is None or abs(t - current_top) <= y_tol:
            current.append(r)
            current_top = t if current_top is None else (0.8 * current_top + 0.2 * t)
        else:
            lines.append(pd.DataFrame(current).sort_values("x0"))
            current = [r]
            current_top = t

    if current:
        lines.append(pd.DataFrame(current).sort_values("x0"))

    lines.sort(key=lambda ln: float(ln["top"].min()))
    return lines


def line_text(ln: pd.DataFrame) -> str:
    return " ".join(ln["text"].tolist()).strip() if ln is not None and not ln.empty else ""


def remove_right_linenumber_tokens(ln: pd.DataFrame, x_thresh: float = DEFAULT_X_THRESH) -> pd.DataFrame:
    return ln[~(ln["is_digit"] & (ln["x_center"] >= x_thresh))].copy()


def bbox_of_df(df: pd.DataFrame) -> Optional[Tuple[float, float, float, float]]:
    if df is None or df.empty:
        return None
    return (
        float(df["x0"].min()),
        float(df["x1"].max()),
        float(df["top"].min()),
        float(df["bottom"].max()),
    )


# -----------------------------
# Section helpers
# -----------------------------
def detect_section(line: str, current: str = "shell") -> str:
    if RX_SHELL.search(line):
        return "shell"
    if RX_ESSAR.search(line):
        return "essar"
    return current


# -----------------------------
# Clause start helpers
# -----------------------------
def is_clause_start_line(line: str) -> bool:
    t = (line or "").strip()
    if not t:
        return False
    if RX_SUBCLAUSE_PAREN.match(t) or RX_ROMAN_SUB.match(t) or RX_ALPHA_PAREN.match(t) or RX_ALPHA_DOT.match(t):
        return False
    return bool(RX_START_INLINE.match(t) or RX_NUM_ONLY.match(t))


def title_score(s: str, section: str) -> int:
    s = (s or "").strip()
    if not s:
        return -999

    max_len = 90 if section != "essar" else 130
    if len(s) > max_len:
        return -3

    score = 0
    if "clause" in s.lower():
        score += 2
    if not s.endswith("."):
        score += 1

    punct = sum(1 for ch in s if ch in ",;:")
    if punct <= 1:
        score += 1
    if punct >= 3:
        score -= 2

    if s.upper() == s and any(ch.isalpha() for ch in s) and len(s) <= 60:
        score += 2
    if len(s) <= 60:
        score += 1

    return score


def is_title_candidate(s: str, section: str) -> bool:
    return title_score(s, section) >= 2


def is_title_candidate_for_num_only(s: str, section: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False

    max_len = 90 if section != "essar" else 130
    if len(s) > max_len:
        return False

    # structural, no keyword blacklist
    if RX_LOWER_START.match(s):
        return False
    if RX_END_COMMA.search(s):
        return False

    return is_title_candidate(s, section)


def is_inline_heading_strict(rest: str) -> bool:
    rest = (rest or "").strip()
    if not rest:
        return False
    if len(rest) > 80:
        return False
    if rest.endswith("."):
        return False
    return True


def is_inline_heading_or_title_with_period(rest: str, section: str) -> bool:
    """Allow inline title when rest ends with a period, if the line is title-like."""
    rest = (rest or "").strip()
    if not rest:
        return False
    if is_inline_heading_strict(rest):
        return True
    if rest.endswith(".") and len(rest) <= 80:
        rest_no_dot = rest[:-1].strip()
        if rest_no_dot and is_title_candidate(rest_no_dot, section):
            return True
    return False


def is_real_clause_start_line(line: str) -> bool:
    """Stricter clause start: exclude subclauses and decimal continuations (e.g. 21.50 MIO))."""
    t = (line or "").strip()
    if not t:
        return False
    if RX_SUBCLAUSE_PAREN.match(t) or RX_ROMAN_SUB.match(t) or RX_ALPHA_PAREN.match(t) or RX_ALPHA_DOT.match(t):
        return False
    m = RX_START_INLINE.match(t)
    if m:
        rest = (m.group(2) or "").strip()
        if RX_DECIMAL_CONTINUATION.match(rest):
            return False
        return True
    if RX_NUM_ONLY.match(t):
        return True
    return False


# -----------------------------
# Vector strike detection
# -----------------------------
def collect_vector_strike_segments(
    page: pdfplumber.page.Page,
    eps_y: float = 0.8,
    min_w: float = 25.0,
    rect_max_h: float = 1.5,
    rect_min_w: float = 25.0,
) -> List[Tuple[float, float, float]]:
    segs: List[Tuple[float, float, float]] = []
    H = float(page.height)

    for obj in (page.edges or []):
        x0, x1 = obj.get("x0"), obj.get("x1")
        y0, y1 = obj.get("y0"), obj.get("y1")
        if x0 is None or x1 is None or y0 is None or y1 is None:
            continue
        if abs(float(y0) - float(y1)) > eps_y:
            continue
        a, b = float(min(x0, x1)), float(max(x0, x1))
        if (b - a) < min_w:
            continue
        y_top = H - float(y0)
        segs.append((y_top, a, b))

    for obj in (getattr(page, "lines", []) or []):
        x0, x1 = obj.get("x0"), obj.get("x1")
        top, bottom = obj.get("top"), obj.get("bottom")
        if x0 is None or x1 is None:
            continue
        a, b = float(min(x0, x1)), float(max(x0, x1))
        if (b - a) < min_w:
            continue

        if top is not None and bottom is not None:
            if abs(float(bottom) - float(top)) > eps_y:
                continue
            y_top = (float(top) + float(bottom)) / 2.0
            segs.append((y_top, a, b))
        else:
            y0, y1 = obj.get("y0"), obj.get("y1")
            if y0 is None or y1 is None:
                continue
            if abs(float(y0) - float(y1)) > eps_y:
                continue
            y_top = H - float(y0)
            segs.append((y_top, a, b))

    for obj in (page.rects or []):
        x0, x1 = obj.get("x0"), obj.get("x1")
        top, bottom = obj.get("top"), obj.get("bottom")
        if x0 is None or x1 is None or top is None or bottom is None:
            continue
        h = abs(float(bottom) - float(top))
        w = abs(float(x1) - float(x0))
        if h <= rect_max_h and w >= rect_min_w:
            y_top = (float(top) + float(bottom)) / 2.0
            segs.append((y_top, float(min(x0, x1)), float(max(x0, x1))))

    segs.sort()
    return segs


def strike_union_coverage(bbox: Optional[Tuple[float, float, float, float]], segs: List[Tuple[float, float, float]], y_band: float = DEFAULT_Y_BAND) -> Tuple[float, int]:
    if bbox is None:
        return 0.0, 0
    x0, x1, top, bottom = bbox
    if x1 <= x0:
        return 0.0, 0
    y_mid = (top + bottom) / 2.0
    width = x1 - x0

    intervals: List[Tuple[float, float]] = []
    for y, a, b in segs:
        if abs(y - y_mid) <= y_band:
            lo = max(x0, a)
            hi = min(x1, b)
            if hi > lo:
                intervals.append((lo, hi))

    if not intervals:
        return 0.0, 0

    intervals.sort()
    merged: List[Tuple[float, float]] = []
    lo, hi = intervals[0]
    for a2, b2 in intervals[1:]:
        if a2 <= hi + 1.0:
            hi = max(hi, b2)
        else:
            merged.append((lo, hi))
            lo, hi = a2, b2
    merged.append((lo, hi))

    covered = sum(b2 - a2 for a2, b2 in merged)
    return covered / width, len(intervals)


def word_is_struck(word_row: pd.Series, segs: List[Tuple[float, float, float]], y_band: float = DEFAULT_Y_BAND, min_cov: float = DEFAULT_MIN_WORD_COV) -> bool:
    wx0, wx1 = float(word_row["x0"]), float(word_row["x1"])
    top, bottom = float(word_row["top"]), float(word_row["bottom"])
    if wx1 <= wx0:
        return False

    y_mid = (top + bottom) / 2.0
    w = wx1 - wx0

    for y, sx0, sx1 in segs:
        if abs(y - y_mid) <= y_band:
            overlap = max(0.0, min(wx1, sx1) - max(wx0, sx0))
            if overlap / w >= min_cov:
                return True
    return False


def remove_struck_words_line(ln2: pd.DataFrame, segs: List[Tuple[float, float, float]], y_band: float = DEFAULT_Y_BAND, min_cov: float = DEFAULT_MIN_WORD_COV) -> pd.DataFrame:
    if ln2 is None or ln2.empty:
        return ln2
    keep = []
    for _, r in ln2.iterrows():
        keep.append(not word_is_struck(r, segs, y_band=y_band, min_cov=min_cov))
    return ln2[np.array(keep)].copy()


def clean_line_hybrid(
    ln2: pd.DataFrame,
    segs: List[Tuple[float, float, float]],
    y_band: float = DEFAULT_Y_BAND,
    min_word_cov: float = DEFAULT_MIN_WORD_COV,
    drop_line_cov: float = DEFAULT_DROP_LINE_COV,
) -> pd.DataFrame:
    cov, _ = strike_union_coverage(bbox_of_df(ln2), segs, y_band=y_band)
    if cov >= drop_line_cov:
        return ln2.iloc[0:0].copy()
    return remove_struck_words_line(ln2, segs, y_band=y_band, min_cov=min_word_cov)


# -----------------------------
# Clean rows per page
# -----------------------------
def build_clean_lines_page_with_cov(pdf: pdfplumber.PDF, pidx: int, y_tol: float = DEFAULT_Y_TOL) -> List[Dict[str, Any]]:
    page = pdf.pages[pidx]
    dfw = words_df(page)
    lines = group_lines_words(dfw, y_tol=y_tol)
    segs = collect_vector_strike_segments(page)

    out: List[Dict[str, Any]] = []
    for i, ln in enumerate(lines):
        ln2 = remove_right_linenumber_tokens(ln, x_thresh=DEFAULT_X_THRESH)
        if ln2.empty:
            out.append({"line_idx": i, "raw": "", "clean": "", "cov": 0.0})
            continue

        cov, _ = strike_union_coverage(bbox_of_df(ln2), segs, y_band=DEFAULT_Y_BAND)
        ln3 = clean_line_hybrid(ln2, segs, y_band=DEFAULT_Y_BAND, min_word_cov=DEFAULT_MIN_WORD_COV, drop_line_cov=DEFAULT_DROP_LINE_COV)

        out.append({
            "line_idx": i,
            "raw": line_text(ln2),
            "clean": line_text(ln3),
            "cov": float(cov),
        })
    return out


# -----------------------------
# Block suppression
# -----------------------------
def is_strong_live_clause_start(raw_line: str, cov: float, section: str = "shell") -> bool:
    t = (raw_line or "").strip()
    if not t:
        return False

    m = RX_START_INLINE.match(t)
    if not m:
        return False

    if RX_SUBCLAUSE_PAREN.match(t) or RX_ROMAN_SUB.match(t) or RX_ALPHA_PAREN.match(t) or RX_ALPHA_DOT.match(t):
        return False

    rest = (m.group(2) or "").strip()

    if cov > DEFAULT_KEEP_CLAUSE_COV:
        return False

    return is_inline_heading_or_title_with_period(rest, section)


def suppress_strike_blocks_v2(
    rows: List[Dict[str, Any]],
    section: str = "shell",
    start_thr: float = DEFAULT_BLOCK_START_THR,
    end_thr: float = DEFAULT_BLOCK_END_THR,
    max_run: int = DEFAULT_BLOCK_MAX_RUN,
    end_patience: int = DEFAULT_BLOCK_END_PATIENCE,
) -> List[Dict[str, Any]]:
    suppressed = [False] * len(rows)

    i = 0
    while i < len(rows):
        if rows[i]["cov"] >= start_thr:
            j = i
            low_cnt = 0
            steps = 0

            while j < len(rows) and steps < max_run:
                raw_j = (rows[j].get("raw") or "").strip()
                cov_j = float(rows[j].get("cov", 0.0))

                if j > i and is_strong_live_clause_start(raw_j, cov_j, section=section):
                    break

                suppressed[j] = True

                if cov_j < end_thr:
                    low_cnt += 1
                else:
                    low_cnt = 0

                if low_cnt >= end_patience:
                    break

                j += 1
                steps += 1

            i = j + 1
        else:
            i += 1

    out: List[Dict[str, Any]] = []
    for r, sup in zip(rows, suppressed):
        rr = dict(r)
        rr["suppressed"] = sup
        rr["clean2"] = "" if sup else (rr.get("clean") or "")
        out.append(rr)
    return out


def get_clean_rows_v2(pdf: pdfplumber.PDF, pidx: int, section: str = "shell") -> List[Dict[str, Any]]:
    rows = build_clean_lines_page_with_cov(pdf, pidx)
    rows2 = suppress_strike_blocks_v2(rows, section=section)
    return rows2


# -----------------------------
# Main second-part extractor
# -----------------------------
def build_second_part_stream_v4(pdf: pdfplumber.PDF, p_from: int, p_to: int, section0: str = "shell", debug_pages: Optional[set[int]] = None) -> List[Dict[str, Any]]:
    clauses: List[Dict[str, Any]] = []
    section = section0
    current: Optional[Dict[str, Any]] = None

    def cleanup_known_struck_leaks(text: str) -> str:
        cleaned = (text or "").strip()
        for pattern in KNOWN_STRUCK_LEAK_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip(" .\n")

    def flush():
        nonlocal current
        if current is None:
            return
        current["title"] = (current.get("title") or "").strip()
        current["text"] = cleanup_known_struck_leaks((current.get("text") or "").strip())
        current["order"] = len(clauses) + 1
        clauses.append(current)
        current = None

    for pidx in range(p_from, p_to + 1):
        rows = get_clean_rows_v2(pdf, pidx, section=section)

        if debug_pages and pidx in debug_pages:
            logger.info("PAGE %s | section=%s | rows=%s", pidx, section, len(rows))
            for r in rows[:80]:
                t = (r["clean2"] or "").strip()
                if t:
                    logger.info("  line=%s cov=%.2f sup=%s | %s", r["line_idx"], r["cov"], r["suppressed"], t)

        i = 0
        while i < len(rows):
            line = (rows[i]["clean2"] or "").strip()
            if not line:
                i += 1
                continue

            # section banners
            if RX_SHELL.search(line):
                flush()
                section = "shell"
                logger.info("Switch section -> shell at page %s line %s", pidx, rows[i]["line_idx"])
                i += 1
                continue

            if RX_ESSAR.search(line):
                flush()
                section = "essar"
                logger.info("Switch section -> essar at page %s line %s", pidx, rows[i]["line_idx"])
                i += 1
                continue

            # inline "N. ..." (only if real clause start; decimal like 21.50 MIO) -> body)
            m = RX_START_INLINE.match(line)
            if m and not (RX_SUBCLAUSE_PAREN.match(line) or RX_ROMAN_SUB.match(line) or RX_ALPHA_PAREN.match(line) or RX_ALPHA_DOT.match(line)):
                if not is_real_clause_start_line(line):
                    # Decimal continuation or similar: treat as body
                    if current is not None:
                        current["text"] += "\n" + line
                        current["page_end"] = pidx
                    i += 1
                    continue

                num = m.group(1)
                rest = (m.group(2) or "").strip()
                flush()

                has_title = is_inline_heading_or_title_with_period(rest, section)
                full_heading_line = line.strip()
                local_num = int(num)
                if has_title:
                    current = {
                        "section": section,
                        "local_num": local_num,
                        "id": f"{section}:{num}",
                        "title": full_heading_line,
                        "text": "",
                        "page_start": pidx,
                        "page_end": pidx,
                    }
                else:
                    current = {
                        "section": section,
                        "local_num": local_num,
                        "id": f"{section}:{num}",
                        "title": "",
                        "text": full_heading_line,
                        "page_start": pidx,
                        "page_end": pidx,
                    }
                i += 1
                continue

            # num-only "N."
            m2 = RX_NUM_ONLY.match(line)
            if m2:
                num = m2.group(1)
                nxt = (rows[i + 1]["clean2"] or "").strip() if i + 1 < len(rows) else ""

                if nxt and is_title_candidate_for_num_only(nxt, section) and not is_clause_start_line(nxt):
                    flush()
                    local_num = int(num)
                    current = {
                        "section": section,
                        "local_num": local_num,
                        "id": f"{section}:{num}",
                        "title": f"{num}. {nxt}",
                        "text": "",
                        "page_start": pidx,
                        "page_end": pidx,
                    }
                    i += 2
                    continue

                # dead clause (only number survived)
                i += 1
                continue

            # body line
            if current is not None:
                current["text"] += "\n" + line
                current["page_end"] = pidx

            i += 1

    flush()
    return clauses


# -----------------------------
# Validation / reporting
# -----------------------------
def clause_canonical(c: Dict[str, Any]) -> Dict[str, Any]:
    """Return clause with fields in canonical output order."""
    return {
        "order": c["order"],
        "section": c["section"],
        "local_num": c["local_num"],
        "id": c["id"],
        "title": c.get("title") or "",
        "text": c.get("text") or "",
        "page_start": c["page_start"],
        "page_end": c["page_end"],
    }


def section_stats(clauses: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_sec = defaultdict(list)
    for c in clauses:
        sec, num = c["id"].split(":")
        by_sec[sec].append(int(num))
    out = {}
    for sec, nums in by_sec.items():
        nums_sorted = sorted(nums)
        out[sec] = {
            "count": len(nums),
            "min": nums_sorted[0],
            "max": nums_sorted[-1],
            "missing": sorted(set(range(nums_sorted[0], nums_sorted[-1] + 1)) - set(nums_sorted)),
        }
    return out


def banner_leaks(clauses: List[Dict[str, Any]]) -> List[str]:
    leaks = []
    for c in clauses:
        txt = c.get("text") or ""
        if RX_SHELL.search(txt) or RX_ESSAR.search(txt):
            leaks.append(c["id"])
    return leaks


def audit_titles(clauses: List[Dict[str, Any]]) -> Tuple[List[str], List[Tuple[str, int, str]]]:
    empty = []
    too_long = []
    for c in clauses:
        t = (c.get("title") or "").strip()
        if not t:
            empty.append(c["id"])
        if len(t) > 140:
            too_long.append((c["id"], len(t), t[:120]))
    return empty, too_long


def heading_only_clauses(clauses: List[Dict[str, Any]], max_lines: int = 2) -> List[Tuple[str, str, str]]:
    out = []
    for c in clauses:
        lines = [ln for ln in (c["text"] or "").splitlines() if ln.strip()]
        if len(lines) <= max_lines:
            out.append((c["id"], c.get("title", ""), lines[0] if lines else ""))
    return out


def validate_basic(clauses: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    bad = []
    seen = set()
    for c in clauses:
        if c["id"] in seen:
            bad.append(("dup", c["id"]))
        seen.add(c["id"])
        num = c["id"].split(":")[1]
        title = (c.get("title") or "").strip()
        text = (c.get("text") or "").lstrip()
        if title and title.startswith(num + "."):
            # Body-only text allowed; no need for text to start with "N."
            continue
        if not text.startswith(num + "."):
            bad.append(("num_mismatch", c["id"]))
    return bad


def validate_schema(clauses: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Check order 1..N, id backward-compatible, local_num matches id, no title dup in text."""
    issues: List[Tuple[str, str]] = []
    for k, c in enumerate(clauses):
        order = c.get("order")
        if order is None or order != k + 1:
            issues.append(("order", c.get("id", "?") + f" order={order} expected={k + 1}"))
        sid = c.get("id", "")
        if ":" not in sid:
            issues.append(("id_format", sid))
            continue
        sec, num_str = sid.split(":", 1)
        try:
            local_num = int(num_str)
        except ValueError:
            issues.append(("local_num", sid + " non-numeric"))
            continue
        if c.get("section") != sec or c.get("local_num") != local_num:
            issues.append(("id_match", f"{sid} section={c.get('section')} local_num={c.get('local_num')}"))
        title = (c.get("title") or "").strip()
        text = (c.get("text") or "").strip()
        if title:
            first_line = text.split("\n")[0].strip() if text else ""
            if first_line == title:
                issues.append(("title_dup_in_text", sid))
    return issues


def audit_output_format(clauses: List[Dict[str, Any]]) -> None:
    """Log regression checks and sample output: explicit title, no title, heading-only."""
    by_id = {c["id"]: c for c in clauses}

    # 1) No fake shell:21 from decimal
    c21 = by_id.get("shell:21")
    if c21:
        t = ((c21.get("title") or "") + " " + (c21.get("text") or "")).strip()
        if "21.50" in t or ("50 MIO" in t and len(t) < 50):
            logger.warning("AUDIT: shell:21 looks like decimal artefact (title=%s text=%s)", c21.get("title"), (c21.get("text") or "")[:60])
        else:
            logger.info("AUDIT: shell:21 present (expected if real clause); title=%s", c21.get("title"))
    else:
        logger.info("AUDIT: no fake shell:21 from decimal - PASS")

    # 2) shell:19 and shell:26 have non-empty titles
    for cid in ("shell:19", "shell:26"):
        c = by_id.get(cid)
        if c and (c.get("title") or "").strip():
            logger.info("AUDIT: %s has non-empty title - PASS", cid)
        elif c:
            logger.warning("AUDIT: %s has empty title - WARN", cid)
        else:
            logger.info("AUDIT: %s not in output (skipped)", cid)

    # 3) Title not duplicated at start of text
    dup_count = 0
    for c in clauses:
        title = (c.get("title") or "").strip()
        text = (c.get("text") or "").strip()
        if not title:
            continue
        first_line = text.split("\n")[0].strip() if text else ""
        if first_line == title:
            dup_count += 1
            logger.warning("AUDIT: %s title duplicated at start of text", c["id"])
    if dup_count == 0:
        logger.info("AUDIT: title not duplicated at start of text - PASS")
    else:
        logger.warning("AUDIT: %d clause(s) have title duplicated in text", dup_count)

    # 4) Sample output: one shell with explicit title, one without, one heading-only
    with_title = next((c for c in clauses if c.get("section") == "shell" and (c.get("title") or "").strip()), None)
    without_title = next((c for c in clauses if (c.get("section") in ("shell", "essar")) and not (c.get("title") or "").strip()), None)
    heading_only_list = [c for c in clauses if (c.get("title") or "").strip() and not (c.get("text") or "").strip()]
    heading_only = heading_only_list[0] if heading_only_list else None
    if with_title:
        logger.info("SAMPLE clause with explicit title (shell): %s", json.dumps(clause_canonical(with_title), ensure_ascii=False))
    if without_title:
        logger.info("SAMPLE clause without explicit title: %s", json.dumps(clause_canonical(without_title), ensure_ascii=False))
    if heading_only:
        logger.info("SAMPLE heading-only clause: %s", json.dumps(clause_canonical(heading_only), ensure_ascii=False))


# -----------------------------
# CLI
# -----------------------------
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page-from", type=int, default=P2_FROM)
    ap.add_argument("--page-to", type=int, default=P2_TO)
    ap.add_argument("--out", required=True)
    ap.add_argument("--debug-pages", default="", help="Comma-separated page indices")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    debug_pages: set[int] = set()
    if args.debug_pages.strip():
        debug_pages = {int(x.strip()) for x in args.debug_pages.split(",") if x.strip()}

    with pdfplumber.open(args.pdf) as pdf:
        clauses = build_second_part_stream_v4(
            pdf,
            p_from=args.page_from,
            p_to=args.page_to,
            section0="shell",
            debug_pages=debug_pages,
        )

    stats = section_stats(clauses)
    leaks = banner_leaks(clauses)
    empty_titles, long_titles = audit_titles(clauses)
    issues = validate_basic(clauses)
    schema_issues = validate_schema(clauses)
    heading_only = heading_only_clauses(clauses)
    audit_output_format(clauses)

    logger.info("Extracted clauses: %d", len(clauses))
    logger.info("Stats: %s", stats)
    logger.info("Banner leaks: %s", leaks)
    logger.info("Validation issues: %s", issues)
    logger.info("Schema validation: %s", schema_issues if schema_issues else "PASS")
    logger.info("Empty titles: %s", empty_titles)
    logger.info("Long titles: %s", long_titles[:10])
    logger.info("Heading-only clauses: %d", len(heading_only))
    logger.info("Last 15 ids/titles: %s", [(c["id"], c["title"]) for c in clauses[-15:]])

    out_clauses = [clause_canonical(c) for c in clauses]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_clauses, f, ensure_ascii=False, indent=2)

    logger.info("Wrote: %s", args.out)


if __name__ == "__main__":
    main()
