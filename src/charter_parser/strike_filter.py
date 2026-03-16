from __future__ import annotations

from collections.abc import Iterable

from charter_parser.models import WordIR


DEFAULT_Y_BAND = 1.1
DEFAULT_MIN_WORD_COV = 0.45


def _rect_to_segment(rect, source: str, min_width: float = 25.0, max_height: float = 1.5) -> dict | None:
    if rect is None:
        return None
    x0 = float(min(rect.x0, rect.x1))
    x1 = float(max(rect.x0, rect.x1))
    y0 = float(min(rect.y0, rect.y1))
    y1 = float(max(rect.y0, rect.y1))
    if (x1 - x0) < min_width or (y1 - y0) > max_height:
        return None
    return {"y": (y0 + y1) / 2.0, "x0": x0, "x1": x1, "source": source}


def collect_vector_strike_segments(drawings: Iterable[dict]) -> list[dict]:
    segs: list[dict] = []
    for drawing in drawings or []:
        segment = _rect_to_segment(drawing.get("rect"), "drawing_rect")
        if segment is not None:
            segs.append(segment)
        for item in drawing.get("items", []) or []:
            op = item[0] if item else None
            if op == "re" and len(item) >= 2:
                segment = _rect_to_segment(item[1], "path_rect")
                if segment is not None:
                    segs.append(segment)
            elif op == "l" and len(item) >= 3:
                p0, p1 = item[1], item[2]
                if abs(float(p0.y) - float(p1.y)) > 0.8:
                    continue
                x0 = float(min(p0.x, p1.x))
                x1 = float(max(p0.x, p1.x))
                if (x1 - x0) < 25.0:
                    continue
                segs.append({"y": float((p0.y + p1.y) / 2.0), "x0": x0, "x1": x1, "source": "line_crossing"})
    unique: dict[tuple[float, float, float, str], dict] = {}
    for seg in segs:
        key = (round(seg["y"], 3), round(seg["x0"], 3), round(seg["x1"], 3), seg["source"])
        unique[key] = seg
    return [unique[key] for key in sorted(unique)]


def strike_evidence(word: WordIR, segs: list[dict], y_band: float = DEFAULT_Y_BAND) -> tuple[float, list[str], float | None]:
    width = float(word.x1 - word.x0)
    if width <= 0:
        return 0.0, [], None
    y_mid = float((word.y0 + word.y1) / 2.0)
    intervals: list[tuple[float, float]] = []
    sources: set[str] = set()
    min_center_delta: float | None = None
    for seg in segs:
        delta = abs(float(seg["y"]) - y_mid)
        if delta > y_band:
            continue
        lo = max(float(word.x0), float(seg["x0"]))
        hi = min(float(word.x1), float(seg["x1"]))
        if hi <= lo:
            continue
        intervals.append((lo, hi))
        sources.add(str(seg["source"]))
        min_center_delta = delta if min_center_delta is None else min(min_center_delta, delta)
    if not intervals:
        return 0.0, [], None
    intervals.sort()
    merged: list[tuple[float, float]] = []
    cur_lo, cur_hi = intervals[0]
    for lo, hi in intervals[1:]:
        if lo <= cur_hi + 1.0:
            cur_hi = max(cur_hi, hi)
        else:
            merged.append((cur_lo, cur_hi))
            cur_lo, cur_hi = lo, hi
    merged.append((cur_lo, cur_hi))
    coverage = min(1.0, sum(hi - lo for lo, hi in merged) / width)
    return coverage, sorted(sources), None if min_center_delta is None else round(min_center_delta, 4)


def mark_struck_words(
    words: list[WordIR],
    drawings: Iterable[dict],
    *,
    y_band: float = DEFAULT_Y_BAND,
    min_cov: float = DEFAULT_MIN_WORD_COV,
) -> list[WordIR]:
    segs = collect_vector_strike_segments(drawings)
    if not segs:
        return words
    out: list[WordIR] = []
    for word in words:
        coverage, sources, center_delta = strike_evidence(word, segs, y_band=y_band)
        out.append(
            word.model_copy(
                update={
                    "is_struck": coverage >= min_cov,
                    "strike_coverage": round(coverage, 4),
                    "strike_sources": sources,
                    "strike_min_center_delta": center_delta,
                }
            )
        )
    return out
