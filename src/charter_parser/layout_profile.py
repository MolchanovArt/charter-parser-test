from __future__ import annotations

from charter_parser.models import LayoutBand, LayoutProfile, PageIR, PageLayoutProfile


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    items = sorted(values)
    if len(items) == 1:
        return items[0]
    pos = max(0.0, min(1.0, q)) * (len(items) - 1)
    lower = int(pos)
    upper = min(len(items) - 1, lower + 1)
    frac = pos - lower
    return items[lower] * (1.0 - frac) + items[upper] * frac


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _line_width(line) -> float:
    x0, _, x1, _ = line.bbox
    return max(0.0, x1 - x0)


def _body_lines(page: PageIR):
    widths = [_line_width(line) for line in page.lines]
    if not widths:
        return []
    median_width = _percentile(widths, 0.5)
    body = [line for line in page.lines if _line_width(line) >= median_width]
    return body or list(page.lines)


def _band_confidence(signal_share: float, gap: float, scale: float) -> float:
    share_score = _clamp(signal_share * 2.0)
    gap_score = _clamp(gap / max(scale, 1.0))
    return round(_clamp(0.6 * share_score + 0.4 * gap_score), 3)


def _repeated_y_bands(pages: list[PageIR]) -> dict[str, float]:
    top_edges = []
    bottom_edges = []
    for page in pages:
        if not page.lines or page.height <= 0:
            continue
        top_edges.append(min(line.bbox[3] for line in page.lines) / page.height)
        bottom_edges.append(max(line.bbox[1] for line in page.lines) / page.height)
    if not top_edges:
        return {"header_cut": 0.0, "header_coverage": 0.0, "footer_cut": 1.0, "footer_coverage": 0.0}

    header_cut = _percentile(top_edges, 0.75)
    footer_cut = _percentile(bottom_edges, 0.25)
    header_coverage = sum(1 for edge in top_edges if edge <= header_cut + 0.01) / len(top_edges)
    footer_coverage = sum(1 for edge in bottom_edges if edge >= footer_cut - 0.01) / len(bottom_edges)
    return {
        "header_cut": round(min(header_cut, 0.15), 4),
        "header_coverage": round(header_coverage, 4),
        "footer_cut": round(max(footer_cut, 0.85), 4),
        "footer_coverage": round(footer_coverage, 4),
    }


def infer_page_layout(page: PageIR, repeated_bands: dict[str, float] | None = None) -> PageLayoutProfile:
    if not page.lines:
        return PageLayoutProfile(page_index=page.page_index, page_type="unknown", confidence=0.0, notes=["no lines"])

    repeated_bands = repeated_bands or {}
    width = max(page.width, 1.0)
    lines = list(page.lines)
    body_lines = _body_lines(page)
    body_starts = [line.bbox[0] for line in body_lines]
    body_ends = [line.bbox[2] for line in body_lines]
    body_x0 = _percentile(body_starts, 0.35)
    body_x1 = _percentile(body_ends, 0.65)
    body_width = max(1.0, body_x1 - body_x0)

    left_gap = max(12.0, width * 0.025)
    right_gap = max(10.0, width * 0.02)
    left_lines = [
        line for line in lines
        if line.bbox[0] <= body_x0 - left_gap and _line_width(line) <= body_width * 0.7
    ]
    right_lines = [
        line for line in lines
        if line.bbox[2] >= body_x1 + right_gap and _line_width(line) <= body_width * 0.5
    ]

    left_conf = _band_confidence(len(left_lines) / len(lines), body_x0 - min(line.bbox[0] for line in lines), width * 0.12)
    right_conf = _band_confidence(max(0.0, len(right_lines) / len(lines)), max(line.bbox[2] for line in lines) - body_x1, width * 0.1)
    body_conf = round(
        _clamp(
            0.5 * _clamp(len(body_lines) / len(lines)) +
            0.5 * _clamp(body_width / (width * 0.45))
        ),
        3,
    )

    notes: list[str] = []
    bands = [
        LayoutBand(name="body_band_hint", x0=round(body_x0, 2), x1=round(body_x1, 2), confidence=body_conf),
    ]
    if left_lines:
        left_x1 = _percentile([line.bbox[2] for line in left_lines], 0.8)
        bands.append(LayoutBand(name="left_band_hint", x0=0.0, x1=round(left_x1, 2), confidence=left_conf))
        notes.append(f"left-band candidates={len(left_lines)}")
    if right_lines:
        right_x0 = _percentile([line.bbox[0] for line in right_lines], 0.2)
        bands.append(LayoutBand(name="right_band_hint", x0=round(right_x0, 2), x1=round(width, 2), confidence=right_conf))
        notes.append(f"right-band candidates={len(right_lines)}")

    header_cut = repeated_bands.get("header_cut", 0.0)
    header_coverage = repeated_bands.get("header_coverage", 0.0)
    footer_cut = repeated_bands.get("footer_cut", 1.0)
    footer_coverage = repeated_bands.get("footer_coverage", 0.0)
    if header_coverage >= 0.5 and any(line.bbox[3] / page.height <= header_cut for line in lines):
        bands.append(LayoutBand(name="header_band_hint", x0=0.0, x1=round(width, 2), confidence=round(header_coverage, 3)))
        notes.append("repeated top band present")
    if footer_coverage >= 0.5 and any(line.bbox[1] / page.height >= footer_cut for line in lines):
        bands.append(LayoutBand(name="footer_band_hint", x0=0.0, x1=round(width, 2), confidence=round(footer_coverage, 3)))
        notes.append("repeated bottom band present")

    if left_conf >= 0.3 and right_conf >= 0.3:
        page_type = "mixed"
        confidence = round(_clamp(0.35 + 0.35 * left_conf + 0.3 * right_conf), 3)
    elif left_conf >= 0.35:
        page_type = "margin_title_like"
        confidence = round(_clamp(0.3 + 0.45 * left_conf + 0.25 * body_conf), 3)
    else:
        page_type = "inline_title_like"
        confidence = round(_clamp(0.25 + 0.35 * body_conf - 0.15 * right_conf + 0.1 * (1.0 - left_conf)), 3)
        notes.append("body band dominates")

    return PageLayoutProfile(page_index=page.page_index, page_type=page_type, confidence=confidence, bands=bands, notes=notes)


def infer_layout_profile(pages: list[PageIR]) -> LayoutProfile:
    repeated_bands = _repeated_y_bands(pages)
    return LayoutProfile(
        page_count=len(pages),
        pages=[infer_page_layout(page, repeated_bands) for page in pages],
    )
