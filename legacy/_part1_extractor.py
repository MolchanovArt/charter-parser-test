#!/usr/bin/env python3
"""
Part II Charterparty clause extractor (Mode A pages with right-margin line numbers).

Goal:
- Extract clauses as [{id, title, text}] in reading order.
- Remove struck-through content:
  - Partial strike: remove only struck words (keeps remaining text on the same line).
  - Full-line strike: drop the whole MAIN text of the line (prevents "leaks" from fully struck blocks).
- Build title from left-column heading blocks (NOT from inline title; per your requirement).
- Stream across pages so a clause can continue on the next page.
- Post-process:
  - Drop "dead clauses" like "29." when clause is fully struck incl. heading.
  - Cleanup tail garbage (e.g., "and\n(1)\n(2)\n(3)" and short dangling tokens).

Usage example:
  python extract_part2.py --pdf /path/to/voyage-charter-example.pdf \
    --page-from 5 --page-to 38 \
    --out part2.json \
    --debug-pages 5,6,15

Notes:
- page indices are 0-based (pdfplumber pages list). In your work:
  Part II starts at page_idx=5 (PDF human page 6).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pdfplumber


# -----------------------------
# Config defaults
# -----------------------------

DEFAULT_X_THRESH = 520          # right margin line numbers x-center threshold
DEFAULT_LEFT_X = 105            # left column heading x-center threshold for Mode A split
DEFAULT_Y_TOL = 3.0             # group words into lines tolerance
DEFAULT_Y_BAND = 1.1            # y-band for word strike detection
DEFAULT_MIN_WORD_COV = 0.45     # overlap ratio for word struck by segment
DEFAULT_DROP_LINE_COV = 0.85    # union coverage threshold to drop full MAIN line content
DEFAULT_HEADING_GAP = 14.0      # max y gap to merge heading tokens into a heading block
DEFAULT_HEADING_LOOKBACK = 650  # how far above (in y) heading can be to apply to clause start
# When appending to a suppressed-start clause, only append clearly non-struck lines (cov <= this), or longer lines (likely real)
DEFAULT_SUPPRESSED_APPEND_COV = 0.15
# Min length to allow appending to suppressed-start when cov exceeds threshold (avoids dropping full ETA-style lines)
DEFAULT_SUPPRESSED_APPEND_MIN_LEN = 50


CLAUSE_START_RE = re.compile(r"^(\d{1,3})\.\s*(.*)$")
ONLY_NUM_RE = re.compile(r"^\s*(\d{1,3})\.\s*$")


def title_heading_only(s: str) -> str:
    """Remove leading clause number from heading text so first-block title never includes it."""
    if not s or not s.strip():
        return (s or "").strip()
    t = s.strip()
    return re.sub(r"^\d{1,3}\.\s*", "", t).strip() or t


# -----------------------------
# Logging
# -----------------------------

logger = logging.getLogger("extract_part2")


# -----------------------------
# PDF -> words/lines utilities
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
    """Group extracted words into visual lines based on 'top' proximity."""
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
            ln = pd.DataFrame(current).sort_values("x0")
            lines.append(ln)
            current = [r]
            current_top = t

    if current:
        lines.append(pd.DataFrame(current).sort_values("x0"))

    lines.sort(key=lambda ln: float(ln["top"].min()))
    return lines


def line_text(ln: pd.DataFrame) -> str:
    return " ".join(ln["text"].tolist()).strip() if ln is not None and not ln.empty else ""


def line_y(ln2: pd.DataFrame) -> float:
    return float(ln2["top"].min()) if (ln2 is not None and not ln2.empty) else float("nan")


def right_linenumber_token(ln: pd.DataFrame, x_thresh: float = DEFAULT_X_THRESH) -> Optional[str]:
    digits = ln[ln["is_digit"] & (ln["x_center"] >= x_thresh)]
    if digits.empty:
        return None
    r = digits.sort_values("x_center").iloc[-1]
    return str(r["text"])


def remove_right_linenumber_tokens(ln: pd.DataFrame, x_thresh: float = DEFAULT_X_THRESH) -> pd.DataFrame:
    return ln[~(ln["is_digit"] & (ln["x_center"] >= x_thresh))].copy()


def split_left_main(ln: pd.DataFrame, left_x: float = DEFAULT_LEFT_X) -> Tuple[str, str]:
    """Split a line into left heading column and main text column by x_center threshold."""
    if ln is None or ln.empty:
        return "", ""
    left = ln[ln["x_center"] <= left_x]
    main = ln[ln["x_center"] > left_x]
    left_txt = " ".join(left.sort_values("x0")["text"].tolist()).strip()
    main_txt = " ".join(main.sort_values("x0")["text"].tolist()).strip()
    return left_txt, main_txt


# -----------------------------
# Vector strike segments (edges/lines/rects)
# -----------------------------

def collect_vector_strike_segments(
    page: pdfplumber.page.Page,
    eps_y: float = 0.8,
    min_w: float = 25.0,
    rect_max_h: float = 1.5,
    rect_min_w: float = 25.0,
) -> List[Tuple[float, float, float]]:
    """
    Build horizontal segments (y_top, x0, x1) in TOP-origin coordinate system.

    pdfplumber:
      - words use top-origin via 'top/bottom'
      - edges usually use bottom-origin y0/y1 => convert: y_top = page.height - y0
    """
    segs: List[Tuple[float, float, float]] = []
    H = float(page.height)

    # edges: bottom-origin y0/y1
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

    # lines: may expose top/bottom or y0/y1
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

    # thin rects: top-origin
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


# -----------------------------
# Strike scoring & cleaning
# -----------------------------

def bbox_of_df(df: pd.DataFrame) -> Optional[Tuple[float, float, float, float]]:
    """Return bbox (x0,x1,top,bottom)."""
    if df is None or df.empty:
        return None
    return (
        float(df["x0"].min()),
        float(df["x1"].max()),
        float(df["top"].min()),
        float(df["bottom"].max()),
    )


def strike_union_coverage(bbox: Tuple[float, float, float, float], segs: List[Tuple[float, float, float]], y_band: float) -> Tuple[float, int]:
    """Union coverage ratio in X for segments near line mid-y."""
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
    cur_lo, cur_hi = intervals[0]
    for lo, hi in intervals[1:]:
        if lo <= cur_hi + 1.0:
            cur_hi = max(cur_hi, hi)
        else:
            merged.append((cur_lo, cur_hi))
            cur_lo, cur_hi = lo, hi
    merged.append((cur_lo, cur_hi))

    covered = sum(hi - lo for lo, hi in merged)
    return float(covered / width), len(intervals)


def word_is_struck_by_segments(word_row: pd.Series, segs: List[Tuple[float, float, float]], y_band: float, min_cov: float) -> bool:
    """Return True if any segment overlaps the word bbox sufficiently near its midline."""
    wx0, wx1 = float(word_row["x0"]), float(word_row["x1"])
    top, bottom = float(word_row["top"]), float(word_row["bottom"])
    if wx1 <= wx0:
        return False
    y_mid = (top + bottom) / 2.0
    w = wx1 - wx0
    for y, sx0, sx1 in segs:
        if abs(y - y_mid) <= y_band:
            overlap = max(0.0, min(wx1, sx1) - max(wx0, sx0))
            if (overlap / w) >= min_cov:
                return True
    return False


def remove_struck_words_from_line(ln2: pd.DataFrame, segs: List[Tuple[float, float, float]], y_band: float, min_cov: float) -> pd.DataFrame:
    if ln2 is None or ln2.empty:
        return ln2
    keep_mask = []
    for _, r in ln2.iterrows():
        keep_mask.append(not word_is_struck_by_segments(r, segs, y_band=y_band, min_cov=min_cov))
    return ln2[np.array(keep_mask)].copy()


def hybrid_clean_line_main(
    ln2: pd.DataFrame,
    segs: List[Tuple[float, float, float]],
    left_x: float,
    y_band: float,
    min_word_cov: float,
    drop_line_cov: float,
) -> pd.DataFrame:
    """
    Hybrid:
    - If MAIN area union coverage >= drop_line_cov => drop entire MAIN text (prevents leakage from fully struck blocks)
    - Else => remove only struck words
    """
    if ln2 is None or ln2.empty:
        return ln2

    main_df = ln2[ln2["x_center"] > left_x].copy()
    main_bbox = bbox_of_df(main_df)
    if main_bbox is not None:
        cov, _ = strike_union_coverage(main_bbox, segs, y_band=y_band)
        if cov >= drop_line_cov:
            # Drop MAIN words entirely, keep left tokens (headings)
            return ln2[ln2["x_center"] <= left_x].copy()

    # Partial clean
    return remove_struck_words_from_line(ln2, segs, y_band=y_band, min_cov=min_word_cov)


# -----------------------------
# Heading blocks
# -----------------------------

def merge_heading_blocks(headings: List[Dict[str, Any]], max_gap: float = DEFAULT_HEADING_GAP) -> List[Dict[str, Any]]:
    """
    headings: list {y, text, line_idx, cov, start_num}
    Returns blocks: list {y, text, line_idx_start, line_idx_end, min_cov}
    Do not merge heading rows that belong to different clause starts (different start_num).
    """
    hs = sorted(headings, key=lambda h: h["y"])
    blocks: List[Dict[str, Any]] = []
    buf_texts: List[str] = []
    buf_covs: List[float] = []
    buf_start_num: Optional[int] = None
    y_start: Optional[float] = None
    last_y: Optional[float] = None
    li_start: Optional[int] = None
    li_end: Optional[int] = None

    def flush():
        nonlocal buf_texts, buf_covs, buf_start_num, y_start, last_y, li_start, li_end
        if not buf_texts:
            return
        text = " ".join(buf_texts).strip()
        min_cov = min(buf_covs) if buf_covs else 0.0
        # drop header-like
        if text and ("Issued" not in text) and ("SHELLVOY" not in text):
            blocks.append({
                "y": y_start,
                "text": text,
                "line_idx_start": li_start,
                "line_idx_end": li_end,
                "min_cov": min_cov,
            })
        buf_texts, buf_covs, buf_start_num, y_start, last_y, li_start, li_end = [], [], None, None, None, None, None

    for h in hs:
        y = float(h["y"])
        cov = float(h.get("cov", 0.0))
        start_num = h.get("start_num")
        # Do not merge if this heading belongs to a different clause start
        if buf_start_num is not None and start_num is not None and start_num != buf_start_num:
            flush()
        if y_start is None:
            y_start = y
            last_y = y
            li_start = int(h["line_idx"])
            li_end = int(h["line_idx"])
            buf_texts = [h["text"]]
            buf_covs = [cov]
            buf_start_num = start_num
            continue

        if abs(y - float(last_y)) <= max_gap and (buf_start_num is None or start_num is None or start_num == buf_start_num):
            buf_texts.append(h["text"])
            buf_covs.append(cov)
            last_y = y
            li_end = int(h["line_idx"])
        else:
            flush()
            y_start = y
            last_y = y
            li_start = int(h["line_idx"])
            li_end = int(h["line_idx"])
            buf_texts = [h["text"]]
            buf_covs = [cov]
            buf_start_num = start_num

    flush()
    return blocks


def build_heading_index(blocks: List[Dict[str, Any]]) -> Tuple[List[float], List[Dict[str, Any]]]:
    blocks2 = sorted(blocks, key=lambda b: b["y"])
    ys = [float(b["y"]) for b in blocks2]
    return ys, blocks2


def find_heading_for_y(y: float, ys: List[float], blocks: List[Dict[str, Any]], max_lookback: float) -> str:
    """
    Take the nearest heading block ABOVE y within max_lookback.
    """
    block = find_heading_block_for_y(y, ys, blocks, max_lookback)
    return str(block["text"]) if block else ""


def find_heading_block_for_y(y: float, ys: List[float], blocks: List[Dict[str, Any]], max_lookback: float) -> Optional[Dict[str, Any]]:
    """Return the nearest heading block above y within max_lookback, or None."""
    if not ys or not blocks:
        return None
    idx = bisect_right(ys, y) - 1
    if idx < 0:
        return None
    cand = blocks[idx]
    if (y - float(cand["y"])) > max_lookback:
        return None
    return cand


def is_heading_block_struck(block: Dict[str, Any], thresh: float = DEFAULT_DROP_LINE_COV) -> bool:
    """True if the heading block is considered struck (all lines have high strike coverage)."""
    return float(block.get("min_cov", 0.0)) >= thresh


# -----------------------------
# Stream building (per page)
# -----------------------------

def build_streams_mode_a(
    pdf: pdfplumber.PDF,
    page_idx: int,
    x_thresh: float,
    left_x: float,
    y_tol: float,
    y_band: float,
    min_word_cov: float,
    drop_line_cov: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns:
      headings: list of dict {y, text, line_idx}
      body_lines: list of dict {y, text, rn, line_idx, main_before, raw_before, raw_after, cov}
    """
    page = pdf.pages[page_idx]
    dfw = words_df(page)
    lines = group_lines_words(dfw, y_tol=y_tol)
    segs = collect_vector_strike_segments(page)

    headings: List[Dict[str, Any]] = []
    body_lines: List[Dict[str, Any]] = []

    for i, ln in enumerate(lines):
        rn = right_linenumber_token(ln, x_thresh=x_thresh)
        ln2 = remove_right_linenumber_tokens(ln, x_thresh=x_thresh)
        if ln2.empty:
            continue

        left_before, main_before = split_left_main(ln2, left_x=left_x)

        # Strike coverage for main (right) column: used to avoid appending leaked fragments to suppressed-start clauses
        main_df = ln2[ln2["x_center"] > left_x]
        main_bbox = bbox_of_df(main_df) if not main_df.empty else None
        cov = 0.0
        if main_bbox is not None:
            cov, _ = strike_union_coverage(main_bbox, segs, y_band=y_band)

        ln3 = hybrid_clean_line_main(
            ln2, segs,
            left_x=left_x,
            y_band=y_band,
            min_word_cov=min_word_cov,
            drop_line_cov=drop_line_cov,
        )
        _, main_after = split_left_main(ln3, left_x=left_x)

        y = line_y(ln2)

        # Clause number on this line (from right column) so heading blocks don't merge across clause boundaries
        start_num: Optional[int] = None
        for main_txt in [(main_after or "").strip(), (main_before or "").strip()]:
            if main_txt:
                mm = CLAUSE_START_RE.match(main_txt)
                if mm:
                    try:
                        start_num = int(mm.group(1))
                        break
                    except ValueError:
                        pass

        if left_before.strip():
            # Strike coverage for left (heading) column: used to skip clauses whose heading is struck
            left_df = ln2[ln2["x_center"] <= left_x]
            left_bbox = bbox_of_df(left_df) if not left_df.empty else None
            left_cov = 0.0
            if left_bbox is not None:
                left_cov, _ = strike_union_coverage(left_bbox, segs, y_band=y_band)
            headings.append({
                "y": y,
                "text": left_before.strip(),
                "line_idx": i,
                "cov": float(left_cov),
                "start_num": start_num,
            })

        body_lines.append({
            "y": y,
            "rn": rn,
            "text": (main_after or "").strip(),
            "line_idx": i,
            "main_before": (main_before or "").strip(),
            "raw_before": line_text(ln2),
            "raw_after": line_text(ln3),
            "cov": float(cov),
        })

    return headings, body_lines


def build_page_artifacts_mode_a(
    pdf: pdfplumber.PDF,
    page_idx: int,
    x_thresh: float,
    left_x: float,
    y_tol: float,
    y_band: float,
    min_word_cov: float,
    drop_line_cov: float,
    heading_gap: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    headings, body = build_streams_mode_a(
        pdf, page_idx,
        x_thresh=x_thresh,
        left_x=left_x,
        y_tol=y_tol,
        y_band=y_band,
        min_word_cov=min_word_cov,
        drop_line_cov=drop_line_cov,
    )
    blocks = merge_heading_blocks(headings, max_gap=heading_gap)
    return blocks, body


# -----------------------------
# Clause streaming across pages
# -----------------------------

def clause_canonical(c: Dict[str, Any]) -> Dict[str, Any]:
    """Return clause with fields in canonical output order (unified schema)."""
    return {
        "order": c["order"],
        "section": c["section"],
        "local_num": c["local_num"],
        "id": c["id"],
        "title": (c.get("title") or "").strip(),
        "text": (c.get("text") or "").strip(),
        "page_start": c["page_start"],
        "page_end": c["page_end"],
    }


def build_clauses_mode_a_stream(
    pdf: pdfplumber.PDF,
    page_indices: List[int],
    section: str,
    x_thresh: float,
    left_x: float,
    y_tol: float,
    y_band: float,
    min_word_cov: float,
    drop_line_cov: float,
    heading_gap: float,
    max_heading_lookback: float,
    debug_pages: Optional[set[int]] = None,
) -> List[Dict[str, Any]]:
    """
    Streaming: carries current clause across page boundaries.
    Title comes ONLY from heading blocks (no inline fallback).
    Output schema: order, section, local_num, id, title, text, page_start, page_end.
    """
    clauses: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def flush():
        nonlocal current
        if current is None:
            return
        current["title"] = (current.get("title") or "").strip()
        current["text"] = (current.get("text") or "").strip()
        current["order"] = len(clauses) + 1
        clauses.append(current)
        current = None

    for pidx in page_indices:
        blocks, body = build_page_artifacts_mode_a(
            pdf, pidx,
            x_thresh=x_thresh,
            left_x=left_x,
            y_tol=y_tol,
            y_band=y_band,
            min_word_cov=min_word_cov,
            drop_line_cov=drop_line_cov,
            heading_gap=heading_gap,
        )
        ys, blks = build_heading_index(blocks)

        if debug_pages and pidx in debug_pages:
            logger.info("DEBUG page_idx=%s heading_blocks=%s body_lines=%s", pidx, len(blocks), len(body))
            for b in blocks:
                logger.info("  HB y=%.1f lines %s-%s | %s", float(b["y"]), b["line_idx_start"], b["line_idx_end"], b["text"])
            for r in body:
                t = (r["text"] or "").strip()
                if CLAUSE_START_RE.match(t):
                    logger.info("  START line %s y=%.1f rn=%s | %s", r["line_idx"], float(r["y"]), r["rn"], t)

        for r in body:
            text = (r["text"] or "").strip()
            raw_main = (r.get("main_before") or "").strip()

            # Normal clause start: cleaned line has content and matches "N. ..."
            m = CLAUSE_START_RE.match(text) if text else None
            if m:
                num = m.group(1)
                cid = f"{section}:{num}"
                # If current is already this clause (e.g. was opened as suppressed), merge instead of reopening
                if current is not None and current.get("id") == cid:
                    heading_text = find_heading_for_y(float(r["y"]), ys, blks, max_lookback=max_heading_lookback)
                    new_title = title_heading_only(heading_text)
                    if new_title and (not current.get("title") or len(new_title) > len(current.get("title") or "")):
                        current["title"] = new_title
                    current["text"] = (current.get("text") or "").strip()
                    if text:
                        current["text"] += ("\n" + text) if current["text"] else text
                    current["page_end"] = pidx
                    current["_suppressed_start"] = False  # now has real body
                    continue
                flush()

                heading_text = find_heading_for_y(float(r["y"]), ys, blks, max_lookback=max_heading_lookback)
                title = title_heading_only(heading_text)

                local_num = int(num)
                current = {
                    "section": section,
                    "local_num": local_num,
                    "id": cid,
                    "title": title,
                    "text": text,
                    "page_start": pidx,
                    "page_end": pidx,
                }
                continue

            # Suppressed clause start: raw main was "N. ..." but cleaned is empty or num-only (body struck)
            if (not text or ONLY_NUM_RE.match(text)) and CLAUSE_START_RE.match(raw_main):
                m2 = CLAUSE_START_RE.match(raw_main)
                num = m2.group(1)
                cid = f"{section}:{num}"
                # Do not reopen same clause: keep appending to current if already this clause
                if current is not None and current.get("id") == cid:
                    continue
                # Same clause number already emitted on this page (duplicate suppressed): resume it for appending
                if clauses and clauses[-1].get("id") == cid and clauses[-1].get("page_start") == pidx:
                    current = clauses.pop()
                    continue
                flush()
                block = find_heading_block_for_y(float(r["y"]), ys, blks, max_lookback=max_heading_lookback)
                # Skip clause entirely if the heading block itself is struck through
                if block is not None and is_heading_block_struck(block):
                    continue
                # Require a valid heading block; title is clean heading text only (no number)
                heading_text = (block["text"] or "").strip() if block else ""
                if not heading_text:
                    continue
                title = title_heading_only(heading_text)
                local_num = int(num)
                current = {
                    "section": section,
                    "local_num": local_num,
                    "id": cid,
                    "title": title,
                    "text": "",
                    "page_start": pidx,
                    "page_end": pidx,
                    "_suppressed_start": True,
                }
                continue

            # Body line: append to current (belongs to current clause, e.g. ETA tail to part2:28)
            if text and current is not None:
                # Suppressed-start clauses: skip short high-cov fragments (leaked), allow long lines (real content)
                if current.get("_suppressed_start"):
                    cov = r.get("cov", 1.0)
                    if cov > DEFAULT_SUPPRESSED_APPEND_COV and len(text) < DEFAULT_SUPPRESSED_APPEND_MIN_LEN:
                        continue  # skip leaked fragment
                current["text"] += ("\n" + text) if current["text"] else text
                current["page_end"] = pidx

    flush()
    return clauses


# -----------------------------
# Post-processing (dead clauses + tail cleanup)
# -----------------------------

def is_dead_clause_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if ONLY_NUM_RE.match(t):
        return True
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) == 1 and len(lines[0]) <= 6:
        return True
    return False


def drop_dead_clauses(clauses: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    kept: List[Dict[str, Any]] = []
    dropped: List[str] = []
    for c in clauses:
        text = c.get("text", "")
        title = (c.get("title") or "").strip()
        # Keep heading-only clauses (e.g. suppressed-start with empty text) if title is non-empty
        if is_dead_clause_text(text) and not title:
            dropped.append(c.get("id", ""))
        else:
            kept.append(c)
    return kept, dropped


# Double enumerator prefix: (3) (4) or 3) 4) at start of line -> keep only second
RX_DOUBLE_PAREN = re.compile(r"^\s*\(\d+\)\s+(\(\d+\))")
RX_DOUBLE_BARE = re.compile(r"^\s*\d+\)\s+(\d+\))")


def normalize_double_enumerator_prefix(text: str) -> str:
    """If a line starts with two consecutive enumerator prefixes, keep only the second. E.g. (3) (4) -> (4)."""
    if not text or not text.strip():
        return text
    out: List[str] = []
    for line in text.splitlines():
        s = line
        s = RX_DOUBLE_PAREN.sub(r"\1", s)
        s = RX_DOUBLE_BARE.sub(r"\1", s)
        out.append(s)
    return "\n".join(out)


def cleanup_tail(text: str) -> str:
    t = (text or "").rstrip()
    t = normalize_double_enumerator_prefix(t)

    # (A) drop leading single short fragment when rest looks like clause body (e.g. "and\n41. ITOPF...")
    lines = t.splitlines()
    if len(lines) >= 2 and len(lines[0].strip()) <= 10:
        second = lines[1].strip()
        if second and second[0].isdigit() and "." in second[:4]:  # "41." style
            t = "\n".join(lines[1:]).strip()

    # (B) drop trailing "and\n(1)\n(2)\n(3)" style
    t = re.sub(r"\n(and)\s*\n(?:\(\d+\)\s*\n)+\(\d+\)\s*$", "", t, flags=re.I)

    # (C) drop trailing bare enumerators (1)(2)(3) or 1)2)3)
    t = re.sub(r"(?:\n\(?\d+\)?\s*){2,}$", "", t)

    # (D) drop tail of multiple ultra-short dangling lines (heuristic)
    lines = t.splitlines()
    tail = lines[-8:]
    short = [ln for ln in tail if len(ln.strip()) <= 5]
    if len(short) >= 3:
        while lines and len(lines[-1].strip()) <= 5:
            lines.pop()
        t = "\n".join(lines)

    return t.strip()


def merge_duplicate_clauses(clauses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge consecutive clauses with same section, local_num, and page_start (safety safeguard)."""
    if not clauses:
        return clauses
    out: List[Dict[str, Any]] = []
    for c in clauses:
        c = dict(c)
        if out:
            prev = out[-1]
            if (
                prev.get("section") == c.get("section")
                and prev.get("local_num") == c.get("local_num")
                and prev.get("page_start") == c.get("page_start")
            ):
                # Prefer title that has number or is longer
                pt, ct = (prev.get("title") or "").strip(), (c.get("title") or "").strip()
                if ct and (not pt or len(ct) > len(pt) or (ct and ct[0].isdigit() and not (pt and pt[0].isdigit()))):
                    prev["title"] = ct
                prev_text = (prev.get("text") or "").strip()
                new_text = (c.get("text") or "").strip()
                prev["text"] = (prev_text + "\n" + new_text).strip() if prev_text and new_text else (prev_text or new_text)
                prev["page_end"] = c.get("page_end", prev.get("page_end"))
                continue
        out.append(c)
    return out


def cleanup_micro_fragments(clauses: List[Dict[str, Any]], max_lines: int = 2, max_line_len: int = 15) -> List[Dict[str, Any]]:
    """Set text to empty for clauses whose body is only 1-2 tiny residual lines (e.g. board., leg., and)."""
    out: List[Dict[str, Any]] = []
    for c in clauses:
        c2 = dict(c)
        text = (c2.get("text") or "").strip()
        if text:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if len(lines) <= max_lines and all(len(ln) <= max_line_len for ln in lines):
                c2["text"] = ""
        out.append(c2)
    return out


def apply_cleanup(clauses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in clauses:
        c2 = dict(c)
        c2["text"] = cleanup_tail(c2.get("text", ""))
        out.append(c2)
    return out


# -----------------------------
# Validation
# -----------------------------

def validate_schema_part1(clauses: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Check order 1..N, local_num matches id, no title duplicated at start of text."""
    issues: List[Tuple[str, str]] = []
    for k, c in enumerate(clauses):
        order = c.get("order")
        if order is None or order != k + 1:
            issues.append(("order", (c.get("id") or "?") + f" order={order} expected={k + 1}"))
        sid = c.get("id", "")
        if ":" not in sid:
            issues.append(("id_format", sid))
            continue
        _, num_str = sid.split(":", 1)
        try:
            local_num = int(num_str)
        except ValueError:
            issues.append(("local_num", sid + " non-numeric"))
            continue
        if c.get("local_num") != local_num:
            issues.append(("id_match", f"{sid} local_num={c.get('local_num')}"))
        title = (c.get("title") or "").strip()
        text = (c.get("text") or "").strip()
        if title:
            first_line = text.split("\n")[0].strip() if text else ""
            if first_line == title:
                issues.append(("title_dup_in_text", sid))
    return issues


def validate_numbers(clauses: List[Dict[str, Any]]) -> List[str]:
    bad: List[str] = []
    for c in clauses:
        cid = c["id"]
        try:
            nid = int(cid.split(":")[1])
        except Exception:
            bad.append(cid)
            continue
        text = (c.get("text") or "").strip()
        title = (c.get("title") or "").strip()
        # heading-only clauses may have empty text; skip number-in-text check
        if not text and title:
            continue
        # suppressed clause: number in title (e.g. "27. Heating of cargo"); text may not start with N.
        if title.startswith(f"{nid}."):
            continue
        m = re.match(r"^\s*(\d{1,3})\.", text)
        nt = int(m.group(1)) if m else None
        if nt != nid:
            bad.append(cid)
    return bad


def find_sus_tails(clauses: List[Dict[str, Any]]) -> List[str]:
    sus: List[str] = []
    for c in clauses:
        t = (c.get("text") or "").strip()
        if re.search(r"\n\(\d+\)\s*\n\(\d+\)\s*\n\(\d+\)\s*$", t) or re.search(r"\n(and)\s*\n\(\d+\)\s*$", t, re.I):
            sus.append(c["id"])
    return sus


# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", required=True, help="Path to PDF")
    p.add_argument("--page-from", type=int, required=True, help="Start page index (0-based)")
    p.add_argument("--page-to", type=int, required=True, help="End page index (0-based, inclusive)")
    p.add_argument("--out", required=True, help="Output JSON path")
    p.add_argument("--section", default="part2", help="Section prefix for ids (default: part2)")
    p.add_argument("--debug-pages", default="", help="Comma-separated page indices to print detailed logs for")
    p.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")

    # tuning knobs
    p.add_argument("--x-thresh", type=float, default=DEFAULT_X_THRESH)
    p.add_argument("--left-x", type=float, default=DEFAULT_LEFT_X)
    p.add_argument("--y-tol", type=float, default=DEFAULT_Y_TOL)
    p.add_argument("--y-band", type=float, default=DEFAULT_Y_BAND)
    p.add_argument("--min-word-cov", type=float, default=DEFAULT_MIN_WORD_COV)
    p.add_argument("--drop-line-cov", type=float, default=DEFAULT_DROP_LINE_COV)
    p.add_argument("--heading-gap", type=float, default=DEFAULT_HEADING_GAP)
    p.add_argument("--heading-lookback", type=float, default=DEFAULT_HEADING_LOOKBACK)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    debug_pages = set()
    if args.debug_pages.strip():
        debug_pages = {int(x.strip()) for x in args.debug_pages.split(",") if x.strip()}

    logger.info("Opening PDF: %s", args.pdf)
    with pdfplumber.open(args.pdf) as pdf:
        page_indices = list(range(args.page_from, args.page_to + 1))
        logger.info("Processing pages %s..%s (%s pages)", args.page_from, args.page_to, len(page_indices))

        clauses_raw = build_clauses_mode_a_stream(
            pdf,
            page_indices=page_indices,
            section=args.section,
            x_thresh=args.x_thresh,
            left_x=args.left_x,
            y_tol=args.y_tol,
            y_band=args.y_band,
            min_word_cov=args.min_word_cov,
            drop_line_cov=args.drop_line_cov,
            heading_gap=args.heading_gap,
            max_heading_lookback=args.heading_lookback,
            debug_pages=debug_pages,
        )

    logger.info("Built clauses (raw): %d", len(clauses_raw))

    # merge duplicate same-id clauses (same section, local_num, page_start)
    clauses_merged = merge_duplicate_clauses(clauses_raw)

    # drop dead clauses (fully struck)
    clauses_mid, dropped = drop_dead_clauses(clauses_merged)
    if dropped:
        logger.info("Dropped dead clauses: %s", dropped)

    # tail cleanup
    clauses_final = apply_cleanup(clauses_mid)
    # micro-fragment cleanup: empty text when only 1-2 tiny lines (e.g. board., leg., and)
    clauses_final = cleanup_micro_fragments(clauses_final)

    # renumber order to 1..N after drops (preserves streaming order)
    for k, c in enumerate(clauses_final):
        c["order"] = k + 1

    # validation
    bad = validate_numbers(clauses_final)
    if bad:
        logger.warning("Number mismatch in clauses: %s", bad)

    schema_issues = validate_schema_part1(clauses_final)
    if schema_issues:
        logger.warning("Schema validation issues: %s", schema_issues)
    else:
        logger.info("Schema validation: PASS")

    sus = find_sus_tails(clauses_final)
    if sus:
        logger.warning("Suspicious tails remain (consider extending cleanup): %s", sus)

    # sample logging: first 3 clauses (id, order, title, page_start/page_end)
    for c in clauses_final[:3]:
        logger.info(
            "SAMPLE clause id=%s order=%s title=%s page_start=%s page_end=%s",
            c.get("id"),
            c.get("order"),
            (c.get("title") or "")[:50] + ("..." if len(c.get("title") or "") > 50 else ""),
            c.get("page_start"),
            c.get("page_end"),
        )

    # write output (canonical field order)
    out_clauses = [clause_canonical(c) for c in clauses_final]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_clauses, f, ensure_ascii=False, indent=2)

    logger.info("Wrote output: %s (clauses=%d)", args.out, len(clauses_final))

    # concise preview
    logger.info("First 5 ids: %s", [c["id"] for c in clauses_final[:5]])
    logger.info("Last 5 ids:  %s", [c["id"] for c in clauses_final[-5:]])

    # quick summary: missing titles
    missing_titles = [c["id"] for c in clauses_final if not (c.get("title") or "").strip()]
    if missing_titles:
        logger.warning("Clauses with empty titles: %s", missing_titles)


if __name__ == "__main__":
    main()